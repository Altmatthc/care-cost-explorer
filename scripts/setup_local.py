#!/usr/bin/env python3
"""
Set up this project to run on your own machine.

    python3 scripts/setup_local.py          (macOS / Linux)
    python  scripts\\setup_local.py          (Windows)

Creates an isolated environment in .venv, installs the two dependencies, and
runs the test suite to prove it works. Safe to run again at any time.
"""

import os
import platform
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
DEPS = ["requests", "ijson"]

IS_WINDOWS = platform.system() == "Windows"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def say(msg, mark="-"):
    print(f"  {mark} {msg}")


def main():
    print("\nCare Cost Explorer — local setup\n" + "=" * 40)

    # --- Python version ---
    v = sys.version_info
    print(f"\nPython {v.major}.{v.minor}.{v.micro} on {platform.system()}")
    if (v.major, v.minor) < (3, 9):
        say("Python 3.9 or newer is required.", "!")
        say("Get it from https://www.python.org/downloads/", "!")
        return 1
    say("version is fine", "ok")

    # --- isolated environment ---
    print("\nEnvironment")
    if VENV_PY.exists():
        say(f"already exists at {VENV}", "ok")
    else:
        say(f"creating {VENV} ...")
        try:
            venv.EnvBuilder(with_pip=True).create(VENV)
            say("created", "ok")
        except Exception as e:
            say(f"could not create it: {e}", "!")
            say("You can still run without one — install the dependencies "
                "with:  pip install requests ijson", "!")
            return 1

    # --- dependencies ---
    print("\nDependencies")
    try:
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "--quiet",
                        "--upgrade", "pip"], check=False,
                       capture_output=True)
        if DEPS:
            r = subprocess.run([str(VENV_PY), "-m", "pip", "install", "--quiet"] + DEPS,
                               capture_output=True, text=True)
            if r.returncode != 0:
                say("install failed:", "!")
                print(r.stderr[-1200:])
                if "No matching distribution" in r.stderr or "Network" in r.stderr:
                    say("This usually means no internet connection, or a "
                        "corporate proxy/firewall is blocking PyPI.", "!")
                    say("If you're behind a proxy, set HTTPS_PROXY and retry.", "!")
                return 1
        for d in DEPS:
            say(f"{d} installed", "ok")
    except Exception as e:
        say(f"install failed: {e}", "!")
        return 1

    # --- prove it works ---
    print("\nVerifying")
    r = subprocess.run([str(VENV_PY), str(ROOT / "scripts" / "smoke_test.py")],
                       capture_output=True, text=True)
    passes = r.stdout.count("[PASS]")
    if r.returncode == 0:
        say(f"test suite passed ({passes} checks)", "ok")
    else:
        say("test suite FAILED:", "!")
        detail = (r.stderr or "").strip() or (r.stdout or "").strip()
        print(detail[-1800:])
        if "ModuleNotFoundError" in detail:
            say("A dependency is missing — re-run this script, and if it "
                "fails again check your internet connection.", "!")
        else:
            say("Your local files may be out of date. Pull the latest, then "
                "re-run.", "!")
        return 1

    # --- what to do next ---
    activate = (f"{VENV}\\Scripts\\activate" if IS_WINDOWS
                else f"source {VENV.relative_to(Path.cwd()) if VENV.is_relative_to(Path.cwd()) else VENV}/bin/activate")
    print("\n" + "=" * 40)
    print("Ready.\n")
    print("Use the runner for everything — it handles the environment for you:\n")
    print("  python scripts/run.py test              quick self-check")
    print("  python scripts/run.py refresh           update San Diego")
    print("  python scripts/run.py region ca         pull all of California")
    print("  python scripts/run.py check             validate the data")
    print("  python scripts/run.py trends            show price movements")
    print("  python scripts/run.py help              everything else")
    print(f"\n(If you prefer to activate the environment yourself: {activate})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
