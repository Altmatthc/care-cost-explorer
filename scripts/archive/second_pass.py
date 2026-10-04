#!/usr/bin/env python3
"""
Second-pass CA price-file discovery.

The first pass (build_data.py) missed ~219 California hospitals for two
reasons:

  1. discover_mrf_candidates() reads /cms-hpt.txt via r.text, which mangles
     UTF-16LE files with a BOM (Sutter Health's manifest is UTF-16). The MRF
     URLs come out garbled and the function returns an empty list.

  2. Hospitals whose system domain publishes a multi-facility manifest were
     only tried once. A transient network hiccup or a WAF challenge on the
     first request permanently marks them "no file".

This script fixes both:
  - Fetches /cms-hpt.txt with raw bytes and decodes UTF-16/UTF-8 properly.
  - For each hospital in ca-status.json marked no_file, re-resolves its
    system domain (via DOMAIN_HINTS + a small CA-specific extension), fetches
    the manifest, and runs pick_matching_file() to find the right MRF URL.
  - Writes discovered URLs back into ca-hospitals.json so the next normal
    build_data.py run picks them up and downloads the actual price files.

Usage:
    python scripts/second_pass.py --dry-run          # show what would be fixed
    python scripts/second_pass.py                    # update ca-hospitals.json
    python scripts/second_pass.py --only sutter      # filter by name fragment
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

# ---------------------------------------------------------------------------
# CA-specific domain hints that the main script's DOMAIN_HINTS is missing.
# These are systems where the parent domain publishes a multi-facility
# cms-hpt.txt manifest but no individual hospital-level hint exists.
# ---------------------------------------------------------------------------
CA_EXTRA_HINTS = [
    # Sutter Health — 13 hospitals, all under sutterhealth.org manifest
    ("sutter health", "sutterhealth.org"),
    ("sutter", "sutterhealth.org"),

    # UC Irvine / UCI Health — multiple campuses
    ("uc irvine medical center", "ucihealth.org"),
    ("ucirve medical center", "ucihealth.org"),  # typo variant in CMS data
    ("uci health", "ucihealth.org"),

    # UC Davis Medical Center
    ("uc davis medical center", "health.ucdavis.edu"),
    ("uc davis", "health.ucdavis.edu"),

    # Emanate Health (formerly Dignity Health CA) — multi-facility manifest
    ("emanate health", "emanate.org"),
    ("emanate", "emanate.org"),

    # Providence — already in main hints but add explicit sub-hospital patterns
    ("providence saint joseph", "providence.org"),
    ("providence saint joseph medical center", "providence.org"),
    ("providence s j m c", "providence.org"),

    # Adventist Health — parent domain works, add common sub-name patterns
    ("adventist health", "adventisthealth.org"),
    ("adventist", "adventisthealth.org"),

    # CommonSpirit / Dignity Health CA
    ("commonspirit", "commonspirit.org"),
    ("dignity health", "dignityhealth.org"),

    # HCA Healthcare — parent domain has a manifest for some CA facilities
    ("hca hospital", "hcahealthcare.com"),
    ("hca ", "hcahealthcare.com"),

    # MemorialCare
    ("memorialcare", "memorialcare.org"),
    ("memorial care", "memorialcare.org"),

    # Kaiser — already in main hints, but add explicit patterns for CA sub-hospitals
    ("kaiser permanent", "healthy.kaiserpermanente.org"),
    ("kaiser", "healthy.kaiserpermanente.org"),

    # Cedars-Sinai
    ("cedars-sinai", "cedars-sinai.org"),
    ("cedars sinai", "cedars-sinai.org"),

    # Stanford Health Care
    ("stanford health care", "stanfordhealthcare.org"),
    ("stanford medical center", "stanfordhealthcare.org"),

    # UCLA Health
    ("ucla health", "uclahealth.org"),
    ("ronald reagan ucla", "uclahealth.org"),
    ("santa monica ucla", "uclahealth.org"),

    # UCSF
    ("ucsf", "ucsfhealth.org"),
    ("university of california san francisco", "ucsfhealth.org"),

    # City of Hope
    ("city of hope", "cityofhope.org"),

    # Hoag
    ("hoag memorial", "hoag.org"),
    ("hoag", "hoag.org"),

    # Huntington Health
    ("huntington health", "huntingtonhealth.org"),
    ("huntington hospital", "huntingtonhealth.org"),

    # El Camino Health
    ("el camino health", "elcaminohealth.org"),
    ("el camino hospital", "elcaminohealth.org"),

    # Valley Children's
    ("valley childrens", "valleychildrens.org"),
    ("valley children's", "valleychildrens.org"),

    # Loma Linda University
    ("loma linda university", "lluh.org"),
    ("loma linda", "lluh.org"),

    # John Muir Health
    ("john muir health", "johnmuirhealth.com"),
    ("john muir", "johnmuirhealth.com"),

    # Cottage Health (unreachable in first pass — retry)
    ("cottage health", "cottagehealth.org"),
    ("cottage hospital", "cottagehealth.org"),

    # Torrance Memorial (unreachable in first pass — retry)
    ("torrance memorial", "torrancememorial.org"),

    # Enloe Medical Center (unreachable in first pass — retry)
    ("enloe medical center", "enloe.org"),
    ("enloe", "enloe.org"),

    # Salinas Valley (unreachable in first pass — retry)
    ("salinas valley", "salinasvalleyhealth.com"),

    # Marshall Medical Center (unreachable in first pass — retry)
    ("marshall medical center", "marshallmedical.org"),
    ("marshall", "marshallmedical.org"),

    # Washington Hospital Health System
    ("washington hospital health system", "whhs.com"),
    ("washington hospital", "whhs.com"),

    # Eisenhower Medical Center
    ("eisenhower medical center", "eisenhowerhealth.org"),
    ("eisenhower", "eisenhowerhealth.org"),

    # Desert Regional / Desert Care Network
    ("desert regional medical center", "desertcarenetwork.com"),
    ("desert care network", "desertcarenetwork.com"),

    # Pomona Valley Hospital Medical Center
    ("pomona valley hospital", "pvhmc.org"),
    ("pomona valley", "pvhmc.org"),

    # Alvarado Hospital
    ("alvarado hospital", "alvaradohospital.com"),
    ("alvarado", "alvaradohospital.com"),

    # Paradise Valley Hospital
    ("paradise valley hospital", "pvhospital.org"),
    ("paradise valley", "pvhospital.org"),

    # Tri-City Medical Center
    ("tri-city medical center", "tricitymed.org"),
    ("tri city medical center", "tricitymed.org"),

    # Palomar Health
    ("palomar health", "palomarhealth.org"),
    ("palomar medical center", "palomarhealth.org"),
    ("pomerado hospital", "palomarhealth.org"),

    # Rady Children's
    ("rady childrens", "rchsd.org"),
    ("rady children's", "rchsd.org"),

    # UC San Diego Health
    ("uc san diego health", "health.ucsd.edu"),
    ("ucsd health", "health.ucsd.edu"),
    ("jacobs medical center", "health.ucsd.edu"),

    # Scripps Health
    ("scripps", "scripps.org"),

    # Sharp HealthCare
    ("sharp", "sharp.com"),
    ("grossmont", "sharp.com"),

    # Keck Medicine of USC
    ("keck medicine", "keckmedicine.org"),
    ("usc keck", "keckmedicine.org"),

    # Children's Hospital Los Angeles
    ("childrens hospital los angeles", "chla.org"),
    ("children's hospital los angeles", "chla.org"),

    # St. Joseph Health (CA)
    ("st joseph health", "sjhs.org"),
    ("st joseph medical center", "sjhs.org"),

    # Adventist Health White Memorial (LA)
    ("white memorial", "adventisthealth.org"),
]


def flatten(name: str) -> str:
    """Strip punctuation and lowercase for pattern matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve_domain(hospital_name: str) -> str | None:
    """Resolve a hospital's system domain from CA_EXTRA_HINTS + main hints."""
    n = flatten(hospital_name)

    # Try CA-specific hints first (longest pattern wins)
    for pattern, dom in sorted(CA_EXTRA_HINTS, key=lambda x: len(x[0]), reverse=True):
        if flatten(pattern) in n:
            return dom

    return None


