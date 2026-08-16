#!/usr/bin/env python3
"""
Report data size and projected git growth.

Every refresh that changes a file stores a new version in git history
permanently. Run this occasionally to see where you stand rather than
discovering the problem at 5 GB.

    python scripts/data_size_report.py
"""
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main():
    if not DATA.exists():
        print("no data directory yet")
        return

    files = sorted(DATA.glob("*.json"), key=lambda p: -p.stat().st_size)
    total = sum(f.stat().st_size for f in files)
    print("Current data files:")
    for f in files:
        size = f.stat().st_size
        flag = ""
        if size > 50 * 1024 * 1024:
            flag = "  <-- over 50MB, GitHub warns"
        if size > 100 * 1024 * 1024:
            flag = "  <-- OVER 100MB, GitHub BLOCKS this file"
        print(f"  {f.name:34s} {human(size):>10}{flag}")
    print(f"  {'TOTAL':34s} {human(total):>10}\n")

    try:
        out = subprocess.run(["git", "count-objects", "-vH"],
                             capture_output=True, text=True, cwd=DATA.parent)
        for line in out.stdout.splitlines():
            if line.startswith("size-pack"):
                print(f"Repository packed size: {line.split(':')[1].strip()}")
    except Exception:
        pass

    print("\nProjected growth if every refresh changes every file:")
    for label, per_year in (("monthly", 12), ("weekly", 52)):
        raw = total * per_year
        print(f"  {label:8s} {human(raw):>10}/year raw, "
              f"~{human(raw * 0.25):>10}/year after packing")

    print("\nThresholds: 100MB blocks a single file; 5GB triggers repo warnings.")
    print("If you approach either, move data to GitHub Releases — see README.")


if __name__ == "__main__":
    main()
