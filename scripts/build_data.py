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


# ---------------------------------------------------------------------------
# HOSPITAL WEBSITE RESOLUTION
# The CMS registry has no website field, so we can't discover /cms-hpt.txt
# without knowing each hospital's domain. Two-tier approach:
#   DOMAIN_HINTS  maps hospital-name patterns to the system's domain, which is
#                 enough for cms-hpt.txt discovery to take over.
#   KNOWN_MRF     hardcodes file URLs already confirmed by hand, for systems
#                 where automatic discovery fails.
# Both are keyed by lowercase substring match on the CMS facility name.
# Add entries here as you expand to new regions.
# ---------------------------------------------------------------------------
DOMAIN_HINTS = [
    ("scripps",              "scripps.org"),
    ("sharp",                "sharp.com"),
    ("uc san diego",         "health.ucsd.edu"),
    ("ucsd",                 "health.ucsd.edu"),
    ("jacobs medical",       "health.ucsd.edu"),
    ("east campus",          "health.ucsd.edu"),
    ("kaiser",               "healthy.kaiserpermanente.org"),
    ("rady",                 "rchsd.org"),
    ("palomar",              "palomarhealth.org"),
    ("pomerado",             "palomarhealth.org"),
    ("tri-city",             "tricitymed.org"),
    ("tri city",             "tricitymed.org"),
    ("alvarado",             "alvaradohospital.com"),
    ("paradise valley",      "pvhospital.org"),
    ("naval medical",        None),          # federal, exempt from the rule
    ("veterans affairs",     None),          # federal, exempt
    ("va medical",           None),
]

# Verified by hand from the system's own price transparency page.
KNOWN_MRF = {
    "scripps green":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Green-Hospital_standardcharges.csv",
    "scripps memorial hospital encinitas":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Memorial-Hospital-Encinitas_standardcharges.csv",
    "scripps memorial hospital la jolla":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Memorial-Hospital-La-Jolla_standardcharges.csv",
    "scripps mercy hospital chula vista":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Mercy-Hospital-Chula-Vista_standardcharges.csv",
    "scripps mercy hospital san diego":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Mercy-Hospital-San-Diego_standardcharges.csv",
}


def resolve_source(name: str) -> tuple[Optional[str], Optional[str]]:
    """
    Given a CMS facility name, return (known_mrf_url, domain).
    Longest pattern wins so 'scripps mercy hospital san diego' beats 'scripps'.
    """
    n = (name or "").lower()

    mrf = None
    for pattern in sorted(KNOWN_MRF, key=len, reverse=True):
        if pattern in n:
            mrf = KNOWN_MRF[pattern]
            break

    domain = None
    for pattern, dom in sorted(DOMAIN_HINTS, key=lambda x: len(x[0]), reverse=True):
        if pattern in n:
            domain = dom
            break

    return mrf, domain


# ===========================================================================
# STAGE 1 — REGISTRY
# ===========================================================================
def field(row: dict, *names) -> str:
    """
    CMS field names vary between dataset versions and casing conventions
    ("Facility Name", "facility_name", "County/Parish", "county_parish").
    Normalize punctuation away so a lookup works against any of them.
    """
    def norm(s):
        return re.sub(r"[^a-z0-9]", "", str(s).lower())

    lowered = {norm(k): v for k, v in row.items()}
    for n in names:
        v = lowered.get(norm(n))
        if v not in (None, ""):
            return str(v)
    return ""


def fetch_all_rows(debug: bool = False) -> list[dict]:
    """
    Page through the whole dataset with no server-side filtering.

    Filtering locally is deliberate: CMS's filter syntax has changed between
    API versions, and a wrong filter fails silently by returning zero rows
    rather than an error. The dataset is only ~5,400 rows of metadata, so
    fetching it all and filtering here is both cheap and far more robust.
    """
    out, offset, limit = [], 0, 500
    while True:
        r = requests.get(CMS_API, params={"limit": limit, "offset": offset},
                         headers=UA, timeout=60)
        r.raise_for_status()
        payload = r.json()

        # Response shape has varied across API versions.
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("results") or payload.get("data") or []
            if isinstance(rows, dict):
                rows = list(rows.values())
        else:
            rows = []

        if offset == 0:
            if not rows:
                print("  !! API returned no rows at all.", file=sys.stderr)
                print(f"  !! Response keys: {list(payload)[:10] if isinstance(payload, dict) else type(payload)}",
                      file=sys.stderr)
                print(f"  !! URL: {r.url}", file=sys.stderr)
                return []
            print(f"  API OK. Available fields: {sorted(rows[0].keys())}")
            if debug:
                print(f"  Sample row: {json.dumps(rows[0], indent=2)[:1200]}")

        out.extend(rows)
        offset += limit
        if len(rows) < limit:
            break
        time.sleep(0.2)
    return out


