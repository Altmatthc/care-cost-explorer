"""Pipeline configuration constants — extracted verbatim from build_data.py.

Pure data only (region map, state list, hospital-type filter, target procedure
codes, CMS/geocoder endpoints, paths). No functions, no runtime state — safe to
import at module load with no ordering concerns.
"""
from __future__ import annotations

from pathlib import Path

CMS_DATASET = "xubh-q36u"  # Hospital General Information
CMS_API = f"https://data.cms.gov/provider-data/api/1/datastore/query/{CMS_DATASET}/0"
CENSUS_GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

# build_data.py lives in scripts/, so parents[1] of this file (scripts/ccx/) is
# the same repo root build_data computed as Path(__file__).resolve().parent.parent.
ROOT = Path(__file__).resolve().parent.parent.parent
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
