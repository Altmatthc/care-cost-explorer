#!/usr/bin/env python3
"""
Publish the code catalogue to a GitHub Release.

Release assets live OUTSIDE git history — you replace them in place rather
than accumulating a copy of every version forever. That's the difference
between a repository that grows by ~100 MB per refresh and one that doesn't
grow at all.

    python scripts/publish_release.py --region san-diego
    python scripts/publish_release.py --region ca --dry-run

Requires the GitHub CLI (`gh`), which handles authentication for you:
    Windows:  winget install --id GitHub.cli
    macOS:    brew install gh
    then:     gh auth login

Only CHANGED files are uploaded. A refresh where most prices held steady
uploads a handful of assets, not eleven hundred.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def have_gh() -> bool:
    return shutil.which("gh") is not None


def gh(*args, check=True, quiet=False):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0 and check and not quiet:
        print(f"  gh {' '.join(args[:3])}... failed:")
        print("  " + (r.stderr or r.stdout).strip()[:400])
    return r


def repo_slug() -> str | None:
    r = gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner",
           check=False, quiet=True)
    return r.stdout.strip() if r.returncode == 0 else None


def digest(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="san-diego")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be uploaded, change nothing")
    ap.add_argument("--force", action="store_true",
                    help="re-upload everything, ignoring the manifest")
    args = ap.parse_args()

    codes_dir = DATA / f"{args.region}-codes"
    index_file = DATA / f"{args.region}-search.json"
    if not codes_dir.exists() or not index_file.exists():
        print(f"No catalogue for {args.region}. Run the pipeline first:\n"
              f"  python scripts/run.py full\n"
              f"  python scripts/run.py merge")
        return 1

    if not have_gh() and not args.dry_run:
        print("The GitHub CLI (gh) isn't installed.\n"
              "  Windows:  winget install --id GitHub.cli\n"
              "  macOS:    brew install gh\n"
              "Then run:  gh auth login")
        return 1

    tag = f"data-{args.region}"
    files = sorted(codes_dir.glob("*.json")) + [index_file]
    total_mb = sum(f.stat().st_size for f in files) / 1e6

    # Manifest of what we last uploaded, so only changes go up.
    man_path = DATA / f".release-manifest-{args.region}.json"
    previous = {}
    if man_path.exists() and not args.force:
        try:
            previous = json.loads(man_path.read_text())
        except Exception:
            previous = {}

    def asset_name(f: Path) -> str:
        """
        Asset names must match exactly what the site requests:
            <region>--search.json
            <region>--codes-<prefix>.json
        Release assets share one flat namespace, so the region prefix keeps
        buckets from different states colliding.
        """
        if f.name.endswith("-search.json"):
            return f"{args.region}--search.json"
        return f"{args.region}--codes-{f.stem}.json"

    current, changed = {}, []
    for f in files:
        d = digest(f)
        asset = asset_name(f)
        current[asset] = d
        if previous.get(asset) != d:
            changed.append((asset, f))

    removed = [a for a in previous if a not in current]

    print(f"\nCatalogue for {args.region}")
    print(f"  {len(files)} file(s), {total_mb:.1f} MB total")
    print(f"  {len(changed)} changed and would be uploaded")
    if removed:
        print(f"  {len(removed)} no longer needed and would be deleted")
    if not changed and not removed:
        print("\nNothing to do — the published catalogue is already current.")
        return 0

    if args.dry_run:
        for asset, f in changed[:10]:
            print(f"    upload {asset} ({f.stat().st_size/1024:.0f} KB)")
        if len(changed) > 10:
            print(f"    ... and {len(changed)-10} more")
        print("\n(dry run — nothing changed)")
        return 0

    slug = repo_slug()
    if not slug:
        print("Couldn't determine the repository. Run `gh auth login` first.")
        return 1

    # Create the release if it doesn't exist yet.
    if gh("release", "view", tag, check=False, quiet=True).returncode != 0:
        print(f"\nCreating release {tag} ...")
        notes = (f"Code catalogue for {args.region}.\n\n"
                 f"Published here rather than in git so that refreshing the "
                 f"data doesn't grow the repository. Assets are replaced in "
                 f"place.\n\nGenerated by scripts/publish_release.py — not "
                 f"intended to be downloaded by hand.")
        if gh("release", "create", tag, "--title", f"Data: {args.region}",
              "--notes", notes).returncode != 0:
            return 1

    print(f"\nUploading {len(changed)} file(s) to {slug} release {tag} ...")
    ok = 0
    BATCH = 40
    for i in range(0, len(changed), BATCH):
        batch = changed[i:i+BATCH]
        staged = DATA / ".release-staging"
        staged.mkdir(exist_ok=True)
        for asset, f in batch:
            shutil.copy(f, staged / asset)
        paths = [str(staged / asset) for asset, _ in batch]
        r = gh("release", "upload", tag, *paths, "--clobber")
        for p in paths:
            Path(p).unlink(missing_ok=True)
        if r.returncode == 0:
            ok += len(batch)
            print(f"  {ok}/{len(changed)}")
        else:
            print(f"  batch failed at {i}; stopping so the manifest stays honest")
            break
        shutil.rmtree(staged, ignore_errors=True)

    for asset in removed:
        gh("release", "delete-asset", tag, asset, "--yes", check=False, quiet=True)

    if ok == len(changed):
        man_path.write_text(json.dumps(current, indent=1, sort_keys=True))
        base = f"https://github.com/{slug}/releases/download/{tag}"
        print(f"\nPublished. Catalogue base URL:\n  {base}")
        print("\nRecording it in regions.json so the site knows where to look...")
        rp = DATA / "regions.json"
        try:
            man = json.loads(rp.read_text()) if rp.exists() else {}
            if args.region in man:
                man[args.region]["catalogue_base"] = base
                rp.write_text(json.dumps(man, indent=1))
                print("  done — commit regions.json to make it live")
        except Exception as e:
            print(f"  couldn't update regions.json: {e}")
        print("\nYou can now stop committing the catalogue. Add to .gitignore:")
        print("  data/*-search.json")
        print("  data/*-codes/")
    else:
        print(f"\nOnly {ok}/{len(changed)} uploaded. The manifest was NOT "
              f"updated, so re-running will retry the rest.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
