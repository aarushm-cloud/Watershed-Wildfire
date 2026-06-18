"""ingest.py -- the front door (the A15 seam): load DEM/burn/assets/creeks, SELECT the one burn
source by precedence, remap its classes to per-cell weights + the coverage mask, and emit the
single burn-source provenance. See ARCHITECTURE.md and DECISIONS A2/A3/A4/A8/A15.

P2.2a SCOPE (behavior-preserving): realises the A15 ingest seam. Burn-source SELECTION (A3
precedence), the class->weight remap + A18 coverage mask (moved here VERBATIM from score.py), and
the single Provenance stamp now live in this one file -- so adding the dNBR arm (P2.2b) is a change
INSIDE this file, not surgery across the pipeline. SBS-only: the dNBR branch is present but inert
(raises NotImplementedError("dNBR arm: P2.2b")). Outputs are bit-identical (Montecito SBS covers
the AOI -> resolves to SBS; new code path, identical values). Deliberately NOT here: the DEM/SBS
alignment check (stays at gate.stage_2a's call site, now via grids.assert_aligned) and the
per-basin mean_burn reduction (stays in score.py -- it needs the delineated basins, which do not
exist at ingest). No Provenance dataclass / no aggregator (C9/A19): the provenance is a loose dict.

IMPORT-TIME I/O BAN: every read lives inside a function; nothing here touches the filesystem at
module load, so the module is importable without any input present (keeps it path-agnostic --
paths are owned by gate.py / run.py and passed as arguments).
"""
from __future__ import annotations

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.warp import reproject, Resampling
from pysheds.grid import Grid

from src.config import BURN_WEIGHTS, DNBR_BIN_EDGES, DNBR_CLAMP, DNBR_FLOOR
from src.grids import GateAbort, assert_aligned


# --- BAER SBS thematic codeset: the known class values a valid SBS cell may hold. 1-4 = soil burn
# severity (Unburned/very-low .. High), 0 = Masked (Developed), 15 = outside-perimeter/NoData. A
# cell holding ANY OTHER value is genuinely missing data. sbs.tif declares NO rasterio nodata, so
# the GDAL mask is trivially 100% and cannot define "valid" -- membership in this codeset does
# (owner decision 2026-06-16; class 15 counts as covered, see select_burn_source).
SBS_CODESET = (0, 1, 2, 3, 4, 15)

# --- dNBR-arm mechanics (P2.2b). NOT frozen-science scalars (those are config.DNBR_*); these are the
# raster-plumbing sentinels for the reproject path:
DNBR_CLASS15 = 15        # non-covered sentinel -- reuses the SBS class-15 encoding so _burn_weight_raster
#                          gives it weight 0.0 + covered=False for free (A23 operational, P2.1 §4).
DNBR_NODATA = -9999.0    # reproject fill value for uncovered/NoData canonical cells. Matches the P2.0
#                          native raster's own nodata (validation/out/montecito_dnbr provenance), and
#                          sits far outside the raw dNBR range (~ -1.7..1.8), so `== DNBR_NODATA` is an
#                          unambiguous "no data here" test.


def load_dem(path):
    """Load the DEM into a pysheds Grid + raw elevation array (metres). Raw read only.

    Returns (grid, dem, dem_raw) -- the FULL consumed surface of this read:
      grid    -- pysheds Grid built from the DEM (downstream: conditioning, flow, catchments)
      dem     -- the pysheds Raster from read_raster (downstream: grid.fill_pits input)
      dem_raw -- float64 copy of the elevation raster (metres), used for slope + contour tests
    The rasterio metadata read (CRS/shape/transform) for DEM/SBS alignment stays in gate -- it is
    cross-input validation, not a raw read. `read_raster` is called exactly as the gate did it
    (no nodata/masked args; pysheds defaults unchanged)."""
    grid = Grid.from_raster(str(path))
    dem = grid.read_raster(str(path))
    dem_raw = np.asarray(dem, dtype=np.float64).copy()  # raw terrain elevation (m)
    return grid, dem, dem_raw


