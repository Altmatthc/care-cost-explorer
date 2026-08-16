#!/usr/bin/env python3
"""
Merge sharded price output into the single compact file the website loads.

Also does the payer-name normalization that makes the insurance dropdown work:
hospitals write payer names however they like ("BLUE CROSS OF CALIFORNIA",
"Anthem BC PPO", "AnthemBlueCross_HMO"), so we map them onto a small set of
consistent plan families.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Order matters — first match wins, so put specific patterns before general.
PAYER_PATTERNS = [
    ("kaiser",     r"kaiser"),
    ("medicare",   r"\bmedicare\b|\bmcr\b"),
    ("medical",    r"medi-?cal|medicaid|\bmcd\b"),
    ("anthem",     r"anthem|blue\s*cross"),
    ("blueshield", r"blue\s*shield"),
    ("aetna",      r"aetna"),
    ("cigna",      r"cigna"),
    ("uhc",        r"united\s*health|\buhc\b|unitedhealthcare"),
    ("healthnet",  r"health\s*net"),
    ("tricare",    r"tricare"),
]


def normalize_payer(name: str) -> str | None:
    n = (name or "").lower()
    for key, pattern in PAYER_PATTERNS:
        if re.search(pattern, n):
            return key
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="san-diego")
    args = ap.parse_args()

    # Collect every shard
    merged: dict[str, dict] = {}
    shards = sorted(DATA.glob(f"{args.region}-prices*.json"))
    for path in shards:
        try:
            merged.update(json.loads(path.read_text()))
        except Exception as e:
            print(f"  skipping unreadable shard {path.name}: {e}")
    print(f"merged {len(shards)} shard file(s) -> {len(merged)} price records")

    # Reshape into what the site wants: procedure -> hospital -> prices
    site: dict[str, dict] = {}
    unmapped: dict[str, int] = {}
    for rec in merged.values():
        proc = rec["procedure"]
        hid = rec["hospital_id"]
        payers: dict[str, float] = {}
        for raw_name, rate in (rec.get("payers") or {}).items():
            key = normalize_payer(raw_name)
            if key is None:
                unmapped[raw_name] = unmapped.get(raw_name, 0) + 1
                continue
            # A plan family can appear many times (HMO, PPO, tiers). Keep the
            # median-ish value by taking the lowest, which is what a patient
            # on that plan family would most plausibly encounter.
            if key not in payers or rate < payers[key]:
                payers[key] = rate
        site.setdefault(proc, {})[hid] = {
            "cash": rec.get("cash"),
            "gross": rec.get("gross"),
            "min": rec.get("min"),
            "max": rec.get("max"),
            "payers": payers,
            "shared": bool(rec.get("shared_source")),
        }

    out = DATA / f"{args.region}.json"
    from datetime import date
    out.write_text(json.dumps({"region": args.region,
                               "updated": date.today().isoformat(),
                               "prices": site},
                              separators=(",", ":")))
    size_kb = out.stat().st_size / 1024
    print(f"wrote {out.name} ({size_kb:.0f} KB, {len(site)} procedures)")

    if unmapped:
        top = sorted(unmapped.items(), key=lambda x: -x[1])[:15]
        print("\nUnmapped payer names (add patterns to PAYER_PATTERNS if these matter):")
        for name, count in top:
            print(f"  {count:>6}x  {name}")

    # Clean up shard files so the repo stays tidy
    for path in shards:
        if re.search(r"-prices-\d+\.json$", path.name):
            path.unlink()


if __name__ == "__main__":
    main()
