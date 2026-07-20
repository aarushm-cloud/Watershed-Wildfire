"""A39 -- WhiteboxTools whole-network sub-basin delineation for incised terrain.

Range-front fires do NOT use this module; they keep the pysheds canyon-mouth path.
Incised terrain has no mountain front to anchor an outlet on, so basins are split at
channel confluences instead. Conditioning is breach-carve rather than fill: filling raises
an incised canyon floor to its spill level and smears the channel, which is the specific
failure mode on this terrain.

Construction is two-phase because the pipeline computes burn weights and slope AFTER
delineation:
  segment_subbasins()      -- needs only the DEM; runs at the delineation site
  build_geometry_records() -- needs only the DEM; runs at the delineation site
  filter_burned_steep()    -- needs burn + slope; runs after the dNBR ingest
"""
import shutil
from pathlib import Path

import numpy as np
import rasterio

from src.config import (CELL_M, MIN_BASIN_KM2, SUBBASIN_ACC_THRESHOLD_CELLS,
                        SUBBASIN_BREACH_DIST_CELLS, SUBBASIN_BURN_FRAC_MIN,
                        SUBBASIN_SLOPE_FLOOR_TAN)
from src.delineate import _valid_dem_mask   # A33: single source of truth for terrain cells
from src.grids import GateAbort

_CELL_AREA_KM2 = (CELL_M * CELL_M) / 1e6

# Every file this function writes into work_dir. Used to purge stale output before a run.
_WORK_FILES = ("dem.tif", "dem_breached.tif", "d8.tif", "acc.tif", "streams.tif", "subbasins.tif")


def segment_subbasins(dem_tif, work_dir, *,
                      acc_threshold_cells=SUBBASIN_ACC_THRESHOLD_CELLS):
    """Delineate whole-network sub-basins. Returns (labels int32, meta dict)."""
    import whitebox

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    # work_dir is caller-supplied and reused across re-runs of the same fire (retry, demo prep,
    # re-parameterising), so purge only this function's own known filenames rather than the
    # whole directory -- we don't own the directory itself, only these files, and a broad
    # rmtree/clear would be destructive to a path we don't control. Without this, a WBT step
    # that silently no-ops (see _run below) would leave a prior run's file sitting there, and it
    # would be read back below as this run's output (A8 -- fail loud, never fabricate).
    for name in _WORK_FILES:
        (work / name).unlink(missing_ok=True)

    shutil.copyfile(dem_tif, work / "dem.tif")

    wbt = whitebox.WhiteboxTools()
    wbt.set_working_dir(str(work))
    wbt.set_verbose_mode(False)

    def _run(fn, *args, out, **kwargs):
        rc = fn(*args, **kwargs)
        if rc != 0:
            raise GateAbort(
                f"FAIL: WhiteboxTools {fn.__name__} returned {rc} -- sub-basin delineation "
                f"aborted (A39). Do NOT proceed on a partial hydrology product.")
        # whitebox==2.3.6's run_tool() reads the subprocess's stdout to completion and then
        # returns 0 UNCONDITIONALLY -- it never inspects the WBT binary's exit code. rc!=0 only
        # ever fires on a Python-level OSError/ValueError/CalledProcessError or a user cancel, so
        # a Rust-side panic that writes nothing still reports rc==0. Require the expected output
        # file to actually exist, or fail loud instead of proceeding on a partial product (A8).
        if not (work / out).exists():
            raise GateAbort(
                f"FAIL: WhiteboxTools {fn.__name__} returned 0 but did not write its expected "
                f"output {out!r} -- sub-basin delineation aborted (A39). Do NOT proceed on a "
                f"partial hydrology product.")

    _run(wbt.breach_depressions_least_cost, "dem.tif", "dem_breached.tif",
         dist=SUBBASIN_BREACH_DIST_CELLS, fill=True, out="dem_breached.tif")
    _run(wbt.d8_pointer, "dem_breached.tif", "d8.tif", out="d8.tif")
    _run(wbt.d8_flow_accumulation, "dem_breached.tif", "acc.tif", out_type="cells",
         out="acc.tif")
    _run(wbt.extract_streams, "acc.tif", "streams.tif",
         threshold=float(acc_threshold_cells), out="streams.tif")
    _run(wbt.subbasins, "d8.tif", "streams.tif", "subbasins.tif", out="subbasins.tif")

    with rasterio.open(work / "subbasins.tif") as ds:
        labels = ds.read(1)
    labels = np.where(labels < 0, 0, labels).astype(np.int32)

    with rasterio.open(work / "acc.tif") as ds:
        acc = ds.read(1)

    meta = {"engine": "whiteboxtools",
            "wbt_version": (wbt.version() or "").splitlines()[0],
            "acc_threshold_cells": int(acc_threshold_cells),
            "breach_dist_cells": SUBBASIN_BREACH_DIST_CELLS,
            "_acc": acc}
    return labels, meta


