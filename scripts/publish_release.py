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

    # GitHub hard-caps a release at 1000 assets, so large regions roll across
    # multiple releases: data-<region>, data-<region>-2, ... . The per-region
    # bucket map (data/.release-map-<region>.json) tells the site which
    # release each code lives in.
    MAX_ASSETS = 1000

    def tag_for(n: int) -> str:
        return f"data-{args.region}" if n == 1 else f"data-{args.region}-{n}"

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

    # Bucket map: code key (e.g. "AB1") -> release tag. search.json lives on
    # the base release for every region. A code's bucket is immutable — the
    # site only checks one release per code, so a re-upload must land where
    # the code already lives even if that release is full.
    map_path = DATA / f".release-map-{args.region}.json"
    bucket_map = {}
    if map_path.exists() and not args.force:
        try:
            bucket_map = json.loads(map_path.read_text())
        except Exception:
            bucket_map = {}

    def code_key(f: Path) -> str:
        return f.stem.replace("codes-", "")

    current, changed = {}, []
    for f in files:
        d = digest(f)
        asset = asset_name(f)
        current[asset] = d
        if previous.get(asset) != d:
            changed.append((asset, f))
        ck = None if f.name.endswith("-search.json") else code_key(f)
        if ck and ck not in bucket_map:
            # New code: assign it to the least-loaded release with room.
            best, best_load = tag_for(1), 10**9
            for i in range(1, 20):
                t = tag_for(i)
                load = sum(1 for v in bucket_map.values() if v == t) + (1 if i == 1 else 0)
                if load < best_load:
                    best, best_load = t, load
            if best_load >= MAX_ASSETS - 5:
                print(f"\nERROR: {args.region} has no release with room "
                      f"({MAX_ASSETS}/release cap). Split the region or prune.")
                return 1
            bucket_map[ck] = best

    removed = [a for a in previous if a not in current]

    print(f"\nCatalogue for {args.region}")
    print(f"  {len(files)} file(s), {total_mb:.1f} MB total")
    print(f"  {len(changed)} changed and would be uploaded across "
          f"{len(set(bucket_map.values())) or 1} release(s)")
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

    # Create any releases we need.
    needed_tags = sorted({tag_for(1), *bucket_map.values()})
    for t in needed_tags:
        if gh("release", "view", t, check=False, quiet=True).returncode != 0:
            print(f"\nCreating release {t} ...")
            notes = (f"Code catalogue for {args.region}. Part of a multi-release "
                     f"set because GitHub caps releases at 1000 assets.\n\n"
                     f"Generated by scripts/publish_release.py — not intended "
                     f"to be downloaded by hand.")
            if gh("release", "create", t, "--title", f"Data: {args.region}",
                  "--notes", notes).returncode != 0:
                return 1

    # Upload in batches so a failure doesn't lose everything. Each code goes
    # to the release its bucket map assigns it to; search.json stays on base.
    print(f"\nUploading {len(changed)} file(s) across "
          f"{len(needed_tags)} release(s) to {slug} ...")
    ok = 0
    BATCH = 40
    for i in range(0, len(changed), BATCH):
        batch = changed[i:i+BATCH]
        staged = DATA / ".release-staging"
        staged.mkdir(exist_ok=True)
        paths = []
        for asset, f in batch:
            shutil.copy(f, staged / asset)
            paths.append(str(staged / asset))
        # Group this batch by target release.
        groups = {}
        for asset, _ in batch:
            ck = None if asset.endswith("-search.json") else \
                asset.split("codes-")[1].removesuffix(".json")
            t = tag_for(1) if ck is None else bucket_map.get(ck, tag_for(1))
            groups.setdefault(t, []).append(asset)
        failed = False
        for t, assets in groups.items():
            r = gh("release", "upload", t, *[str(staged / a) for a in assets], "--clobber")
            if r.returncode != 0:
                msg = (r.stderr or r.stdout).strip()[:200]
                print(f"  batch failed at {i} on release {t}: {msg}")
                # Rate limits and transient network errors clear themselves —
                # wait a few minutes and retry the same batch before giving up.
                for attempt in range(1, 4):
                    if "rate limit" not in msg.lower() and "timeout" not in msg.lower():
                        break
                    wait = 180 * attempt
                    print(f"  waiting {wait//60} min (attempt {attempt}) ...")
                    import time as _t; _t.sleep(wait)
                    r = gh("release", "upload", t, *[str(staged / a) for a in assets], "--clobber")
                    msg = (r.stderr or r.stdout).strip()[:200] if r.returncode != 0 else ""
                if r.returncode != 0:
                    print(f"  still failing after retries; stopping so the manifest stays honest")
                    failed = True
                    break
        for p in paths:
            Path(p).unlink(missing_ok=True)
        shutil.rmtree(staged, ignore_errors=True)
        if failed:
            return 1
        ok += len(batch)
        print(f"  {ok}/{len(changed)}")

    # Delete stale assets from the release they live on.
    for asset in removed:
        ck = None if asset.endswith("-search.json") else \
            asset.split("codes-")[1].removesuffix(".json")
        t = tag_for(1) if ck is None else bucket_map.get(ck, tag_for(1))
        gh("release", "delete-asset", t, asset, "--yes", check=False, quiet=True)

    if ok == len(changed):
        man_path.write_text(json.dumps(current, indent=1, sort_keys=True))
        map_path.write_text(json.dumps(bucket_map, indent=1, sort_keys=True))
        base = f"https://github.com/{slug}/releases/download/{tag_for(1)}"
        print(f"\nPublished. Catalogue base URL:\n  {base}")
        if len(set(bucket_map.values())) > 1:
            # Multi-release split: the site needs the bucket->release map to
            # know which release each codes-<prefix>.json lives on. Commit it
            # alongside regions.json (both are small, gitignored nowhere).
            map_dest = DATA / f"release-map-{args.region}.json"
            shutil.copy(map_path, map_dest)
            print(f"Buckets split across releases — copied map to {map_dest.name} "
                  "(commit it with regions.json so the site can resolve buckets).")
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
    else:
        print(f"\nOnly {ok}/{len(changed)} uploaded. The manifest was NOT "
              f"updated, so re-running will retry the rest.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
