"""ingest.py -- the front door: load DEM/burn/assets, select the burn source by
precedence (SBS if it covers the whole AOI, else dNBR; never blended), and emit
the single Provenance object every downstream stage trusts. Owns the first
fail-loud check. See ARCHITECTURE.md and DECISIONS A2/A3/A4/A8/A15.
"""
