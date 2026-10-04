# scripts/archive/

Ad-hoc pipeline scripts from the **CA price-recovery effort (2026-10)**. Kept for
reference and as templates for future regions — they are NOT part of the canonical
`run.py` / `build_data.py` flow. They were written inline during CA work and contain
CA-specific paths, hospital IDs, and hardcoded assumptions; treat them as starting
points to adapt, not drop-in tools.

| Script | Purpose |
|---|---|
| `second_pass.py`   | URL-resolution pass: re-resolves hospitals whose CMS-HPT lookup failed by walking the hospital's own website for a price file / manifest. Fixed 17 CA hospitals (ok 65 → 82). |
| `third_pass.py`    | Discovery/enrichment pass: finds non-standard CMS-HPT sources (Box shared links, hospital portals) that the standard scan misses. Produced the Box/portal URLs for UCSF and Palomar; wrote a report to `data/ca-third-pass.json` (now deleted). |
| `recover_local_files.py` | Ingests locally-downloaded MRF files through `build_data.py`'s own extraction pipeline (feeds them via a tiny local HTTP server, skipping only the filename-ownership check for manually-verified IDs). Recovered UCSF + Palomar x2 after their live URLs were WAF-blocked. |

## Canonical flow (do not confuse with these)
The real pipeline is `scripts/run.py` (scan → prices → merge → release) backed by
`build_data.py`. These archived scripts exist because the canonical scan's CMS-HPT
discovery misses files that are published but not linked from the standard endpoint.
