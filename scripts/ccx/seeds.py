"""Community MRF seed list (TPAFS transparency-data, Apache-2.0), slimmed.

data/mrf-seeds.csv maps CCN -> the hospital's own site domain + the last known
file URL. The 2022 URLs are mostly stale, but the DOMAIN is what discovery
needs: ~80% of seeded domains serve a live /cms-hpt.txt manifest today. The
registry uses the domain as a fallback after DOMAIN_HINTS; the harvest tries
the bare seed URL as a last candidate, still gated by verify_file_belongs.
Nothing here is trusted without the normal identity check.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
SEEDS_PATH = ROOT / "data" / "mrf-seeds.csv"

# File hosts that are vendor CDNs, not the hospital's own site. A domain
# guessed from these would point discovery at the wrong organisation.
CDN_HOSTS = (
    "blob.core.windows.net", "amazonaws.com", "panaceainc.com", "para-hcfs.com",
    "changehealthcare.com", "cloudfront.net", "sharepoint.com", "box.com",
    "dropbox.com", "googleapis.com", "azureedge.net", "clearhealthcosts",
    "hospitalpricedisclosure.com", "cleverley", "mlessentia",
)


def is_cdn(host: str) -> bool:
    h = (host or "").lower()
    return any(c in h for c in CDN_HOSTS)


_SEEDS: Optional[dict[str, dict]] = None


def load_seeds() -> dict[str, dict]:
    """CCN -> {state, domain, file_host, url, fmt}. Cached; empty if absent."""
    global _SEEDS
    if _SEEDS is None:
        _SEEDS = {}
        if SEEDS_PATH.exists():
            with SEEDS_PATH.open(encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    if r.get("ccn"):
                        _SEEDS[r["ccn"]] = r
    return _SEEDS


def seed_for(ccn: str) -> Optional[dict]:
    return load_seeds().get((ccn or "").strip())


def seed_domain(ccn: str) -> Optional[str]:
    s = seed_for(ccn)
    return (s or {}).get("domain") or None


def seed_url(ccn: str) -> Optional[str]:
    s = seed_for(ccn)
    u = (s or {}).get("url") or ""
    # Only shapes the harvester can parse; portals/xlsx/pdf are dead ends.
    return u if u.lower().split("?")[0].endswith((".csv", ".json", ".zip")) else None
