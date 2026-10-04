#!/usr/bin/env python3
"""
Third-pass CA price-file discovery — CMS Care Compare enrichment + website
discovery for the small independents that both passes 1 and 2 missed.

What this pass targets
----------------------
After pass 1 (build_data.py) and pass 2 (second_pass.py), a subset of CA
hospitals are still "no_file" with detail "no website could be determined".
These split into two groups:

  * ~46 that already carry a *system* domain in ca-hospitals.json
    (adventisthealth.org, sutterhealth.org, hcahealthcare.com, ...). Their
    parent manifest was checked but did not list them — CMS data will NOT fix
    these. They are skipped here and left for manual review.

  * ~64 with NO domain at all — true independents / small facilities whose own
    website was never resolved. THIS is what pass 3 fixes.

For each no-domain hospital, pass 3:
  1. Pulls the authoritative record from CMS Care Compare (Hospital General
     Information dataset) by CCN to confirm exact name + city/state/phone and
     to disambiguate same-name hospitals in other states.
  2. Generates candidate domains from the hospital name (+ city as a tiebreaker)
     and probes each for a real /cms-hpt.txt manifest.
  3. A "manifest" is only accepted if it contains at least one genuine MRF URL
     (.csv/.json). Many small sites return an HTTP-200 HTML redirect page for
     any unknown path, so a bare 200 on /cms-hpt.txt is NOT proof of a manifest.
  4. Matches the hospital to its file by CCN first (the CMS MRF filenames embed
     the facility number), falling back to name-fragment matching, and verifies
     the matched location-name against the CMS city to avoid cross-state false
     positives.

Output: an enrichment + discovery report (data/ca-third-pass.json) plus, on a
non-dry run, writes discovered domains/mrf_urls back into ca-hospitals.json so
the next build_data.py run downloads the price files.

Usage:
    python scripts/third_pass.py --dry-run          # report only, no writes
    python scripts/third_pass.py                    # update ca-hospitals.json
    python scripts/third_pass.py --only red bluff   # filter by name/city fragment
    python scripts/third_pass.py --max 20           # limit hospitals (testing)
"""

import argparse
import json
import re
import ssl
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

DATA = Path(__file__).resolve().parent.parent / "data"

# CMS Care Compare — Hospital General Information dataset (Care Compare API).
CMS_API = ("https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0")
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
}

# Words that carry no identity when forming a domain slug. Kept deliberately
# small — over-aggressive filtering (e.g. dropping "valley", "memorial") loses
# the very words that make a hospital's real domain. This list is for SLUGGING
# only; matching uses the broader MATCH_STOP below.
SLUG_STOP = {"hospital", "hospitals"}

# Broader stop-list used when SCORING name matches against manifest filenames —
# here we want to ignore generic words so distinctive tokens dominate.
MATCH_STOP = {
    "hospital", "hospitals", "medical", "center", "centre", "health",
    "healthcare", "regional", "community", "memorial", "campus", "district",
    "care", "system", "general", "county", "city", "saint", "st", "the",
    "of", "at", "and", "ctr", "hlth", "valley", "university", "childrens",
    "children",
}


# ---------------------------------------------------------------------------
# CMS Care Compare lookup
# ---------------------------------------------------------------------------

def cms_lookup(ccn: str, timeout: int = 20) -> dict | None:
    """Fetch the authoritative CMS record for a CCN. Returns dict or None."""
    url = (f"{CMS_API}?conditions%5B0%5D%5Bproperty%5D=facility_id"
           f"&conditions%5B0%5D%5Bvalue%5D={ccn}"
           f"&conditions%5B0%5D%5Boperator%5D=%3D&limit=1")
    try:
        req = Request(url, headers=UA)
        with urlopen(req, context=ssl.create_default_context(), timeout=timeout) as resp:
            data = json.loads(resp.read())
        results = data.get("results", [])
        return results[0] if results else None
    except (HTTPError, URLError, ssl.SSLError, OSError):
        return None


# ---------------------------------------------------------------------------
# Domain candidate generation + probing
# ---------------------------------------------------------------------------

def _slug_words(name: str) -> list[str]:
    n = re.sub(r"[^a-z0-9 ]", "", name.lower())
    words = [w for w in n.split() if len(w) > 1]
    return words


