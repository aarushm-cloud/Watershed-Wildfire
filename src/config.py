"""config.py -- frozen scalar tunables. The dependency leaf: imports nothing from the project.

FROZEN values -- never tuned to hit a known answer. Governance: DECISIONS + the pre-registrations.
"""

# --- frozen scalar tunables ---
CONTOUR_M            = 150     # mountain-front contour elevation (m)
ACC_THRESHOLD_CELLS  = 500     # min flow-accumulation (cells) for a channel cell
MIN_BASIN_KM2        = 0.1     # discard catchments below this (km^2)
DRAINS_TO_ASSET_M    = 600     # keep basins whose channel reaches within this of assets (m)
TRUTH_MATCH_M        = 250     # max creek -> outlet match distance (m)  [used in 2f]

# --- SBS pixel -> class encoding (do NOT re-derive) ---
# 1 = Unburned/very-low, 2 = Low, 3 = Moderate, 4 = High, 0 = Masked (Developed), 15 = NoData
BURN_WEIGHTS = {1: 0.0, 2: 0.33, 3: 0.67, 4: 1.0}   # 0/15 weight -> 0.0

# --- dNBR knobs: RAW dNBR (dimensionless, NEVER x1000). FROZEN by the dNBR pre-registration
# (validation/reports/P2_PREREGISTRATION.md, A20/A21); test_dnbr_frozen_constants.py is the fuse. ---
DNBR_BIN_EDGES = (0.100, 0.270, 0.440, 0.660)   # Arm A interior edges, left-closed/right-open, 5->4 collapse
DNBR_CLAMP = (0.100, 1.300)                     # Arm B: linear (b-lo)/(hi-lo)
DNBR_FLOOR = 0.100            # below-floor -> non-covered (class-15, weight 0.0); shared by both arms
DNBR_NODATA_FAILLOUD_FRAC = 0.20   # NoData/cloud over more of a flowed basin -> fail loud (A8)
BURN_LOW_COVERAGE = 0.80      # flag basins with < this fraction of SBS-covered cells
SLOPE_LOW_COVERAGE = 0.80     # flag basins with < this fraction of clean (non-nodata-ring) slope cells

# --- canonical grid (metres) ---
CANONICAL_CRS  = "EPSG:32611"   # Montecito validation zone; the per-fire default (A25)
ALLOWED_UTM_ZONES = set(range(32610, 32620))   # CONUS UTM 10N-19N coverage bound (A25/A37); fails loud outside
CELL_M         = 10.0                      # DEM resolution (m)

# --- master-outlet FM-1 anti-collapse guard (scale-free, A38) ---
MASTER_KNOWN_KM2 = 39.19          # Week-0 documented master area; print-only reference, no live logic
MASTER_MIN_AOI_FRACTION = 0.05    # master catchment / valid AOI floor, else GateAbort (FM-1); derived, see A38

# pysheds default D8 dirmap, listed in the order [N, NE, E, SE, S, SW, W, NW].
DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)
D8_OFFSETS = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1),
              4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1)}

# --- incised-terrain sub-basin segmentation (WhiteboxTools path; FROZEN, A39) ---
SUBBASIN_ACC_THRESHOLD_CELLS = 3000    # 0.30 km2 trunk network for confluence splitting
SUBBASIN_BURN_FRAC_MIN = 0.25          # keep basins at least a quarter burned
SUBBASIN_SLOPE_FLOOR_TAN = 0.05        # ~2.9 deg -- drops degenerate flat basins only
SUBBASIN_BREACH_DIST_CELLS = 100       # WBT least-cost breach search radius (1 km at 10 m)
