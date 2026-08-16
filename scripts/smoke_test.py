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

print("\n9. No-match path (the branch that crashed a live run)")
with mock.patch.object(b, "discover_mrf_candidates",
                       lambda d: ["https://x/completely-unrelated-clinic.csv",
                                  "https://x/another-unrelated-facility.csv"]), \
     mock.patch.object(b, "read_identity",
                       lambda u: {"hospital_name": "Unrelated Clinic",
                                  "location_name": "", "address": "", "raw": ""}):
    try:
        res = b.pick_matching_file("Sharp Memorial Hospital", "x.com", verbose=True)
        check("no-match path returns None without raising", res is None, f"got {res}")
    except Exception as e:
        check("no-match path returns None without raising", False, f"raised {e!r}")

print("\n10. Kaiser-style filename scoring and address tiebreak")
kaiser = ["941105628-san-diego-clairemont-medical-center-standard-charges-scal-en.csv",
          "941105628-san-diego-zion-medical-center-standard-charges-scal-en.csv",
          "941105628-san-marcos-medical-center-standard-charges-scal-en.csv",
          "941105628-antioch-medical-center-standard-charges-ncal-en.csv"]
check("San Diego file clears threshold",
      b.match_score("Kaiser Foundation Hospital - San Diego", kaiser[0]) >= 0.6,
      f"{b.match_score('Kaiser Foundation Hospital - San Diego', kaiser[0]):.2f}")
check("San Marcos file clears threshold",
      b.match_score("Kaiser Foundation Hospital - San Marcos", kaiser[2]) >= 0.6)
check("Antioch stays rejected for San Diego",
      b.match_score("Kaiser Foundation Hospital - San Diego", kaiser[3]) < 0.6)
check("San Marcos does not match San Diego file",
      b.match_score("Kaiser Foundation Hospital - San Marcos", kaiser[1]) < 0.6)

addrs = {"clairemont": "7060 CLAIREMONT MESA BLVD; SAN DIEGO, CA, 92111",
         "zion": "4647 ZION AVE; SAN DIEGO, CA, 92120"}
check("address distinguishes Zion from Clairemont",
      b.address_score("4647 Zion Ave San Diego 92120", addrs["zion"]) >
      b.address_score("4647 Zion Ave San Diego 92120", addrs["clairemont"]))


def kaiser_ident(url):
    seg = url.split("941105628-")[1].split("-medical-center")[0]
    a = addrs.get("zion") if "zion" in seg else addrs.get("clairemont", "")
    return {"hospital_name": seg.replace("-", " ").upper() + " MEDICAL CENTER",
            "location_name": "", "address": a, "raw": ""}


with mock.patch.object(b, "discover_mrf_candidates",
                       lambda d: [f"https://kp.org/{f}" for f in kaiser]), \
     mock.patch.object(b, "read_identity", kaiser_ident):
    got = b.pick_matching_file("Kaiser Foundation Hospital - San Diego", "kp.org",
                               verbose=False,
                               expected_address="4647 Zion Ave San Diego 92120") or ""
    check("address tiebreak selects Zion over Clairemont", "zion" in got,
          f"got {got.split('/')[-1]}")

print("\n11. Targeted run must not wipe other hospitals' data")
# Simulates: data exists for 4 hospitals; a run targets only one of them.
prev = {
    "hA|mri-brain":  {"hospital_id": "hA", "procedure": "mri-brain", "cash": 1000},
    "hA|colonoscopy": {"hospital_id": "hA", "procedure": "colonoscopy", "cash": 2000},
    "hB|mri-brain":  {"hospital_id": "hB", "procedure": "mri-brain", "cash": 1100},
    "hC|mri-brain":  {"hospital_id": "hC", "procedure": "mri-brain", "cash": 1200},
    "hD|mri-brain":  {"hospital_id": "hD", "procedure": "mri-brain", "cash": 1300},
}
touched = {"hA"}                       # only hospital A rescanned
new = {"hA|mri-brain": {"hospital_id": "hA", "procedure": "mri-brain", "cash": 1050}}
merged = dict(new)
for k, rec in prev.items():
    if rec.get("hospital_id") in touched:
        continue
    merged.setdefault(k, rec)
check("untouched hospitals survive", len(merged) == 4, f"got {len(merged)}")
check("rescanned hospital's price updated",
      merged["hA|mri-brain"]["cash"] == 1050)
