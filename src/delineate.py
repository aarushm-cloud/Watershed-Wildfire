"""delineate.py -- canyon-mouth outlet detection and upslope catchment
delineation in INDEX mode (row, col); discard tiny catchments, keep only
asset-draining ones, larger basins claim cells first. See ARCHITECTURE.md
and FAILURE_MODES FM-1.

P1.4 SCOPE (behavior-preserving extract from validation/gate.py stages 2b+2c): the two
contiguous delineation functions, lifted VERBATIM. EXPLICIT-ARGS signatures (not the hydro
bag) -- the caller (gate) unpacks hydro at the call site, so the dict-key coupling stays in
gate, not here. Deliberately NOT here (stay in gate): scoring + mean_burn/mean_slope (2e),
_burn_weight_raster + A18 coverage, evaluate (2f), and the whole-domain master-outlet /
scale-free anti-collapse guard block (2a). No new types/dataclasses (C9); basins stay loose dicts.

FM-1: the per-basin grid.catchment(...) runs in INDEX mode (xytype="index", x=col, y=row,
integer coords) -- coordinate mode silently returns 0 km^2. Carried byte-for-byte, with both
0-km^2 guards intact. The claim-order sort + shared-mask mutation are load-bearing (reordering
silently changes which basin wins a contested cell) -- preserved exactly.

IMPORT-TIME I/O BAN: nothing executes at module load except the pure-arithmetic CELL_AREA_KM2
derivation; no filesystem access. Imports numpy + scipy.cKDTree (third-party) and config/grids.
"""
from __future__ import annotations

import logging

import numpy as np
from scipy.spatial import cKDTree

from src.config import (
    CONTOUR_M,
    ACC_THRESHOLD_CELLS,
    MIN_BASIN_KM2,
    DRAINS_TO_ASSET_M,
    D8_OFFSETS,
    DIRMAP,
    CELL_M,
)
from src.grids import GateAbort, _rc_to_xy

# CELL_AREA_KM2 is the P1.1 gate-local derivation, recomputed here from CELL_M (same value, no new
# config binding): m^2 per cell -> km^2 (= 1e-4 km^2/cell). Pure arithmetic, no import-time I/O.
CELL_AREA_KM2 = (CELL_M * CELL_M) / 1.0e6

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared valid-cell definition -- single source of truth (A27).
# ---------------------------------------------------------------------------
def _valid_dem_mask(dem_raw: np.ndarray, dem_nodata) -> np.ndarray:
    """Boolean mask of valid DEM cells (finite AND != nodata). Single source of truth; both
    assert_contour_in_dem_range and assess_hypsometric_applicability use this, so the two guards
    can never disagree about which cells are terrain.

    Definition (carried VERBATIM from the original assert_contour_in_dem_range masking, so the
    extract is behavior-identical -- proven by the mask-parity checksum test on the Montecito DEM):
      valid = finite cells, AND (when a nodata sentinel exists) cells != that sentinel.

    dem_raw     -- raw metric DEM (m).
    dem_nodata  -- the DEM's nodata sentinel. CRITICAL (FM-12): pysheds defaults an UNDECLARED
                   nodata to 0, so for such a DEM this is 0 and the 0-fill cells MUST be excluded
                   (otherwise the valid min collapses to 0). Cells == dem_nodata (and any
                   non-finite) are excluded. Pass None only if there is genuinely no sentinel
                   (then only non-finite cells are dropped).
    """
    valid = np.isfinite(dem_raw)
    if dem_nodata is not None:
        valid &= (dem_raw != dem_nodata)        # FM-12: drop nodata-as-0 fill, never count it as terrain
    return valid