def fetch_registry(region: str, debug: bool = False,
                   type_filter: bool = True) -> list[dict]:
    """Pull hospitals for a region from the CMS Provider Data Catalog."""
    cfg = REGIONS[region]
    rows = fetch_all_rows(debug=debug)
    print(f"  fetched {len(rows)} total rows from CMS")
    if not rows:
        return []

    # --- filter by state ---
    if cfg["state"]:
        before = len(rows)
        rows = [r for r in rows
                if field(r, "state", "state_code", "provider_state").upper() == cfg["state"]]
        print(f"  state == {cfg['state']}: {before} -> {len(rows)}")
        if not rows:
            sample = {field(r, "state", "state_code") for r in fetch_all_rows()[:50]}
            print(f"  !! No rows matched. Sample state values seen: {sample}", file=sys.stderr)
            return []

    # --- filter by county ---
    if cfg["counties"]:
        wanted = {c.upper().replace(" COUNTY", "").strip() for c in cfg["counties"]}
        before = len(rows)
        kept = []
        seen = set()
        for r in rows:
            county = field(r, "county_parish", "county_name", "county",
                           "countyparish").upper().replace(" COUNTY", "").strip()
            seen.add(county)
            if county in wanted:
                kept.append(r)
        print(f"  county in {sorted(wanted)}: {before} -> {len(kept)}")
        if not kept:
            print(f"  !! No county match. Values seen in {cfg['state']}: "
                  f"{sorted(c for c in seen if c)[:25]}", file=sys.stderr)
            return []
        rows = kept

    # --- filter by hospital type ---
    if type_filter:
        before = len(rows)
        kept, seen = [], set()
        for r in rows:
            htype = field(r, "hospital_type", "type", "facility_type")
            seen.add(htype)
            # substring match, so "Acute Care Hospitals" and
            # "Acute Care - Veterans Administration" both pass
            if any(k.lower() in htype.lower() or htype.lower() in k.lower()
                   for k in KEEP_TYPES if htype):
                kept.append(r)
        print(f"  hospital type filter: {before} -> {len(kept)}")
        if not kept:
            print(f"  !! No type match. Values seen: {sorted(t for t in seen if t)}",
                  file=sys.stderr)
            print("  !! Re-run with --no-type-filter to keep everything.", file=sys.stderr)
            return []
        rows = kept

    # --- shape into our records ---
    hospitals = []
    for r in rows:
        ccn = field(r, "facility_id", "provider_id", "ccn", "federal_provider_number")
        stars_raw = field(r, "hospital_overall_rating", "overall_rating")
        fac_name = field(r, "facility_name", "provider_name", "name")
        known_mrf, domain = resolve_source(fac_name)
        hospitals.append({
            "id": f"ccn-{ccn}" if ccn else f"x-{len(hospitals)}",
            "ccn": ccn,
            "name": field(r, "facility_name", "provider_name", "name").title(),
            "system": field(r, "hospital_ownership", "ownership").title(),
            "city": field(r, "citytown", "city", "city_town").title(),
            "state": field(r, "state", "state_code"),
            "county": field(r, "county_parish", "county_name", "county").title(),
            "address": field(r, "address", "address_line_1", "street_address").title(),
            "zip": field(r, "zip_code", "zip", "postal_code"),
            "phone": field(r, "telephone_number", "phone_number", "phone"),
            "type": field(r, "hospital_type", "type"),
            "stars": int(stars_raw) if stars_raw.isdigit() else None,
            "hasER": field(r, "emergency_services", "has_emergency_services").lower().startswith("y"),
            "domain": domain,
            "lat": None, "lng": None, "mrf_url": known_mrf,
        })
    with_src = sum(1 for h in hospitals if h["mrf_url"] or h["domain"])
    print(f"  -> {len(hospitals)} hospitals in {cfg['label']}")
    print(f"     {with_src} have a known price-file source, "
          f"{len(hospitals) - with_src} need one added to DOMAIN_HINTS")
    for h in hospitals:
        if not (h["mrf_url"] or h["domain"]):
            print(f"       ? {h['name']}")
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


# A real code column is exactly "code", "code|1", "code_1" etc. Matching
# loosely on the substring "code" is what previously caused the parser to
# mistake a hospital's legal attestation paragraph (which contains the word
# "encoded") for the column header row.
CODE_COL_RE = re.compile(r"^code\s*[|_]?\s*\d*$")
CHARGE_HINTS = ("standard_charge", "gross_charge", "discounted_cash",
                "cash_price", "negotiated", "payer_name")