def load_burn(path):
    """Load the burn raster band 1 as the RAW SBS class array (no remap). Raw read only.

    Returns the integer SBS class raster (classes per config.BURN_WEIGHTS encoding). The
    class->weight remap and A18 coverage masking now live in this module (`_burn_weight_raster`,
    moved from score.py in P2.2a) and are applied by the `ingest_burn` seam. Same `read(1)` call,
    same band, default masked=False -- nodata propagation unchanged."""
    with rasterio.open(path) as s:
        return s.read(1)


def load_assets(path):
    """Load the asset (building) layer as a GeoDataFrame. Raw read only.

    Returns the GeoDataFrame verbatim from `gpd.read_file`; the gate still does the
    `_assert_metric_crs(.crs)` guard and the x/y coordinate extraction downstream."""
    return gpd.read_file(path)


def load_creeks(path):
    """Load the truth creek/channel layer as a GeoDataFrame. Raw read only.

    Returns the GeoDataFrame verbatim from `gpd.read_file`; the gate still does the
    `_assert_metric_crs(.crs)` guard and the geometry-validity check downstream."""
    return gpd.read_file(path)


# ---------------------------------------------------------------------------
# The A15 burn seam: select ONE source -> remap to weights + coverage -> stamp provenance.
# ---------------------------------------------------------------------------
def select_burn_source(sbs: np.ndarray) -> str:
    """A3/A15 precedence: SBS if it covers the WHOLE AOI, else dNBR for the whole AOI (never
    blended). SBS-only for now; the dNBR branch is inert until P2.2b.

    AOI = the analysis grid. The DEM/SBS alignment check (grids.assert_aligned, run UPSTREAM in
    gate.stage_2a) guarantees the SBS raster shares the DEM grid cell-for-cell, so "SBS valid-data
    extent contains the whole AOI" (DATA_SOURCES.md s5) reduces to "every SBS cell holds a valid
    class value." VALID = value in SBS_CODESET {0,1,2,3,4,15}; class 15 (outside-perimeter) COUNTS
    as covered ("assessed: outside the burn"), NOT as missing -- owner decision 2026-06-16 (sbs.tif
    has no declared nodata, so codeset membership, not a GDAL mask, defines validity). "Covers the
    AOI" = EVERY cell is in-codeset; partial coverage -> NOT whole-area -> dNBR (A3)."""
    n_invalid = int((~np.isin(sbs, SBS_CODESET)).sum())   # cells outside the known SBS codeset
    if n_invalid == 0:
        return "SBS"
    # A3: partial SBS -> dNBR for the whole AOI (never blended). P2.2b fills this arm: the dNBR path
    # (reproject -> per-arm normalization -> A23 coverage) lives in ingest_dnbr_both_arms below; this
    # branch is the A22 source-selection sense (dNBR coverage for an un-assessed fire with no full SBS).
    return "dNBR"


def _burn_weight_raster(sbs: np.ndarray):
    """Per-cell burn weight (A17, canonical): classes 1-4 -> BURN_WEIGHTS; Developed(0) and
    outside-perimeter/NoData(15) -> 0.0, all INCLUDED in the denominator (coverage-weighted).
    Returns (wt, covered); covered = cells with a real burn assessment, class in {1,2,3,4}
    (excludes Developed=0 and NoData=15) -- the A18/C8 fix; used only for the burn_coverage_frac
    caveat, NOT to gate the mean.

    P2.2a: moved VERBATIM from score.py into the ingest seam so the weighted raster + coverage mask
    are produced once at ingest; score.py now consumes them (the per-basin mean stays in score)."""
    wt = np.zeros(sbs.shape, dtype=np.float64)
    for cls, w in BURN_WEIGHTS.items():      # classes 1..4 (0 and 15 stay 0.0)
        wt[sbs == cls] = w
    covered = np.isin(sbs, (1, 2, 3, 4))
    return wt, covered


