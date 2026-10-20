#!/usr/bin/env python3
"""
Build the small cross-state summary + per-state trend files the site's
"Compare states" and "Price movers" views read.

    python scripts/build_compare.py            # every region in data/regions.json
    python scripts/build_compare.py --region tx # one region's trends file only (+ compare.json)

Outputs (all committed with data/):
  data/compare.json          one object per region: coverage/compliance counts,
                             per-procedure cash summary (n, median, p25, p75,
                             min, max; omitted when n < 5), trend block, and a
                             compact per-carrier index; plus a national
                             "insurers" pool (one record per hospital).
  data/<region>-trends.json  movers (every real price change, newest first)
                             + per-procedure statewide median series, one
                             point per refresh date.
  data/<region>-insurers.json per-procedure negotiated-rate summary by carrier
                             family, with the cash median as reference.

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


def payer_stats(vals: list[float]) -> dict:
    vals = sorted(vals)
    return {"n": len(vals), "median": round(statistics.median(vals)),
            "p25": round(q(vals, 0.25)), "p75": round(q(vals, 0.75))}


def insurer_summary(prices: dict, hid_region: dict | None = None) -> dict:
    """
    Carrier-family comparison over a prices map (proc -> hospital -> record).

    procs:  proc -> {cash: {n, median}, payers: {family: {n, median, p25, p75}}}
            (a family is listed only where >= MIN_N hospitals publish a rate)
    payers: family -> {hospitals, procs, cheapest_in, pairs, ratio[, states]}
            ratio = median over (hospital, procedure) pairs of
                    negotiated rate / that same hospital's cash price,
            so it compares like with like rather than two different hospital sets.
    """
    procs: dict[str, dict] = {}
    ratios: dict[str, list] = defaultdict(list)
    hosps: dict[str, set] = defaultdict(set)
    for proc, byh in prices.items():
        cash = sorted(r["cash"] for r in byh.values() if r.get("cash"))
        per: dict[str, list] = defaultdict(list)
        for hid, r in byh.items():
            for fam, rate in (r.get("payers") or {}).items():
                if not rate:
                    continue
                per[fam].append(rate)
                hosps[fam].add(hid)
                if r.get("cash"):
                    ratios[fam].append(rate / r["cash"])
        entry = {"payers": {f: payer_stats(v) for f, v in per.items() if len(v) >= MIN_N}}
        if len(cash) >= MIN_N:
            entry["cash"] = {"n": len(cash), "median": round(statistics.median(cash))}
        if entry["payers"]:
            procs[proc] = entry
    payers = {}
    for fam, hs in hosps.items():
        covered = [p for p, e in procs.items() if fam in e["payers"]]
        cheapest = sum(1 for p in covered
                       if procs[p]["payers"][fam]["median"]
                       <= min(x["median"] for x in procs[p]["payers"].values()))
        rr = ratios[fam]
        d = {"hospitals": len(hs), "procs": len(covered), "cheapest_in": cheapest,
             "pairs": len(rr),
             "ratio": round(statistics.median(rr), 2) if len(rr) >= MIN_N else None}
        if hid_region:
            d["states"] = len({hid_region[h] for h in hs if h in hid_region})
        payers[fam] = d
    return {"procs": procs, "payers": payers}


def region_summary(region: str, site: dict | None = None) -> dict | None:
    site = site or load(DATA / f"{region}.json", None)
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
        "insurers": write_insurers(region, site, prices),
    }


def write_insurers(region: str, site: dict, prices: dict) -> dict:
    """Per-state carrier file for the Compare-insurers screen; returns the compact
    carrier index that goes into compare.json so a carrier can be compared across states."""
    ins = insurer_summary(prices)
    (DATA / f"{region}-insurers.json").write_text(json.dumps(
        {"region": region, "updated": site.get("updated"),
         "procs": ins["procs"], "payers": ins["payers"]},
        separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return ins["payers"]


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
    argparse.ArgumentParser(description=__doc__).parse_args()
    regions = load(DATA / "regions.json", {})
    if not regions:
        print("no regions.json")
        return 1
    out_path = DATA / "compare.json"
    compare: dict[str, dict] = {}
    # National pool for the insurer comparison: one record per hospital, so a
    # metro region that overlaps its state (San Diego inside California) is
    # not counted twice. Regions are visited in sorted order; states sort
    # ahead of "san-diego".
    pooled: dict[str, dict] = defaultdict(dict)
    hid_region: dict[str, str] = {}
    for code in sorted(regions):
        site = load(DATA / f"{code}.json", None)
        if not site:
            continue
        s = region_summary(code, site)
        if not s:
            continue
        compare[code] = s
        c = s["coverage"]
        print(f"{code}: {c['ok']}/{c['subject_to_rule']} readable, "
              f"{len(s['procedures'])} procedures summarised, "
              f"{s['trend']['moves_total']} moves over {s['trend']['refreshes']} refreshes, "
              f"{len(s['insurers'])} carrier families")
        for proc, byh in site.get("prices", {}).items():
            for hid, rec in byh.items():
                if hid in hid_region and hid_region[hid] != code:
                    continue
                hid_region.setdefault(hid, code)
                pooled[proc].setdefault(hid, rec)
    national = insurer_summary(pooled, hid_region)
    ranked = sorted((v["ratio"], k) for k, v in national["payers"].items() if v["ratio"] is not None)
    print("national carriers vs cash:", ", ".join(f"{k} {r}x" for r, k in ranked))
    payload = {"updated": date.today().isoformat(), "regions": compare, "insurers": national}
    out_path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"wrote {out_path.name} ({out_path.stat().st_size // 1024} KB, {len(compare)} regions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