# ---------------------------------------------------------------------------
# A25 carve-out (council Q3) -- fail loud if CONTOUR_M is grossly mis-set for this fire's DEM.
# ---------------------------------------------------------------------------
def assert_contour_in_dem_range(dem_raw: np.ndarray, dem_nodata, *,
                                contour_m: float = CONTOUR_M) -> None:
    """Fail loud unless the mountain-front contour CONTOUR_M (m) falls inside the DEM's VALID
    elevation range. Catches the GROSS mis-set: an entirely-wrong fire's contour for this DEM --
    e.g. the 150 m Montecito value on South Fork's 1976-3312 m DEM (below the DEM minimum) -- which
    would otherwise make stage_2b_outlets straddle no cell and yield zero/wrong canyon-mouth
    outlets. Converts that one silent footgun (the per-fire CRS work does not touch it) into a
    clear abort. Keys off `dem_raw`, the SAME raw metric array (m) stage_2b_outlets applies the
    contour to (lines below: `dem_raw >= CONTOUR_M`), so the guard and the test see one elevation.

    SCOPE (do not oversell): catches a contour OUTSIDE the DEM range only -- NOT geomorphic
    correctness. An in-range-but-wrong contour still passes; per-fire contour tuning is out of A25
    scope and stays a documented limitation.

    dem_raw     -- raw metric DEM (m); the contour-test array.
    dem_nodata  -- the DEM's nodata sentinel. CRITICAL (FM-12): pysheds defaults an UNDECLARED
                   nodata to 0, so for such a DEM this is 0 and the 0-fill cells MUST be excluded --
                   otherwise the valid min collapses to 0, `0 <= contour <= max` is trivially true,
                   and the guard silently never fires (the guard-killing trap). Cells == dem_nodata
                   (and any non-finite) are excluded from the min/max. Pass None only if there is
                   genuinely no sentinel (then only non-finite cells are dropped).
    """
    valid = _valid_dem_mask(dem_raw, dem_nodata)   # shared single-source definition (A27); same cells as before
    if not valid.any():
        raise GateAbort("CONTOUR_M guard: DEM has no valid (non-nodata) cells -- cannot range-check "
                        "the contour (FM-10).")
    lo = float(dem_raw[valid].min())            # min valid terrain elevation (m)
    hi = float(dem_raw[valid].max())            # max valid terrain elevation (m)
    if not (lo <= contour_m <= hi):
        raise GateAbort(
            f"CONTOUR_M={contour_m} m is outside this DEM's valid elevation range "
            f"[{lo:.1f}, {hi:.1f}] m -- the wrong fire's contour for this DEM (it would yield "
            f"zero/wrong canyon-mouth outlets). Set CONTOUR_M for this fire. (A25 carve-out)")


# ---------------------------------------------------------------------------
# A27 -- terrain-applicability refusal trigger (frozen hypsometric-span rule).
# DECISIONS.md A27 / A27.1. The 50 m constant is pre-registered and FROZEN: never tuned, never
# per-fire, no parameter, no config.py override. The detector reads only WHETHER a contour is
# well-posed (a boolean over the hypsometry span); it returns NO absolute-elevation/contour value
# and consumes none -- that firewall line is what keeps A27 off the category-two scoring fence.
# ---------------------------------------------------------------------------
HYPSOMETRIC_SPAN_THRESHOLD_M = 50.0  # A27-frozen; never tuned, never per-fire, no override