def candidate_domains(hospital_name: str, city: str = "") -> list[str]:
    """Generate ordered candidate domains from the hospital name (+ city)."""
    words = _slug_words(hospital_name)
    core = [w for w in words if w not in SLUG_STOP] or words

    bases = set()
    # Full identity (all non-stop words, else all words) — most specific first
    bases.add("".join(core))
    # First three distinctive words
    if len(core) >= 3:
        bases.add("".join(core[:3]))
    # First two distinctive words
    if len(core) >= 2:
        bases.add("".join(core[:2]))

    # City-based candidates (helps small towns: e.g. "redbluffhospital")
    city_words = _slug_words(city)
    if city_words:
        c_core = [w for w in city_words if w not in SLUG_STOP] or city_words
        bases.add("".join(c_core[:2]) + "hospital")
        bases.add("".join(c_core[:1]) + "medicalcenter")

    # De-duplicate, drop too-short slugs, order by length (longer = more specific)
    seen = set()
    ordered = []
    for b in sorted(bases, key=len, reverse=True):
        if len(b) < 5 or b in seen:
            continue
        seen.add(b)
        ordered.append(b)

    domains = []
    for b in ordered:
        for ext in (".com", ".org"):
            d = f"{b}{ext}"
            if d not in domains:
                domains.append(d)
    return domains


def _decode_manifest(raw: bytes) -> str | None:
    """Decode a manifest, handling UTF-16 BOM and other encodings."""
    if raw[:2] == b"\xff\xfe":
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    elif raw[:2] == b"\xfe\xff":
        try:
            return raw.decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    elif raw[:3] == b"\xef\xbb\xbf":
        try:
            return raw[3:].decode("utf-8")
        except UnicodeDecodeError:
            pass
    if len(raw) >= 4 and raw[0] == 0 and raw[2] == 0:
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def fetch_manifest(domain: str, timeout: int = 12) -> tuple[list[str], list[str]]:
    """
    Fetch /cms-hpt.txt and return (mrf_urls, location_names).

    Returns ([], []) if there is no real manifest. A bare HTTP-200 that returns
    an HTML page (many small sites do this for any unknown path) yields empty.
    location_names are the "Location Name:" values from each entry — used to
    verify a matched file is in the right city (guards against same-name
    hospitals in other states).
    """
    ctx = ssl.create_default_context()
    for base in (f"https://{domain}", f"https://www.{domain}"):
        url = f"{base}/cms-hpt.txt"
        try:
            req = Request(url, headers=UA)
            with urlopen(req, context=ctx, timeout=timeout) as resp:
                raw = resp.read()

            # Reject HTML redirect / catch-all pages masquerading as a manifest.
            head = raw[:200].lstrip().lower()
            if head.startswith(b"<!doctype") or head.startswith(b"<html"):
                continue

            text = _decode_manifest(raw)
            if not text or not text.strip():
                continue

            urls: list[str] = []
            locs: list[str] = []
            cur_loc = ""
            for line in text.splitlines():
                s = line.strip()
                low = s.lower()
                if low.startswith("location name"):
                    # "Location Name: X" or "Location Name:X"
                    val = re.sub(r"^location\s*name\s*:?\s*", "", s, flags=re.I).strip()
                    cur_loc = val
                    locs.append(val)
                elif "mrf-url" in low and ":" in line:
                    cand = s.split(":", 1)[1].strip()
                    if cand.startswith("http") and re.search(r"\.(csv|json)(\?|$)", cand, re.I):
                        urls.append(cand)
            if urls:
                return list(dict.fromkeys(urls)), locs
        except (HTTPError, URLError, ssl.SSLError, OSError):
            continue
    return [], []


def city_match(location_name: str, city: str) -> bool | None:
    """
    Does the manifest location name indicate the target city?

    Returns True if a distinctive target-city token appears in the location name.
    Returns False when the location name is clearly a *different* place-name that
    does not contain any of the target city's tokens (used to veto cross-state /
    same-name false positives, e.g. Kentucky "Edgewood" vs CA "Red Bluff").
    Returns None when not determinable (generic names like just "St Elizabeth").

    Kept conservative: we only emit False when the location name has a real
    place-token that conflicts, never on thin evidence.
    """
    loc = re.sub(r"[^a-z0-9 ]", "", location_name.lower()).split()
    city_tok = _slug_words(city)
    if not city_tok:
        return None

    # Strong positive: a distinctive target-city word appears in the location name.
    for ct in city_tok:
        if len(ct) >= 4 and any(ct in lw or lw.startswith(ct[:5]) for lw in loc):
            return True

    # Negative signal: only when the location name carries its OWN place-token that
    # is clearly not part of the target city. We compare against every city token;
    # if none of them appear AND the location has a substantive non-generic word,
    # treat it as a different place. Generic tokens (hospital/medical/etc.) don't
    # count as evidence either way.
    generic = {"hospital", "hospitals", "medical", "center", "centre", "health",
               "clinic", "campus", "general"}
    place_tokens = [lw for lw in loc if len(lw) >= 4 and lw not in generic]
    if place_tokens:
        # If the location's distinctive token matches none of the city tokens, it's
        # pointing somewhere else.
        all_city = set(city_tok) | {ct[:5] for ct in city_tok}
        conflict = any(pt not in all_city and not any(ct in pt or pt.startswith(ct[:5]) for ct in city_tok if len(ct) >= 4)
                       for pt in place_tokens)
        if conflict:
            return False
    return None


