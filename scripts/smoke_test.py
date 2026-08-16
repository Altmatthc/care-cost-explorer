#!/usr/bin/env python3
"""
Offline smoke test for the pipeline.

Runs the real code paths against synthetic files with the network mocked, so
undefined names, bad ordering, and parsing regressions surface here instead of
30 minutes into a GitHub Actions run.

    python scripts/smoke_test.py

Exits non-zero on failure.
"""

import csv
import io
import json
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data as b  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
def make_tall_csv() -> list[str]:
    """CMS v3 tall format: 2 metadata rows, then header, then data."""
    attest = ("To the best of its knowledge and belief, this hospital has included all "
              "applicable standard charge information ... the information encoded is true.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["hospital_name", "last_updated_on", "version", "location_name",
                "hospital_address", "license_number|CA", "type_2_npi", attest])
    w.writerow(["Test General Hospital", "2026-04-01", "3.0.0", "Test General Hospital",
                "1 Main St", "123", "999", "True"])
    w.writerow(["description", "code|1", "code|1|type", "code|2", "code|2|type",
                "location_name", "standard_charge|gross", "standard_charge|discounted_cash",
                "payer_name", "standard_charge|negotiated_dollar",
                "standard_charge|min", "standard_charge|max"])
    w.writerow(["SUPPLY GAUZE", "PX-1", "CDM", "", "", "Test General Hospital",
                "14", "9", "Aetna", "6", "5", "9"])
    w.writerow(["MRI BRAIN", "PX-2", "CDM", "70551", "CPT", "Test General Hospital",
                "4200", "1450", "Aetna", "980", "870", "1310"])
    w.writerow(["MRI BRAIN", "PX-2", "CDM", "70551", "CPT", "Test General Hospital",
                "4200", "1450", "Anthem Blue Cross", "1020", "870", "1310"])
    w.writerow(["KNEE REPLACEMENT", "", "", "470", "DRG", "Test General Hospital",
                "88000", "38500", "Medicare", "19800", "18000", "31000"])
    w.writerow(["MRI BRAIN", "PX-2", "CDM", "70551", "CPT", "Other Campus Hospital",
                "5000", "1900", "Aetna", "1200", "1100", "1600"])
    return buf.getvalue().strip().split("\n")


def make_wide_csv() -> list[str]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["hospital_name", "version"])
    w.writerow(["Wide Format Hospital", "3.0.0"])
    w.writerow(["description", "code|1", "code|1|type",
                "standard_charge|gross", "standard_charge|discounted_cash",
                "standard_charge|aetna hmo/ppo|negotiated_dollar",
                "standard_charge|blue shield of ca|negotiated_dollar",
                "standard_charge|medicare|negotiated_dollar"])
    w.writerow(["MRI BRAIN", "70551", "CPT", "4100", "1470", "1010", "1180", "520"])
    w.writerow(["COLONOSCOPY", "45378", "CPT", "5900", "2350", "1720", "1890", "640"])
    return buf.getvalue().strip().split("\n")


def rows_from(lines):
    header = None
    for row in csv.reader(lines):
        if header is None:
            if b.looks_like_header(row):
                header = [c.strip().lower() for c in row]
            continue
        yield dict(zip(header, row))


# ---------------------------------------------------------------------------
print("\n1. Required functions exist")
for name in ["stream_rows", "looks_like_header", "extract_prices",
             "extract_prices_json", "pick_matching_file", "verify_file_belongs",
             "discover_mrf_candidates", "find_code_columns", "find_wide_payers",
             "match_code", "names_match", "consolidate", "read_identity"]:
    check(name, hasattr(b, name))

print("\n2. Header detection skips metadata and attestation rows")
lines = make_tall_csv()
hdr_idx = [i for i, l in enumerate(lines) if b.looks_like_header(next(csv.reader([l])))]
check("exactly one header row identified", len(hdr_idx) == 1, f"found {hdr_idx}")
check("header is the third row", hdr_idx == [2], f"got {hdr_idx}")

print("\n3. Tall-format extraction")
with mock.patch.object(b, "stream_rows", lambda u: rows_from(make_tall_csv())):
    tall = b.extract_prices("h1", "http://x/f.csv", verbose=False)
check("supply row ignored, targets found", len(tall) == 4, f"got {len(tall)}")
check("MS-DRG matched despite 'DRG' label",
      any(r["procedure"] == "knee-replacement" for r in tall))