def _footprint_edge_ids(labels, valid):
    """Labels touching the raster border OR abutting invalid terrain.

    Border-only checking is not enough: a basin truncated against an interior nodata hole
    (a DEM that does not cover the whole burn) silently yields partial area and burn stats.
    """
    ids = set(np.unique(np.concatenate(
        [labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])).tolist())
    invalid = ~valid
    if invalid.any():
        near = np.zeros_like(invalid)
        near[1:, :] |= invalid[:-1, :]
        near[:-1, :] |= invalid[1:, :]
        near[:, 1:] |= invalid[:, :-1]
        near[:, :-1] |= invalid[:, 1:]
        ids |= set(np.unique(labels[near & (labels > 0)]).tolist())
    ids.discard(0)
    return ids


def build_geometry_records(labels, dem_raw, dem_nodata, acc):
    """Phase 1: DEM-only sub-basin records. Burn/slope filtering happens in phase 2.

    `asset_m` is always None -- the drains-to-asset filter does not apply on incised
    terrain (A39), where there is no depositional plain for assets to sit on.
    """
    valid = _valid_dem_mask(dem_raw, dem_nodata)
    edge_ids = _footprint_edge_ids(labels, valid)

    kept = []
    for lab_id in np.unique(labels):
        if lab_id == 0 or lab_id in edge_ids:
            continue
        mask = (labels == lab_id) & valid
        n = int(mask.sum())
        if n == 0 or n * _CELL_AREA_KM2 < MIN_BASIN_KM2:
            continue
        flat = int(np.argmax(np.where(mask, acc, -np.inf)))
        frozen = mask.copy()
        frozen.flags.writeable = False
        kept.append({"outlet": (flat // mask.shape[1], flat % mask.shape[1]),
                     "mask": frozen, "area_km2": n * _CELL_AREA_KM2,
                     "asset_m": None, "basin_id": -1})

    kept.sort(key=lambda r: r["outlet"])
    for i, rec in enumerate(kept):
        rec["basin_id"] = i
    return kept


def filter_burned_steep(records, burn_weight, slope_tan):
    """Phase 2: keep sub-basins that are meaningfully burned and not degenerately flat.

    A "burned" cell is burn_weight > 0, which reuses the frozen burn binning (dNBR >= 0.100
    AND valid) rather than introducing a second severity threshold. Note that clouded or
    NoData cells therefore count as unburned -- conservative, and consistent with the
    frozen ingest.

    basin_id is renumbered contiguously so downstream consumers see a dense 0..n-1 range.
    """
    kept = []
    for rec in records:
        m = rec["mask"]
        bw = burn_weight[m]
        st = slope_tan[m]
        finite = np.isfinite(bw) & np.isfinite(st)
        if not finite.any():
            continue
        burn_frac = float((bw[finite] > 0).mean())
        if burn_frac < SUBBASIN_BURN_FRAC_MIN:
            continue
        if float(np.mean(st[finite])) < SUBBASIN_SLOPE_FLOOR_TAN:
            continue
        out = dict(rec)
        out["burn_frac"] = burn_frac
        kept.append(out)

    for i, rec in enumerate(kept):
        rec["basin_id"] = i
    return kept
