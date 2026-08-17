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

# Every state as its own region. Nationwide coverage is built by refreshing
# states on a rotation rather than attempting one enormous run: ~5,400
# hospitals is roughly 415 hours of downloading, which cannot fit in GitHub's
# 6-hour job limit no matter how it's sharded.
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR",
]
for _s in US_STATES:
    REGIONS.setdefault(_s.lower(), {"label": _s, "state": _s, "counties": None})

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
    # MS-DRG 470 covers major hip AND knee joint replacement without
    # complications — one code, two procedures. The description text is the
    # only way to tell them apart, handled in match_code below.
    ("MS-DRG", "470"): "joint-replacement",
    ("MS-DRG", "460"): "spinal-fusion",
    # Procedure-specific codes, preferred when a hospital publishes them since
    # they distinguish hip from knee where MS-DRG 470 cannot.
    ("CPT", "27447"): "knee-replacement",
    ("CPT", "27130"): "hip-replacement",
    ("MS-DRG", "775"): "vaginal-delivery",
    ("MS-DRG", "786"): "cesarean",
}

UA = {"User-Agent": "care-cost-explorer/1.0 (public price transparency tool)"}

# Hospitals' file servers throttle or refuse repeated automated requests —
# Scripps began refusing connections after several runs. Retry with backoff
# rather than losing a hospital to a transient block.
def _make_session() -> requests.Session:
    s = requests.Session()
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry = Retry(
            total=4, connect=4, read=3, backoff_factor=3,
            status_forcelist=[408, 429, 500, 502, 503, 504],
            allowed_methods=["GET"], raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=4)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
    except Exception:
        pass
    s.headers.update(UA)
    return s


SESSION = _make_session()


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
    ("grossmont",            "sharp.com"),
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

# Verified by hand from each system's own price transparency page.
# Keys are matched against a punctuation-stripped facility name, so
# "Scripps Memorial Hospital - Encinitas" matches "scripps memorial hospital encinitas".
KNOWN_MRF = {
    "scripps green hospital":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Green-Hospital_standardcharges.csv",
    "scripps memorial hospital encinitas":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Memorial-Hospital-Encinitas_standardcharges.csv",
    "scripps memorial hospital la jolla":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Memorial-Hospital-La-Jolla_standardcharges.csv",
    "scripps mercy hospital chula vista":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Mercy-Hospital-Chula-Vista_standardcharges.csv",
    "scripps mercy hospital san diego":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Mercy-Hospital-San-Diego_standardcharges.csv",
    # CMS lists the Hillcrest campus simply as "Scripps Mercy Hospital"
    "scripps mercy hospital":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Mercy-Hospital-San-Diego_standardcharges.csv",
}


def _flatten(s: str) -> str:
    """Lowercase and strip punctuation so naming variants compare equal."""
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())


