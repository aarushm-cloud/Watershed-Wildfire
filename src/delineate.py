"""delineate.py -- canyon-mouth outlet detection and upslope catchment
delineation in INDEX mode (row, col); discard tiny catchments, keep only
asset-draining ones, larger basins claim cells first. See ARCHITECTURE.md
and FAILURE_MODES FM-1.

P1.4 SCOPE (behavior-preserving extract from validation/gate.py stages 2b+2c): the two
contiguous delineation functions, lifted VERBATIM. EXPLICIT-ARGS signatures (not the hydro
bag) -- the caller (gate) unpacks hydro at the call site, so the dict-key coupling stays in
gate, not here. Deliberately NOT here (stay in gate): scoring + mean_burn/mean_slope (2e),
_burn_weight_raster + A18 coverage, evaluate (2f), and the whole-domain master-outlet /
classify_master_zone block (2a). No new types/dataclasses (C9); basins stay loose dicts.

FM-1: the per-basin grid.catchment(...) runs in INDEX mode (xytype="index", x=col, y=row,
integer coords) -- coordinate mode silently returns 0 km^2. Carried byte-for-byte, with both
0-km^2 guards intact. The claim-order sort + shared-mask mutation are load-bearing (reordering
silently changes which basin wins a contested cell) -- preserved exactly.

IMPORT-TIME I/O BAN: nothing executes at module load except the pure-arithmetic CELL_AREA_KM2
derivation; no filesystem access. Imports numpy + scipy.cKDTree (third-party) and config/grids.
"""
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# 2b -- canyon-mouth outlets
# ---------------------------------------------------------------------------
def stage_2b_outlets(acc, fdir, dem_raw, shape) -> list[tuple[int, int]]:
    """Channel cells (acc > threshold) that cross the 150 m contour going downhill.

    A channel cell with raw elevation >= CONTOUR_M whose D8-downstream neighbour's raw
    elevation is < CONTOUR_M is a canyon-mouth outlet (VALIDATION_REPORT s3.2). Contour
    test on RAW terrain; routing on conditioned-DEM fdir. Returns (row, col) tuples.

    Args (explicit, from hydro at the gate call site): acc -- flow-accumulation array;
    fdir -- flow-direction ARRAY (np.asarray(fdir); for the integer fdir[r,c] lookup);
    dem_raw -- raw metric DEM (m); shape -- (nrows, ncols)."""
    nrows, ncols = shape
    channel = acc > ACC_THRESHOLD_CELLS

    outlets: list[tuple[int, int]] = []
    cand_rows, cand_cols = np.where(channel & (dem_raw >= CONTOUR_M))
    for r, c in zip(cand_rows.tolist(), cand_cols.tolist()):
        off = D8_OFFSETS.get(int(fdir[r, c]))
        if off is None:
            continue
        nr, nc = r + off[0], c + off[1]
        if 0 <= nr < nrows and 0 <= nc < ncols and dem_raw[nr, nc] < CONTOUR_M:
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
        kept.append({"outlet": b["outlet"], "mask": own,
                     "area_km2": own_km2, "asset_m": b["asset_m"]})

    # stable basin_id by outlet (row, col)
    kept.sort(key=lambda b: (b["outlet"][0], b["outlet"][1]))
    for i, b in enumerate(kept):
        b["basin_id"] = i
    return kept
