#!/usr/bin/env python3
"""
Run the pipeline locally without remembering flags.

    python scripts/run.py refresh            update San Diego
    python scripts/run.py region ca          pull a whole state
    python scripts/run.py check              validate published data
    python scripts/run.py trends             show price movements
    python scripts/run.py help               full list

Automatically uses .venv if setup_local.py created one, so there's nothing to
activate. Every command prints the underlying build_data.py invocation, so you
can see what it's actually doing and run it by hand when you want to.
"""

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / (".venv/Scripts/python.exe" if platform.system() == "Windows"
                  else ".venv/bin/python")
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable
DEFAULT_REGION = "san-diego"


def run(args, label=None):
    """Run a script in this project, echoing the real command first."""
    cmd = [PY] + [str(a) for a in args]
    shown = " ".join(["python"] + [str(a) for a in args])
    if label:
        print(f"\n=== {label} ===")
    print(f"$ {shown}\n")
    started = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    mins = (time.time() - started) / 60
    if mins > 1:
        print(f"\n({mins:.1f} minutes)")
    return r.returncode


def build(*args):
    return run(["scripts/build_data.py", *args])


def git(*args, quiet=False):
    """Run a git command, returning (returncode, output)."""
    try:
        r = subprocess.run(["git", *args], cwd=ROOT,
                           capture_output=True, text=True)
        if not quiet and r.stdout.strip():
            print(r.stdout.strip())
        return r.returncode, (r.stdout + r.stderr)
    except FileNotFoundError:
        return 127, "git not found"


def pull_first():
    """
    Pull before collecting.

    data/<region>-history.csv is append-only and irreplaceable — there is no
    public archive of past hospital price files, so a row lost to a botched
    merge is gone permanently. Pulling first means this machine is the only
    thing appending, which is the whole point of running refreshes locally.
    """
    code, out = git("rev-parse", "--is-inside-work-tree", quiet=True)
    if code != 0:
        return True                      # not a git checkout; nothing to do
    print("Checking for remote changes first (protects the price history)...")
    code, out = git("pull", "--ff-only", quiet=True)
    if code == 0:
        msg = "already up to date" if "up to date" in out.lower() else "pulled"
        print(f"  {msg}\n")
        return True

    low = out.lower()
    # No remote configured, or simply offline. Neither can cause the conflict
    # we're guarding against, so don't block the run over it.
    benign = ("no tracking information", "does not appear to be a git repository",
              "could not resolve host", "no such remote", "unable to access")
    if any(b in low for b in benign):
        print("  no remote to check — continuing\n")
        return True
    print("\n  git pull failed:\n" + "\n".join(
        "    " + l for l in out.strip().splitlines()[:8]))
    print("\n  Resolve this before collecting — continuing risks a conflict")
    print("  in the append-only price history, where rows get lost.\n")
    return False