check("CPT in second code slot matched",
      any(r["procedure"] == "mri-brain" for r in tall))
check("location captured", all("location" in r for r in tall))

print("\n4. Multi-location filtering")
locs = {r["location"] for r in tall}
check("two locations present in file", len(locs) == 2, f"got {locs}")
kept = [r for r in tall if b.names_match("Test General Hospital", r["location"])]
check("rows filtered to requested hospital", len(kept) == 3, f"got {len(kept)}")

print("\n5. Wide-format extraction")
with mock.patch.object(b, "stream_rows", lambda u: rows_from(make_wide_csv())):
    wide = b.extract_prices("h2", "http://x/w.csv", verbose=False)
check("every payer column captured", len(wide) == 6, f"got {len(wide)}")
cons = b.consolidate(wide)
mri = cons.get("h2|mri-brain", {})
check("three payers on one procedure", len(mri.get("payers", {})) == 3,
      f"got {mri.get('payers')}")

print("\n6. JSON extraction")
doc = [{
    "description": "MRI BRAIN",
    "code_information": [{"code": "0610", "type": "RC"}, {"code": "70551", "type": "CPT"}],
    "standard_charges": [{"gross_charge": 5200, "discounted_cash": 2640,
                          "minimum": 1180, "maximum": 3100,
                          "payers_information": [
                              {"payer_name": "Aetna", "plan_name": "PPO",
                               "standard_charge_dollar": 1810},
                              {"payer_name": "Anthem", "standard_charge_percentage": 55,
                               "estimated_amount": 1990}]}]},
    {"description": "GAUZE", "code_information": [{"code": "A6402", "type": "HCPCS"}],
     "standard_charges": [{"gross_charge": 14, "discounted_cash": 9,
                           "payers_information": []}]}]
with mock.patch.object(b, "detect_json_array_key", lambda u: "standard_charge_information"), \
     mock.patch.object(b, "stream_json_items", lambda u, k: iter(doc)):
    js = b.extract_prices("h3", "http://x/f.json", verbose=False)
check("json dispatch by extension", len(js) == 2, f"got {len(js)}")
check("percentage contract used estimated_amount",
      any(r["rate"] == 1990 for r in js))

print("\n7. Identity verification")
pairs_accept = [("Kaiser Foundation Hospital - San Diego", "San Diego Medical Center"),
                ("Grossmont Hospital", "Grossmont Hospital Corporation"),
                ("Scripps Mercy Hospital", "Scripps Mercy Hospital San Diego")]
pairs_reject = [("Kaiser Foundation Hospital - San Diego", "Antioch Medical Center"),
                ("Sharp Memorial Hospital", "Sharp Chula Vista Medical Center"),
                ("Palomar Medical Center Poway", "Palomar Medical Center Escondido")]
for a, c in pairs_accept:
    check(f"accept {c[:34]}", b.names_match(a, c))
for a, c in pairs_reject:
    check(f"reject {c[:34]}", not b.names_match(a, c))

print("\n8. Multi-candidate file selection")
cands = [f"https://x/{n}_standardcharges.csv" for n in
         ["sharp-chula-vista-medical-center", "sharp-memorial-hospital",
          "sharp-coronado-hospital", "grossmont-hospital-corporation"]]


def fake_ident(url):
    name = url.split("/")[-1].replace("_standardcharges.csv", "").replace("-", " ").title()
    return {"hospital_name": name, "location_name": "", "address": "", "raw": ""}


with mock.patch.object(b, "discover_mrf_candidates", lambda d: cands), \
     mock.patch.object(b, "read_identity", fake_ident):
    for target, expect in [("Sharp Memorial Hospital", "sharp-memorial"),
                           ("Grossmont Hospital", "grossmont"),
                           ("Sharp Chula Vista Medical Center", "chula-vista")]:
        got = b.pick_matching_file(target, "x.com", verbose=False) or ""
        check(f"{target[:32]} picks its own file", expect in got, f"got {got[-50:]}")

print("\n9. Known-URL resolution")
for name, expect in [("Scripps Mercy Hospital", "Mercy-Hospital-San-Diego"),
                     ("Scripps Memorial Hospital - Encinitas", "Encinitas"),
                     ("Scripps Green Hospital", "Green")]:
    mrf, _ = b.resolve_source(name)
    check(f"{name[:36]}", mrf is not None and expect in mrf, f"got {mrf}")

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s)")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("All checks passed.")