def resolve_source(name: str) -> tuple[Optional[str], Optional[str]]:
    """
    Given a CMS facility name, return (known_mrf_url, domain).
    Longest pattern wins so 'scripps mercy hospital san diego' beats 'scripps'.
    """
    n = " ".join(_flatten(name).split())

    mrf = None
    for pattern in sorted(KNOWN_MRF, key=len, reverse=True):
        if " ".join(_flatten(pattern).split()) in n:
            mrf = KNOWN_MRF[pattern]
            break

    domain = None
    for pattern, dom in sorted(DOMAIN_HINTS, key=lambda x: len(x[0]), reverse=True):
        if " ".join(_flatten(pattern).split()) in n:
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
        r = SESSION.get(CMS_API, params={"limit": limit, "offset": offset},
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
            "ownership": field(r, "hospital_ownership", "ownership"),
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
            "verified_source": bool(known_mrf),
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
    """
    Resolve one hospital address to coordinates.

    The Census geocoder is authoritative but fails on some addresses — a suite
    number, a campus name, a PO box. A hospital that fails geocoding vanishes
    from the map entirely even when we have its prices, which is a worse
    outcome than a slightly imprecise pin. So: exact address first, then the
    address without its second line, then the ZIP centroid.
    """
    attempts = [
        f"{h['address']}, {h['city']}, {h['state']} {h['zip']}",
        # Drop anything after a comma or "suite"/"bldg" in the street line.
        f"{re.split(r',| suite | ste | bldg | building ', h['address'], flags=re.I)[0]}, "
        f"{h['city']}, {h['state']} {h['zip']}",
        f"{h['city']}, {h['state']} {h['zip']}",
    ]
    for i, addr in enumerate(attempts):
        if _geocode_once(h, addr):
            if i:
                print(f"    (matched {h['name']} on a simplified address)")
            return True
    # Last resort: the ZIP centroid. Good to about a mile, which is fine for
    # ranking by distance and far better than being unmappable.
    if h.get("zip") and _zip_centroid(h):
        print(f"    (fell back to ZIP centroid for {h['name']})")
        return True
    return False


_ZIP_CACHE: dict = {}


def _zip_centroid(h: dict) -> bool:
    z = str(h.get("zip", "")).strip()[:5]
    if not z:
        return False
    if z in _ZIP_CACHE:
        h["lat"], h["lng"] = _ZIP_CACHE[z]
        h["geo_approx"] = True
        return True
    try:
        r = SESSION.get(f"https://api.zippopotam.us/us/{z}", timeout=20)
        if r.ok:
            p = r.json()["places"][0]
            h["lat"] = round(float(p["latitude"]), 5)
            h["lng"] = round(float(p["longitude"]), 5)
            h["geo_approx"] = True
            _ZIP_CACHE[z] = (h["lat"], h["lng"])
            return True
    except Exception:
        pass
    return False


def _geocode_once(h: dict, addr: str) -> bool:
    try:
        r = SESSION.get(CENSUS_GEOCODER, params={
            "address": addr, "benchmark": "Public_AR_Current", "format": "json",
        }, headers=UA, timeout=30)
        r.raise_for_status()
        matches = r.json().get("result", {}).get("addressMatches", [])
        if matches:
            c = matches[0]["coordinates"]
            h["lat"], h["lng"] = round(c["y"], 5), round(c["x"], 5)
            h["geo_approx"] = False
            return True
    except Exception:
        pass
    return False


# ===========================================================================
# STAGE 3 — PRICE FILES
# ===========================================================================
SOURCE_PAGES: dict[str, str] = {}     # domain -> hospital's price page


def discover_mrf_candidates(domain: str) -> list[str]:
    """
    Collect EVERY price-file URL a system publishes in its /cms-hpt.txt.

    A multi-hospital system lists one file per facility. Previously we took
    whichever appeared first, which is how San Diego Kaiser ended up reading a
    Northern California file. Returning all of them lets the caller verify
    each and choose the one that actually belongs to the hospital.
    """
    urls: list[str] = []
    for base in (f"https://{domain}", f"https://www.{domain}"):
        try:
            r = SESSION.get(f"{base}/cms-hpt.txt", headers=UA, timeout=20)
            if not r.ok or not r.text.strip():
                continue
            for line in r.text.splitlines():
                low = line.lower()
                # The spec also carries the human-facing price page.
                if "source-page-url" in low and ":" in line:
                    page = line.split(":", 1)[1].strip()
                    if page.startswith("http"):
                        SOURCE_PAGES.setdefault(domain, page)
                # "mrf-url: https://..." form
                if "mrf-url" in low and ":" in line:
                    cand = line.split(":", 1)[1].strip()
                    if cand.startswith("http"):
                        urls.append(cand)
                # pipe-delimited form
                for part in line.split("|"):
                    part = part.strip()
                    if part.startswith("http") and re.search(r"\.(csv|json)", part, re.I):
                        urls.append(part)
            if urls:
                break
        except requests.RequestException:
            continue
    # de-duplicate, preserving order
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def discover_mrf(domain: str) -> Optional[str]:
    """Backwards-compatible single-result helper."""
    c = discover_mrf_candidates(domain)
    return c[0] if c else None


def pick_matching_file(hospital_name: str, domain: str,
                       verbose: bool = True,
                       expected_address: str = "") -> Optional[str]:
    """
    From everything a system publishes, return the file that identifies itself
    as this hospital. Returns None rather than guessing.
    """
    cands = discover_mrf_candidates(domain)
    if not cands:
        return None
    if verbose:
        print(f"      {len(cands)} file(s) published by {domain}")

    # Rank by how well the FILENAME resembles the hospital, best first. A
    # boolean sort left large systems in alphabetical order — Kaiser publishes
    # 41 files and the San Diego one sorted past the cutoff behind a run of
    # Northern California files.
    scored = sorted(
        ((match_score(hospital_name, u.split("/")[-1]), u) for u in cands),
        key=lambda t: -t[0])
    if verbose and scored:
        top = ", ".join(f"{s:.2f} {u.split('/')[-1][:44]}" for s, u in scored[:3])
        print(f"      best filename matches: {top}")

    # Check every candidate with any token overlap, then fall back to the rest.
    plausible = [u for s, u in scored if s > 0]
    remainder = [u for s, u in scored if s == 0]
    passing: list[tuple[float, str, str]] = []
    for url in (plausible + remainder)[:25]:
        ident = read_identity(url)
        names = [n for n in (ident["hospital_name"], ident["location_name"],
                             url.split("/")[-1]) if n]
        best_name = max((match_score(hospital_name, n) for n in names), default=0.0)
        if best_name < 0.6:
            continue
        addr = address_score(expected_address, ident.get("address", "")) \
            if expected_address else 0.0
        label = names[0][:60] if names else url.split("/")[-1][:60]
        passing.append((best_name + addr, url, f"{label} (name {best_name:.2f}"
                        + (f", address {addr:.2f}" if expected_address else "") + ")"))
        # A perfect name match with no competing candidate needs no tiebreak.
        if best_name >= 0.99 and not expected_address:
            break

    if passing:
        passing.sort(key=lambda t: -t[0])
        if verbose and len(passing) > 1:
            print(f"      {len(passing)} candidate(s) matched; "
                  f"choosing best by name + address:")
            for s, u, why in passing[:4]:
                print(f"        {s:.2f}  {why}")
        best = passing[0]
        if verbose:
            print(f"      selected {best[1].split('/')[-1][:70]} ({best[2]})")
        return best[1]
    if verbose:
        print(f"      none of {len(cands)} published files identify as "
              f"'{hospital_name}'. What was published:")
        for url in [u for _, u in scored[:8]]:
            ident = read_identity(url)
            label = ident.get("hospital_name") or ident.get("location_name") or "?"
            print(f"        {url.split('/')[-1][:66]}")
            print(f"           identifies as: {label[:70]}")
        if len(cands) > 8:
            print(f"        ... and {len(cands)-8} more")
    return None


# A real code column is exactly "code", "code|1", "code_1" etc. Matching
# loosely on the substring "code" previously caused the parser to mistake a
# hospital's legal attestation paragraph (which contains "encoded") for the
# column header row.
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
    Stream a CSV price file without loading it into memory. These run
    30MB-1GB; loading one whole would kill the job.
    """
    with SESSION.get(url, stream=True, headers=UA, timeout=300) as resp:
        resp.raise_for_status()
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
                        widest = max(preamble, key=len)
                        if len(widest) > 3:
                            header = [c.strip().lower() for c in widest]
                            print(f"      (header not identified; falling back "
                                  f"to widest row, {len(header)} columns)")
                        else:
                            return
                continue
            yield dict(zip(header, row))


# ---------------------------------------------------------------------------
# FILE IDENTITY VERIFICATION
# cms-hpt.txt discovery returns whichever file a system happens to list first.
# For a multi-hospital system that file may belong to an entirely different
# facility — in testing, San Diego Kaiser hospitals resolved to a Northern
# California file. Verify the file names the hospital we asked for; if it
# clearly doesn't, refuse it. No data is better than another hospital's data.
# ---------------------------------------------------------------------------
# Only words that carry no distinguishing power. "Memorial", "Community" and
# similar are deliberately NOT here: they are exactly what separates
# "Sharp Memorial" from "Sharp Chula Vista".
GENERIC_TOKENS = {
    "hospital", "medical", "center", "centre", "health", "healthcare", "hlthcr",
    "the", "of", "and", "for", "inc", "llc", "system", "campus", "care",
    "ctr", "med", "svcs", "hosp",
}


# Filenames carry boilerplate that dilutes the real name: an EIN, the words
# "standard charges", a region code, a language code, the extension. Kaiser's
# "941105628-san-diego-clairemont-medical-center-standard-charges-scal-en.csv"
# scored 0.50 purely because of this padding.
FILE_NOISE = {
    "standard", "charges", "standardcharges", "chargemaster", "csv", "json",
    "xlsx", "scal", "ncal", "nw", "co", "mas", "hi", "ga", "en", "es",
    "shoppable", "services", "price", "prices", "pricing", "transparency",
    "machine", "readable", "file", "cdm", "list", "final", "current",
}


def name_tokens(s: str) -> set[str]:
    toks = {t for t in _flatten(s).split()
            if len(t) > 2 and not t.isdigit()}
    distinctive = toks - GENERIC_TOKENS - FILE_NOISE
    return distinctive or (toks - FILE_NOISE) or toks


def match_score(expected: str, actual: str) -> float:
    """
    Similarity between a CMS facility name and a candidate file's name.
    Returns 0..1. Used both to rank candidates and to accept or reject them.
    """
    a, b = name_tokens(expected), name_tokens(actual)
    if not a or not b:
        return 1.0
    shared = a & b
    if not shared:
        return 0.0
    return max(len(shared) / len(a), len(shared) / len(b))


def names_match(expected: str, actual: str, threshold: float = 0.6) -> bool:
    """
    Compare a CMS facility name against the name inside a price file.
    Compares distinctive tokens only, so "Scripps Mercy Hospital" still
    matches "Scripps Mercy Hospital San Diego", while "Sharp Memorial"
    does not match "Sharp Chula Vista".
    """
    a, b = name_tokens(expected), name_tokens(actual)
    if not a or not b:
        return True          # nothing to compare on; don't block
    # Compare in both directions. A system may name its file more briefly than
    # CMS names the facility ("San Diego Medical Center" vs "Kaiser Foundation
    # Hospital - San Diego"), which a one-directional check would reject.
    return match_score(expected, actual) >= threshold


def read_identity(url: str) -> dict:
    """Read a price file's metadata header without downloading the whole file."""
    ident = {"hospital_name": "", "location_name": "", "address": "",
             "last_updated": "", "version": "", "raw": ""}
    try:
        if url.lower().split("?")[0].endswith(".json"):
            with SESSION.get(url, stream=True, headers=UA, timeout=120) as r:
                r.raise_for_status()
                head = next(r.iter_content(60000), b"").decode("utf-8", "replace")
            ident["raw"] = head[:400]
            m = re.search(r'"hospital_name"\s*:\s*"([^"]{2,120})"', head)
            if m:
                ident["hospital_name"] = m.group(1)
            m = re.search(r'"(?:location_name|hospital_location)"\s*:\s*\[?\s*"([^"]{2,120})"', head)
            if m:
                ident["location_name"] = m.group(1)
            return ident

        with SESSION.get(url, stream=True, headers=UA, timeout=120) as r:
            r.raise_for_status()
            lines = []
            for raw in r.iter_lines(decode_unicode=False):
                if raw:
                    lines.append(raw.decode("utf-8", "replace"))
                if len(lines) >= 4:
                    break
        rows = list(csv.reader(lines))
        if len(rows) >= 2:
            hdr = [c.strip().lower() for c in rows[0]]
            vals = rows[1]
            rec = dict(zip(hdr, vals))
            ident["hospital_name"] = rec.get("hospital_name", "")
            ident["location_name"] = rec.get("location_name", "") or rec.get("hospital_location", "")
            ident["address"] = rec.get("hospital_address", "")
            # Freshness and schema version are published in every file's header
            # and nobody aggregates them. A file last updated two years ago is
            # a compliance signal in itself.
            ident["last_updated"] = rec.get("last_updated_on", "")
            ident["version"] = rec.get("version", "")
            ident["raw"] = " | ".join(vals[:5])
    except Exception as e:
        print(f"      identity check failed: {e}")
    return ident


def address_score(expected: str, actual: str) -> float:
    """
    Compare street addresses. Kaiser publishes both a Clairemont and a Zion
    file for San Diego and CMS lists one "Kaiser Foundation Hospital - San
    Diego", so the name alone can't decide. The street address can.
    """
    def parts(s):
        s = _flatten(s)
        nums = {t for t in s.split() if t.isdigit()}
        words = {t for t in s.split()
                 if len(t) > 2 and not t.isdigit()
                 and t not in {"road", "street", "avenue", "drive", "boulevard",
                               "lane", "way", "suite", "ste", "the"}}
        return nums, words

    en, ew = parts(expected)
    an, aw = parts(actual)
    if not (en or ew) or not (an or aw):
        return 0.0
    score = 0.0
    if en & an:
        score += 0.6                      # same street number is strong evidence
    if ew and aw:
        score += 0.4 * len(ew & aw) / len(ew)
    return min(score, 1.0)


def verify_file_belongs(hospital_name: str, url: str) -> tuple[bool, str]:
    """Return (ok, explanation) for whether this file belongs to this hospital."""
    ident = read_identity(url)
    candidates = [ident["hospital_name"], ident["location_name"], url.split("/")[-1]]
    candidates = [c for c in candidates if c]
    if not candidates:
        return True, "no identifying metadata; accepted"
    for c in candidates:
        if names_match(hospital_name, c):
            return True, f"matches '{c[:60]}'"
    return False, (f"file identifies as '{candidates[0][:60]}' "
                   f"which does not match '{hospital_name}'")


# ---------------------------------------------------------------------------
# JSON price files
# CMS publishes a JSON schema alongside the CSV templates. Structure:
#   { "hospital_name": ..., "standard_charge_information": [
#       { "description": ...,
#         "code_information": [ {"code": "70551", "type": "CPT"}, ... ],
#         "standard_charges": [
#           { "gross_charge": .., "discounted_cash": .., "minimum": .., "maximum": ..,
#             "payers_information": [
#               {"payer_name": .., "plan_name": .., "standard_charge_dollar": ..,
#                "estimated_amount": ..} ] } ] } ] }
# These files are as large as the CSVs, so they're streamed with ijson rather
# than loaded whole.
# ---------------------------------------------------------------------------
JSON_ARRAY_KEYS = ("standard_charge_information", "standard_charges", "charges")


def detect_json_array_key(url: str) -> Optional[str]:
    """Read just the head of the file to find which top-level array holds the data."""
    try:
        with SESSION.get(url, stream=True, headers=UA, timeout=120) as resp:
            resp.raise_for_status()
            head = b""
            for chunk in resp.iter_content(65536):
                head += chunk
                if len(head) > 400_000:
                    break
            text = head.decode("utf-8", "replace")
            for key in JSON_ARRAY_KEYS:
                if re.search(rf'"{key}"\s*:\s*\[', text):
                    return key
    except Exception as e:
        print(f"      could not inspect JSON head: {e}")
    return None


def stream_json_items(url: str, array_key: str) -> Iterator[dict]:
    """Stream items out of a large JSON file without loading it into memory."""
    try:
        import ijson
    except ImportError:
        print("      ijson not installed - cannot stream JSON. "
              "Add 'ijson' to the workflow's pip install step.")
        return
    with SESSION.get(url, stream=True, headers=UA, timeout=600) as resp:
        resp.raise_for_status()
        resp.raw.decode_content = True
        for item in ijson.items(resp.raw, f"{array_key}.item"):
            yield item


def _num(v) -> Optional[float]:
    try:
        f = float(v)
        return round(f, 2) if f > 0 else None
    except (TypeError, ValueError):
        return None


def extract_prices_json(hospital_id: str, url: str, verbose: bool = True) -> list[dict]:
    """Parse a CMS-schema JSON price file."""
    array_key = detect_json_array_key(url)
    if not array_key:
        if verbose:
            print("      JSON file, but no recognized standard-charge array found.")
        return []
    if verbose:
        print(f"      JSON format, reading '{array_key}'")

    found, scanned, types_seen, samples = [], 0, {}, []
    for item in stream_json_items(url, array_key):
        scanned += 1
        codes = item.get("code_information") or item.get("codes") or []
        if isinstance(codes, dict):
            codes = [codes]

        pid = None
        for c in codes:
            code = str(c.get("code", "")).strip()
            ctype = str(c.get("type", "")).strip()
            if ctype:
                types_seen[norm_type(ctype)] = types_seen.get(norm_type(ctype), 0) + 1
            if len(samples) < 12 and code and code not in samples:
                samples.append(f"{code}[{ctype or '?'}]")
            hit = match_code(code, ctype)
            if hit:
                pid = hit
                break
        if not pid:
            continue

        desc = clean_text(item.get("description"))
        resolved_ids = refine_by_description(pid, desc)
        charges = item.get("standard_charges") or []
        if isinstance(charges, dict):
            charges = [charges]

        for ch, pid in ((c, p) for c in charges for p in resolved_ids):
            base = {
                "hospital_id": hospital_id,
                "procedure": pid,
                "cash": _num(ch.get("discounted_cash")),
                "gross": _num(ch.get("gross_charge")),
                "min": _num(ch.get("minimum")),
                "max": _num(ch.get("maximum")),
                "description": desc,
            }
            payers = ch.get("payers_information") or []
            if isinstance(payers, dict):
                payers = [payers]
            if not payers:
                found.append({**base, "payer": None, "rate": None})
                continue
            for p in payers:
                rate = (_num(p.get("standard_charge_dollar"))
                        or _num(p.get("estimated_amount"))
                        or _num(p.get("standard_charge_negotiated_dollar")))
                name = clean_text(p.get("payer_name"), 80) or None
                plan = str(p.get("plan_name") or "").strip()
                if name and plan:
                    name = f"{name} {plan}"
                found.append({**base, "payer": name, "rate": rate})

    if verbose:
        print(f"      scanned {scanned:,} items | code types seen: "
              f"{dict(sorted(types_seen.items(), key=lambda x: -x[1])[:6])}")
        if not found:
            print(f"      NO MATCHES. Sample codes in file: {samples}")
    return found


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


# Some codes cover more than one procedure and can only be separated by the
# item description. Maps a procedure id to (keyword, resulting id) rules.
DESCRIPTION_SPLITS = {
    "joint-replacement": [
        ("hip", "hip-replacement"),
        ("knee", "knee-replacement"),
    ],
}


def refine_by_description(pid: str, description: str) -> list[str]:
    """
    Resolve a shared code using the row's description, returning every
    procedure the row legitimately covers.

    MS-DRG 470's official title is "MAJOR HIP AND KNEE JOINT REPLACEMENT OR
    REATTACHMENT OF LOWER EXTREMITY WITHOUT MCC" — it names both, because the
    code genuinely covers both and the hospital's rate is the same either way.
    So an ambiguous row populates BOTH procedures rather than neither. Only a
    description naming one specifically narrows it to that one.
    """
    rules = DESCRIPTION_SPLITS.get(pid)
    if not rules:
        return [pid]
    d = (description or "").lower()
    hits = [target for kw, target in rules if kw in d]
    if len(hits) == 1:
        return hits
    return [target for _, target in rules]      # covers both


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


# Wide format embeds the payer in the column name:
#   standard_charge|aetna hmo/ppo|negotiated_dollar
# Tall format instead has one payer_name column plus standard_charge|negotiated_dollar.
WIDE_RATE_RE = re.compile(
    r"^standard_charge\|(?P<payer>.+?)\|(?:negotiated_dollar|negotiated_rate)$")
WIDE_ALT_RE = re.compile(
    r"^(?:estimated_amount|median_amount)\|(?P<payer>.+?)$")


def find_wide_payers(keys: list[str]) -> dict[str, dict]:
    """Map payer name -> {rate column, fallback column} for wide-format files."""
    payers: dict[str, dict] = {}
    for k in keys:
        m = WIDE_RATE_RE.match(k.strip().lower())
        if m:
            payers.setdefault(m.group("payer").strip(), {})["rate"] = k
    for k in keys:
        m = WIDE_ALT_RE.match(k.strip().lower())
        if m:
            payers.setdefault(m.group("payer").strip(), {})["alt"] = k
    return payers


def clean_text(v, limit: int = 120) -> str:
    """
    Normalise hospital-supplied text before it reaches the published data.
    The site escapes on output, but keeping junk out of the files means a
    stray control character or a megabyte-long description can't degrade the
    data or the page. Defence at both ends.
    """
    s = str(v or "")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


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


# Bound per-hospital capture. A malformed file claiming a million codes
# shouldn't be able to exhaust memory mid-run.
MAX_CODES_PER_HOSPITAL = 60_000

# Set per region in main(); each hospital's catalogue is written here.
CATALOGUE_DIR = None


def extract_prices(hospital_id: str, url: str, verbose: bool = True,
                   capture_all: bool = True) -> list[dict]:
    """
    Pull only rows matching TARGET_CODES, capturing cash price and payer rates.
    Prints what it detected so a zero-row result is diagnosable without
    re-downloading a very large file.
    """
    if url.lower().split("?")[0].endswith(".json"):
        return extract_prices_json(hospital_id, url, verbose=verbose)

    found: list[dict] = []
    keys = None
    code_pairs: list[tuple[str, Optional[str]]] = []
    cash_c = gross_c = payer_c = rate_c = desc_c = loc_c = None
    dunit_c = dtype_c = None
    median_c = min_c = max_c = None
    wide_payers: dict[str, dict] = {}
    scanned = 0
    types_seen: dict[str, int] = {}
    sample_codes: list[str] = []
    # CMS requires a dollar amount wherever one can be expressed. Hospitals
    # that answer with "percentage" or "algorithm" instead are technically
    # compliant but practically useless to a patient. Nobody measures how
    # often that happens; we can, because we're reading every row anyway.
    quality = {"dollar": 0, "percentage": 0, "algorithm": 0}
    # Coverage: how many distinct billing codes this hospital publishes a cash
    # price for. CMS requires 300 shoppable services; nobody aggregates how
    # many hospitals actually meet it.
    coded_with_cash: set = set()
    # Full-catalogue capture. The curated 58 procedures keep their payer-level
    # detail; everything else is stored as cash + gross only. That's the
    # difference between 26 MB and 860 MB nationally, and someone searching an
    # obscure code overwhelmingly wants the cash price.
    all_codes: dict[str, dict] = {}
    # Drugs are billed under HCPCS "J" codes. Rather than guess which J-code
    # maps to which drug — a good way to publish wrong data — capture them all
    # with their descriptions and let the merge step keep the ones that appear
    # at enough hospitals to compare.
    drugs: dict[str, dict] = {}

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
            rate_c = col(keys, "negotiated_dollar", "negotiated_rate", "negotiated")
            # CY2026 rule: when a contract is a percentage or algorithm rather
            # than a flat dollar amount, hospitals must publish actual allowed
            # amounts instead. Use those when no dollar rate is present.
            median_c = col(keys, "median_amount", "allowed_amount")
            min_c = col(keys, "standard_charge|min", "_min", "minimum")
            max_c = col(keys, "standard_charge|max", "_max", "maximum")
            desc_c = col(keys, "description")
            loc_c = col(keys, "location_name", "hospital_location", "location")
            dunit_c = col(keys, "drug_unit_of_measurement", "drug_unit")
            dtype_c = col(keys, "drug_type_of_measurement", "drug_type")
            wide_payers = find_wide_payers(keys)
            if verbose:
                print(f"      columns: {len(keys)} | code slots: "
                      f"{[c for c, _ in code_pairs]}")
                if wide_payers:
                    print(f"      WIDE format: {len(wide_payers)} payer columns "
                          f"e.g. {list(wide_payers)[:3]}")
                else:
                    print(f"      TALL format: payer={payer_c} rate={rate_c}")
                print(f"      cash={cash_c} gross={gross_c}")

        row_cash = money(row.get(cash_c))
        for code_c, type_c in code_pairs:
            code = (row.get(code_c) or "").strip()
            if not code:
                continue
            ctype = (row.get(type_c) or "").strip() if type_c else ""

            nt = norm_type(ctype)
            if row_cash and nt in ("CPT", "HCPCS", "MSDRG", "DRG", "APC"):
                key = f"{nt}:{code.strip().upper()}"
                coded_with_cash.add(f"{nt}:{norm_code(code)}")
                if capture_all and len(all_codes) < MAX_CODES_PER_HOSPITAL:
                    prev = all_codes.get(key)
                    # Keep the lowest cash price seen for a code: files repeat
                    # a code across settings, and the lower is the one a
                    # patient could actually be offered.
                    if not prev or row_cash < prev["cash"]:
                        all_codes[key] = {
                            "d": clean_text(row.get(desc_c), 70),
                            "cash": round(row_cash),
                            "gross": round(money(row.get(gross_c)) or 0) or None,
                        }

            # HCPCS J-codes are drugs administered in a facility.
            if nt == "HCPCS" and code.upper().startswith("J") and row_cash:
                key = code.upper()
                # Unit matters enormously: the same J-code priced per-mg at
                # one hospital and per-vial at another differs by thousands of
                # times, which is a measurement artefact, not a price finding.
                unit = clean_text(row.get(dunit_c), 20) if dunit_c else ""
                utype = clean_text(row.get(dtype_c), 20) if dtype_c else ""
                ukey = f"{key}|{unit.lower()}|{utype.lower()}"
                d = drugs.setdefault(ukey, {
                    "code": key,
                    "unit": unit,
                    "unit_type": utype,
                    "description": clean_text(row.get(desc_c), 90),
                    "cash": row_cash,
                    "gross": money(row.get(gross_c)),
                })
                if row_cash < d["cash"]:
                    d["cash"] = row_cash
            if len(sample_codes) < 12 and code not in sample_codes:
                sample_codes.append(f"{code}[{ctype or '?'}]")
            if ctype:
                types_seen[norm_type(ctype)] = types_seen.get(norm_type(ctype), 0) + 1

            pid = match_code(code, ctype)
            if not pid:
                continue

            pct_c = col(keys, "negotiated_percentage")
            algo_c = col(keys, "negotiated_algorithm")
            if money(row.get(rate_c)):
                quality["dollar"] += 1
            elif pct_c and str(row.get(pct_c) or "").strip():
                quality["percentage"] += 1
            elif algo_c and str(row.get(algo_c) or "").strip():
                quality["algorithm"] += 1

            base = {
                "hospital_id": hospital_id,
                "procedure": pid,
                "cash": money(row.get(cash_c)),
                "gross": money(row.get(gross_c)),
                "min": money(row.get(min_c)),
                "max": money(row.get(max_c)),
                "location": (row.get(loc_c) or "").strip() if loc_c else "",
                "description": clean_text(row.get(desc_c)),
            }

            # A shared code (MS-DRG 470) can cover more than one procedure.
            for resolved in refine_by_description(pid, row.get(desc_c) or ""):
                rec = {**base, "procedure": resolved}
                if wide_payers:
                    any_rate = False
                    for pname, cols in wide_payers.items():
                        rate = (money(row.get(cols.get("rate")))
                                or money(row.get(cols.get("alt"))))
                        if rate is None:
                            continue
                        any_rate = True
                        found.append({**rec, "payer": clean_text(pname, 80), "rate": rate})
                    if not any_rate:
                        found.append({**rec, "payer": None, "rate": None})
                else:
                    found.append({
                        **rec,
                        "payer": clean_text(row.get(payer_c), 80) or None,
                        "rate": money(row.get(rate_c)) or money(row.get(median_c)),
                    })
            break  # one code slot per row is enough

    if verbose:
        print(f"      scanned {scanned:,} rows | code types seen: "
              f"{dict(sorted(types_seen.items(), key=lambda x: -x[1])[:6])}")
        tot = sum(quality.values())
        if tot:
            print(f"      rate quality: {quality['dollar']*100//tot}% dollar amounts, "
                  f"{quality['percentage']*100//tot}% percentage-only, "
                  f"{quality['algorithm']*100//tot}% algorithm-only")
    if verbose:
        print(f"      coverage: {len(coded_with_cash):,} distinct codes with a "
              f"cash price | {len(drugs)} drug (J) codes | "
              f"{len(all_codes):,} codes catalogued for search")
    # The catalogue is written to its own file, once per hospital, and never
    # enters the prices file. The prices file is rewritten after every
    # hospital for checkpointing — carrying a 160 MB catalogue through that
    # would mean tens of gigabytes of pointless writes on a statewide run.
    meta = {"quality": quality,
            "codes_with_cash": len(coded_with_cash),
            "drugs": drugs}
    if found:
        found[0]["_meta"] = meta
    if all_codes and CATALOGUE_DIR is not None:
        try:
            CATALOGUE_DIR.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", hospital_id)
            (CATALOGUE_DIR / f"{safe}.json").write_text(
                json.dumps(all_codes, separators=(",", ":")))
        except Exception as e:
            print(f"      could not save catalogue: {e}")
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
            "cash": None, "gross": None, "min": None, "max": None,
            "payers": {}, "description": r["description"],
        })
        if r.get("min") and not rec["min"]:
            rec["min"] = r["min"]
        if r.get("max") and not rec["max"]:
            rec["max"] = r["max"]
        if r["cash"] and not rec["cash"]:
            rec["cash"] = r["cash"]
        if r["gross"] and not rec["gross"]:
            rec["gross"] = r["gross"]
        if r["payer"] and r["rate"]:
            rec["payers"][r["payer"]] = r["rate"]
        if r.get("_meta") and "_meta" not in rec:
            rec["_meta"] = r["_meta"]
    return out