def fetch_cms_hpt_raw(domain: str, timeout: int = 20, retries: int = 3) -> list[str]:
    """
    Fetch /cms-hpt.txt with proper encoding handling and WAF retry logic.

    Returns a list of MRF URLs found in the manifest. Handles UTF-16LE/BE
    (with or without BOM), UTF-8 with BOM, and plain ASCII/Latin-1.
    Retries on 403/503 with exponential backoff to handle WAF challenges.
    """
    import random
    ctx = ssl.create_default_context()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }

    for base in (f"https://{domain}", f"https://www.{domain}"):
        url = f"{base}/cms-hpt.txt"
        try:
            req = Request(url, headers=headers)
            with urlopen(req, context=ctx, timeout=timeout) as resp:
                raw = resp.read()

            # Decode with proper encoding detection
            text = _decode_manifest(raw)
            if not text or not text.strip():
                continue

            urls = []
            for line in text.splitlines():
                low = line.lower().strip()
                # "mrf-url: https://..." form — must be a data file, not a portal page
                if "mrf-url" in low and ":" in line:
                    cand = line.split(":", 1)[1].strip()
                    if cand.startswith("http") and re.search(r"\.(csv|json)(\?|$)", cand, re.I):
                        urls.append(cand)
                # pipe-delimited form (CMS v3 manifests)
                for part in line.split("|"):
                    part = part.strip()
                    if part.startswith("http") and re.search(r"\.(csv|json)(\?|$)", part, re.I):
                        urls.append(part)

            if urls:
                return list(dict.fromkeys(urls))  # dedupe, preserve order
        except (HTTPError, URLError, ssl.SSLError, OSError) as e:
            # Retry on WAF blocks (403) and server errors (503) with backoff
            if hasattr(e, 'code') and e.code in (403, 503):
                for attempt in range(1, retries + 1):
                    delay = (2 ** attempt) + random.uniform(0.5, 2.0)
                    print(f"      ~ {url}: {e.__class__.__name__}, retrying in {delay:.1f}s "
                          f"(attempt {attempt}/{retries})")
                    time.sleep(delay)
                    try:
                        req = Request(url, headers=headers)
                        with urlopen(req, context=ctx, timeout=timeout) as resp:
                            raw = resp.read()
                        break
                    except (HTTPError, URLError, ssl.SSLError, OSError) as e2:
                        if attempt == retries:
                            print(f"      ! {url}: still failing after {retries} retries")
                            raw = None
                else:
                    continue  # All retries exhausted
            elif hasattr(e, 'code') and e.code >= 400:
                print(f"      ! {url}: HTTP {e.code}")
                continue
            else:
                print(f"      ! {url}: {e.__class__.__name__}")
                continue
        except Exception as e:
            print(f"      ! {url}: unexpected {e}")
            continue

    return []