def assess_hypsometric_applicability(dem_raw: np.ndarray, dem_nodata) -> dict:
    """A27 terrain-applicability pre-check (frozen hypsometric-span rule).

    REFUSE iff `(p10 - p1) > HYPSOMETRIC_SPAN_THRESHOLD_M` on valid (nodata-masked, finite) DEM
    elevation, where p1, p10 are the 1st and 10th percentiles of valid-cell elevation (m). A wide
    low tail (`p10 - p1` large) = an incised valley floor with no compact depositional plain; a
    true range-front-over-plain compresses p1->p10 to ~20-30 m (DECISIONS A27).

    FIREWALL (A27): this function reads WHETHER the contour-anchoring is well-posed, never WHAT
    contour VALUE to use. It emits NO absolute percentile elevation and consumes none. p1/p10 are
    computed, LOGGED, and discarded -- they are never returned, never stashed on a global/attribute.
    `span_m = p10 - p1` is a difference (a vertical extent), the only elevation-derived number that
    leaves the function; an absolute percentile elevation must not. Returns EXACTLY
    `{refuse, reason_code, span_m, span_threshold_m, n_valid}` -- no p1_m/p10_m, no contour-like key.

    On REFUSE the pipeline produces NO ranking: the scored basins are the upslope catchments of
    CONTOUR_M-anchored outlets (delineate.py outlets -> basins -> scores), an anchor incised terrain
    cannot define -- no anchor, no basins, no scores to caveat (A27.1). The caller writes a
    structured refusal instead (src.outputs.write_refusal); this function only classifies.

    dem_raw     -- raw metric DEM (m); read-only (a fancy-index copy is taken, never mutated).
    dem_nodata  -- the DEM's nodata sentinel (see _valid_dem_mask; FM-12).
    """
    valid = _valid_dem_mask(dem_raw, dem_nodata)
    vals = dem_raw[valid]                         # fancy-index COPY (m); dem_raw is never mutated
    n_valid = int(vals.size)
    if n_valid == 0:
        # No valid terrain to assess -- a broken/empty DEM, not an incised-terrain refusal. Fail loud
        # rather than emit a meaningless span (A8 fail-loud; mirrors the A25 guard's no-valid-cells case).
        raise GateAbort("A27 hypsometric pre-check: DEM has no valid (non-nodata, finite) cells -- "
                        "cannot assess terrain applicability (FM-10).")

    # p1, p10 = 1st and 10th percentiles of valid elevation (m). method='linear' fixed (no interp drift).
    p1, p10 = np.percentile(vals, [1, 10], method='linear')
    p1 = float(p1)
    p10 = float(p10)
    span_m = float(p10 - p1)                       # vertical extent (m); the ONLY elevation-derived value returned
    refuse = span_m > HYPSOMETRIC_SPAN_THRESHOLD_M  # strict >
    reason_code = "REFUSED_INCISED_TERRAIN" if refuse else "OK_RANGE_FRONT_APPLICABLE"

    # p1/p10 are LOGGED for diagnostics, never returned (firewall: no absolute elevation leaves).
    _log.info("A27 hypsometric pre-check: p1=%.4f m, p10=%.4f m, span_m=%.4f m, n_valid=%d, "
              "threshold=%.1f m, refuse=%s", p1, p10, span_m, n_valid,
              HYPSOMETRIC_SPAN_THRESHOLD_M, refuse)

    return {
        "refuse": bool(refuse),
        "reason_code": reason_code,
        "span_m": span_m,
        "span_threshold_m": HYPSOMETRIC_SPAN_THRESHOLD_M,
        "n_valid": n_valid,
    }


# ---------------------------------------------------------------------------
# 2b -- canyon-mouth outlets
# ---------------------------------------------------------------------------
def stage_2b_outlets(acc, fdir, dem_raw, shape, *, contour_m: float = CONTOUR_M) -> list[tuple[int, int]]:
    """Channel cells (acc > threshold) that cross the CONTOUR_M mountain-front contour going downhill.

    A channel cell with raw elevation >= CONTOUR_M whose D8-downstream neighbour's raw
    elevation is < CONTOUR_M is a canyon-mouth outlet (VALIDATION_REPORT s3.2). Contour
    test on RAW terrain; routing on conditioned-DEM fdir. Returns (row, col) tuples.

    Args (explicit, from hydro at the gate call site): acc -- flow-accumulation array;
    fdir -- flow-direction ARRAY (np.asarray(fdir); for the integer fdir[r,c] lookup);
    dem_raw -- raw metric DEM (m); shape -- (nrows, ncols)."""
    nrows, ncols = shape
    channel = acc > ACC_THRESHOLD_CELLS

    outlets: list[tuple[int, int]] = []
    cand_rows, cand_cols = np.where(channel & (dem_raw >= contour_m))
    for r, c in zip(cand_rows.tolist(), cand_cols.tolist()):
        off = D8_OFFSETS.get(int(fdir[r, c]))
        if off is None:
            continue
        nr, nc = r + off[0], c + off[1]
        if 0 <= nr < nrows and 0 <= nc < ncols and dem_raw[nr, nc] < contour_m:
            outlets.append((r, c))

    if not outlets:
        raise GateAbort("Zero canyon-mouth outlets detected -- contour/accumulation logic "
                        "or the AOI is wrong (FM-10). Refusing empty result.")
    return sorted(outlets)  # stable order