def probe(url: str, rows: int = 40):
    """
    Peek at a price file's structure without downloading the whole thing.
    Use this to diagnose a hospital that returns zero matches, instead of
    waiting through another full run.
    """
    print(f"probing {url}")

    if url.lower().split("?")[0].endswith(".json"):
        key = detect_json_array_key(url)
        print(f"  JSON file. standard-charge array: {key or 'NOT FOUND'}")
        if not key:
            print("  Top of file:")
            with SESSION.get(url, stream=True, headers=UA, timeout=120) as r:
                r.raise_for_status()
                head = next(r.iter_content(4000), b"")
            print("  " + head.decode("utf-8", "replace")[:1500])
            return
        shown = 0
        for item in stream_json_items(url, key):
            codes = item.get("code_information") or []
            charges = item.get("standard_charges") or []
            payers = (charges[0].get("payers_information") if charges else []) or []
            print(f"    {str(item.get('description',''))[:52]:54s} "
                  f"codes={[(c.get('code'), c.get('type')) for c in codes][:3]}")
            if shown == 0 and charges:
                print(f"      charge keys: {sorted(charges[0].keys())}")
                if payers:
                    print(f"      payer keys:  {sorted(payers[0].keys())}")
            shown += 1
            if shown >= rows:
                break
        return

    seen = 0
    for row in stream_rows(url):
        if "_raw_json_line" in row:
            print("  (unexpected JSON content)")
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