def looks_like_header(cells: list[str]) -> bool:
    """
    CMS v2/v3 files begin with two metadata rows before the real header.
    Identify the header by requiring a genuine code column, plus either a
    description column or a recognizable charge column. Long prose cells are
    ignored so attestation text can't masquerade as a header.
    """
    cl = [c.strip().lower() for c in cells if len(c.strip()) < 80]
    if not cl:
        return False
    has_code = any(CODE_COL_RE.match(c) for c in cl)
    has_desc = any(c == "description" for c in cl)
    has_charge = any(any(h in c for h in CHARGE_HINTS) for c in cl)
    return has_code and (has_desc or has_charge)


def stream_rows(url: str) -> Iterator[dict]:
    """
    Stream a price file without loading it into memory. These run 30MB-1GB;
    loading one whole would kill the job.
    """
    with requests.get(url, stream=True, headers=UA, timeout=180) as resp:
        resp.raise_for_status()
        if url.lower().endswith(".json"):
            for raw in resp.iter_lines(decode_unicode=True):
                if raw:
                    yield {"_raw_json_line": raw}
            return

        lines = (l.decode("utf-8", "replace") for l in resp.iter_lines() if l)
        header = None
        preamble = []
        for row in csv.reader(lines):
            if header is None:
                if looks_like_header(row):
                    header = [c.strip().lower() for c in row]
                else:
                    preamble.append(row)
                    if len(preamble) > 25:
                        # Fall back: use the widest row seen, which in practice
                        # is the header, rather than giving up entirely.
                        widest = max(preamble, key=len)
                        if len(widest) > 3:
                            header = [c.strip().lower() for c in widest]
                            print(f"      (header not identified; falling back "
                                  f"to widest row, {len(header)} columns)")
                        else:
                            return
                continue
            # Rows shorter than the header are padded; longer ones truncated.
            yield dict(zip(header, row))


def col(keys, *needles) -> Optional[str]:
    """CMS templates vary (tall vs wide, v1 vs v2). Match columns loosely."""
    for n in needles:
        for k in keys:
            if n in k:
                return k
    return None


def norm_code(v: str) -> str:
    """Codes appear as '470', '0470', '70551 '. Compare them consistently."""
    v = re.sub(r"[^A-Za-z0-9]", "", str(v or "").upper())
    return v.lstrip("0") or v


def norm_type(v: str) -> str:
    """'MS-DRG', 'MSDRG', 'DRG', 'CPT®' -> comparable form."""
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


# Build a fast lookup: normalized code -> list of (normalized type, procedure id)
_CODE_INDEX: dict[str, list[tuple[str, str]]] = {}
for (_t, _c), _pid in TARGET_CODES.items():
    _CODE_INDEX.setdefault(norm_code(_c), []).append((norm_type(_t), _pid))


def match_code(code: str, ctype: str) -> Optional[str]:
    """Return the procedure id this code/type pair refers to, if any."""
    cands = _CODE_INDEX.get(norm_code(code))
    if not cands:
        return None
    ct = norm_type(ctype)
    for want_type, pid in cands:
        # Accept exact, either-direction substring (DRG vs MSDRG), a blank
        # type, or the interchangeable CPT/HCPCS pair.
        if (not ct
                or ct == want_type
                or want_type in ct or ct in want_type
                or (ct in ("CPT", "HCPCS") and want_type in ("CPT", "HCPCS"))):
            return pid
    return None


def find_code_columns(keys: list[str]) -> list[tuple[str, Optional[str]]]:
    """
    CMS tall format allows several code slots (code|1 .. code|4), each with its
    own type column. Hospitals commonly put a revenue code in slot 1 and the
    CPT in a later slot, so every slot must be checked.
    """
    pairs = []
    for k in keys:
        kl = k.lower()
        if "code" not in kl or "type" in kl:
            continue
        if any(skip in kl for skip in ("zip", "postal", "geo")):
            continue
        type_col = None
        for cand in keys:
            cl = cand.lower()
            if cl.startswith(kl) and "type" in cl:
                type_col = cand
                break
        if type_col is None:
            type_col = col(keys, kl + "|type", kl + "_type", "code_type", "code|1|type")
        pairs.append((k, type_col))
    return pairs


def money(v) -> Optional[float]:
    if v in (None, ""):
        return None
    s = re.sub(r"[^\d.]", "", str(v))
    if not s or s.count(".") > 1:
        return None
    try:
        f = float(s)
        return round(f, 2) if f > 0 else None
    except ValueError:
        return None