def publish(region):
    """Offer to commit and push what was just collected."""
    code, _ = git("rev-parse", "--is-inside-work-tree", quiet=True)
    if code != 0:
        return
    code, out = git("status", "--porcelain", "data/", quiet=True)
    if not out.strip():
        print("\nNothing changed in data/ — nothing to publish.")
        return
    changed = len([l for l in out.strip().splitlines() if l.strip()])
    print(f"\n{changed} file(s) changed in data/.")
    try:
        ans = input("Commit and push now? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if ans and not ans.startswith("y"):
        print("Left uncommitted. Publish later with:\n"
              "  git add data/ && git commit -m \"Refresh\" && git push")
        return
    git("add", "data/")
    git("commit", "-m", f"Refresh {region} price data")
    code, _ = git("push")
    print("Published." if code == 0 else "Push failed — see the message above.")


def usage():
    print(__doc__)
    print("""
COMMANDS

  test                    run the test suite (30 seconds, no network)
  refresh [region]        registry + geocode + prices, incremental
                          default region: san-diego
  region <code>           pull a region from scratch: ca, tx, ny, california
  full [region]           re-scan everything, ignoring what's already current
  only <text> [region]    scan just the hospitals matching text, e.g. kaiser
  merge [region]          rebuild the published files from collected data
  check [region]          validate published prices for implausible values
  trends [region]         show price movements from the history archive
  size                    data size and projected repository growth
  release [region]        publish the code catalogue to a GitHub Release
  probe <url>             inspect one hospital's price file
  verify <url> <code>     show the raw rows for one billing code
  serve                   preview the site locally at http://localhost:8000

NOTES

  A local run has no 6-hour limit, so a whole state can run in one go without
  sharding. Use --workers to control concurrency (default 4, one per host).

  Nothing is published until you commit the files in data/ and push.
""")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "-h", "--help"):
        usage()
        return 0

    cmd = sys.argv[1]
    rest = sys.argv[2:]
    region = rest[0] if rest and not rest[0].startswith("-") else DEFAULT_REGION

    if cmd == "test":
        return run(["scripts/smoke_test.py"], "Test suite")

    if cmd == "refresh":
        if not pull_first():
            return 1
        print(f"Refreshing {region}. Hospitals already collected recently are "
              f"skipped,\nand unchanged files are detected without downloading "
              f"them again.")
        for stage in ("registry", "geocode"):
            if build("--region", region, "--stage", stage):
                return 1
        if build("--region", region, "--stage", "prices", "--workers", "4"):
            return 1
        run(["scripts/merge_shards.py", "--region", region], "Publishing")
        rc = run(["scripts/validate_data.py", "--region", region], "Validating")
        publish(region)
        return rc

    if cmd == "region":
        if not rest:
            print("Which region? e.g.  python scripts/run.py region ca")
            return 1
        target = rest[0]
        if not pull_first():
            return 1
        print(f"Pulling {target} from scratch. A whole state is roughly "
              f"400 hospitals\nand several gigabytes — expect hours. It "
              f"checkpoints after every\nhospital, so stopping and resuming "
              f"is safe.\n")
        for stage in ("registry", "geocode"):
            if build("--region", target, "--stage", stage):
                return 1
        if build("--region", target, "--stage", "prices", "--workers", "8"):
            return 1
        run(["scripts/merge_shards.py", "--region", target], "Publishing")
        rc = run(["scripts/validate_data.py", "--region", target], "Validating")
        publish(target)
        return rc

    if cmd == "full":
        if build("--region", region, "--stage", "prices",
                 "--refresh-all", "--workers", "4"):
            return 1
        return run(["scripts/merge_shards.py", "--region", region], "Publishing")

    if cmd == "only":
        if not rest:
            print("Which hospitals? e.g.  python scripts/run.py only kaiser")
            return 1
        target = rest[1] if len(rest) > 1 else DEFAULT_REGION
        if build("--region", target, "--stage", "prices",
                 "--only", rest[0], "--workers", "2"):
            return 1
        return run(["scripts/merge_shards.py", "--region", target], "Publishing")

    if cmd == "release":
        return run(["scripts/publish_release.py", "--region", region],
                   "Publishing catalogue to GitHub Releases")

    if cmd == "merge":
        return run(["scripts/merge_shards.py", "--region", region], "Publishing")

    if cmd == "check":
        return run(["scripts/validate_data.py", "--region", region], "Validating")

    if cmd == "trends":
        return run(["scripts/price_trends.py", "--region", region], "Price movements")

    if cmd == "size":
        return run(["scripts/data_size_report.py"], "Data size")

    if cmd == "probe":
        if not rest:
            print("Needs a URL.")
            return 1
        return build("--stage", "probe", "--url", rest[0])

    if cmd == "verify":
        if len(rest) < 2:
            print("Needs a URL and a code, e.g.\n"
                  "  python scripts/run.py verify <url> 44970")
            return 1
        return build("--stage", "verify", "--url", rest[0], "--code", rest[1])

    if cmd == "serve":
        port = rest[0] if rest and rest[0].isdigit() else "8000"
        # Bind IPv4 explicitly. Left to itself, Python on Windows binds "::"
        # (IPv6 only), and a browser asking for 127.0.0.1 gets an empty
        # response with no error from either side.
        print(f"\nServing at http://127.0.0.1:{port}")
        print("Open that in a browser. Ctrl+C to stop.\n")
        os.chdir(ROOT)
        r = subprocess.run([PY, "-m", "http.server", port,
                            "--bind", "127.0.0.1"])
        if r.returncode not in (0, 1):
            print(f"\nCouldn't serve on port {port}. Something else may be "
                  f"using it — try:  python scripts/run.py serve 8080")
        return r.returncode

    print(f"Unknown command: {cmd}\n")
    usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