# ---------------------------------------------------------------------------
# CONDITIONAL FETCHING
# Hospitals must update these files at least annually; most change quarterly at
# most. Re-downloading a 3GB file that hasn't changed is the single biggest
# waste in a refresh. Store each file's ETag / Last-Modified and ask the server
# whether it changed — a 304 response means we skip the download entirely.
# ---------------------------------------------------------------------------
def file_unchanged(url: str, cache: dict) -> bool:
    """True if the server says this file hasn't changed since we last read it."""
    entry = cache.get(url)
    if not entry:
        return False
    headers = {}
    if entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]
    if entry.get("last_modified"):
        headers["If-Modified-Since"] = entry["last_modified"]
    if not headers:
        return False
    try:
        r = SESSION.get(url, headers=headers, stream=True, timeout=60)
        r.close()
        if r.status_code == 304:
            return True
        # Some servers ignore conditional headers but still report size.
        clen = r.headers.get("Content-Length")
        if (clen and entry.get("length") and clen == entry["length"]
                and r.headers.get("ETag", entry.get("etag")) == entry.get("etag")):
            return True
    except requests.RequestException:
        pass
    return False


def remember_file(url: str, cache: dict):
    """Record validators so the next run can ask 'has this changed?'"""
    try:
        r = SESSION.get(url, stream=True, timeout=60)
        cache[url] = {
            "etag": r.headers.get("ETag"),
            "last_modified": r.headers.get("Last-Modified"),
            "length": r.headers.get("Content-Length"),
            "seen": None,
        }
        r.close()
    except requests.RequestException:
        pass


