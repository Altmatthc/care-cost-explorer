#!/usr/bin/env python3
"""
Merge sharded price output into the single compact file the website loads.

Also does the payer-name normalization that makes the insurance dropdown work:
hospitals write payer names however they like ("BLUE CROSS OF CALIFORNIA",
"Anthem BC PPO", "AnthemBlueCross_HMO"), so we map them onto a small set of
consistent plan families.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# --- contract-level figures masquerading as procedure prices ---------------
# Some hospitals publish ONE number across nearly every line of their file:
# a per-diem, a case rate, or a self-pay floor. It is not a price for the
# procedure on that row, but it parses as one, so the site would show e.g. a
# $4,748 "negotiated rate" for a strep test. Verified sources:
#   Barnesville Hospital (oh) - $4,748 in the BCBS Traditional column on
#       7,101 of 7,559 rows: a strep test, a drug screen, a tracheostomy
#       revision and a tibia graft all carry the identical figure.
#   Nocona General (tx)       - discounted_cash 27.85 on every row.
# The file is wrong, not the parser, so the fix is to withhold the value and
# say why rather than to re-parse. Two conditions, because either alone is
# wrong: dominance alone flags genuinely common low prices (a critical-access
# hospital repeating $55 on 91% of its procedures is real, and only 3.3x its
# other rates), while absurdity alone flags a single expensive outlier. A
# contract-level figure is BOTH repeated AND wildly out of line with the rest
# of that hospital's prices. Calibrated against all 17 published regions:
# these thresholds flag 2 hospitals, a bare 30% share would flag 18.
FLAT_MIN_PROCS = 8
FLAT_DOMINANCE = 0.3
FLAT_ABSURDITY = 5.0
# A junk figure is indifferent to what the procedure IS: it lands on cheap labs
# and major surgery alike. A real bundled rate is coherent -- one price for a
# category of similar work. 13 New Jersey hospitals publish a single same-day
# surgery price across exactly these 19 procedures while still pricing labs,
# imaging and inpatient work separately; that is deliberate pricing and the
# most useful number an uninsured patient can get, so it must survive.
# Treat a repeated value as REAL when the procedures carrying it are all drawn
# from one coherent band, and as junk when they span the full price range.
_SURGICAL = {
    "appendectomy", "biopsy-needle", "carpal-tunnel", "cast-fracture",
    "cataract", "colonoscopy", "endoscopy", "epidural-inj", "gallbladder",
    "hernia-repair", "hysterectomy", "iud-insertion", "joint-injection",
    "knee-scope", "laceration", "lithotripsy", "prostate-biopsy",
    "shoulder-scope", "tonsillectomy", "hip-replacement", "knee-replacement",
}
_ER = {"er-level1", "er-level2", "er-level3", "er-level4", "er-level5"}
# Minor work a hospital plausibly folds into an ER/urgent-care flat fee. The
# real ccn-310008 shape is $600 across ER levels PLUS these, so the ER band
# has to admit them or a legitimate bundle reads as junk.
_MINOR = {"cast-fracture", "laceration", "joint-injection", "urgent-care",
          "ekg", "strep-test", "iv-hydration"}
_COHERENT_BANDS = (_SURGICAL, _ER | _MINOR)


def _is_coherent_bundle(carriers: set[str]) -> bool:
    """Do the procedures sharing one price form a plausible bundle?

    A bundled rate covers one kind of work. If every procedure at the mode
    sits inside a single band (same-day surgery, ER levels), the hospital is
    pricing a category, not mis-publishing a contract figure.
    """
    if len(carriers) < 3:
        return False
    return any(carriers <= band for band in _COHERENT_BANDS)


def _flat_figure(pairs: list[tuple[str, float]]) -> tuple[float, int] | None:
    """The dominant, out-of-line value in `pairs`, or None.

    `pairs` is (procedure, value). Returns (value, count) when one value
    repeats on >= FLAT_DOMINANCE of the records, is >= FLAT_ABSURDITY times
    out of line with the others, and is NOT a coherent bundled rate.
    """
    if len(pairs) < FLAT_MIN_PROCS:
        return None
    values = [v for _, v in pairs]
    val, n = Counter(values).most_common(1)[0]
    if n / len(values) < FLAT_DOMINANCE:
        return None
    if _is_coherent_bundle({p for p, v in pairs if v == val}):
        return None
    others = [v for v in values if v != val]
    if not others:
        # Every single record carries it: nothing to compare against, but a
        # file with one price for every procedure is not pricing per procedure.
        return (val, n)
    omed = statistics.median(others)
    if not omed:
        return (val, n)
    # Symmetric: a flat figure can sit far ABOVE the other prices (a per-diem
    # stamped on every line) or far BELOW them (a self-pay floor). Nocona's
    # $27.85 is 1/32 of its other cash prices; Barnesville's $4,748 is 17x
    # its other rates. Both are contract-level, so compare the ratio in
    # whichever direction it falls.
    ratio = val / omed
    if ratio < 1:
        ratio = 1 / ratio
    if ratio < FLAT_ABSURDITY:
        return None
    return (val, n)


def flat_value_fields(byproc: dict[str, dict], hid: str) -> dict[str, str]:
    """Which fields for this hospital look contract-level, not per-procedure?

    Returns {field: reason} for 'cash' and/or 'payers'. Judged per field:
    Barnesville's cash prices are fine and only its negotiated rates are
    broken, so a single verdict for the whole hospital would be wrong.
    """
    recs = [(proc, e) for proc, byh in byproc.items()
            if isinstance(byh, dict) and isinstance(e := byh.get(hid), dict)]
    if len(recs) < FLAT_MIN_PROCS:
        return {}

    flagged: dict[str, str] = {}

    cash_pairs = [(p, e["cash"]) for p, e in recs
                  if isinstance(e.get("cash"), (int, float))]
    found = _flat_figure(cash_pairs)
    if found:
        val, n = found
        flagged["cash"] = (f"the file lists ${val:,.0f} as the cash price for "
                           f"{n} of {len(cash_pairs)} procedures, so it is a "
                           f"self-pay minimum rather than a per-procedure price")

    # Lowest rate per record is what the site surfaces, so judge that.
    rate_pairs = [(p, min(e["payers"].values())) for p, e in recs
                  if isinstance(e.get("payers"), dict) and e["payers"]]
    found = _flat_figure(rate_pairs)
    if found:
        val, n = found
        flagged["payers"] = (f"the file repeats ${val:,.0f} as a negotiated rate "
                             f"on {n} of {len(rate_pairs)} procedures, so it is a "
                             f"contract-level figure, not a procedure price")
    return flagged


# Order matters — first match wins, so put specific patterns before general.
# Hospitals abbreviate heavily in their files. Tri-City alone publishes "BC",
# "HN" and "UH" — nearly 200 records each — which were being dropped entirely
# because they didn't match a full plan name.
PAYER_PATTERNS = [
    ("kaiser",     r"kaiser"),
    # Abbreviations, anchored so they can't match inside a longer word.
    ("anthem",     r"^\s*bc\b|\bbcbs\b"),
    ("healthnet",  r"^\s*hn\b"),
    ("uhc",        r"^\s*uh\b|^\s*uhc\b"),
    ("aetna",      r"^\s*aet\b"),
    ("cigna",      r"^\s*cig\b"),
    ("blueshield", r"^\s*bs\b|\bbsc\b"),
    ("medicare",   r"\bmedicare\b|\bmcr\b"),
    ("medical",    r"medi-?cal|medicaid|\bmcd\b"),
    ("anthem",     r"anthem|blue\s*cross"),
    ("blueshield", r"blue\s*shield"),
    ("aetna",      r"aetna"),
    ("cigna",      r"cigna"),
    ("uhc",        r"united\s*health|\buhc\b|unitedhealthcare"),
    ("healthnet",  r"health\s*net"),
    ("tricare",    r"tricare"),
    ("alignment",  r"alignment"),
    ("centivo",    r"centivo"),
    ("scan",       r"\bscan\b"),
    ("molina",     r"molina"),
    ("oscar",      r"oscar"),
    ("brandnew",   r"brand new day"),
]

# Medical groups and IPAs are not insurance plans a patient can choose. They
# appear in these files because capitated arrangements are negotiated through
# them, but nobody shops for "Sharp Community Medical Group" the way they
# shop for Aetna. They are deliberately not mapped, and are counted separately
# rather than reported as gaps.
PROVIDER_GROUP_HINTS = (
    "med grp", "medical group", "med group", "ipa", "physicians",
    "phys ", "medical foundation", "health system", "healthcare system",
    "prime healthcare", "palomar health", "ucsd", "scripps", "sharp rees",
    "prospect", "perlman", "integrated health partners", "veba",
)


def is_provider_group(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in PROVIDER_GROUP_HINTS)


def normalize_payer(name: str) -> str | None:
    n = (name or "").lower()
    for key, pattern in PAYER_PATTERNS:
        if re.search(pattern, n):
            return key
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="san-diego")
    args = ap.parse_args()

    # Collect every shard
    merged: dict[str, dict] = {}
    shards = sorted(DATA.glob(f"{args.region}-prices*.json"))
    for path in shards:
        try:
            merged.update(json.loads(path.read_text()))
        except Exception as e:
            print(f"  skipping unreadable shard {path.name}: {e}")
    print(f"merged {len(shards)} shard file(s) -> {len(merged)} price records")

    # Reshape into what the site wants: procedure -> hospital -> prices
    site: dict[str, dict] = {}
    unmapped: dict[str, int] = {}
    for rec in merged.values():
        proc = rec["procedure"]
        if not proc:
            # Carrier record from a zero-curated-match harvest: it exists
            # only to carry _meta into hospital_meta/drugs below. It must
            # not create an empty procedure bucket on the site.
            continue
        hid = rec["hospital_id"]
        payers: dict[str, float] = {}
        for raw_name, rate in (rec.get("payers") or {}).items():
            key = normalize_payer(raw_name)
            if key is None:
                if not is_provider_group(raw_name):
                    unmapped[raw_name] = unmapped.get(raw_name, 0) + 1
                continue
            # A plan family can appear many times (HMO, PPO, tiers). Keep the
            # median-ish value by taking the lowest, which is what a patient
            # on that plan family would most plausibly encounter.
            if key not in payers or rate < payers[key]:
                payers[key] = rate
        # Compact the record: whole dollars, and omit anything empty.
        # Every byte here is stored in git history forever, and prices to the
        # cent are false precision on a published list price anyway.
        def r(v):
            return round(v) if isinstance(v, (int, float)) and v else None

        entry = {}
        for k, v in (("cash", r(rec.get("cash"))), ("gross", r(rec.get("gross"))),
                     ("min", r(rec.get("min"))), ("max", r(rec.get("max")))):
            if v:
                entry[k] = v
        if payers:
            entry["payers"] = {k: r(v) for k, v in sorted(payers.items()) if r(v)}
        if rec.get("shared_source"):
            entry["shared"] = True
        site.setdefault(proc, {})[hid] = entry

    # --- withhold contract-level figures -----------------------------------
    # Done after the full reshape because the test is per hospital across all
    # of its procedures: one record in isolation cannot reveal that the same
    # number repeats on 94% of the file's rows.
    all_hids = {hid for byh in site.values() for hid in byh}
    suppressed: dict[str, dict[str, str]] = {}
    for hid in sorted(all_hids):
        flags = flat_value_fields(site, hid)
        if not flags:
            continue
        suppressed[hid] = flags
        for byh in site.values():
            entry = byh.get(hid)
            if not isinstance(entry, dict):
                continue
            for field in flags:
                entry.pop(field, None)
            # 'min' mirrors the self-pay floor in these files, so it carries
            # the same bad figure; drop it alongside a suppressed cash price.
            if "cash" in flags:
                entry.pop("min", None)
        # An entry stripped of every price is noise on the site: it would
        # render as a hospital row with nothing in it. Drop those outright,
        # the status block already explains the hospital.
        for proc_key, byh in list(site.items()):
            e = byh.get(hid)
            if isinstance(e, dict) and not any(
                    k in e for k in ("cash", "gross", "payers", "min", "max")):
                del byh[hid]
    if suppressed:
        print(f"\nwithheld contract-level figures at {len(suppressed)} hospital(s):")
        for hid, flags in suppressed.items():
            for field, why in flags.items():
                print(f"  {hid} {field}: {why}")

    # Fold in scan status so the site can explain missing hospitals
    status = {}
    spath = DATA / f"{args.region}-status.json"
    if spath.exists():
        try:
            status = json.loads(spath.read_text())
        except Exception as e:
            print(f"  could not read status file: {e}")

    # ------------------------------------------------------------------
    # Detect hospitals publishing IDENTICAL prices.
    # Kaiser selects a per-facility file for San Diego (Zion) and San Marcos,
    # yet both produce the same figures — the chargemaster is system-wide.
    # Presenting that as facility-specific pricing implies a precision the
    # data doesn't have, so flag it and let the site say so.
    # ------------------------------------------------------------------
    import hashlib

    sig: dict[str, list[str]] = {}
    for hid in {h for byh in site.values() for h in byh}:
        parts = []
        for proc in sorted(site):
            rec = site[proc].get(hid)
            if not rec:
                continue
            parts.append(f"{proc}:{rec.get('cash')}:{rec.get('gross')}:"
                         f"{sorted((rec.get('payers') or {}).items())}")
        if parts:
            digest = hashlib.sha1("|".join(parts).encode()).hexdigest()
            sig.setdefault(digest, []).append(hid)

    identical = {d: hs for d, hs in sig.items() if len(hs) > 1}
    if identical:
        print("\nHospitals publishing identical prices (flagged as system-wide):")
        for hs in identical.values():
            print("  " + ", ".join(hs))
            for hid in hs:
                for proc in site:
                    if hid in site[proc]:
                        site[proc][hid]["shared"] = True
                        site[proc][hid]["identical_to"] = sorted(x for x in hs if x != hid)
    else:
        print("\nNo two hospitals publish identical prices.")

    reg = DATA / f"{args.region}-hospitals.json"

    # ------------------------------------------------------------------
    # DRUG PRICES + HOSPITAL METADATA
    # Drugs are captured generically by J-code with their descriptions, so we
    # never guess which code means which drug. Keep only codes that appear at
    # several hospitals — a price you can't compare isn't much use.
    # ------------------------------------------------------------------
    drug_rows: dict[str, dict] = {}
    hosp_meta: dict[str, dict] = {}
    for rec in merged.values():
        meta = rec.get("_meta")
        if not meta:
            continue
        hid = rec["hospital_id"]
        if hid not in hosp_meta:
            hosp_meta[hid] = {
                "codes_with_cash": meta.get("codes_with_cash", 0),
                "quality": meta.get("quality", {}),
            }
            for ukey, d in (meta.get("drugs") or {}).items():
                # Keyed by code AND unit, so we only ever compare like with
                # like. A drug priced per-mg at one hospital and per-vial at
                # another is not a price difference.
                entry = drug_rows.setdefault(ukey, {
                    "code": d.get("code", ukey.split("|")[0]),
                    "unit": d.get("unit", ""),
                    "unit_type": d.get("unit_type", ""),
                    "description": d["description"],
                    "by_hospital": {},
                })
                entry["by_hospital"][hid] = d["cash"]
                if len(d["description"]) > len(entry["description"]):
                    entry["description"] = d["description"]

    MIN_HOSPITALS = 3
    MIN_PRICE = 1.0          # sub-dollar entries are per-unit fragments
    MAX_CREDIBLE_SPREAD = 50  # beyond this it's a measurement artefact

    drugs, suppressed = {}, 0
    for ukey, d in drug_rows.items():
        prices = {h: v for h, v in d["by_hospital"].items()
                  if v and v >= MIN_PRICE}
        if len(prices) < MIN_HOSPITALS:
            continue
        lo, hi = min(prices.values()), max(prices.values())
        spread = hi / lo if lo else 0
        if spread > MAX_CREDIBLE_SPREAD:
            # Almost certainly hospitals measuring the same drug differently
            # despite publishing the same unit string. Publishing a
            # "800,000x difference" would be nonsense and would discredit the
            # figures that ARE sound.
            suppressed += 1
            continue
        d = {**d, "by_hospital": prices, "spread": round(spread, 1)}
        drugs[ukey] = d
    print(f"\ndrugs: {len(drug_rows)} code+unit combinations seen, "
          f"{len(drugs)} comparable across {MIN_HOSPITALS}+ hospitals")
    if suppressed:
        print(f"  {suppressed} suppressed for implausible spread (>"
              f"{MAX_CREDIBLE_SPREAD}x) — hospitals measuring the same drug "
              f"differently, not a real price gap")
    if drugs:
        worst = sorted(drugs.items(), key=lambda kv: -(kv[1].get("spread") or 0))[:5]
        print("  widest price variation between hospitals:")
        for code, d in worst:
            print(f"    {d.get('spread', 1)}x  {code}  {d['description'][:52]}")

    # Coverage against the 300 shoppable services CMS requires.
    for hid, m in hosp_meta.items():
        m["meets_300"] = m["codes_with_cash"] >= 300
    short = [h for h, m in hosp_meta.items() if not m["meets_300"]]
    print(f"coverage: {len(hosp_meta) - len(short)}/{len(hosp_meta)} hospitals "
          f"publish 300+ codes with a cash price")

    # ------------------------------------------------------------------
    # SAME-SYSTEM CAMPUS VARIATION
    # A system charging different amounts at its own campuses for identical
    # care is a real finding, and only visible if you hold every campus at once.
    # ------------------------------------------------------------------
    systems: dict[str, list] = {}
    priced_ids = {x for byh in site.values() for x in byh}
    try:
        for h in json.loads(reg.read_text()):
            if h["id"] not in priced_ids:
                continue
            # Hospitals sharing a price-file domain belong to the same system.
            # CMS publishes no system field, and its "ownership" value
            # ("Voluntary Non-Profit - Private") would group every non-profit
            # in the county together — which is why this previously found
            # nothing at all.
            key = h.get("domain") or (h.get("source_url", "") or "").split("/")[2:3]
            key = key if isinstance(key, str) else (key[0] if key else None)
            if key:
                systems.setdefault(key, []).append(h["id"])
    except Exception as e:
        print(f"  could not group hospitals by system: {e}")
    multi = {k: v for k, v in systems.items() if len(v) > 1}
    if multi:
        print(f"\nsystems with multiple campuses: " +
              ", ".join(f"{k} ({len(v)})" for k, v in multi.items()))

    sys_variation = 0
    for proc, byh in site.items():
        for sysname, ids in systems.items():
            prices = {i: byh[i]["cash"] for i in ids
                      if i in byh and byh[i].get("cash")}
            if len(prices) < 2:
                continue
            lo, hi = min(prices.values()), max(prices.values())
            if lo and hi / lo >= 1.15:
                cheapest = min(prices, key=prices.get)
                for i in prices:
                    if i != cheapest:
                        byh[i]["sys_cheaper"] = {
                            "at": cheapest,
                            "pct": round((prices[i] - lo) / lo * 100),
                        }
                sys_variation += 1
    print(f"same-system variation: {sys_variation} procedure/system combinations "
          f"where campuses of the SAME system differ by 15%+")

    # ------------------------------------------------------------------
    # DERIVED INSIGHTS
    # Computed from data we already hold, and not published anywhere:
    #
    #   cash_beats_insurance  the cash price is LOWER than a payer's negotiated
    #                         rate. Insured patients can be worse off using
    #                         their plan, and nobody tells them.
    #   payer_spread          the ratio between the best and worst negotiated
    #                         rate for the same procedure at the same hospital.
    #   markup                gross "chargemaster" charge over the cash price.
    # ------------------------------------------------------------------
    insight_counts = {"cash_beats": 0, "wide_spread": 0}
    for proc, byh in site.items():
        for hid, rec in byh.items():
            cash = rec.get("cash")
            payers = rec.get("payers") or {}
            if payers:
                lo, hi = min(payers.values()), max(payers.values())
                if lo and hi / lo >= 1.5:
                    rec["spread"] = round(hi / lo, 1)
                    insight_counts["wide_spread"] += 1
                if cash:
                    # Which plans are WORSE than simply paying cash here.
                    worse = sorted(k for k, v in payers.items() if v > cash * 1.02)
                    if worse:
                        rec["cash_wins"] = worse
                        insight_counts["cash_beats"] += 1
            if cash and rec.get("gross") and cash:
                mk = rec["gross"] / cash
                if mk >= 1.5:
                    rec["markup"] = round(mk, 1)

    print(f"\nderived: {insight_counts['cash_beats']} hospital/procedure pairs where "
          f"the cash price beats at least one insurer's rate")
    print(f"derived: {insight_counts['wide_spread']} where insurers differ by 50%+ "
          f"for identical care")

    # ------------------------------------------------------------------
    # FULL CODE CATALOGUE — sharded for search
    #
    # Capturing every code is ~860 MB nationally. No browser downloads that to
    # look up one MRI, and no single file that size can even be committed.
    # So the catalogue is split two ways:
    #
    #   <region>-search.json          code -> short description + hospital count.
    #                                 Small enough to load when someone starts
    #                                 typing; it's what powers the search box.
    #   <region>-codes/<prefix>.json  prices, bucketed by the first 3
    #                                 characters of the code. Searching 70551
    #                                 fetches one ~60 KB file, not the catalogue.
    # ------------------------------------------------------------------
    catalogue: dict[str, dict] = {}
    cat_dir = DATA / f"{args.region}-catalogue"
    cat_files = sorted(cat_dir.glob("*.json")) if cat_dir.exists() else []
    for f in cat_files:
        hid = f.stem
        try:
            per_hospital = json.loads(f.read_text())
        except Exception as e:
            print(f"  skipping unreadable catalogue {f.name}: {e}")
            continue
        for key, entry in per_hospital.items():
            c = catalogue.setdefault(key, {"d": entry.get("d", ""), "h": {}})
            c["h"][hid] = [entry.get("cash"), entry.get("gross")]
            if len(entry.get("d", "")) > len(c["d"]):
                c["d"] = entry["d"]
    if cat_files:
        print(f"\nread catalogues for {len(cat_files)} hospital(s)")

    if catalogue:
        codes_dir = DATA / f"{args.region}-codes"
        codes_dir.mkdir(exist_ok=True)
        for old in codes_dir.glob("*.json"):
            old.unlink()

        buckets: dict[str, dict] = {}
        search_index: dict[str, list] = {}
        for key, c in catalogue.items():
            ctype, code = key.split(":", 1)
            prefix = re.sub(r"[^A-Z0-9]", "", code.upper())[:3] or "OTHER"
            buckets.setdefault(prefix, {})[key] = c
            # description, hospitals with a price, cheapest cash price
            prices = [v[0] for v in c["h"].values() if v[0]]
            search_index[key] = [c["d"], len(c["h"]),
                                 round(min(prices)) if prices else None]

        for prefix, rows in buckets.items():
            (codes_dir / f"{prefix}.json").write_text(
                json.dumps(rows, separators=(",", ":"), sort_keys=True))

        idx = DATA / f"{args.region}-search.json"
        idx.write_text(json.dumps(search_index, separators=(",", ":"),
                                  sort_keys=True))
        biggest = max((f.stat().st_size for f in codes_dir.glob("*.json")),
                      default=0)
        print(f"\ncatalogue: {len(catalogue):,} distinct codes across "
              f"{len(buckets)} bucket files "
              f"(largest {biggest/1024:.0f} KB, search index "
              f"{idx.stat().st_size/1024:.0f} KB)")

    # ------------------------------------------------------------------
    # OUTLIER FLAGGING
    # Some published prices are implausible — a $2 blood test, a $37,000
    # cataract. We can't know from here whether that's the hospital's own
    # oddity or a parsing artefact, and guessing either way is wrong:
    # hiding a real price misleads by omission, showing a wrong one sends
    # someone to the wrong hospital. So flag it, show it, and say plainly
    # that it needs confirming.
    # ------------------------------------------------------------------
    import statistics as _stats

    flagged = 0
    for proc, byh in site.items():
        vals = [r["cash"] for r in byh.values() if r.get("cash")]
        if len(vals) < 4:
            continue
        med = _stats.median(vals)
        if not med:
            continue
        for rec in byh.values():
            c = rec.get("cash")
            if not c:
                continue
            if c > med * 8:
                rec["outlier"] = "high"
                rec["peer_median"] = round(med)
                flagged += 1
            elif c < med / 8:
                rec["outlier"] = "low"
                rec["peer_median"] = round(med)
                flagged += 1
    if flagged:
        print(f"\n{flagged} price(s) flagged as far from their peers — shown "
              f"on the site with a caution rather than hidden or trusted")

    # ------------------------------------------------------------------
    # PRICE HISTORY (append-only)
    # Snapshots get overwritten each refresh, so a change is invisible unless
    # we record it. We keep two things:
    #   <region>-history.csv  an append-only archive, one line per observed
    #                         price CHANGE. Appending lines makes tiny git
    #                         deltas, unlike rewriting a JSON blob.
    #   prev / since fields   folded into the price records so the site can
    #                         show "up 8% since March" without a second fetch.
    # A (hospital, procedure) pair gets a history row when:
    #   1. it is observed for the first time,
    #   2. its cash price changed (pct_change filled in), or
    #   3. the price is unchanged but the last row is RECONFIRM_DAYS+ old
    #      (pct_change empty). Without this, a stable price never gets a
    #      second dated observation, so nothing can say "steady since X";
    #      the gap keeps monthly refreshes from adding a row every run.
    # ------------------------------------------------------------------
    import csv as _csv

    hist_path = DATA / f"{args.region}-history.csv"
    today = date.today().isoformat()
    RECONFIRM_DAYS = 45

    last_seen: dict[tuple, tuple] = {}       # (hid, proc) -> (date, cash)
    # (hid, proc) -> date the current price was first seen, so an unchanged
    # pair can report the start of its run rather than the latest reconfirm.
    run_start: dict[tuple, str] = {}
    # (hid, proc) -> (date, prev_cash) of the MOST RECENT actual price change,
    # so an unchanged merge can keep surfacing a real move instead of dropping
    # prev the cycle after the change was first detected. A real move recorded
    # in history must stay visible until a newer move supersedes it.
    last_change: dict[tuple, tuple] = {}
    if hist_path.exists():
        try:
            with hist_path.open() as fh:
                for row in _csv.DictReader(fh):
                    try:
                        cash = float(row["cash"]) if row.get("cash") else None
                    except ValueError:
                        cash = None
                    if cash:
                        key = (row["hospital_id"], row["procedure"])
                        before = last_seen.get(key)
                        if before is None or round(before[1]) != round(cash):
                            # The price we're moving AWAY from had been in effect
                            # since the PRIOR run's start (captured before we
                            # overwrite run_start). Skip first observations.
                            if before is not None:
                                prior_since = run_start.get(key, before[0])
                                last_change[key] = (prior_since, before[1])
                            run_start[key] = row["date"]
                        last_seen[key] = (row["date"], cash)
        except Exception as e:
            print(f"  could not read price history: {e}")

    new_rows = []
    for proc, byh in site.items():
        for hid, rec in byh.items():
            cash = rec.get("cash")
            if not cash:
                continue
            prior = last_seen.get((hid, proc))
            if prior is None:
                new_rows.append({"date": today, "hospital_id": hid,
                                 "procedure": proc, "cash": cash,
                                 "gross": rec.get("gross") or "",
                                 "pct_change": ""})
            elif round(prior[1]) != round(cash):
                pct = (cash - prior[1]) / prior[1] * 100 if prior[1] else 0
                new_rows.append({"date": today, "hospital_id": hid,
                                 "procedure": proc, "cash": cash,
                                 "gross": rec.get("gross") or "",
                                 "pct_change": f"{pct:.1f}"})
                # Expose the move to the site.
                rec["prev"] = round(prior[1])
                rec["since"] = prior[0]
            else:
                # Unchanged since last refresh. If this pair's CURRENT price is
                # the result of a real move recorded earlier in history, keep
                # surfacing that move (prev + since) until a newer move
                # supersedes it — otherwise the badge would vanish the cycle
                # after a change was first detected (prev was written only on
                # the change run). Only re-expose when the recorded move's new
                # price still matches today's cash; a stale last_change whose
                # price no longer holds must not resurrect a wrong prev.
                lc = last_change.get((hid, proc))
                if lc and round(lc[1]) != round(cash):
                    rec["prev"] = round(lc[1])
                    rec["since"] = lc[0]
                else:
                    # Truly flat run: "since" without "prev" is ignored by the
                    # site's priceChange() (it needs prev to render a move), so
                    # this adds the run's start date without claiming a change.
                    rec["since"] = run_start.get((hid, proc), prior[0])
                try:
                    stale = (date.fromisoformat(today)
                             - date.fromisoformat(prior[0])).days >= RECONFIRM_DAYS
                except ValueError:
                    stale = False
                if stale:
                    new_rows.append({"date": today, "hospital_id": hid,
                                     "procedure": proc, "cash": cash,
                                     "gross": rec.get("gross") or "",
                                     "pct_change": ""})

    if new_rows:
        fresh = not hist_path.exists()
        with hist_path.open("a", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=["date", "hospital_id", "procedure",
                                                "cash", "gross", "pct_change"])
            if fresh:
                w.writeheader()
            for r in sorted(new_rows, key=lambda x: (x["hospital_id"], x["procedure"])):
                w.writerow(r)
        changes = [r for r in new_rows if r["pct_change"]]
        firsts = [r for r in new_rows
                  if (r["hospital_id"], r["procedure"]) not in last_seen]
        print(f"\nprice history: appended {len(new_rows)} row(s) "
              f"({len(changes)} were changes, {len(firsts)} first "
              f"observations, {len(new_rows)-len(changes)-len(firsts)} "
              f"unchanged reconfirmations)")
        if changes:
            biggest = sorted(changes, key=lambda r: -abs(float(r["pct_change"])))[:5]
            print("  largest moves since last refresh:")
            for r in biggest:
                print(f"    {r['pct_change']:>7}%  {r['procedure']:<20} {r['hospital_id']}")
    else:
        print("\nprice history: no price changes since last refresh")

    # Sort keys so successive refreshes produce minimal diffs. Git stores
    # deltas between versions; stable ordering means an unchanged hospital
    # contributes almost nothing to repo growth.
    site = {p: dict(sorted(site[p].items())) for p in sorted(site)}

    out = DATA / f"{args.region}.json"
    # Carry each hospital's own price page through for the estimator link
    pages = {}
    try:
        for h in json.loads(reg.read_text()):
            if h.get("price_page"):
                pages[h["id"]] = h["price_page"]
    except Exception:
        pass

    for byh in site.values():
        for rec in byh.values():
            rec.pop("_meta", None)
    for rec in merged.values():
        rec.pop("_meta", None)

    payload = {"region": args.region,
               "prices": site,
               "status": status,
               "price_pages": pages,
               "drugs": drugs,
               "hospital_meta": hosp_meta}

    # Only rewrite the file if the DATA changed. Without this, the date alone
    # would create a commit every run even when no price moved.
    import hashlib
    new_hash = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    old_hash = None
    if out.exists():
        try:
            old = json.loads(out.read_text())
            old_stamp = old.pop("updated", None)
            old.pop("content_hash", None)
            old_hash = hashlib.sha1(
                json.dumps(old, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        except Exception:
            pass

    wrote_file = old_hash != new_hash
    if not wrote_file:
        print(f"{out.name}: prices unchanged since last refresh, leaving file "
              f"untouched (no commit, no history growth)")
    else:
        payload["updated"] = date.today().isoformat()
        payload["content_hash"] = new_hash
        out.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    if wrote_file:
        size_kb = out.stat().st_size / 1024
        print(f"wrote {out.name} ({size_kb:.0f} KB, {len(site)} procedures)")

    # ------------------------------------------------------------------
    # Region manifest. The site reads this first to decide WHICH data file
    # to load for a given ZIP, so a user never downloads the whole country
    # to look up one MRI.
    # ------------------------------------------------------------------
    manifest_path = DATA / "regions.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            manifest = {}

    hospital_ids = {h for byh in site.values() for h in byh}
    states, zip3s = set(), set()
    try:
        for h in json.loads(reg.read_text()):
            if h["id"] in hospital_ids:
                if h.get("state"):
                    states.add(h["state"].upper())
                if h.get("zip"):
                    zip3s.add(str(h["zip"])[:3])
    except Exception:
        pass

    prev_entry = manifest.get(args.region) or {}
    manifest[args.region] = {
        "file": out.name,
        "updated": date.today().isoformat(),
        "hospitals": len(hospital_ids),
        "procedures": len(site),
        "states": sorted(states),
        "zip3": sorted(zip3s),
    }
    # catalogue_base is set by publish_release.py, not by the merge — carry it
    # over or a merge silently unlinks the site from its Release catalogue.
    if prev_entry.get("catalogue_base"):
        manifest[args.region]["catalogue_base"] = prev_entry["catalogue_base"]
    manifest_path.write_text(json.dumps(manifest, indent=1))
    print(f"wrote regions.json ({len(manifest)} region(s): "
          f"{', '.join(sorted(manifest))})")

    if unmapped:
        top = sorted(unmapped.items(), key=lambda x: -x[1])[:15]
        print("\nUnmapped payer names — add to PAYER_PATTERNS if a patient "
              "could plausibly choose one:")
        for name, count in top:
            print(f"  {count:>6}x  {name}")
        print("  (medical groups and IPAs are excluded on purpose — they are "
              "not plans a patient selects)")

    # Clean up shard files so the repo stays tidy
    for path in shards:
        if re.search(r"-prices-\d+\.json$", path.name):
            path.unlink()


if __name__ == "__main__":
    main()