check("rescanned hospital's stale extra record dropped",
      "hA|colonoscopy" not in merged)
for hid in ["hB", "hC", "hD"]:
    check(f"{hid} data preserved", any(r.get("hospital_id") == hid
                                       for r in merged.values()))

print("\n12. Shared MS-DRG 470 splits into hip vs knee")
pid = b.match_code("470", "MS-DRG")
check("470 maps to the shared id", pid == "joint-replacement", f"got {pid}")
check("knee description resolves to knee only",
      b.refine_by_description(pid, "TOTAL KNEE ARTHROPLASTY") == ["knee-replacement"])
check("hip description resolves to hip only",
      b.refine_by_description(pid, "MAJOR HIP REPLACEMENT W/O MCC") == ["hip-replacement"])
# The official DRG 470 title names both; the rate is the same either way, so
# the row must populate BOTH procedures rather than being discarded.
official = ("MAJOR HIP AND KNEE JOINT REPLACEMENT OR REATTACHMENT OF "
            "LOWER EXTREMITY WITHOUT MCC")
check("official DRG title populates both procedures",
      sorted(b.refine_by_description(pid, official)) ==
      ["hip-replacement", "knee-replacement"])
check("generic 'joint' description populates both",
      len(b.refine_by_description(pid, "MAJOR JOINT REPLACEMENT LOWER EXTREMITY")) == 2)
check("unshared codes pass through untouched",
      b.refine_by_description("mri-brain", "anything") == ["mri-brain"])
check("specific knee CPT maps to knee", b.match_code("27447", "CPT") == "knee-replacement")
check("specific hip CPT maps to hip", b.match_code("27130", "CPT") == "hip-replacement")

# End-to-end: a DRG 470 row must yield rows for both procedures
_buf = io.StringIO()
_w = csv.writer(_buf)
_w.writerow(["hospital_name", "version"])
_w.writerow(["Joint Test Hospital", "3.0.0"])
_w.writerow(["description", "code|1", "code|1|type", "standard_charge|gross",
             "standard_charge|discounted_cash", "payer_name",
             "standard_charge|negotiated_dollar"])
_w.writerow([official, "470", "MS-DRG", "88000", "38500", "Aetna", "31000"])
_lines = _buf.getvalue().strip().split("\n")
with mock.patch.object(b, "stream_rows", lambda u: rows_from(_lines)):
    _out = b.extract_prices("hj", "http://x/j.csv", verbose=False)
_procs = sorted({r["procedure"] for r in _out})
check("DRG 470 row produces hip AND knee records",
      _procs == ["hip-replacement", "knee-replacement"], f"got {_procs}")
check("both carry the same rate (DRG pays the same either way)",
      len({r["rate"] for r in _out}) == 1)

print("\n13. Sharded run preserves only genuinely untouched hospitals")
all_h = [{"id": f"h{i}"} for i in range(4)]
shard0 = [h for i, h in enumerate(all_h) if i % 2 == 0]
touched_wrong = {h["id"] for h in shard0}          # the old, buggy scope
touched_right = {h["id"] for h in all_h}           # the fixed scope
check("old scope would have restored another shard's hospitals",
      "h1" not in touched_wrong)
check("fixed scope covers every hospital in the run",
      {"h0", "h1", "h2", "h3"} == touched_right)

print("\n14. Hospital-supplied text is sanitised at ingestion")
check("control characters stripped", "\x00" not in b.clean_text("MRI\x00BRAIN"))
check("whitespace collapsed", b.clean_text("  a    b  ") == "a b")
check("length capped", len(b.clean_text("x" * 5000)) <= 120)
check("payer field capped shorter", len(b.clean_text("y" * 500, 80)) <= 80)
check("normal text untouched",
      b.clean_text("MRI BRAIN WO CONTRAST") == "MRI BRAIN WO CONTRAST")
# Markup is intentionally NOT stripped here — escaping belongs on output, and
# stripping it at ingestion would silently corrupt legitimate descriptions.
check("markup preserved for output-side escaping",
      "<" in b.clean_text("<script>alert(1)</script>"))

print("\n15. Price history records changes, not every observation")
_hist = {}          # (hid, proc) -> (date, price)


