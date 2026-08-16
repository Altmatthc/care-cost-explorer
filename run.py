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
        print(f"\nRefreshing {region}. Hospitals already collected recently are "
              f"skipped,\nand unchanged files are detected without downloading "
              f"them again.")
        for stage in ("registry", "geocode"):
            if build("--region", region, "--stage", stage):
                return 1
        if build("--region", region, "--stage", "prices", "--workers", "4"):
            return 1
        run(["scripts/merge_shards.py", "--region", region], "Publishing")
        return run(["scripts/validate_data.py", "--region", region], "Validating")

    if cmd == "region":
        if not rest:
            print("Which region? e.g.  python scripts/run.py region ca")
            return 1
        target = rest[0]
        print(f"\nPulling {target} from scratch. A whole state is roughly "
              f"400 hospitals\nand several gigabytes — expect hours. It "
              f"checkpoints after every\nhospital, so stopping and resuming "
              f"is safe.\n")
        for stage in ("registry", "geocode"):
            if build("--region", target, "--stage", stage):
                return 1
        if build("--region", target, "--stage", "prices", "--workers", "6"):
            return 1
        run(["scripts/merge_shards.py", "--region", target], "Publishing")
        return run(["scripts/validate_data.py", "--region", target], "Validating")

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
