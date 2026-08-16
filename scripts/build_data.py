#!/usr/bin/env python3
"""
Care Cost Explorer — data pipeline
==================================
Builds the JSON the website reads. Runs automatically via GitHub Actions;
you never need a terminal.

Three stages, each independently runnable:

  1. registry  — pull hospitals from CMS (name, address, type, star rating)
  2. geocode   — turn addresses into map coordinates (US Census, free)
  3. prices    — find and parse each hospital's federally required price file

Scope is controlled entirely by --region, so expanding from San Diego to
California to nationwide is a config change, not a rewrite.

  python scripts/build_data.py --region san-diego
  python scripts/build_data.py --region california
  python scripts/build_data.py --region us          # see SCALING NOTES below

DATA SOURCES (all public, no API keys, no authentication):
  CMS Hospital General Information ... data.cms.gov dataset xubh-q36u
      ~5,400 Medicare-registered hospitals: name, address, type, ownership,
      emergency services flag, CMS overall star rating.
  US Census Geocoder ................ geocoding.geo.census.gov
  Hospital price files .............. each hospital's own website, located via
      the /cms-hpt.txt convention that CMS has required since the CY2024 rule.

SCALING NOTES — read before running --region us:
  San Diego (~20 hospitals)  : minutes. Fine on free GitHub Actions.
  California (~400 hospitals): hours. Use --shard to split across parallel
                               jobs; the workflow does this automatically.
  Nationwide (~5,400)        : price files average 50MB-1GB EACH. A full
                               national refresh means downloading several
                               terabytes. This will NOT fit in free GitHub
                               Actions (6h job limit, limited storage).
                               Options at that scale: (a) run on a rented
                               server with real bandwidth, (b) refresh each
                               state on a rotating schedule rather than all
                               at once, or (c) license an already-parsed
                               dataset from an aggregator. Start with (b).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from pathlib import Path
from typing import Iterator, Optional

import requests

CMS_DATASET = "xubh-q36u"  # Hospital General Information
CMS_API = f"https://data.cms.gov/provider-data/api/1/datastore/query/{CMS_DATASET}/0"
CENSUS_GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ---------------------------------------------------------------------------
# REGIONS — this is the only thing you edit to expand coverage.
# ---------------------------------------------------------------------------
REGIONS = {
    "san-diego": {
        "label": "San Diego County",
        "state": "CA",
        "counties": ["SAN DIEGO"],
    },
    "california": {
        "label": "California",
        "state": "CA",
        "counties": None,          # None = whole state
    },
    "us": {
        "label": "United States",
        "state": None,             # None = everywhere
        "counties": None,
    },
}

# Hospital types worth including. Psychiatric and specialty facilities publish
# price files too, but they aren't what people comparison-shop.
KEEP_TYPES = {
    "Acute Care Hospitals",
    "Critical Access Hospitals",
    "Childrens",
    "Acute Care - Veterans Administration",
    "Acute Care - Department of Defense",
}

# ---------------------------------------------------------------------------
# PROCEDURES to extract. Keys are (code_type, code).
# Everything else in the price file is skipped — this is what keeps the job
# tractable. A hospital file can hold 100,000+ rows; we want ~60.
# ---------------------------------------------------------------------------
TARGET_CODES = {
    # imaging
    ("CPT", "70551"): "mri-brain",
    ("CPT", "70450"): "ct-head",
    ("CPT", "72148"): "mri-spine",
    ("CPT", "73721"): "mri-knee",
    ("CPT", "74177"): "ct-abd-pelvis",
    ("CPT", "76700"): "us-abdomen",
    ("CPT", "76805"): "us-pregnancy",
    ("CPT", "76536"): "thyroid-us",
    ("CPT", "71046"): "chest-xray",
    ("CPT", "73110"): "xray-extremity",
    ("CPT", "73562"): "xray-leg",
    ("CPT", "77067"): "mammogram-screen",
    ("CPT", "77065"): "mammogram-diag",
    ("CPT", "93306"): "echocardiogram",
    # labs & clinic
    ("CPT", "85025"): "blood-cbc",
    ("CPT", "80053"): "blood-metabolic",
    ("CPT", "87880"): "strep-test",
    ("CPT", "88175"): "pap-smear",
    ("CPT", "90686"): "flu-shot",
    ("CPT", "92557"): "hearing-test",
    ("CPT", "93000"): "ekg",
    ("CPT", "93015"): "stress-test",
    ("CPT", "95004"): "allergy-test",
    ("CPT", "20610"): "joint-injection",
    ("CPT", "58300"): "iud-insertion",
    ("CPT", "96360"): "iv-hydration",
    ("CPT", "96365"): "iv-infusion",
    # emergency
    ("CPT", "99283"): "er-level3",
    ("CPT", "99284"): "er-level4",
    ("CPT", "99285"): "er-level5",
    ("CPT", "99213"): "urgent-care",
    ("CPT", "12002"): "laceration",
    ("CPT", "29075"): "cast-fracture",
    # recurring
    ("CPT", "97110"): "physical-therapy",
    ("CPT", "90935"): "dialysis",
    ("CPT", "96413"): "chemo-infusion",
    # outpatient procedures
    ("CPT", "45378"): "colonoscopy",
    ("CPT", "43235"): "endoscopy",
    ("CPT", "66984"): "cataract",
    ("CPT", "64721"): "carpal-tunnel",
    ("CPT", "29881"): "knee-scope",
    ("CPT", "29827"): "shoulder-scope",
    ("CPT", "62323"): "epidural-inj",
    ("CPT", "19083"): "biopsy-needle",
    ("CPT", "55700"): "prostate-biopsy",
    ("CPT", "95810"): "sleep-study",
    ("CPT", "49505"): "hernia-repair",
    ("CPT", "50590"): "lithotripsy",
    ("CPT", "93458"): "cardiac-cath",
    ("CPT", "42826"): "tonsillectomy",
    ("CPT", "44970"): "appendectomy",
    ("CPT", "47562"): "gallbladder",
    ("CPT", "58571"): "hysterectomy",
    # inpatient stays
    ("MS-DRG", "470"): "knee-replacement",
    ("MS-DRG", "460"): "spinal-fusion",
    ("MS-DRG", "775"): "vaginal-delivery",
    ("MS-DRG", "786"): "cesarean",
}

UA = {"User-Agent": "care-cost-explorer/1.0 (public price transparency tool)"}


# ===========================================================================
# STAGE 1 — REGISTRY
# ===========================================================================
def fetch_registry(region: str) -> list[dict]:
    """Pull hospitals for a region from the CMS Provider Data Catalog."""
    cfg = REGIONS[region]
    conditions, i = [], 0
    if cfg["state"]:
        conditions.append({"property": "state", "value": cfg["state"], "operator": "="})

    out, offset, limit = [], 0, 500
    while True:
        params = {"limit": limit, "offset": offset}
        if conditions:
            for n, c in enumerate(conditions):
                params[f"conditions[{n}][property]"] = c["property"]
                params[f"conditions[{n}][value]"] = c["value"]
                params[f"conditions[{n}][operator]"] = c["operator"]
        r = requests.get(CMS_API, params=params, headers=UA, timeout=60)
        r.raise_for_status()
        rows = r.json().get("results", [])
        if not rows:
            break
        out.extend(rows)
        offset += limit
        if len(rows) < limit:
            break
        time.sleep(0.2)

    hospitals = []
    counties = cfg["counties"]
    for row in out:
        county = (row.get("county_parish") or row.get("county_name") or "").upper()
        if counties and county not in counties:
            continue
        htype = row.get("hospital_type", "")
        if KEEP_TYPES and htype not in KEEP_TYPES:
            continue
        ccn = row.get("facility_id") or row.get("provider_id")
        name = row.get("facility_name", "").title()
        stars_raw = row.get("hospital_overall_rating", "")
        stars = int(stars_raw) if str(stars_raw).isdigit() else None
        hospitals.append({
            "id": f"ccn-{ccn}",
            "ccn": ccn,
            "name": name,
            "system": (row.get("hospital_ownership") or "").title(),
            "city": (row.get("citytown") or row.get("city") or "").title(),
            "state": row.get("state", ""),
            "county": county.title(),
            "address": (row.get("address") or "").title(),
            "zip": row.get("zip_code") or row.get("zip", ""),
            "phone": row.get("telephone_number", ""),
            "type": htype,
            "stars": stars,
            "hasER": (row.get("emergency_services", "") or "").lower().startswith("y"),
            "lat": None, "lng": None, "mrf_url": None,
        })
    return hospitals


# ===========================================================================
# STAGE 2 — GEOCODE
# ===========================================================================
def geocode(h: dict) -> bool:
    """Resolve one hospital address to coordinates via the free Census geocoder."""
    addr = f"{h['address']}, {h['city']}, {h['state']} {h['zip']}"
    try:
        r = requests.get(CENSUS_GEOCODER, params={
            "address": addr, "benchmark": "Public_AR_Current", "format": "json",
        }, headers=UA, timeout=30)
        r.raise_for_status()
        matches = r.json().get("result", {}).get("addressMatches", [])
        if matches:
            c = matches[0]["coordinates"]
            h["lat"], h["lng"] = round(c["y"], 5), round(c["x"], 5)
            return True
    except Exception as e:
        print(f"    geocode failed for {h['name']}: {e}", file=sys.stderr)
    return False


# ===========================================================================
# STAGE 3 — PRICE FILES
# ===========================================================================
def discover_mrf(domain: str) -> Optional[str]:
    """
    Locate a hospital's price file via /cms-hpt.txt, which CMS has required
    in the website root since the CY2024 rule. Format is key: value lines
    including mrf-url.
    """
    for base in (f"https://{domain}", f"https://www.{domain}"):
        try:
            r = requests.get(f"{base}/cms-hpt.txt", headers=UA, timeout=15)
            if not r.ok or not r.text.strip():
                continue
            for line in r.text.splitlines():
                if "mrf-url" in line.lower():
                    url = line.split(":", 1)[1].strip() if ":" in line else ""
                    if url.startswith("http"):
                        return url
                for part in line.split("|"):
                    part = part.strip()
                    if part.startswith("http") and re.search(r"\.(csv|json)", part, re.I):
                        return part
        except requests.RequestException:
            continue
    return None


def stream_rows(url: str) -> Iterator[dict]:
    """
    Stream a price file without loading it into memory. These run 30MB-1GB;
    loading one whole would kill the job.
    """
    with requests.get(url, stream=True, headers=UA, timeout=180) as resp:
        resp.raise_for_status()
        if url.lower().endswith(".json"):
            # Large JSON: fall back to a line-oriented scan for code/price pairs.
            for raw in resp.iter_lines(decode_unicode=True):
                if raw:
                    yield {"_raw_json_line": raw}
            return
        lines = (l.decode("utf-8", "replace") for l in resp.iter_lines() if l)
        # CMS files carry 2-3 metadata rows before the real header.
        header = None
        for row in csv.reader(lines):
            if header is None:
                lowered = [c.strip().lower() for c in row]
                if any("code" in c for c in lowered) and any(
                    "charge" in c or "price" in c or "rate" in c for c in lowered
                ):
                    header = lowered
                continue
            yield dict(zip(header, row))


def col(keys, *needles) -> Optional[str]:
    """CMS templates vary (tall vs wide, v1 vs v2). Match columns loosely."""
    for n in needles:
        for k in keys:
            if n in k:
                return k
    return None


def extract_prices(hospital_id: str, url: str) -> list[dict]:
    """Pull only rows matching TARGET_CODES, capturing cash price and payer rates."""
    found, keys = [], None
    code_c = type_c = cash_c = gross_c = payer_c = rate_c = desc_c = None

    for row in stream_rows(url):
        if "_raw_json_line" in row:
            continue  # JSON hospitals need a per-schema parser; logged as unsupported
        if keys is None:
            keys = list(row.keys())
            code_c = col(keys, "code|1", "code_1", "code", "hcpcs", "cpt")
            type_c = col(keys, "code|1|type", "code_1_type", "code_type")
            cash_c = col(keys, "discounted_cash", "cash_price", "self_pay")
            gross_c = col(keys, "gross_charge", "gross")
            payer_c = col(keys, "payer_name", "payer")
            rate_c = col(keys, "standard_charge|negotiated_dollar",
                         "negotiated_dollar", "negotiated_rate", "allowed_amount")
            desc_c = col(keys, "description")

        code = (row.get(code_c) or "").strip().upper()
        if not code:
            continue
        ctype = (row.get(type_c) or "CPT").strip().upper()
        key = None
        for (t, c), pid in TARGET_CODES.items():
            if code == c and (t in ctype or ctype in ("", "CPT", "HCPCS")):
                key = pid
                break
        if not key:
            continue

        def money(v):
            if not v:
                return None
            v = re.sub(r"[^\d.]", "", str(v))
            try:
                f = float(v)
                return round(f, 2) if f > 0 else None
            except ValueError:
                return None

        found.append({
            "hospital_id": hospital_id,
            "procedure": key,
            "cash": money(row.get(cash_c)),
            "gross": money(row.get(gross_c)),
            "payer": (row.get(payer_c) or "").strip() or None,
            "rate": money(row.get(rate_c)),
            "description": (row.get(desc_c) or "")[:120],
        })
    return found


def consolidate(rows: list[dict]) -> dict:
    """Collapse many raw rows into one record per hospital+procedure."""
    out: dict[str, dict] = {}
    for r in rows:
        k = f"{r['hospital_id']}|{r['procedure']}"
        rec = out.setdefault(k, {
            "hospital_id": r["hospital_id"], "procedure": r["procedure"],
            "cash": None, "gross": None, "payers": {}, "description": r["description"],
        })
        if r["cash"] and not rec["cash"]:
            rec["cash"] = r["cash"]
        if r["gross"] and not rec["gross"]:
            rec["gross"] = r["gross"]
        if r["payer"] and r["rate"]:
            rec["payers"][r["payer"]] = r["rate"]
    return out


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="san-diego", choices=list(REGIONS))
    ap.add_argument("--stage", default="all",
                    choices=["all", "registry", "geocode", "prices"])
    ap.add_argument("--shard", type=int, default=0, help="which shard (0-indexed)")
    ap.add_argument("--shards", type=int, default=1, help="total shards")
    ap.add_argument("--limit", type=int, default=0, help="cap hospitals, for testing")
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    reg_path = DATA / f"{args.region}-hospitals.json"
    price_path = DATA / f"{args.region}-prices.json"

    # --- registry ---
    if args.stage in ("all", "registry"):
        print(f"[registry] fetching {REGIONS[args.region]['label']} from CMS...")
        hospitals = fetch_registry(args.region)
        if args.limit:
            hospitals = hospitals[: args.limit]
        print(f"[registry] {len(hospitals)} hospitals")
        reg_path.write_text(json.dumps(hospitals, indent=1))
    else:
        hospitals = json.loads(reg_path.read_text())

    # --- geocode ---
    if args.stage in ("all", "geocode"):
        todo = [h for h in hospitals if h.get("lat") is None]
        print(f"[geocode] resolving {len(todo)} addresses...")
        ok = 0
        for i, h in enumerate(todo, 1):
            if geocode(h):
                ok += 1
            if i % 25 == 0:
                print(f"    {i}/{len(todo)} ({ok} located)")
            time.sleep(0.15)
        print(f"[geocode] {ok}/{len(todo)} located")
        reg_path.write_text(json.dumps(hospitals, indent=1))

    # --- prices ---
    if args.stage in ("all", "prices"):
        shard = [h for i, h in enumerate(hospitals) if i % args.shards == args.shard]
        print(f"[prices] shard {args.shard+1}/{args.shards}: {len(shard)} hospitals")
        all_rows, stats = [], {"found": 0, "no_mrf": 0, "error": 0}
        for h in shard:
            url = h.get("mrf_url")
            if not url:
                domain = re.sub(r"^www\.", "",
                                (h.get("website") or "").replace("https://", "")
                                .replace("http://", "").split("/")[0])
                if domain:
                    url = discover_mrf(domain)
                    h["mrf_url"] = url
            if not url:
                stats["no_mrf"] += 1
                print(f"    - {h['name']}: no price file found")
                continue
            try:
                rows = extract_prices(h["id"], url)
                all_rows.extend(rows)
                stats["found"] += 1
                print(f"    ✓ {h['name']}: {len(rows)} matching rows")
            except Exception as e:
                stats["error"] += 1
                print(f"    ! {h['name']}: {e}")

        merged = consolidate(all_rows)
        if args.shards > 1:
            price_path = DATA / f"{args.region}-prices-{args.shard}.json"
        price_path.write_text(json.dumps(merged, indent=1))
        reg_path.write_text(json.dumps(hospitals, indent=1))
        print(f"[prices] {stats}")
        print(f"[prices] wrote {price_path}")


if __name__ == "__main__":
    main()