def ingest_burn(burn_path):
    """A15 seam: select the one burn source, load it, remap to per-cell weights + coverage mask,
    and emit the single burn-source provenance (A4). SBS-only (the dNBR arm is inert, P2.2b).

    Returns (wt, covered, provenance):
      wt         -- per-cell burn weight raster [0-1, dimensionless], float64 (A17)
      covered    -- per-cell real-assessment mask, bool (A18)
      provenance -- the single loose-dict burn-source stamp every output carries (A4/A19)
    The per-basin mean_burn reduction stays in score.py (it needs the delineated basins)."""
    sbs = load_burn(burn_path)               # raw SBS class raster (band 1)
    burn_source = select_burn_source(sbs)    # A3 precedence -> "SBS" for Montecito (or "dNBR" if partial)
    wt, covered = _burn_weight_raster(sbs)   # A17 weights + A18 coverage, computed once at ingest
    provenance = {"burn_source": burn_source}  # A4/A19: single loose-dict stamp, read everywhere
    return wt, covered, provenance


# ---------------------------------------------------------------------------
# The A15 dNBR arm (P2.2b): reproject native dNBR -> canonical grid -> per-arm normalization ->
# A23 layered coverage. The SBS path above is UNTOUCHED; this is a change INSIDE ingest.py (the seam),
# never surgery across the pipeline. NO comparison to SBS happens here -- that is P2.3.
# ---------------------------------------------------------------------------
def reproject_dnbr(native_path, dem_profile, resampling):
    """Reproject the native dNBR raster onto the canonical DEM grid (P2.1 §5, FROZEN ordering).

    Snaps to the DEM grid via the DEM's EXPLICIT dst_transform/dst_shape -- never the dNBR scene's own
    grid (a half-pixel GRID-ALIGNMENT offset would shift every downstream value by up to a cell: the
    P0.5 north-shift ghost. This is about grid alignment at resample time -- there are no classes yet;
    binning happens AFTER, on the snapped grid, P2.1 §4/§5). This is a 30 m -> 10 m UPSAMPLE: the P2.0
    raster is Landsat 30 m, NOT the 20 m Sentinel-2 the pre-registration originally assumed (documented
    sensor caveat, A21/A4). `resampling` is the rasterio Resampling enum -- nearest for Arm A, bilinear
    for Arm B (P2.1 §5).

    Returns (canonical_dnbr [float32, RAW dNBR, dimensionless], dnbr_profile). Uncovered/NoData cells
    carry DNBR_NODATA so the caller can build the valid mask. The single reproject() call below is the
    pinned form (explicit dst_transform/shape; NOT a read(out_shape=) shortcut), P2.1 §5."""
    with rasterio.open(native_path) as src:
        src_arr = src.read(1)
        src_transform = src.transform
        src_crs = src.crs
        src_nodata = src.nodata           # P2.0 wrote nodata=-9999.0; pass it so resampling masks it
    height, width = dem_profile["height"], dem_profile["width"]
    dst = np.full((height, width), DNBR_NODATA, dtype="float32")   # uncovered cells stay = DNBR_NODATA
    reproject(
        source=src_arr, destination=dst,
        src_transform=src_transform, src_crs=src_crs, src_nodata=src_nodata,
        dst_transform=dem_profile["transform"], dst_crs=dem_profile["crs"], dst_nodata=DNBR_NODATA,
        resampling=resampling,
    )
    dnbr_profile = dict(dem_profile)
    dnbr_profile.update(dtype="float32", count=1, nodata=DNBR_NODATA)
    return dst, dnbr_profile


