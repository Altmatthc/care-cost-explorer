#!/usr/bin/env python3
"""
Build the small cross-state summary + per-state trend files the site's
"Compare states" and "Price movers" views read.

    python scripts/build_compare.py            # every region in data/regions.json
    python scripts/build_compare.py --region tx # one region's trends file only (+ compare.json)

Outputs (all committed with data/):
  data/compare.json          one object per region: coverage/compliance counts,
                             per-procedure cash summary (n, median, p25, p75,
                             min, max; omitted when n < 5), trend block.
  data/<region>-trends.json  movers (every real price change, newest first)
                             + per-procedure statewide median series, one
                             point per refresh date.

Both are derived purely from files the merge already writes (<region>.json,
<region>-status.json, <region>-history.csv), so re-running is always safe.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MIN_N = 5          # fewer priced hospitals than this -> no summary for that procedure
MOVE_WINDOW = 90   # days, for the "recent moves" counts


def q(vals: list[float], p: float) -> float:
    """Percentile by nearest-rank on a sorted list."""
    if not vals:
        return 0.0
    k = max(0, min(len(vals) - 1, round(p * (len(vals) - 1))))
    return vals[k]


def load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def history_rows(region: str) -> list[dict]:
    p = DATA / f"{region}-history.csv"
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as fh:
        return [r for r in csv.DictReader(fh)]


def region_summary(region: str) -> dict | None:
    site = load(DATA / f"{region}.json", None)
    if not site:
        return None
    status = site.get("status") or load(DATA / f"{region}-status.json", {})
    meta = site.get("hospital_meta", {})
    prices = site.get("prices", {})

    # --- coverage / compliance ---
    cls = Counter(v.get("status") for v in status.values())
    subject = sum(1 for v in status.values() if v.get("rule_applies"))
    ok = cls.get("ok", 0)
    m300 = sum(1 for m in meta.values() if m.get("meets_300"))
    dollar_share = []
    for m in meta.values():
        qd = m.get("quality") or {}
        tot = sum(qd.values())
        if tot:
            dollar_share.append(qd.get("dollar", 0) / tot)
    coverage = {
        "hospitals": len(status),
        "subject_to_rule": subject,
        "ok": ok,
        "no_file": cls.get("no_file", 0),
        "unreachable": cls.get("unreachable", 0) + cls.get("wrong_file", 0),
        "empty": cls.get("empty", 0),
        "exempt": cls.get("exempt", 0),
        "readable_pct": round(100 * ok / subject) if subject else None,
        "meets_300_pct": round(100 * m300 / len(meta)) if meta else None,
        "dollar_rate_pct": round(100 * statistics.mean(dollar_share)) if dollar_share else None,
    }

    # --- per-procedure cash summary ---
    procs = {}
    for proc, byh in prices.items():
        vals = sorted(r["cash"] for r in byh.values() if r.get("cash"))
        if len(vals) < MIN_N:
            continue
        procs[proc] = {
            "n": len(vals),
            "median": round(statistics.median(vals)),
            "p25": round(q(vals, 0.25)),
            "p75": round(q(vals, 0.75)),
            "min": round(vals[0]),
            "max": round(vals[-1]),
        }

    # --- trend block + trends file ---
    trends = build_trends(region, prices)
    recent = [m for m in trends["movers"]
              if m["date"] >= (date.today() - timedelta(days=MOVE_WINDOW)).isoformat()]
    pcts = sorted(abs(m["pct"]) for m in recent)
    trend = {
        "pairs_tracked": trends["pairs_tracked"],
        "first_date": trends["dates"][0] if trends["dates"] else None,
        "last_date": trends["dates"][-1] if trends["dates"] else None,
        "refreshes": len(trends["dates"]),
        "moves_total": len(trends["movers"]),
        "moves_90d": len(recent),
        "up_90d": sum(1 for m in recent if m["pct"] > 0),
        "median_abs_pct_90d": round(statistics.median(pcts), 1) if pcts else None,
    }
    out_trends = DATA / f"{region}-trends.json"
    out_trends.write_text(json.dumps(
        {"region": region, "updated": site.get("updated"),
         "dates": trends["dates"], "pairs_tracked": trends["pairs_tracked"],
         "movers": trends["movers"], "series": trends["series"]},
        separators=(",", ":"), sort_keys=True), encoding="utf-8")

    return {
        "updated": site.get("updated"),
        "coverage": coverage,
        "procedures": procs,
        "trend": trend,
    }


def build_trends(region: str, prices: dict) -> dict:
    rows = history_rows(region)
    by_pair: dict[tuple, list] = defaultdict(list)
    for r in rows:
        try:
            cash = float(r["cash"]) if r.get("cash") else None
        except ValueError:
            cash = None
        if cash:
            by_pair[(r["hospital_id"], r["procedure"])].append((r["date"], cash))
    dates = sorted({d for pts in by_pair.values() for d, _ in pts})

    # Movers: consecutive observations whose rounded cash differs.
    movers = []
    for (hid, proc), pts in by_pair.items():
        pts.sort()
        for (d0, c0), (d1, c1) in zip(pts, pts[1:]):
            if round(c0) != round(c1) and c0:
                movers.append({"h": hid, "p": proc, "prev": round(c0), "cash": round(c1),
                               "pct": round((c1 - c0) / c0 * 100, 1), "date": d1,
                               "since": d0})
    movers.sort(key=lambda m: (m["date"], -abs(m["pct"])), reverse=True)

    # Statewide median per procedure per refresh date. A pair's price is
    # carried forward between observations (the history only writes a row on
    # first sight, change, or 45-day reconfirm), so each date's median covers
    # every pair known by then.
    series: dict[str, list] = {}
    by_proc: dict[str, dict] = defaultdict(dict)       # proc -> hid -> sorted pts
    for (hid, proc), pts in by_pair.items():
        by_proc[proc][hid] = pts
    for proc, hids in by_proc.items():
        points = []
        for d in dates:
            cur = []
            for pts in hids.values():
                last = None
                for pd, pc in pts:
                    if pd <= d:
                        last = pc
                    else:
                        break
                if last:
                    cur.append(last)
            if len(cur) >= MIN_N:
                points.append([d, round(statistics.median(cur)), len(cur)])
        if points:
            series[proc] = points

    return {"dates": dates, "pairs_tracked": len(by_pair), "movers": movers, "series": series}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="", help="limit the trends rebuild to one region")
    a = ap.parse_args()
    regions = load(DATA / "regions.json", {})
    if not regions:
        print("no regions.json")
        return 1
    out_path = DATA / "compare.json"
    compare = load(out_path, {}).get("regions", {}) if a.region else {}
    for code in sorted(regions):
        if a.region and code != a.region:
            continue
        s = region_summary(code)
        if s:
            compare[code] = s
            c = s["coverage"]
            print(f"{code}: {c['ok']}/{c['subject_to_rule']} readable, "
                  f"{len(s['procedures'])} procedures summarised, "
                  f"{s['trend']['moves_total']} moves over {s['trend']['refreshes']} refreshes")
    # drop regions no longer in the manifest
    compare = {k: v for k, v in compare.items() if k in regions}
    payload = {"updated": date.today().isoformat(), "regions": compare}
    out_path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"wrote {out_path.name} ({out_path.stat().st_size // 1024} KB, {len(compare)} regions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