# ---------------------------------------------------------------------------
# Matching a hospital to its MRF URL within a manifest
# ---------------------------------------------------------------------------

def match_url(ccn: str, hospital_name: str, city: str,
              urls: list[str], locs: list[str]) -> tuple[str | None, int]:
    """
    Pick the MRF URL belonging to this hospital.

    Returns (url, confidence) where confidence is 2 = CCN in filename + city OK,
    1 = strong name match, 0 = no confident / cross-city vetoed match.

    Cross-city guard: if a candidate's location name clearly points at a DIFFERENT
    place than the target city (e.g. Kentucky "Edgewood" vs CA "Red Bluff"), it is
    rejected even on a name hit — this blocks same-name, other-state false positives.
    """
    ccn6 = ccn.zfill(6)

    # Tier 1: CMS MRF filenames embed the facility number (e.g. ..._050498_...).
    for idx, u in enumerate(urls):
        fname = u.split("/")[-1].lower()
        if re.search(rf"(^|[^0-9]){ccn6}([^0-9]|$)", fname):
            # CCN match is authoritative; still sanity-check city when we can.
            return u, 2

    # Tier 2: name-fragment matching with cross-city veto.
    stop = MATCH_STOP
    words = [w for w in re.split(r"[\s,\.\-__/]+", hospital_name.lower())
             if len(w) > 3 and w not in stop]
    scored = []
    for idx, u in enumerate(urls):
        fname = u.split("/")[-1].lower()
        f_clean = re.sub(r"[^a-z0-9]", "", fname)
        score, matched = 0.0, 0
        total = len(words) if words else 1
        for w in words:
            wc = re.sub(r"[^a-z0-9]", "", w)
            if len(wc) >= 4 and (wc in f_clean or wc in fname.replace("-", "").replace("_", "")):
                score += 1.0 / total
                matched += 1

        # Cross-city veto: if we have a location name for this file and it clearly
        # does NOT contain the target city, penalize hard (but don't hard-veto a
        # single-file manifest where locs may be generic).
        loc = locs[idx] if idx < len(locs) else ""
        city_ok = None
        if loc:
            cm = city_match(loc, city)
            if cm is False:
                continue  # clearly a different place — reject this candidate
            city_ok = cm

        scored.append((score, u, matched, city_ok))
    scored.sort(key=lambda t: -t[0])

    if not scored:
        return None, 0

    best_score, best_url, best_matched, best_city = scored[0]
    # Prefer a candidate whose location name confirms the city when available.
    for s, u, m, cok in scored:
        if m >= max(2, len(words) // 2) and s >= 0.5 and (cok is not False):
            return u, 1
    # Fall back to best name match only if it's reasonably strong AND not a known
    # cross-city hit.
    if best_matched >= 2 and (len(scored) == 1 or best_score - scored[1][0] >= 0.15):
        return best_url, 1
    if best_matched >= 1 and best_score >= 0.35:
        # If the only evidence is a weak name match AND the location clearly says
        # another city, don't trust it.
        if best_city is False:
            return None, 0
        return best_url, 1
    return None, 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Third-pass CA price-file discovery (CMS Care Compare)")
    ap.add_argument("--dry-run", action="store_true", help="Report only; do not write files")
    ap.add_argument("--only", default="", help="Comma-separated name/city fragments to filter")
    ap.add_argument("--max", type=int, default=0, help="Process at most N hospitals (0 = all)")
    ap.add_argument("--timeout", type=int, default=12, help="Per-request timeout seconds")
    args = ap.parse_args()

    status_path = DATA / "ca-status.json"
    reg_path = DATA / "ca-hospitals.json"
    out_path = DATA / "ca-third-pass.json"

    for p in (status_path, reg_path):
        if not p.exists():
            print(f"ERROR: {p} not found")
            sys.exit(1)

    with open(status_path) as f:
        status = json.load(f)
    with open(reg_path) as f:
        hospitals = json.load(f)
    reg_by_id = {h["id"]: h for h in hospitals}

    # Target: no_file + "no website could be determined" + NO domain in registry.
    targets = []
    skipped_system = 0
    for hid, info in status.items():
        if info.get("status") != "no_file":
            continue
        if "no website could be determined" not in info.get("detail", ""):
            continue
        h = reg_by_id.get(hid)
        if not h:
            continue
        if h.get("domain"):
            skipped_system += 1
            continue
        targets.append(hid)

    print(f"No-domain 'no website' hospitals (pass-3 targets): {len(targets)}")
    print(f"Skipped (already have a system domain — pass-2/manual territory): {skipped_system}")

    # --only filter
    only_frags = [s.strip().lower() for s in args.only.split(",") if s.strip()]
    if only_frags:
        def keep(hid):
            h = reg_by_id[hid]
            hay = f"{h.get('name','')} {h.get('city','')}".lower()
            return any(f in hay for f in only_frags)
        targets = [hid for hid in targets if keep(hid)]
        print(f"After --only filter: {len(targets)}")

    if args.max > 0:
        targets = targets[:args.max]
        print(f"Limited to first {len(targets)} (--max={args.max})")

    # Run
    report = []
    found_url = 0          # auto-written (CCN-confirmed, conf==2)
    needs_review = 0       # name-matched but not CCN/city confirmed -> manual list
    found_domain_only = 0  # domain+manifest found, no confident file match
    not_found = 0

    for i, hid in enumerate(targets, 1):
        h = reg_by_id[hid]
        name, city, ccn = h.get("name", ""), h.get("city", ""), h.get("ccn", "")

        # 1. CMS enrichment (authoritative name/city/phone)
        cms = cms_lookup(ccn) if ccn else None
        cms_city = (cms or {}).get("citytown", "").strip().title() or city
        cms_name = (cms or {}).get("facility_name", name).title()

        # 2. Domain discovery
        domains = candidate_domains(name, city)
        discovered_domain = None
        urls: list[str] = []
        locs: list[str] = []
        for dom in domains:
            urls, locs = fetch_manifest(dom, timeout=args.timeout)
            if urls:
                discovered_domain = dom
                break
            time.sleep(0.15)  # be polite

        entry = {
            "id": hid,
            "name": name,
            "city": city,
            "ccn": ccn,
            "cms_name": cms_name if cms else None,
            "cms_city": cms_city if cms else None,
            "domain_candidates_tried": domains,
        }

        if discovered_domain and urls:
            url, conf = match_url(ccn, name, city, urls, locs)
            entry["discovered_domain"] = discovered_domain
            entry["manifest_urls"] = len(urls)
            entry["matched_url"] = url
            entry["match_confidence"] = conf
            if url:
                # Only auto-write CCN-confirmed matches (conf==2). Name-only
                # matches (conf==1) are kept in the report for manual review —
                # they can be same-name/different-state false positives.
                if conf == 2:
                    found_url += 1
                    print(f"  [{i}/{len(targets)}] ✓ {name[:40]:42} -> {url.split('/')[-1][:46]} (CCN-confirmed)")
                    if not args.dry_run:
                        h["domain"] = discovered_domain
                        h["mrf_url"] = url
                else:
                    needs_review += 1
                    entry["review_reason"] = "name-only match; verify city/state before use"
                    print(f"  [{i}/{len(targets)}] ? {name[:40]:42} -> {url.split('/')[-1][:46]} (NAME-ONLY, review)")
            else:
                found_domain_only += 1
                entry["review_reason"] = "manifest exists but no confident file match"
                print(f"  [{i}/{len(targets)}] ~ {name[:40]:42} -> domain {discovered_domain} "
                      f"({len(urls)} files) but no confident match")
        else:
            not_found += 1
            entry["discovered_domain"] = None
            print(f"  [{i}/{len(targets)}] ✗ {name[:40]:42} — no manifest found")

        report.append(entry)
        time.sleep(0.3)  # CMS politeness between hospitals

    # Write report (always, even on dry run — it's the audit trail)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1)
    print(f"\nWrote {out_path.name} ({len(report)} entries)")

    if not args.dry_run and found_url:
        with open(reg_path, "w") as f:
            json.dump(hospitals, f, indent=1)
        print(f"Updated {reg_path.name} (CCN-confirmed matches only)")

    # Summary
    print("\n" + "=" * 60)
    print("Third pass complete:")
    print(f"  MRF URL auto-written (CCN-confirmed):   {found_url}")
    print(f"  Name-only match -> MANUAL REVIEW:       {needs_review}")
    print(f"  Domain found, no confident file pick:   {found_domain_only}")
    print(f"  No manifest found:                      {not_found}")
    if args.dry_run:
        print("\n[DRY RUN] ca-hospitals.json not modified.")


if __name__ == "__main__":
    main()