# ---------------------------------------------------------------------------
# SCAN STATUS TRACKING
# Records what happened to every hospital on every attempt, so the site can
# show why a hospital has no prices and how stale the answer is — and so
# repeat runs don't re-hammer servers we already read successfully.
# ---------------------------------------------------------------------------
STATUS_OK = "ok"                  # prices collected
STATUS_NO_FILE = "no_file"        # nothing published at the standard location
STATUS_UNREACHABLE = "unreachable"  # published but server refused / timed out
STATUS_WRONG_FILE = "wrong_file"  # only found a file belonging to another facility
STATUS_EMPTY = "empty"            # file parsed but contained none of our procedures
STATUS_EXEMPT = "exempt"          # federal facility, rule doesn't apply

# 45 CFR 180 applies to Medicare-enrolled hospitals and non-Medicare
# institutions licensed as a hospital by a State. Federal facilities are not
# State-licensed, so a missing file there is not a compliance question.
FEDERAL_MARKERS = ("veterans affairs", "va medical", "va san diego",
                   "naval", "nmc ", "nh ", "army", "air force",
                   "department of defense", "military")


def is_federal(name: str, ownership: str = "") -> bool:
    blob = f"{name} {ownership}".lower()
    return any(m in blob for m in FEDERAL_MARKERS)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def days_since(iso: str) -> float:
    from datetime import date
    try:
        y, m, d = (int(x) for x in iso.split("T")[0].split("-"))
        return (date.today() - date(y, m, d)).days
    except Exception:
        return 1e6


