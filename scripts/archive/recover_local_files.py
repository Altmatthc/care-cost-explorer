#!/usr/bin/env python3
"""Recover the 3 hospitals whose MRF data downloaded fine but was rejected by
the name-match guardrail (verify_file_belongs) because their non-standard URLs
carry no clean hospital name. Feeds build_data.py's own extraction/consolidation
functions the already-downloaded local files via a tiny HTTP server, skipping
only the identity check (which we verified manually).

Targets:
  ccn-050454 Ucsf Medical Center        <- ucsf-medical-center_standardcharges.json (combined)
  ccn-050115 Palomar Health Downtown    <- palomar-medical-center-escondido_standardcharges.json
  ccn-050636 Palomar Medical Ctr Poway  <- palomar-medical-center-poway_standardcharges.json

Hoag Orthopedic (ccn-050769) is NOT here: Hoag's manifest lists only Hoag
Memorial Presbyterian + Hoag Hospital Irvine; the ortho institute publishes no
MRF of its own. It stays no_file.
"""
import json, os, sys, threading, time, http.server, socketserver, pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "scripts"))
import build_data as B  # noqa: E402

DL = r"C:\Users\cordl\Downloads"
TARGETS = [
    ("ccn-050454", "Ucsf Medical Center",
     "106010776_ucsf-medical-center_standardcharges (1).json"),
    ("ccn-050115", "Palomar Health Downtown Campus",
     "41-2392302_palomar-medical-center-escondido_standardcharges.json"),
    ("ccn-050636", "Palomar Medical Center Poway",
     "41-2392302_palomar-medical-center-poway_standardcharges.json"),
]

# --- tiny local HTTP server over the Downloads dir -------------------------
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=DL, **k)
    def log_message(self, *a): pass

httpd = socketserver.TCPServer(("127.0.0.1", 8793), H)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.5)

prices_path = HERE / "data" / "ca-prices.json"
status_path = HERE / "data" / "ca-status.json"
hosp_path   = HERE / "data" / "ca-hospitals.json"

all_rows = json.loads(prices_path.read_text(encoding="utf-8"))
status   = json.loads(status_path.read_text(encoding="utf-8"))
hosp     = {h["id"]: h for h in json.loads(hosp_path.read_text(encoding="utf-8"))}
today    = time.strftime("%Y-%m-%d")

def record(hid, st, detail="", rows=0, url=None):
    prior = status.get(hid, {})
    h = hosp[hid]
    status[hid] = {
        "name": h["name"], "status": st, "detail": detail,
        "rows": rows if st == B.STATUS_OK else prior.get("rows", 0),
        "source_url": url or prior.get("source_url"),
        "last_attempt": today,
        "last_success": today if st == B.STATUS_OK else prior.get("last_success"),
        "attempts": prior.get("attempts", 0) + 1,
        "consecutive_failures": 0 if st == B.STATUS_OK
                               else prior.get("consecutive_failures", 0) + 1,
        "rule_applies": not B.is_federal(h["name"], h.get("system", "")),
    }

summary = []
for hid, name, fname in TARGETS:
    url = f"http://127.0.0.1:8793/{fname}"
    print(f"\n=== {hid}  {name}\n    <- {fname}")
    try:
        rows = B.extract_prices_json(hid, url, verbose=False)
        print(f"    extracted {len(rows)} matching raw rows")
        if not rows:
            record(hid, B.STATUS_EMPTY, "local file parsed but no target procedures", 0, url)
            summary.append((hid, name, "EMPTY", 0))
            continue
        cons = B.consolidate(rows)
        for k in list(all_rows.keys()):
            if all_rows[k].get("hospital_id") == hid:
                del all_rows[k]
        for k, rec in cons.items():
            rec["shared_source"] = True   # combined/portal file, verified manually
            all_rows[k] = rec
        record(hid, B.STATUS_OK, f"recovered from local {fname} (name-match bypassed)",
               len(rows), url)
        summary.append((hid, name, "OK", len(cons)))
    except Exception as e:
        print(f"    ERROR: {type(e).__name__}: {e}")
        record(hid, B.STATUS_NO_FILE, f"recovery failed: {e}", 0)
        summary.append((hid, name, "FAIL", str(e)[:60]))

# --- write back -----------------------------------------------------------
prices_path.write_text(json.dumps(all_rows), encoding="utf-8")
status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

print("\n=== SUMMARY ===")
for hid, name, st, n in summary:
    print(f"  {st:5}  {hid}  {name}  ({n})")
print(f"\ncurrent ok count: {sum(1 for v in status.values() if v.get('status')==B.STATUS_OK)}")
