"""delineate.py -- canyon-mouth outlet detection + upslope catchment delineation; discard
tiny, keep asset-draining, larger basins claim cells first.

FM-1: grid.catchment runs in INDEX mode (xytype="index", x=col, y=row) -- coordinate mode
silently returns 0 km^2. The claim-order sort is load-bearing.
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

CELL_AREA_KM2 = (CELL_M * CELL_M) / 1.0e6   # m^2 per cell -> km^2

_log = logging.getLogger(__name__)


def _valid_dem_mask(dem_raw: np.ndarray, dem_nodata) -> np.ndarray:
    """Valid DEM cells: finite AND != nodata. Single source of truth for every terrain guard."""
    valid = np.isfinite(dem_raw)
    if dem_nodata is not None:
        # FM-12: pysheds defaults an undeclared nodata to 0 -- 0-fill must never count as terrain.
        valid &= (dem_raw != dem_nodata)
    return valid


def assert_contour_in_dem_range(dem_raw: np.ndarray, dem_nodata, *,
                                contour_m: float = CONTOUR_M) -> None:
    """Fail loud unless the contour (m) falls inside the DEM's VALID elevation range (A25).
    Catches a wrong-fire contour only, not geomorphic correctness. Range over valid cells --
    counting nodata-as-0 fill would make the check trivially pass (FM-12)."""
    valid = _valid_dem_mask(dem_raw, dem_nodata)
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


HYPSOMETRIC_SPAN_THRESHOLD_M = 50.0  # A27-frozen; never tuned, never per-fire, no override


def assess_hypsometric_applicability(dem_raw: np.ndarray, dem_nodata) -> dict:
    """A27 terrain pre-check: refuse iff valid-cell (p10 - p1) span (m) > the frozen 50 m threshold.
    FIREWALL: classifies only -- returns no absolute elevation, no contour value. On refuse the
    caller ROUTES to the WBT sub-basin engine (A39)."""
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


def stage_2b_outlets(acc, fdir, dem_raw, shape, *, contour_m: float = CONTOUR_M) -> list[tuple[int, int]]:
    """Canyon-mouth outlets: channel cells (acc > threshold) crossing the contour (m) going
    downhill. Contour test on RAW terrain; routing on conditioned-DEM fdir. Returns (row, col)s."""
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


def stage_2c_delineate(grid, acc, fdir_raster, transform, shape, outlets, asset_xy):
    """Delineate per outlet, discard < MIN_BASIN_KM2, keep asset-draining (<= 600 m), dedup
    (larger basins claim cells first; deterministic tie-breaks)."""
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