def normalize_dnbr_arm_a(dnbr, valid):
    """Arm A (PRIMARY, P2.1 §2): bin RAW dNBR -> SBS 4-class encoding via the frozen 5->4 collapse,
    then hand the class raster to _burn_weight_raster REUSED UNTOUCHED (same function, same
    BURN_WEIGHTS, same A17/A18 semantics as SBS). Binning is LEFT-CLOSED / RIGHT-OPEN
    (np.digitize right=False, config.DNBR_BIN_EDGES) so a pixel exactly on an edge (e.g. 0.270 after
    float noise) classifies deterministically (fix 6).

    The 5->4 collapse (config.DNBR_BIN_EDGES = 0.100/0.270/0.440/0.660):
      dNBR < 0.100 -> non-covered(15) | [0.100,0.270) -> 2 | [0.270,0.440) -> 3 |
      [0.440,0.660) -> 3 (Moderate-high collapses into SBS's single "Moderate") | >= 0.660 -> 4.
    No dNBR pixel is ever assigned SBS class 1 (below-floor routes to 15, not 1).

    valid: bool mask (True = a usable dNBR value). Invalid cells (NaN/nodata/cloud) AND below-floor
    both route to the class-15 non-covered sentinel. A NaN NEVER reaches a threshold comparison
    (P2.1 §1: `NaN < 0.100` is False, so an unmasked NaN would dodge the floor and digitize into the
    top bin -- the silent-wrong defect): invalid cells are replaced with a below-floor sentinel BEFORE
    np.digitize, and overwritten to 15 AFTER. Returns (wt [0-1 weight], covered [bool], cls [int16])."""
    safe = np.where(valid, dnbr, -1.0).astype("float64")    # invalid -> -1.0 (< floor -> bin 0); no NaN binned
    bins = np.digitize(safe, DNBR_BIN_EDGES, right=False)   # 0..4, [lo, hi) per the frozen edges
    cls = np.full(np.shape(dnbr), DNBR_CLASS15, dtype="int16")   # default: non-covered (bin 0 + invalid)
    cls[bins == 1] = 2     # [0.100, 0.270) Low           -> SBS 2
    cls[bins == 2] = 3     # [0.270, 0.440) Moderate-low  -> SBS 3
    cls[bins == 3] = 3     # [0.440, 0.660) Moderate-high -> SBS 3 (the single genuine 5->4 merge)
    cls[bins == 4] = 4     # >= 0.660       High          -> SBS 4
    cls[~valid] = DNBR_CLASS15    # belt-and-suspenders: invalid is non-covered regardless of `safe`
    wt, covered = _burn_weight_raster(cls)   # REUSED untouched: 1-4 -> BURN_WEIGHTS, 0/15 -> 0.0/not-covered
    return wt, covered, cls


def normalize_dnbr_arm_b(dnbr, valid):
    """Arm B (COMPANION, P2.1 §3): continuous transfer. b = clip(dNBR, lo, hi);
    mean_burn_pixel = (b - lo) / (hi - lo), LINEAR (no power curve -- that would be a tunable), with
    (lo, hi) = config.DNBR_CLAMP. Below-floor (dNBR < DNBR_FLOOR) and invalid cells -> non-covered,
    weight 0.0 -- the SAME 0.1 floor as Arm A, and A17-consistent (included in the mean_burn
    denominator as 0.0). Returns (wt [0-1, dimensionless], covered [bool])."""
    lo, hi = DNBR_CLAMP
    arr = np.asarray(dnbr, dtype="float64")
    b = np.clip(np.where(valid, arr, lo), lo, hi)        # invalid -> lo so the map stays finite
    wt = (b - lo) / (hi - lo)                            # linear [0,1]; lo maps to exactly 0.0
    covered = np.asarray(valid, dtype=bool) & (arr >= DNBR_FLOOR)   # below-floor + invalid -> non-covered
    wt = np.where(covered, wt, 0.0)                      # non-covered cells contribute 0.0 (A17)
    return wt, covered