def verify_code(url: str, code: str, limit: int = 6):
    """
    Print the raw rows a price file holds for one billing code, with every
    column, plus the values this pipeline extracts from them.

    This is the check that proves a number on the site is the number in the
    hospital's file — not a plausible-looking figure read from the wrong
    column. Use it whenever a price looks surprising.
    """
    print(f"looking for code {code} in {url}\n")
    want = norm_code(code)
    shown = 0

    if url.lower().split("?")[0].endswith(".json"):
        key = detect_json_array_key(url)
        if not key:
            print("  no standard-charge array found")
            return
        for item in stream_json_items(url, key):
            codes = item.get("code_information") or []
            if not any(norm_code(str(c.get("code", ""))) == want for c in codes):
                continue
            print(f"  description: {item.get('description','')}")
            print(f"  codes: {[(c.get('code'), c.get('type')) for c in codes]}")
            for ch in (item.get("standard_charges") or []):
                print(f"    gross={ch.get('gross_charge')} "
                      f"cash={ch.get('discounted_cash')} "
                      f"min={ch.get('minimum')} max={ch.get('maximum')} "
                      f"setting={ch.get('setting')}")
                for p in (ch.get("payers_information") or [])[:6]:
                    print(f"      {p.get('payer_name')} / {p.get('plan_name')}: "
                          f"dollar={p.get('standard_charge_dollar')} "
                          f"est={p.get('estimated_amount')}")
            shown += 1
            print()
            if shown >= limit:
                break
        if not shown:
            print(f"  code {code} not present in this file")
        return

    keys = None
    code_pairs = []
    for row in stream_rows(url):
        if keys is None:
            keys = list(row.keys())
            code_pairs = find_code_columns(keys)
        if not any(norm_code(row.get(c, "")) == want for c, _ in code_pairs):
            continue
        print(f"  --- match {shown+1} ---")
        for k, v in row.items():
            if str(v).strip():
                print(f"    {k} = {v}")
        shown += 1
        print()
        if shown >= limit:
            break
    if not shown:
        print(f"  code {code} not present in this file")
    else:
        print(f"  The pipeline reads 'standard_charge|discounted_cash' as the "
              f"cash price\n  and 'standard_charge|gross' as the list charge. "
              f"Compare those above\n  against what the site displays.")


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="san-diego", choices=list(REGIONS))
    ap.add_argument("--stage", default="all",
                    choices=["all", "registry", "geocode", "prices",
                             "probe", "verify"])
    ap.add_argument("--url", default="", help="file URL for --stage probe/verify")
    ap.add_argument("--code", default="", help="billing code to look up with --stage verify")
    ap.add_argument("--shard", type=int, default=0, help="which shard (0-indexed)")
    ap.add_argument("--shards", type=int, default=1, help="total shards")
    ap.add_argument("--limit", type=int, default=0, help="cap hospitals, for testing")
    ap.add_argument("--debug", action="store_true", help="print a sample CMS record")
    ap.add_argument("--max-age", type=int, default=30,
                    help="skip hospitals whose data succeeded within this many days")
    ap.add_argument("--refresh-all", action="store_true",
                    help="ignore --max-age and re-scan every hospital")
    ap.add_argument("--only", default="",
                    help="comma-separated hospital ids or name fragments to scan")
    ap.add_argument("--workers", type=int, default=4,
                    help="hospitals to download concurrently (one per host)")
    ap.add_argument("--cooldown-days", type=int, default=1,
                    help="days to leave a host alone after it refuses connections")
    ap.add_argument("--no-type-filter", action="store_true",
                    help="keep every facility type (use if the type filter finds nothing)")
    args = ap.parse_args()

    if args.stage == "verify":
        if not (args.url and args.code):
            print("--stage verify needs --url and --code", file=sys.stderr)
            sys.exit(1)
        verify_code(args.url, args.code)
        return

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

        # Carry forward work already done. Rebuilding from scratch would throw
        # away every geocode and every discovered file URL, forcing a full
        # re-geocode (and a fresh hammering of the Census API) on every run.
        prior_reg = {h["id"]: h for h in load_json(reg_path, [])}
        reused = 0
        for h in hospitals:
            old = prior_reg.get(h["id"])
            if not old:
                continue
            if old.get("lat") is not None:
                h["lat"], h["lng"] = old["lat"], old["lng"]
                reused += 1
            # Keep a discovered URL, but never override a hand-verified one.
            if not h.get("mrf_url") and old.get("mrf_url"):
                h["mrf_url"] = old["mrf_url"]
                h["verified_source"] = old.get("verified_source", False)
        print(f"[registry] {len(hospitals)} hospitals "
              f"({reused} coordinates reused from previous run)")
        reg_path.write_text(json.dumps(hospitals, indent=1))
    else:
        hospitals = json.loads(reg_path.read_text())

    # --- geocode ---
    if args.stage in ("all", "geocode"):
        todo = [h for h in hospitals if h.get("lat") is None]
        if todo:
            print(f"[geocode] {len(todo)} need coordinates "
                  f"({len(hospitals) - len(todo)} already located)")
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
        from datetime import date
        today = date.today().isoformat()

        global CATALOGUE_DIR
        CATALOGUE_DIR = DATA / f"{args.region}-catalogue"

        status_path = DATA / f"{args.region}-status.json"
        status = load_json(status_path, {})
        prev_prices = load_json(price_path, {})
        cooldowns = load_json(DATA / "host-cooldowns.json", {})
        http_cache = load_json(DATA / "file-cache.json", {})

        only = [s.strip().lower() for s in args.only.split(",") if s.strip()]

        # run_scope is every hospital this RUN covers across all shards. Used
        # for preservation, so shard 0 doesn't restore stale records for a
        # hospital shard 1 is refreshing right now.
        run_scope = hospitals
        if only:
            run_scope = [h for h in run_scope
                         if any(o in h["id"].lower() or o in h["name"].lower()
                                for o in only)]
        shard = [h for i, h in enumerate(run_scope) if i % args.shards == args.shard]

        print(f"[prices] shard {args.shard+1}/{args.shards}: {len(shard)} hospitals")
        all_rows: list[dict] = []
        stats = {"found": 0, "skipped": 0, "unchanged": 0, "no_mrf": 0,
                 "error": 0, "wrong_file": 0, "exempt": 0, "cooled": 0}
        url_users: dict[str, list[str]] = {}
        cache: dict[str, list[dict]] = {}
        last_host_time: dict[str, float] = {}

        def host_of(u: str) -> str:
            return u.split("/")[2] if "//" in u else u

        def be_polite(u: str):
            host = host_of(u)
            wait = 2.0 - (time.time() - last_host_time.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            last_host_time[host] = time.time()

        def host_cooling(u: str) -> bool:
            c = cooldowns.get(host_of(u))
            return bool(c and days_since(c) < args.cooldown_days)

        def cool_host(u: str):
            cooldowns[host_of(u)] = today

        def filter_to_location(rows, hospital_name):
            locs = {r.get("location") for r in rows if r.get("location")}
            if len(locs) <= 1:
                return rows
            keep = [r for r in rows if names_match(hospital_name, r.get("location", ""))]
            print(f"      file covers {len(locs)} locations; "
                  f"kept {len(keep)}/{len(rows)} rows for this hospital")
            return keep if keep else rows

        def record(h, st, detail="", rows=0, url=None):
            prior = status.get(h["id"], {})
            status[h["id"]] = {
                "name": h["name"],
                "status": st,
                "detail": detail,
                "rows": rows if st == STATUS_OK else prior.get("rows", 0),
                "source_url": url or prior.get("source_url"),
                "last_attempt": today,
                "last_success": today if st == STATUS_OK else prior.get("last_success"),
                "attempts": prior.get("attempts", 0) + 1,
                "consecutive_failures": 0 if st == STATUS_OK
                                        else prior.get("consecutive_failures", 0) + 1,
                "rule_applies": not is_federal(h["name"], h.get("system", "")),
            }

        def carry_over(h):
            """Reuse prices already collected for a hospital we're skipping."""
            kept = {k: v for k, v in prev_prices.items()
                    if v.get("hospital_id") == h["id"]}
            return kept

        carried: dict[str, dict] = {}
        import threading
        from concurrent.futures import ThreadPoolExecutor
        lock = threading.Lock()

        def checkpoint():   # callers may be on worker threads
            """
            Write results after every hospital.

            Runs take 30-60 minutes and download gigabytes. Writing only at the
            end means a timeout, a cancelled job, or one unhandled error throws
            away every hospital already processed. Writing as we go costs
            milliseconds and makes any interrupted run resumable.
            """
            with lock:
                snapshot = list(all_rows)
                carried_snapshot = dict(carried)
            partial = consolidate(snapshot)
            partial.update(carried_snapshot)
            touched_now = {h["id"] for h in run_scope}
            for k, rec in prev_prices.items():
                if rec.get("hospital_id") in touched_now:
                    continue
                partial.setdefault(k, rec)
            try:
                price_path.write_text(json.dumps(partial, indent=1))
                status_path.write_text(json.dumps(status, indent=1))
                (DATA / "host-cooldowns.json").write_text(json.dumps(cooldowns, indent=1))
                (DATA / "file-cache.json").write_text(json.dumps(http_cache, indent=1))
                (DATA / "file-cache.json").write_text(json.dumps(http_cache, indent=1))
                reg_path.write_text(json.dumps(hospitals, indent=1))
            except Exception as e:
                print(f"      checkpoint write failed: {e}")

        if args.shards > 1:
            price_path = DATA / f"{args.region}-prices-{args.shard}.json"

        def handle(h):
            """Process one hospital. Shared state is mutated under a lock."""
            prior = status.get(h["id"], {})

            # Federal facilities: the rule doesn't reach them, so don't keep trying.
            if is_federal(h["name"], h.get("system", "")):
                record(h, STATUS_EXEMPT, "federal facility; not State-licensed")
                stats["exempt"] += 1
                print(f"    – {h['name']}: federal facility, rule does not apply")
                return

            # Already have fresh data? Leave the server alone.
            existing = carry_over(h)
            fresh = (prior.get("status") == STATUS_OK
                     and prior.get("last_success")
                     and days_since(prior["last_success"]) < args.max_age
                     and bool(existing))     # status says OK but data is gone -> rescan
            if (prior.get("status") == STATUS_OK and not existing
                    and not args.refresh_all):
                print(f"    ↻ {h['name']}: marked current but no records found; "
                      f"re-scanning")
            if fresh and not args.refresh_all and not only:
                kept = existing
                carried.update(kept)
                stats["skipped"] += 1
                age = int(days_since(prior["last_success"]))
                print(f"    · {h['name']}: skipped, {len(kept)} records "
                      f"already collected {age}d ago")
                return

            url = h.get("mrf_url")
            if url and host_cooling(url):
                carried.update(carry_over(h))
                stats["cooled"] += 1
                print(f"    · {h['name']}: {host_of(url)} is cooling down "
                      f"after refusing connections; skipping today")
                return

            if not url:
                domain = h.get("domain") or resolve_source(h.get("name", ""))[1]
                if domain and host_cooling(f"https://{domain}/"):
                    carried.update(carry_over(h))
                    stats["cooled"] += 1
                    print(f"    · {h['name']}: {domain} cooling down; skipping")
                    return
                if domain:
                    print(f"    ... discovering price file for {h['name']} via {domain}")
                    url = pick_matching_file(
                        h["name"], domain,
                        expected_address=f"{h.get('address','')} "
                                         f"{h.get('city','')} {h.get('zip','')}")
                    h["mrf_url"] = url
                    if url:
                        h["verified_source"] = True
                    if SOURCE_PAGES.get(domain):
                        h["price_page"] = SOURCE_PAGES[domain]

            if not url:
                stats["no_mrf"] += 1
                record(h, STATUS_NO_FILE,
                       "no machine-readable file found at the standard "
                       "/cms-hpt.txt location")
                checkpoint()
                print(f"    - {h['name']}: no price file found")
                return

            if not h.get("verified_source"):
                ok, why = verify_file_belongs(h["name"], url)
                if not ok:
                    stats["wrong_file"] += 1
                    record(h, STATUS_WRONG_FILE, why, url=url)
                    print(f"    ✗ {h['name']}: REJECTED — {why}")
                    h["mrf_url"] = None
                    return

            with lock:
                url_users.setdefault(url, []).append(h["name"])
            h["source_url"] = url
            try:
                if url in cache:
                    rows = filter_to_location(
                        [{**r, "hospital_id": h["id"]} for r in cache[url]], h["name"])
                    print(f"    ✓ {h['name']}: {len(rows)} rows "
                          f"(reused, same file as {url_users[url][0]})")
                elif (existing and not args.refresh_all
                      and file_unchanged(url, http_cache)):
                    # Server confirmed the file is byte-identical to last time.
                    rows = list(existing.values())
                    stats["unchanged"] = stats.get("unchanged", 0) + 1
                    print(f"    = {h['name']}: file unchanged since last run, "
                          f"reusing {len(rows)} records")
                    carried.update(existing)
                    record(h, STATUS_OK, "unchanged since last run",
                           rows=len(rows), url=url)
                    checkpoint()
                    return
                else:
                    be_polite(url)
                    rows = extract_prices(h["id"], url)
                    remember_file(url, http_cache)
                    cache[url] = rows
                    rows = filter_to_location(rows, h["name"])
                    print(f"    ✓ {h['name']}: {len(rows)} matching rows")
                with lock:
                    all_rows.extend(rows)
                    stats["found"] += 1
                record(h, STATUS_OK if rows else STATUS_EMPTY,
                       "" if rows else "file parsed but held none of our procedures",
                       rows=len(rows), url=url)
                checkpoint()
            except Exception as e:
                short = str(e).split("(Caused by")[0][:110]
                refused = ("refused" in short.lower() or "timed out" in short.lower()
                           or "max retries" in short.lower())
                if refused:
                    cool_host(url)
                stats["error"] += 1
                record(h, STATUS_UNREACHABLE, short, url=url)
                carried.update(carry_over(h))
                checkpoint()
                print(f"    ! {h['name']}: {short}")
                if refused:
                    print(f"      {host_of(url)} put on a "
                          f"{args.cooldown_days}-day cooldown")


        # Group by host, then run groups concurrently. Hospitals sharing a host
        # stay sequential within their group, so a system like Sharp still sees
        # one request at a time while unrelated systems download in parallel.
        groups: dict[str, list] = {}
        for h in shard:
            key = (h.get("domain")
                   or (h.get("mrf_url", "") or "").split("/")[2:3] or ["_none"])
            key = key if isinstance(key, str) else key[0]
            groups.setdefault(key, []).append(h)

        def run_group(hs):
            for h in hs:
                try:
                    handle(h)
                except Exception as e:
                    with lock:
                        stats["error"] += 1
                    print(f"    ! {h['name']}: unexpected {type(e).__name__}: {e}")

        workers = max(1, min(args.workers, len(groups)))
        print(f"[prices] {len(groups)} host group(s), {workers} concurrent")
        if workers == 1:
            for hs in groups.values():
                run_group(hs)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(run_group, groups.values()))

        # Shared-file disclosure
        shared = {u: names for u, names in url_users.items() if len(names) > 1}
        if shared:
            print("\n[prices] SHARED FILES — one file serves several facilities:")
            for u, names in shared.items():
                print(f"    {u.split('/')[-1][:60]}")
                for n in names:
                    print(f"        - {n}")
        shared_ids = {h["id"] for h in shard
                      for u in shared if h.get("source_url") == u}

        merged = consolidate(all_rows)
        for rec in merged.values():
            rec["shared_source"] = rec["hospital_id"] in shared_ids
        merged.update(carried)          # data for hospitals skipped as fresh

        # Anything collected on an earlier run for a hospital we did NOT touch
        # this time must survive. Without this, a targeted run (--only, or a
        # single shard) silently wipes every other hospital's prices.
        touched = {h["id"] for h in run_scope}
        preserved = 0
        for key, rec in prev_prices.items():
            if rec.get("hospital_id") in touched:
                continue                # rescanned; the new records replace it
            if key not in merged:
                merged[key] = rec
                preserved += 1
        if preserved:
            untouched = {r.get("hospital_id") for r in prev_prices.values()} - touched
            print(f"[prices] preserved {preserved} record(s) for "
                  f"{len(untouched)} hospital(s) outside this run")

        price_path.write_text(json.dumps(merged, indent=1))
        status_path.write_text(json.dumps(status, indent=1))
        (DATA / "host-cooldowns.json").write_text(json.dumps(cooldowns, indent=1))
        (DATA / "file-cache.json").write_text(json.dumps(http_cache, indent=1))
        reg_path.write_text(json.dumps(hospitals, indent=1))

        print(f"\n[prices] {stats}")
        print(f"[prices] {len(merged)} total price records "
              f"({len(carried)} carried over from earlier runs)")

        needs_attention = [s for s in status.values()
                           if s["status"] not in (STATUS_OK, STATUS_EXEMPT)
                           and s.get("rule_applies")]
        if needs_attention:
            print(f"\n[prices] {len(needs_attention)} hospital(s) subject to the rule "
                  f"have no usable price file:")
            for s in sorted(needs_attention, key=lambda x: -x.get("consecutive_failures", 0)):
                print(f"    {s['name']}")
                print(f"        status: {s['status']} — {s['detail'][:80]}")
                print(f"        attempts: {s['attempts']}, "
                      f"consecutive failures: {s['consecutive_failures']}, "
                      f"last checked {s['last_attempt']}")
            print("\n    Hospitals subject to 45 CFR 180 must publish a "
                  "machine-readable file.\n    Complaints can be submitted to CMS "
                  "(anonymously if preferred) via\n    "
                  "https://www.cms.gov/priorities/key-initiatives/hospital-price-transparency")
        print(f"[prices] wrote {price_path}")


if __name__ == "__main__":
    main()
