#!/usr/bin/env python3
"""
Build per-state hospital quality files from CMS Care Compare (Provider Data
Catalog, public domain). Joined to our registry on the CMS Certification
Number (CCN) every hospital record already carries.

    python scripts/build_quality.py --region tx        # one state
    python scripts/build_quality.py --all              # every region in regions.json
    python scripts/build_quality.py --region tx --refresh   # re-download CSVs

Output: data/<region>-quality.json  (~30-60 KB; one object per hospital id)

  {
    "updated": "2026-10-19", "sources": {...dataset -> period end...},
    "national": { "op18b": 162, ... },           # national medians for context
    "hospitals": {
      "ccn-050002": {
        "er":   {"wait": 174, "left": 2.1, "volume": "very high", "stroke_ct": 71},
        "safety": {"better": 1, "same": 9, "worse": 2,          # counts across mortality/complication/infection measures
                   "flags": [{"id": "MORT_30_PN", "cmp": "worse"}, ...]},
        "readmit": {"better": 0, "same": 6, "worse": 1, "flags": [...]},
        "patients": {"stars": 3, "recommend": 68, "rating9_10": 66, "surveys": 540},
        "maternity": {"birthing_friendly": true, "csection": 24.1},
        "flu_staff": 91
      }, ...
    }
  }

Measures that CMS marks "Not Available" or "too few cases" are simply absent.
Datasets (dataset id -> CSV resolved live from the metastore, cached in
data/quality-cache/ for 30 days):
  yv7e-xc69  Timely and Effective Care   (OP_18b, OP_22, EDV, OP_23, IMM_3)
  dgck-syfz  HCAHPS patient survey       (star, recommend, overall rating)
  ynj2-r877  Complications and Deaths    (MORT_30_*, COMP_HIP_KNEE, PSI_90, Hybrid_HWM)
  77hc-ibv8  Healthcare Associated Infections (HAI_1..6 SIR)
  632h-zaca  Unplanned Hospital Visits   (READM_30_*, EDAC_30_*, OP_32, OP_36)
  nrdb-3fcy  Maternal Health             (Birthing-Friendly, PC_02 C-section, SMM)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import ssl
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "quality-cache"
META = "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/{id}"
UA = {"User-Agent": "care-cost-explorer (public CMS data; github.com/Altmatthc/care-cost-explorer)"}
CACHE_DAYS = 30
CTX = ssl.create_default_context()

DATASETS = {
    "timely": "yv7e-xc69",
    "hcahps": "dgck-syfz",
    "deaths": "ynj2-r877",
    "hai": "77hc-ibv8",
    "visits": "632h-zaca",
    "maternal": "nrdb-3fcy",
}

# Mortality / complication / infection measures, with patient-facing names.
SAFETY = {
    "MORT_30_AMI": "death rate, heart attack", "MORT_30_HF": "death rate, heart failure",
    "MORT_30_PN": "death rate, pneumonia", "MORT_30_STK": "death rate, stroke",
    "MORT_30_CABG": "death rate, bypass surgery", "MORT_30_COPD": "death rate, COPD",
    "Hybrid_HWM": "hospital-wide death rate", "COMP_HIP_KNEE": "hip/knee replacement complications",
    "PSI_90": "patient-safety composite", "PSI_04": "death after serious surgical complication",
    "HAI_1_SIR": "central-line bloodstream infections", "HAI_2_SIR": "catheter urinary infections",
    "HAI_3_SIR": "surgical-site infection, colon", "HAI_4_SIR": "surgical-site infection, hysterectomy",
    "HAI_5_SIR": "MRSA bloodstream infections", "HAI_6_SIR": "C. diff infections",
}
READMIT = {
    "READM_30_HOSP_WIDE": "hospital-wide readmissions", "READM_30_AMI": "readmission, heart attack",
    "READM_30_HF": "readmission, heart failure", "READM_30_PN": "readmission, pneumonia",
    "READM_30_COPD": "readmission, COPD", "READM_30_CABG": "readmission, bypass surgery",
    "READM_30_HIP_KNEE": "readmission, hip/knee replacement",
    "OP_32": "ER visit after colonoscopy", "OP_36": "hospital visit after outpatient surgery",
    "EDAC_30_AMI": "extra days in hospital, heart attack", "EDAC_30_HF": "extra days in hospital, heart failure",
    "EDAC_30_PN": "extra days in hospital, pneumonia",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str, timeout: int = 300) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read()


def dataset_csv(key: str, refresh: bool) -> tuple[list[dict], str]:
    """Return (rows, modified) for a dataset, from cache when fresh."""
    CACHE.mkdir(parents=True, exist_ok=True)
    ds = DATASETS[key]
    path = CACHE / f"{key}.csv"
    meta_path = CACHE / f"{key}.json"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < CACHE_DAYS * 86400
    if refresh or not fresh:
        meta = json.loads(fetch(META.format(id=ds), timeout=60).decode("utf-8"))
        url = meta["distribution"][0]["downloadURL"]
        log(f"  downloading {key} ({meta.get('title')}, modified {meta.get('modified')})")
        path.write_bytes(fetch(url))
        meta_path.write_text(json.dumps({"id": ds, "title": meta.get("title"),
                                         "modified": meta.get("modified"), "url": url}), encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    return rows, meta.get("modified", "")


def num(s) -> float | None:
    try:
        v = float(str(s).strip())
        return v
    except (TypeError, ValueError):
        return None


def cmp_word(s: str) -> str | None:
    s = (s or "").lower()
    if "better" in s:
        return "better"
    if "worse" in s:
        return "worse"
    if "no different" in s:
        return "same"
    return None


def build(ccns: set[str], refresh: bool) -> dict:
    out: dict[str, dict] = defaultdict(dict)
    sources: dict[str, dict] = {}
    national: dict[str, float] = {}

    # --- Timely & Effective Care: ER + flu ---
    rows, mod = dataset_csv("timely", refresh)
    sources["timely"] = {"modified": mod}
    nat_pool: dict[str, list] = defaultdict(list)
    for r in rows:
        mid = r["Measure ID"]
        if mid not in ("OP_18b", "OP_22", "EDV", "OP_23", "IMM_3"):
            continue
        if mid in ("OP_18b", "OP_22") and num(r["Score"]) is not None:
            nat_pool[mid].append(num(r["Score"]))
        ccn = r["Facility ID"]
        if ccn not in ccns:
            continue
        h = out[ccn]
        if mid == "OP_18b":
            v = num(r["Score"])
            if v is not None:
                h.setdefault("er", {})["wait"] = round(v)
                sources["timely"]["er_period_end"] = r.get("End Date", "")
        elif mid == "OP_22":
            v = num(r["Score"])
            if v is not None:
                h.setdefault("er", {})["left"] = v
        elif mid == "EDV":
            s = (r["Score"] or "").strip().lower()
            if s and s != "not available":
                h.setdefault("er", {})["volume"] = s
        elif mid == "OP_23":
            v = num(r["Score"])
            if v is not None:
                h.setdefault("er", {})["stroke_ct"] = round(v)
        elif mid == "IMM_3":
            v = num(r["Score"])
            if v is not None:
                h["flu_staff"] = round(v)
    for mid, key in (("OP_18b", "er_wait"), ("OP_22", "er_left")):
        if nat_pool[mid]:
            national[key] = round(statistics.median(nat_pool[mid]), 1)

    # --- HCAHPS ---
    rows, mod = dataset_csv("hcahps", refresh)
    sources["hcahps"] = {"modified": mod}
    for r in rows:
        ccn = r["Facility ID"]
        if ccn not in ccns:
            continue
        mid = r["HCAHPS Measure ID"]
        p = out[ccn].setdefault("patients", {})
        if mid == "H_STAR_RATING":
            v = num(r.get("Patient Survey Star Rating"))
            if v is not None:
                p["stars"] = int(v)
        elif mid == "H_RECMND_DY":
            v = num(r.get("HCAHPS Answer Percent"))
            if v is not None:
                p["recommend"] = round(v)
        elif mid == "H_HSP_RATING_9_10":
            v = num(r.get("HCAHPS Answer Percent"))
            if v is not None:
                p["rating9_10"] = round(v)
        elif mid == "H_COMP_1_A_P":        # nurses always communicated well
            v = num(r.get("HCAHPS Answer Percent"))
            if v is not None:
                p["nurses"] = round(v)
        elif mid == "H_COMP_2_A_P":        # doctors always communicated well
            v = num(r.get("HCAHPS Answer Percent"))
            if v is not None:
                p["doctors"] = round(v)
        elif mid == "H_CLEAN_HSP_A_P":
            v = num(r.get("HCAHPS Answer Percent"))
            if v is not None:
                p["clean"] = round(v)
        n = num(r.get("Number of Completed Surveys"))
        if n is not None:
            p["surveys"] = int(n)
        if not p:
            out[ccn].pop("patients", None)

    # --- Complications & deaths + HAI (same shape: "Compared to National") ---
    for key in ("deaths", "hai"):
        rows, mod = dataset_csv(key, refresh)
        sources[key] = {"modified": mod}
        for r in rows:
            ccn = r["Facility ID"]
            if ccn not in ccns:
                continue
            mid = r["Measure ID"]
            if mid not in SAFETY:
                continue
            cmp_ = cmp_word(r.get("Compared to National", ""))
            if not cmp_:
                continue
            s = out[ccn].setdefault("safety", {"better": 0, "same": 0, "worse": 0, "flags": []})
            s[cmp_] += 1
            if cmp_ != "same":
                s["flags"].append({"id": mid, "name": SAFETY[mid], "cmp": cmp_, "score": num(r.get("Score"))})
            if "period_end" not in sources[key]:
                sources[key]["period_end"] = r.get("End Date", "")

    # --- Unplanned visits ---
    rows, mod = dataset_csv("visits", refresh)
    sources["visits"] = {"modified": mod}
    for r in rows:
        ccn = r["Facility ID"]
        if ccn not in ccns:
            continue
        mid = r["Measure ID"]
        if mid not in READMIT:
            continue
        cmp_ = cmp_word(r.get("Compared to National", ""))
        if not cmp_:
            continue
        s = out[ccn].setdefault("readmit", {"better": 0, "same": 0, "worse": 0, "flags": []})
        s[cmp_] += 1
        if cmp_ != "same":
            s["flags"].append({"id": mid, "name": READMIT[mid], "cmp": cmp_, "score": num(r.get("Score"))})
        if "period_end" not in sources["visits"]:
            sources["visits"]["period_end"] = r.get("End Date", "")

    # --- Maternal health ---
    rows, mod = dataset_csv("maternal", refresh)
    sources["maternal"] = {"modified": mod}
    for r in rows:
        ccn = r["Facility ID"]
        if ccn not in ccns:
            continue
        mid = r.get("Measure ID", "")
        m = out[ccn].setdefault("maternity", {})
        if mid == "MATERNAL_BIRTHING_FRIENDLY" or "birthing" in (r.get("Measure Name", "") or "").lower():
            m["birthing_friendly"] = (r.get("Score", "") or "").strip().upper() in ("Y", "YES")
        elif mid == "PC_02":
            v = num(r.get("Score"))
            if v is not None:
                m["csection"] = v
        elif mid == "SMM":
            v = num(r.get("Score"))
            if v is not None:
                m["severe_complications"] = v
        if not m:
            out[ccn].pop("maternity", None)

    return {"hospitals": dict(out), "sources": sources, "national": national}


def region_ccns(region: str) -> dict[str, str]:
    """ccn -> hospital id for one region."""
    p = DATA / f"{region}-hospitals.json"
    if not p.exists():
        return {}
    return {h["ccn"]: h["id"] for h in json.loads(p.read_text(encoding="utf-8")) if h.get("ccn")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-download the CMS CSVs")
    a = ap.parse_args()
    regions = [a.region] if a.region else []
    if a.all:
        regions = sorted(json.loads((DATA / "regions.json").read_text(encoding="utf-8")))
    if not regions:
        ap.error("--region <code> or --all")

    ccn_maps = {r: region_ccns(r) for r in regions}
    all_ccns = {c for m in ccn_maps.values() for c in m}
    log(f"quality: {len(regions)} region(s), {len(all_ccns)} hospitals with CCN")
    built = build(all_ccns, a.refresh)

    for region in regions:
        m = ccn_maps[region]
        hosp = {hid: built["hospitals"][ccn] for ccn, hid in m.items() if ccn in built["hospitals"]}
        payload = {"region": region, "updated": date.today().isoformat(),
                   "sources": built["sources"], "national": built["national"], "hospitals": hosp}
        out = DATA / f"{region}-quality.json"
        out.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        er = sum(1 for h in hosp.values() if h.get("er", {}).get("wait"))
        saf = sum(1 for h in hosp.values() if h.get("safety"))
        pat = sum(1 for h in hosp.values() if h.get("patients", {}).get("stars"))
        log(f"  {region}: {len(hosp)}/{len(m)} hospitals matched — ER wait {er}, safety {saf}, "
            f"patient stars {pat} -> {out.name} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
