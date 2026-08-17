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
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

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
                        site[proc][hid]["identical_to"] = [x for x in hs if x != hid]
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
    # Only changes are recorded, so a month where nothing moves adds nothing.
    # ------------------------------------------------------------------
    import csv as _csv

    hist_path = DATA / f"{args.region}-history.csv"
    today = date.today().isoformat()

    last_seen: dict[tuple, tuple] = {}       # (hid, proc) -> (date, cash)
    if hist_path.exists():
        try:
            with hist_path.open() as fh:
                for row in _csv.DictReader(fh):
                    try:
                        cash = float(row["cash"]) if row.get("cash") else None
                    except ValueError:
                        cash = None
                    if cash:
                        last_seen[(row["hospital_id"], row["procedure"])] = (
                            row["date"], cash)
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
        print(f"\nprice history: appended {len(new_rows)} row(s) "
              f"({len(changes)} were changes, {len(new_rows)-len(changes)} first "
              f"observations)")
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

    manifest[args.region] = {
        "file": out.name,
        "updated": date.today().isoformat(),
        "hospitals": len(hospital_ids),
        "procedures": len(site),
        "states": sorted(states),
        "zip3": sorted(zip3s),
    }
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
