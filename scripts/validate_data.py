#!/usr/bin/env python3
"""
Validate published data and flag anything implausible.

The failure mode that matters isn't a crash — it's a wrong number that looks
reasonable. Reading the gross charge where we meant the cash price produces a
perfectly plausible figure that happens to be triple the truth. This checks
the output against what the values ought to look like.

    python scripts/validate_data.py --region san-diego

Exits non-zero if anything looks broken, so it can gate a workflow.
"""

import argparse
import json
import statistics
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# Plausible cash-price bounds. Deliberately wide — these catch order-of-
# magnitude errors (a decimal point, a per-unit rate read as a total),
# not genuine variation between hospitals.
BOUNDS = {
    "flu-shot":          (10, 400),
    "blood-cbc":         (10, 600),
    "blood-metabolic":   (10, 700),
    "strep-test":        (10, 600),
    "pap-smear":         (20, 900),
    "urgent-care":       (40, 2_000),
    "chest-xray":        (25, 2_000),
    "xray-extremity":    (25, 2_000),
    "xray-leg":          (25, 2_000),
    "ekg":               (20, 1_500),
    "physical-therapy":  (30, 1_500),
    "hearing-test":      (20, 1_500),
    "joint-injection":   (40, 3_000),
    "mammogram-screen":  (40, 2_500),
    "mammogram-diag":    (50, 3_500),
    "us-abdomen":        (50, 4_000),
    "us-pregnancy":      (50, 4_000),
    "thyroid-us":        (50, 4_000),
    "iv-hydration":      (40, 5_000),
    "mri-brain":         (200, 15_000),
    "mri-spine":         (200, 15_000),
    "mri-knee":          (200, 15_000),
    "ct-head":           (150, 12_000),
    "ct-abd-pelvis":     (150, 15_000),
    "echocardiogram":    (150, 12_000),
    "colonoscopy":       (300, 20_000),
    "endoscopy":         (300, 20_000),
    "cataract":          (500, 30_000),
    "appendectomy":      (2_000, 120_000),
    "gallbladder":       (2_000, 150_000),
    "hernia-repair":     (1_000, 120_000),
    "knee-replacement":  (5_000, 400_000),
    "hip-replacement":   (5_000, 400_000),
    "spinal-fusion":     (8_000, 600_000),
    "vaginal-delivery":  (1_000, 100_000),
    "cesarean":          (2_000, 150_000),
    "cardiac-cath":      (1_000, 150_000),
}

problems, warnings = [], []


