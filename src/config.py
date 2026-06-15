"""config.py -- static scalar per-fire tunables: mountain-front contour elevation,
flow-accumulation threshold, minimum basin area, truth-match tolerance,
drains-to-asset distance, burn-weight mapping. Stored per fire; never globally
edited in place. See ARCHITECTURE.md and DECISIONS A6.

P1.1: these are EXTRACTED VERBATIM from validation/gate.py (the monolithic P0.5 gate)
with ZERO value changes -- the gate imports them back by name. This module is the
dependency LEAF: it imports NOTHING from the project. Frozen values; do NOT tune to hit
a known-answer (A-guardrail, FM-3).
"""

# --- frozen scalar tunables (do NOT tune to hit a known-answer -- A-guardrail, FM-3) ---
CONTOUR_M            = 150     # mountain-front contour elevation (m)
ACC_THRESHOLD_CELLS  = 500     # min flow-accumulation (cells) for a channel cell
MIN_BASIN_KM2        = 0.1     # discard catchments below this (km^2)
DRAINS_TO_ASSET_M    = 600     # keep basins whose channel reaches within this of assets (m)
TRUTH_MATCH_M        = 250     # max creek -> outlet match distance (m)  [used in 2f]

# --- SBS pixel -> class encoding (do NOT re-derive) ---
# 1 = Unburned/very-low, 2 = Low, 3 = Moderate, 4 = High, 0 = Masked (Developed), 15 = NoData
BURN_WEIGHTS = {1: 0.0, 2: 0.33, 3: 0.67, 4: 1.0}   # 0/15 weight -> 0.0
# mean_burn denominator (A17, owner-confirmed canonical): Developed(0) AND outside-perimeter/
# NoData(15) map to 0.0 and are INCLUDED in the denominator (coverage-weighted). Faithful to
# VALIDATION_REPORT s2 "(unburned/outside-perimeter -> 0)"; reproduces flowed_mean=1.619, AUC ~0.972.
BURN_LOW_COVERAGE = 0.80      # flag basins with < this fraction of SBS-covered cells (C8 caveat)

# --- canonical grid (the validation case CRS; metres) ---
CANONICAL_CRS  = "EPSG:32611"
CELL_M         = 10.0                      # DEM resolution (m); dx = dy = 10 m
# NOTE: CELL_AREA_KM2 (= CELL_M**2 / 1e6) is a DERIVATION, not a standalone tunable; per the
# P1.1 named-binding rule it stays computed at its use-site in gate.py from this CELL_M,
# rather than being extracted here.

# --- master-outlet zones (the FM-1 anti-0km2 guard), area in km^2 ---
MASTER_KNOWN_KM2 = 39.19
MASTER_PASS_LO, MASTER_PASS_HI   = 33.3, 45.1   # +/-15% of 39.19 -> PASS
MASTER_ORDER_LO, MASTER_ORDER_HI = 20.0, 80.0   # outside this -> ABORT (order-of-magnitude)

# pysheds default D8 dirmap, listed in the order [N, NE, E, SE, S, SW, W, NW].
DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)
D8_OFFSETS = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1),
              4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1)}
