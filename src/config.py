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

# --- dNBR burn-boundary knobs (P2.2b): FROZEN by validation/P2_PREREGISTRATION.md (ADR A20/A21).
# Transcribed VERBATIM from the pre-registration; take them LITERALLY with zero adjustment -- a
# value that "looks off" is a P2.3 finding, never a P2.2b edit (the anti-fitting firewall). All
# values are RAW dNBR (dimensionless, ~ -0.5..+1.3; the pipeline carries raw, NEVER x1000 -- P2.1 §2).
# tests/test_dnbr_frozen_constants.py is the fuse that asserts these equal the frozen document. ---
# Arm A binning: USGS/UN-SPIDER breaks (Key & Benson 2006 lineage), the four INTERIOR edges. Used
# left-closed/right-open (np.digitize right=False), then the frozen 5->4 collapse (P2.1 §2):
#   dNBR < 0.100 -> non-covered(15) | [0.100,0.270) -> SBS 2 | [0.270,0.440) -> 3 |
#   [0.440,0.660) -> 3 (mod-high collapses into the single SBS "Moderate") | dNBR >= 0.660 -> 4.
DNBR_BIN_EDGES = (0.100, 0.270, 0.440, 0.660)
# Arm B continuous transfer (P2.1 §3): b = clip(dNBR, lo, hi); mean_burn_pixel = (b-lo)/(hi-lo).
DNBR_CLAMP = (0.100, 1.300)
# Outside-burn / coverage floor shared by BOTH arms (P2.1 §4): dNBR < 0.100 -> non-covered
# (class-15 sentinel, weight 0.0, A23 operational). Equals the first bin edge and the lower clamp --
# the three knobs deliberately share ONE number, not four.
DNBR_FLOOR = 0.100
# dNBR NoData/cloud fail-loud guard (P2.1 §4 path 1, A8): if NoData covers MORE than this fraction
# of any flowed basin, the run errors loudly for that basin (a clouded scene is a bad scene, not a
# low-burn finding). Distinct from the below-floor path (which is non-covered but never fails loud).
DNBR_NODATA_FAILLOUD_FRAC = 0.20
# mean_burn denominator (A17, owner-confirmed canonical): Developed(0) AND outside-perimeter/
# NoData(15) map to 0.0 and are INCLUDED in the denominator (coverage-weighted). Faithful to
# VALIDATION_REPORT s2 "(unburned/outside-perimeter -> 0)"; reproduces flowed_mean=1.619, AUC ~0.972.
BURN_LOW_COVERAGE = 0.80      # flag basins with < this fraction of SBS-covered cells (C8 caveat)
SLOPE_LOW_COVERAGE = 0.80     # F4: flag basins with < this fraction of clean (non-nodata-ring) slope cells

# --- canonical grid (the validation case CRS; metres) ---
CANONICAL_CRS  = "EPSG:32611"   # Montecito/UTM-11N validation zone; the PER-FIRE DEFAULT (A25)
# A25: the per-fire CRS is additive -- callers default to CANONICAL_CRS, but a fire's own DEM CRS
# is threaded where it matters. This allowlist is the loud gate on which metric UTM zones may be
# ingested; a zone outside it fails loud, never a silent degrade.
# A37: widened from the two onboarded fires to the whole CONUS coverage -- UTM 10N-19N (EPSG
# 32610..32619), the contiguous-US 3DEP regime the method was built for. The gate is now a COVERAGE
# bound, not an onboarding gate: any CONUS bbox is accepted here, and the SCIENCE gates behind it
# (A27 terrain span, the CONTOUR_M range guard, dNBR coverage fail-loud, the F2 low-confidence
# stamp) decide per-area whether a ranking is produced -- "correct areas only" lives there, not here.
# Validity is a SEPARATE axis from the gate: only Montecito (32611) is VALIDATED; South Fork (32613)
# was generalization-only (A24) and every other zone is covered-but-UNVALIDATED. No new caveat is
# added -- honesty rests on the screening + "dNBR unvalidated for ranking" framing already on every
# output (the Spine). Alaska/Hawaii (a different regime) stay out; adding them is a deliberate edit.
ALLOWED_UTM_ZONES = set(range(32610, 32620))   # CONUS UTM 10N-19N (A37); was {32611, 32613} (A25)
CELL_M         = 10.0                      # DEM resolution (m); dx = dy = 10 m
# NOTE: CELL_AREA_KM2 (= CELL_M**2 / 1e6) is a DERIVATION, not a standalone tunable; per the
# P1.1 named-binding rule it stays computed at its use-site in gate.py from this CELL_M,
# rather than being extracted here.

# --- master-outlet FM-1 anti-collapse guard (SCALE-FREE; supersedes the PASS/FINDING/ABORT bands) ---
# MASTER_KNOWN_KM2 is the Week-0 DOCUMENTED master area, now a print-only reference (validation/gate.py
# main() quotes it). The reconstructed master is 44.7273 km^2 (tests/test_behavior_lock.py); treat that
# as truth and 39.19 as a superseded label. No live logic keys off this value.
MASTER_KNOWN_KM2 = 39.19
# The domain pour-point's catchment must be at least this FRACTION of the AOI's VALID DEM area, else
# GateAbort (FM-1: pysheds coordinate-mode once returned 0 km^2 and silently deleted the two largest
# flowed basins). A fraction, not a km^2 band, so it protects EVERY fire, not just Montecito-sized ones.
# DERIVED: Montecito master 44.7273 km^2 / valid AOI 168.9332 km^2 = 0.2648; floored at that / ~5 (a
# 5x collapse-detection margin). Conservative collapse detector, NOT a delineation-quality threshold;
# lower-only (a master ~= AOI is a clean single-drainage crop, not an error). See DECISIONS A38 (scale-free
# master-outlet guard) + docs/ALGORITHMS_REVIEW.md T5. dimensionless (km^2 / km^2).
MASTER_MIN_AOI_FRACTION = 0.05

# pysheds default D8 dirmap, listed in the order [N, NE, E, SE, S, SW, W, NW].
DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)
D8_OFFSETS = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1),
              4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1)}