def fail(msg):
    problems.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg):
    warnings.append(msg)
    print(f"  warn  {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="san-diego")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as failures")
    args = ap.parse_args()

    dpath = DATA / f"{args.region}.json"
    rpath = DATA / f"{args.region}-hospitals.json"
    if not dpath.exists():
        print(f"No published data at {dpath}. Run the pipeline first.")
        raise SystemExit(1)

    data = json.loads(dpath.read_text())
    prices = data.get("prices", {})
    hospitals = json.loads(rpath.read_text()) if rpath.exists() else []
    hmap = {h["id"]: h for h in hospitals}
    names = {h["id"]: h.get("name", h["id"]) for h in hospitals}

    print(f"\nValidating {args.region}: {len(prices)} procedures, "
          f"{len(hospitals)} hospitals in registry\n")

    # --- registry sanity ---
    print("Registry")
    missing_geo = [h["name"] for h in hospitals if h.get("lat") is None]
    if missing_geo:
        warn(f"{len(missing_geo)} hospital(s) have no coordinates and can't be "
             f"mapped: {', '.join(missing_geo[:4])}")
    else:
        print("  ok    every hospital has coordinates")

    priced = {hid for byh in prices.values() for hid in byh}
    unknown = priced - set(hmap)
    if unknown:
        fail(f"{len(unknown)} hospital id(s) have prices but aren't in the "
             f"registry: {list(unknown)[:4]}")
    else:
        print(f"  ok    all {len(priced)} priced hospitals exist in the registry")

    # --- price plausibility ---
    print("\nPrice plausibility")
    checked = flagged = 0
    for proc, byh in prices.items():
        lo, hi = BOUNDS.get(proc, (None, None))
        vals = [r["cash"] for r in byh.values() if r.get("cash")]
        if not vals:
            continue
        for hid, rec in byh.items():
            cash = rec.get("cash")
            if not cash:
                continue
            checked += 1
            if lo and cash < lo:
                flagged += 1
                fail(f"{proc} at {names.get(hid, hid)[:34]}: ${cash:,.0f} is "
                     f"below the plausible floor of ${lo:,}")
            elif hi and cash > hi:
                flagged += 1
                fail(f"{proc} at {names.get(hid, hid)[:34]}: ${cash:,.0f} is "
                     f"above the plausible ceiling of ${hi:,}")
            if rec.get("gross") and rec["gross"] < cash * 0.99:
                flagged += 1
                warn(f"{proc} at {names.get(hid, hid)[:30]}: gross "
                     f"${rec['gross']:,.0f} is below cash ${cash:,.0f} — "
                     f"columns may be swapped")
        # outliers against peers
        if len(vals) >= 4:
            med = statistics.median(vals)
            for hid, rec in byh.items():
                c = rec.get("cash")
                if c and med and (c > med * 12 or c < med / 12):
                    warn(f"{proc} at {names.get(hid, hid)[:30]}: ${c:,.0f} vs "
                         f"peer median ${med:,.0f} — worth a manual check")
    print(f"  ok    {checked:,} prices checked, {flagged} flagged")

    # --- payer sanity ---
    print("\nPayer data")
    with_payers = sum(1 for byh in prices.values() for r in byh.values()
                      if r.get("payers"))
    total_recs = sum(len(byh) for byh in prices.values())
    if total_recs and with_payers / total_recs < 0.25:
        warn(f"only {with_payers}/{total_recs} records carry negotiated rates — "
             f"the insurance selector will be mostly empty")
    else:
        print(f"  ok    {with_payers}/{total_recs} records carry negotiated rates")

    odd = [(p, h) for p, byh in prices.items() for h, r in byh.items()
           if r.get("payers") and r.get("cash")
           and min(r["payers"].values()) > r["cash"] * 5]
    if odd:
        warn(f"{len(odd)} record(s) where every negotiated rate is 5x the cash "
             f"price — possible column mismatch")

    # --- duplicate hospitals ---
    print("\nDuplicate detection")
    sigs = {}
    for hid in priced:
        key = tuple(sorted(
            (p, prices[p][hid].get("cash")) for p in prices if hid in prices[p]))
        sigs.setdefault(key, []).append(hid)
    dupes = [v for v in sigs.values() if len(v) > 1]
    if dupes:
        for group in dupes:
            flagged_ok = all(
                prices[p][g].get("shared")
                for g in group for p in prices if g in prices[p])
            msg = (f"identical prices at: "
                   f"{', '.join(names.get(g, g)[:28] for g in group)}")
            print(f"  {'ok   ' if flagged_ok else 'warn '} {msg}"
                  f"{'' if flagged_ok else '  (NOT labelled as system-wide)'}")
            if not flagged_ok:
                warnings.append(msg)
    else:
        print("  ok    no two hospitals publish identical prices")

    # --- coverage ---
    print("\nCoverage")
    per_hosp = {hid: sum(1 for p in prices if hid in prices[p]) for hid in priced}
    thin = {h: n for h, n in per_hosp.items() if n < 10}
    if thin:
        warn(f"{len(thin)} hospital(s) have fewer than 10 procedures priced: "
             f"{', '.join(names.get(h, h)[:26] for h in list(thin)[:4])}")
    if per_hosp:
        print(f"  ok    median {statistics.median(per_hosp.values()):.0f} "
              f"procedures priced per hospital")

    # --- summary ---
    print("\n" + "=" * 62)
    print(f"{len(problems)} failure(s), {len(warnings)} warning(s)")
    if problems:
        print("\nFailures indicate a likely parsing error — investigate with:")
        print("  Actions -> Probe a price file -> url + code")
        raise SystemExit(1)
    if warnings and args.strict:
        raise SystemExit(1)
    print("Data looks plausible.")


if __name__ == "__main__":
    main()