def ingest_dnbr_both_arms(native_path, dem_profile):
    """The A15 dNBR arm end-to-end (P2.2b): reproject native dNBR to the canonical grid for BOTH arms,
    derive ONE shared valid footprint, normalize each arm, and emit the A23 coverage layers -- the same
    (wt, covered) handoff the SBS path produces, so stage_2e_score consumes it UNCHANGED. NO comparison
    to SBS here (that is P2.3).

    Reproject (P2.1 §5): Arm A = nearest, Arm B = bilinear, both snapped to the DEM grid. THE SHARED
    VALID FOOTPRINT is derived once (from Arm A's nearest reproject: a canonical cell is valid iff its
    nearest native cell is valid) and applied to BOTH arms, so A and B are byte-identical in footprint
    BY CONSTRUCTION -- independent of whether bilinear and nearest would otherwise agree on a given
    raster. This isolates the P2.3 A<->B agreement to the normalization function alone (P2.1 §1), and
    is the spec's "use the same valid footprint for both arms". A guard (below) fails loud if bilinear
    leaves a hole inside the shared footprint. [On the P2.0 raster the two NAIVE footprints happen to be
    identical anyway -- 9 excluded cells each, around the single interior NoData cell -- but the
    shared-by-construction design does NOT depend on that and correctly generalizes to rasters (other
    scenes/fires at P2.3/P3) where nearest and bilinear WOULD diverge at the margin.]

    assert_aligned (grids.py) confirms both arms share the canonical grid BEFORE any thresholding
    (P2.1 §5; A7/A8). Returns a loose dict (C9 deferred): valid / nodata_mask / covered_interp (the A23
    diagnostic base: below-floor counted as covered) / arm_a{wt,covered,cls} / arm_b{wt,covered} /
    dnbr_a / dnbr_b / profile."""
    # Arm A = nearest: on a pure 30->10 upsample, nearest replicates each native value EXACTLY into the
    # finer cells with zero interpolation, so binning the result == binning native then replicating (no
    # edge artifact near a bin boundary). Bilinear WOULD smear values across native cells before binning.
    # (NOT "nearest preserves classes" -- there are no classes yet at reproject time; binning is after.)
    dnbr_a, prof_a = reproject_dnbr(native_path, dem_profile, Resampling.nearest)    # Arm A: nearest
    # Arm B = bilinear: B is continuous, so the standard antialiased resample is right (P2.1 §5).
    dnbr_b, prof_b = reproject_dnbr(native_path, dem_profile, Resampling.bilinear)   # Arm B: bilinear

    # Alignment to the canonical DEM grid MUST hold before any thresholding (P2.1 §5; fail loud A7/A8).
    assert_aligned(dem_profile, prof_a, other_name="dNBR-A")
    assert_aligned(dem_profile, prof_b, other_name="dNBR-B")

    # ONE shared valid footprint, from Arm A's nearest reproject (P2.1 §1: same footprint both arms).
    valid = (dnbr_a != DNBR_NODATA) & np.isfinite(dnbr_a)
    # Guard (fail loud, A8): no NaN/sentinel may survive into the valid footprint for EITHER arm -- else
    # a non-finite reaches a threshold (the silent-wrong defect) or the A/B footprints truly diverge.
    if not np.isfinite(dnbr_a[valid]).all() or bool((dnbr_a[valid] == DNBR_NODATA).any()):
        raise GateAbort("dNBR Arm A: non-finite/sentinel value inside the valid footprint (P2.2b §1).")
    if not np.isfinite(dnbr_b[valid]).all() or bool((dnbr_b[valid] == DNBR_NODATA).any()):
        raise GateAbort("dNBR Arm B (bilinear) left a hole inside the shared valid footprint -- the A/B "
                        "footprints would differ; failing loud rather than measuring a resample artifact "
                        "as normalization disagreement (P2.2b §1).")

    wt_a, cov_a, cls_a = normalize_dnbr_arm_a(dnbr_a, valid)   # nearest-reprojected raster
    wt_b, cov_b = normalize_dnbr_arm_b(dnbr_b, valid)          # bilinear-reprojected raster

    nodata_mask = ~valid              # §4 path-1 base (NoData/cloud); >20% flowed-basin guard is per-basin
    covered_interp = valid.copy()     # A23 diagnostic: below-floor counted as covered, only NoData excluded

    return {"valid": valid, "nodata_mask": nodata_mask, "covered_interp": covered_interp,
            "arm_a": {"wt": wt_a, "covered": cov_a, "cls": cls_a},
            "arm_b": {"wt": wt_b, "covered": cov_b},
            "dnbr_a": dnbr_a, "dnbr_b": dnbr_b, "profile": prof_a}
