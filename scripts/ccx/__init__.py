"""ccx — Care Cost Explorer pipeline support package.

Low-coupling leaf modules extracted from build_data.py to shrink the monolith
without disturbing its tangled runtime state (CATALOGUE_DIR global, the
sniff_content_kind monkeypatch the smoke test relies on). Only pure data and
constants live here; all pipeline logic remains in build_data.py and imports
these by name.
"""