def extract_prices(hospital_id: str, url: str, verbose: bool = True) -> list[dict]:
    """
    Pull only rows matching TARGET_CODES, capturing cash price and payer rates.
    Prints what it detected so a zero-row result is diagnosable without
    re-downloading a very large file.
    """
    found: list[dict] = []
    keys = None
    code_pairs: list[tuple[str, Optional[str]]] = []
    cash_c = gross_c = payer_c = rate_c = desc_c = None
    scanned = 0
    types_seen: dict[str, int] = {}
    sample_codes: list[str] = []

    for row in stream_rows(url):
        if "_raw_json_line" in row:
            continue
        scanned += 1

        if keys is None:
            keys = list(row.keys())
            code_pairs = find_code_columns(keys)
            cash_c = col(keys, "discounted_cash", "cash_price", "self_pay", "cash")
            gross_c = col(keys, "gross_charge", "gross")
            payer_c = col(keys, "payer_name", "payer")
            rate_c = col(keys, "negotiated_dollar", "negotiated_rate",
                         "allowed_amount", "negotiated")
            desc_c = col(keys, "description")
            if verbose:
                print(f"      columns: {len(keys)} | code slots: "
                      f"{[c for c, _ in code_pairs]}")
                print(f"      cash={cash_c} gross={gross_c} "
                      f"payer={payer_c} rate={rate_c}")

        for code_c, type_c in code_pairs:
            code = (row.get(code_c) or "").strip()
            if not code:
                continue
            ctype = (row.get(type_c) or "").strip() if type_c else ""
            if len(sample_codes) < 12 and code not in sample_codes:
                sample_codes.append(f"{code}[{ctype or '?'}]")
            if ctype:
                types_seen[norm_type(ctype)] = types_seen.get(norm_type(ctype), 0) + 1

            pid = match_code(code, ctype)
            if not pid:
                continue

            found.append({
                "hospital_id": hospital_id,
                "procedure": pid,
                "cash": money(row.get(cash_c)),
                "gross": money(row.get(gross_c)),
                "payer": (row.get(payer_c) or "").strip() or None,
                "rate": money(row.get(rate_c)),
                "description": (row.get(desc_c) or "")[:120],
            })
            break  # one procedure per row is enough

    if verbose:
        print(f"      scanned {scanned:,} rows | code types seen: "
              f"{dict(sorted(types_seen.items(), key=lambda x: -x[1])[:6])}")
        if not found:
            print(f"      NO MATCHES. Sample codes in file: {sample_codes}")
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


def probe(url: str, rows: int = 40):
    """
    Peek at a price file's structure without downloading the whole thing.
    Use this to diagnose a hospital that returns zero matches, instead of
    waiting through another full run.
    """
    print(f"probing {url}")
    seen = 0
    for row in stream_rows(url):
        if "_raw_json_line" in row:
            print("  (JSON file — first line)")
            print("  " + row["_raw_json_line"][:600])
            return
        if seen == 0:
            print(f"  columns ({len(row)}):")
            for k in row:
                print(f"    - {k}")
            print(f"  detected code slots: {find_code_columns(list(row))}")
            print("  first rows:")
        vals = {k: v for k, v in list(row.items())[:8] if v}
        print(f"    {vals}")
        seen += 1
        if seen >= rows:
            break


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="san-diego", choices=list(REGIONS))
    ap.add_argument("--stage", default="all",
                    choices=["all", "registry", "geocode", "prices", "probe"])
    ap.add_argument("--url", default="", help="file URL to inspect with --stage probe")
    ap.add_argument("--shard", type=int, default=0, help="which shard (0-indexed)")
    ap.add_argument("--shards", type=int, default=1, help="total shards")
    ap.add_argument("--limit", type=int, default=0, help="cap hospitals, for testing")
    ap.add_argument("--debug", action="store_true", help="print a sample CMS record")
    ap.add_argument("--no-type-filter", action="store_true",
                    help="keep every facility type (use if the type filter finds nothing)")
    args = ap.parse_args()

    if args.stage == "probe":
        if not args.url:
            print("--stage probe needs --url", file=sys.stderr)
            sys.exit(1)
        probe(args.url)
        return

    DATA.mkdir(exist_ok=True)
    reg_path = DATA / f"{args.region}-hospitals.json"
    price_path = DATA / f"{args.region}-prices.json"

    # --- registry ---
    if args.stage in ("all", "registry"):
        print(f"[registry] fetching {REGIONS[args.region]['label']} from CMS...")
        hospitals = fetch_registry(args.region, debug=args.debug,
                                   type_filter=not args.no_type_filter)
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
                domain = h.get("domain")
                if not domain:
                    _, domain = resolve_source(h.get("name", ""))
                if domain:
                    print(f"    ... discovering price file for {h['name']} via {domain}")
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
