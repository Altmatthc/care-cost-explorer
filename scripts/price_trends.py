#!/usr/bin/env python3
"""
Report price movements from the append-only history.

The published files only ever show today's price. This reads the history we
keep and answers the question nobody else can: what changed, and by how much?

    python scripts/price_trends.py --region san-diego
    python scripts/price_trends.py --region ca --procedure mri-brain
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="san-diego")
    ap.add_argument("--procedure", default="", help="limit to one procedure id")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    hist = DATA / f"{args.region}-history.csv"
    if not hist.exists():
        print(f"No history yet for {args.region}. It starts building on the "
              f"next refresh, and changes only appear once a price actually moves.")
        return

    names = {}
    reg = DATA / f"{args.region}-hospitals.json"
    if reg.exists():
        try:
            names = {h["id"]: h["name"] for h in json.loads(reg.read_text())}
        except Exception:
            pass

    series = defaultdict(list)
    with hist.open() as fh:
        for row in csv.DictReader(fh):
            if args.procedure and row["procedure"] != args.procedure:
                continue
            try:
                series[(row["hospital_id"], row["procedure"])].append(
                    (row["date"], float(row["cash"])))
            except (ValueError, KeyError):
                continue

    moves = []
    for (hid, proc), points in series.items():
        if len(points) < 2:
            continue
        points.sort()
        first_d, first_p = points[0]
        last_d, last_p = points[-1]
        if not first_p:
            continue
        pct = (last_p - first_p) / first_p * 100
        moves.append((pct, hid, proc, first_p, last_p, first_d, last_d, len(points)))

    if not moves:
        obs = sum(len(v) for v in series.values())
        print(f"{obs} price observation(s) recorded, but nothing has changed yet.")
        print("Changes appear once a hospital publishes a different price.")
        return

    moves.sort(key=lambda m: -abs(m[0]))
    print(f"Largest price movements in {args.region} "
          f"({len(moves)} series with a change):\n")
    for pct, hid, proc, fp, lp, fd, ld, n in moves[: args.top]:
        arrow = "UP  " if pct > 0 else "DOWN"
        who = names.get(hid, hid)[:38]
        print(f"  {arrow} {abs(pct):5.1f}%   {proc:<20} {who}")
        print(f"          ${fp:,.0f} ({fd})  ->  ${lp:,.0f} ({ld})   "
              f"{n} observations")

    rises = [m for m in moves if m[0] > 0]
    if rises:
        avg = sum(m[0] for m in rises) / len(rises)
        print(f"\n  {len(rises)} of {len(moves)} tracked prices rose; "
              f"average increase {avg:.1f}%")


if __name__ == "__main__":
    main()