# ---------------------------------------------------------------------------
# 2c -- delineate, discard, drains-to-asset, dedup (DETERMINISTIC)
# ---------------------------------------------------------------------------
def stage_2c_delineate(grid, acc, fdir_raster, transform, shape, outlets, asset_xy):
    """Delineate, discard tiny, keep asset-draining, dedup (larger basins claim first).

    Order (an operational choice; report lists these as prose, s3.3): delineate ->
    discard raw < MIN_BASIN_KM2 -> drains-to-asset (basin CHANNEL cells -> nearest asset
    <= 600 m, "channel reaches within 600 m of the building layer") -> dedup -> re-discard.

    Determinism: dedup ties break by (-area, row, col); basin_id assigned by sorting the
    surviving basins on outlet (row, col). Geometry is unchanged from Part 1 (no exact
    area ties exist with float areas; the keys only canonicalise label/claim order).

    Args (explicit, from hydro at the gate call site): grid -- pysheds Grid; acc -- accumulation
    array; fdir_raster -- flow-direction pysheds RASTER (for grid.catchment); transform -- affine;
    shape -- (nrows, ncols); outlets -- (row,col) list from stage_2b_outlets; asset_xy -- Nx2
    asset coords (m)."""
    channel = acc > ACC_THRESHOLD_CELLS
    asset_tree = cKDTree(asset_xy)

    raw = []  # surviving (outlet, mask, raw_area, asset_dist)
    for (r, c) in outlets:
        # INDEX mode mandatory (FM-1: coordinate mode silently returns 0 km^2).
        mask = np.asarray(grid.catchment(x=int(c), y=int(r), fdir=fdir_raster,
                                         dirmap=DIRMAP, xytype="index", routing="d8"), dtype=bool)
        area = int(mask.sum()) * CELL_AREA_KM2
        if not np.isfinite(area) or area <= 0.0:
            raise GateAbort(f"Outlet (row={r}, col={c}) delineated to {area} km^2 "
                            "(0 / non-finite) -- FM-1 bug class. Aborting.")
        if area < MIN_BASIN_KM2:
            continue
        ch_rows, ch_cols = np.where(mask & channel)
        if ch_rows.size == 0:
            continue
        dmin = float(np.min(asset_tree.query(_rc_to_xy(ch_rows, ch_cols, transform), k=1)[0]))
        if dmin <= DRAINS_TO_ASSET_M:
            raw.append({"outlet": (r, c), "mask": mask, "raw_km2": area, "asset_m": dmin})

    if not raw:
        raise GateAbort("No basins survive discard + drains-to-asset -- FM-10.")

    # dedup: larger claims first; ties -> (-area, row, col) for determinism
    raw.sort(key=lambda b: (-b["raw_km2"], b["outlet"][0], b["outlet"][1]))
    claimed = np.zeros(shape, dtype=bool)
    kept = []
    for b in raw:
        own = b["mask"] & ~claimed
        own_km2 = int(own.sum()) * CELL_AREA_KM2
        if own_km2 < MIN_BASIN_KM2:
            continue
        claimed |= own
        own.flags.writeable = False   # arm-independence hardening: basin masks are read-only downstream
        kept.append({"outlet": b["outlet"], "mask": own,
                     "area_km2": own_km2, "asset_m": b["asset_m"]})

    # stable basin_id by outlet (row, col)
    kept.sort(key=lambda b: (b["outlet"][0], b["outlet"][1]))
    for i, b in enumerate(kept):
        b["basin_id"] = i
    return kept