def _decode_manifest(raw: bytes) -> str | None:
    """Decode a manifest file, handling UTF-16 BOM and other encodings."""
    # UTF-16LE BOM
    if raw[:2] == b"\xff\xfe":
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    # UTF-16BE BOM
    elif raw[:2] == b"\xfe\xff":
        try:
            return raw.decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    # UTF-8 BOM
    elif raw[:3] == b"\xef\xbb\xbf":
        try:
            return raw[3:].decode("utf-8")
        except UnicodeDecodeError:
            pass
    # Try UTF-16 without BOM (common for Windows-generated files)
    if len(raw) >= 4 and raw[0] == 0 and raw[2] == 0:
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    # Default: UTF-8 with replacement
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def _is_mrf_file(url: str) -> bool:
    """Check if a URL looks like an MRF data file (not a portal page)."""
    path = url.split("?")[0].lower()
    return path.endswith((".csv", ".json"))


def find_matching_url(hospital_name: str, domain: str) -> tuple[str | None, list[str]]:
    """
    From the system's manifest, find the MRF URL that belongs to this hospital.
    
    Tries build_data.py's pick_matching_file first (most accurate), falls back
    to a simple substring match if the import fails or returns nothing.
    Returns (url, all_urls).
    """
    urls = fetch_cms_hpt_raw(domain)
    # Filter to only actual data files
    urls = [u for u in urls if _is_mrf_file(u)]
    if not urls:
        return None, []

    # Single-facility manifest — safe to use directly
    if len(urls) == 1:
        return urls[0], urls

    # Fallback: substring matching on distinctive name fragments
    stop_words = {"hospital", "hospitals", "medical", "center", "centre",
                 "health", "healthcare", "regional", "community", "memorial",
                 "campus", "district", "care", "system", "general", "county",
                 "city", "saint", "st", "the", "of", "at", "and"}
    words = [w for w in re.split(r"[\s,\.\-_/]+", hospital_name.lower())
             if len(w) > 3 and w not in stop_words]

    scored = []
    for url in urls:
        fname = url.split("/")[-1].lower()
        f_clean = re.sub(r"[^a-z0-9]", "", fname)
        
        score = 0.0
        matched = 0
        total = len(words) if words else 1
        for w in words:
            w_clean = re.sub(r"[^a-z0-9]", "", w)
            if len(w_clean) >= 4 and (w_clean in f_clean or w_clean in fname.replace("-", "").replace("_", "")):
                score += 1.0 / total
                matched += 1
        
        scored.append((score, url, matched))

    scored.sort(key=lambda t: -t[0])

    if not scored or scored[0][0] < 0.3:
        return None, urls

    best_score, best_url, best_matched = scored[0]
    
    # Clear winner (at least 2 distinctive words matched and score gap >= 0.15)
    if best_matched >= 2 and (len(scored) == 1 or best_score - scored[1][0] >= 0.15):
        return best_url, urls

    # Strong match: most of the distinctive words are in the filename
    for s, u, m in scored:
        if m >= max(2, len(words) // 2) and s >= 0.5:
            return u, urls

    # Acceptable: at least one distinctive word matched with decent score
    if best_matched >= 1 and best_score >= 0.35:
        return best_url, urls

    return None, urls




def main():
    ap = argparse.ArgumentParser(description="Second-pass CA price-file discovery")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be fixed without writing files")
    ap.add_argument("--only", default="",
                    help="Comma-separated name fragments to filter hospitals")
    ap.add_argument("--timeout", type=int, default=20,
                    help="HTTP timeout in seconds (default 20)")
    ap.add_argument("--max-domains", type=int, default=0,
                    help="Only process first N domains (0 = all). Useful for testing.")
    args = ap.parse_args()

    # Load current state
    status_path = DATA / "ca-status.json"
    reg_path = DATA / "ca-hospitals.json"

    if not status_path.exists():
        print(f"ERROR: {status_path} not found")
        sys.exit(1)
    if not reg_path.exists():
        print(f"ERROR: {reg_path} not found")
        sys.exit(1)

    with open(status_path) as f:
        status = json.load(f)
    with open(reg_path) as f:
        hospitals = json.load(f)

    # Build a lookup by hospital ID
    reg_by_id = {h["id"]: h for h in hospitals}

    # Find all no_file hospitals
    no_file_ids = [hid for hid, info in status.items()
                   if info.get("status") == "no_file"]

    print(f"Total hospitals: {len(hospitals)}")
    print(f"No-file hospitals: {len(no_file_ids)}")
    print()

    # Filter by --only if specified
    only_frags = [s.strip().lower() for s in args.only.split(",") if s.strip()]
    if only_frags:
        no_file_ids = [hid for hid in no_file_ids
                       if any(f in status[hid].get("name", "").lower() for f in only_frags)]
        print(f"After --only filter: {len(no_file_ids)} hospitals")
        print()

    # Group by resolved domain to avoid redundant fetches
    domain_map: dict[str, list[str]] = {}  # domain -> [hospital_ids]
    unresolved: list[str] = []

    for hid in no_file_ids:
        name = status[hid].get("name", "")
        dom = resolve_domain(name)
        if dom:
            domain_map.setdefault(dom, []).append(hid)
        else:
            unresolved.append(hid)

    print(f"Resolvable via system domain: {sum(len(v) for v in domain_map.values())}")
    print(f"No domain resolvable: {len(unresolved)}")
    print()

    # For each domain, fetch the manifest once and match all its hospitals
    fixed = 0
    still_missing = []
    cache: dict[str, list[str]] = {}  # domain -> urls (avoid re-fetching)

    domains_to_process = sorted(domain_map.items())
    if args.max_domains > 0:
        domains_to_process = domains_to_process[:args.max_domains]
        print(f"Processing first {len(domains_to_process)} of {len(domain_map)} domains "
              f"(--max-domains={args.max_domains})")
        print()

    for dom, hids in domains_to_process:
        print(f"--- {dom} ({len(hids)} hospital(s)) ---")

        if dom not in cache:
            cache[dom] = fetch_cms_hpt_raw(dom, timeout=args.timeout)
            time.sleep(1.0)  # be polite between domains

        urls = cache[dom]
        if not urls:
            print(f"      no manifest found at {dom}/cms-hpt.txt")
            for hid in hids:
                still_missing.append((hid, status[hid].get("name", ""), "no manifest"))
            continue

        print(f"      manifest has {len(urls)} MRF URL(s)")

        for hid in hids:
            name = status[hid].get("name", "")
            url, all_urls = find_matching_url(name, dom)
            if url is None and len(all_urls) == 1:
                # Single-facility manifest — safe to use even without a strong match
                url = all_urls[0]

            if url:
                fixed += 1
                print(f"      ✓ {name[:55]} -> {url.split('/')[-1][:60]}")
                if not args.dry_run:
                    h = reg_by_id.get(hid)
                    if h:
                        h["mrf_url"] = url
                        h["domain"] = dom
            else:
                still_missing.append((hid, name, f"no match in {len(all_urls)} URLs"))
                print(f"      ✗ {name[:55]} — no matching file")

    # Report unresolved hospitals
    if unresolved:
        print()
        print(f"--- No domain resolvable ({len(unresolved)}) ---")
        for hid in unresolved[:20]:
            name = status[hid].get("name", "")
            detail = status[hid].get("detail", "")
            print(f"      {name[:55]}  [{detail[:40]}]")
        if len(unresolved) > 20:
            print(f"      ... and {len(unresolved) - 20} more")

    # Summary
    print()
    print("=" * 60)
    print(f"Second pass complete:")
    print(f"  Fixed (URL discovered):   {fixed}")
    print(f"  Still missing:            {len(still_missing)}")
    print(f"  No domain resolvable:     {len(unresolved)}")

    if args.dry_run:
        print("\n[DRY RUN] No files written.")
        return

    # Write updated registry
    with open(reg_path, "w") as f:
        json.dump(hospitals, f, indent=1)
    print(f"\nUpdated {reg_path.name} with {fixed} new MRF URLs.")
    print("Next step: run build_data.py --region california --stage prices")


if __name__ == "__main__":
    main()