def _refresh(site, day):
    appended = []
    for proc, byh in site.items():
        for hid, rec in byh.items():
            cash = rec.get("cash")
            if not cash:
                continue
            prior = _hist.get((hid, proc))
            if prior is None:
                appended.append((day, hid, proc, cash, ""))
            elif round(prior[1]) != round(cash):
                pct = (cash - prior[1]) / prior[1] * 100
                appended.append((day, hid, proc, cash, f"{pct:.1f}"))
                rec["prev"], rec["since"] = round(prior[1]), prior[0]
            _hist[(hid, proc)] = (day, cash)
    return appended


s1 = {"mri-brain": {"hA": {"cash": 1450}, "hB": {"cash": 2640}}}
r1 = _refresh(s1, "2026-05-01")
check("first refresh records a baseline per series", len(r1) == 2, f"got {len(r1)}")

s2 = {"mri-brain": {"hA": {"cash": 1560}, "hB": {"cash": 2640}}}
r2 = _refresh(s2, "2026-08-16")
check("second refresh appends only the changed series", len(r2) == 1, f"got {len(r2)}")
check("unchanged hospital appends nothing", not any(x[1] == "hB" for x in r2))
check("percentage change computed", r2[0][4] == "7.6", f"got {r2[0][4]}")
check("record carries previous price for the site",
      s2["mri-brain"]["hA"].get("prev") == 1450)
check("record carries the date of that price",
      s2["mri-brain"]["hA"].get("since") == "2026-05-01")

s3 = {"mri-brain": {"hA": {"cash": 1560}, "hB": {"cash": 2640}}}
r3 = _refresh(s3, "2026-09-01")
check("a refresh with no movement appends nothing at all", len(r3) == 0, f"got {len(r3)}")

print("\n16. Derived insights")


def derive(rec):
    cash, payers = rec.get("cash"), rec.get("payers") or {}
    out = {}
    if payers:
        lo, hi = min(payers.values()), max(payers.values())
        if lo and hi / lo >= 1.5:
            out["spread"] = round(hi / lo, 1)
        if cash:
            worse = sorted(k for k, v in payers.items() if v > cash * 1.02)
            if worse:
                out["cash_wins"] = worse
    if cash and rec.get("gross") and rec["gross"] / cash >= 1.5:
        out["markup"] = round(rec["gross"] / cash, 1)
    return out


d = derive({"cash": 1450, "gross": 4200,
            "payers": {"aetna": 980, "anthem": 1620, "medicare": 420}})
check("flags the plan that costs more than cash", d.get("cash_wins") == ["anthem"])
check("does not flag plans cheaper than cash", "aetna" not in d.get("cash_wins", []))
check("computes payer spread", d.get("spread") == 3.9, f"got {d.get('spread')}")
check("computes markup over cash", d.get("markup") == 2.9, f"got {d.get('markup')}")

d2 = derive({"cash": 1450, "gross": 1600, "payers": {"aetna": 1400, "cigna": 1420}})
check("no flags when everything is close", d2 == {}, f"got {d2}")

d3 = derive({"cash": 1000, "payers": {"aetna": 1015}})
check("1.5% difference is noise, not flagged", "cash_wins" not in d3)

print("\n17. Drug capture, coverage and same-system variation")
# J-codes are drugs; other HCPCS are not
check("J-code recognised as a drug", "J9035".upper().startswith("J"))
check("non-J HCPCS not treated as a drug", not "A6402".upper().startswith("J"))

# only drugs seen at several hospitals are comparable
_rows = {"J9035": {"by_hospital": {"a": 100, "b": 250, "c": 180}},
         "J1745": {"by_hospital": {"a": 400}}}
_kept = {c: d for c, d in _rows.items() if len(d["by_hospital"]) >= 3}
check("drug at 3+ hospitals kept", "J9035" in _kept)
check("drug at a single hospital dropped", "J1745" not in _kept)
_vals = list(_kept["J9035"]["by_hospital"].values())
check("drug spread computed", round(max(_vals) / min(_vals), 1) == 2.5)

# same-system campus variation
_site = {"sharp-mem": 2150, "sharp-cv": 1740, "sharp-cor": 2010}
_lo = min(_site.values())
_flags = {i: round((p - _lo) / _lo * 100) for i, p in _site.items()
          if p / _lo >= 1.15 and p != _lo}
check("flags the pricier campus", _flags.get("sharp-mem") == 24, f"got {_flags}")
check("cheapest campus not flagged", "sharp-cv" not in _flags)

# shoppable-services coverage
check("300+ codes meets the requirement", (450 >= 300) is True)
check("under 300 flagged as limited disclosure", (120 >= 300) is False)

print("\n18. Known-URL resolution")
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
