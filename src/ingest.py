"""ingest.py -- the front door: load inputs, select the ONE burn source, remap to per-cell
weights + coverage, stamp provenance. One source per run, never blended (A2/A3/A15).

Two burn entries, dispatched by the pipeline on the fire config: ingest_burn (SBS) and
ingest_dnbr_both_arms (dNBR, A34).
"""
from __future__ import annotations

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.warp import reproject, Resampling
from pysheds.grid import Grid

from src.config import BURN_WEIGHTS, DNBR_BIN_EDGES, DNBR_CLAMP, DNBR_FLOOR
from src.grids import GateAbort, assert_aligned


# BAER SBS codeset: 1-4 = severity, 0 = Developed, 15 = outside-perimeter/NoData. Codeset
# membership -- not the GDAL mask -- defines validity (owner decision 2026-06-16).
SBS_CODESET = (0, 1, 2, 3, 4, 15)

DNBR_CLASS15 = 15        # non-covered sentinel (reuses the SBS class-15 encoding)
DNBR_NODATA = -9999.0    # reproject fill for uncovered/NoData cells; far outside the raw dNBR range


def load_dem(path):
    """Load the DEM -> (pysheds Grid, pysheds Raster, raw float64 elevation array (m))."""
    grid = Grid.from_raster(str(path))
    dem = grid.read_raster(str(path))
    dem_raw = np.asarray(dem, dtype=np.float64).copy()  # raw terrain elevation (m)
    return grid, dem, dem_raw


def load_burn(path):
    """Load burn raster band 1 as the raw SBS class array (no remap)."""
    with rasterio.open(path) as s:
        return s.read(1)


def load_assets(path):
    """Load the asset (building) layer as a GeoDataFrame."""
    return gpd.read_file(path)


def load_creeks(path):
    """Load the truth creek/channel layer as a GeoDataFrame."""
    return gpd.read_file(path)


def select_burn_source(sbs: np.ndarray) -> str:
    """A3 precedence: "SBS" iff every cell is in-codeset (class 15 counts as covered), else "dNBR"."""
    n_invalid = int((~np.isin(sbs, SBS_CODESET)).sum())
    if n_invalid == 0:
        return "SBS"
    return "dNBR"


def _burn_weight_raster(sbs: np.ndarray):
    """Per-cell (wt, covered): classes 1-4 -> BURN_WEIGHTS, 0/15 -> 0.0 INCLUDED in the mean
    (A17); covered = class in {1,2,3,4} (A18), flag-only."""
    wt = np.zeros(sbs.shape, dtype=np.float64)
    for cls, w in BURN_WEIGHTS.items():
        wt[sbs == cls] = w
    covered = np.isin(sbs, (1, 2, 3, 4))
    return wt, covered


def ingest_burn(burn_path):
    """The SBS seam (A15): select, load, remap to (wt, covered), stamp provenance. FAILS LOUD
    (A29) on a non-SBS selection -- dNBR runs enter through ingest_dnbr_both_arms, never here."""
    sbs = load_burn(burn_path)
    burn_source = select_burn_source(sbs)
    if burn_source != "SBS":
        # A29: scoring SBS-derived weights under a dNBR stamp would be a silent mislabel.
        raise GateAbort(
            f"ingest_burn: burn-source selection returned {burn_source!r}, but the dNBR end-to-end "
            "arm is built and unit-tested yet NOT wired into ingest_burn (P2.2c pending). Refusing to "
            f"stamp {burn_source!r} provenance while scoring SBS-derived weights. Wire the dNBR "
            "dispatch (ingest_dnbr_both_arms) before running a fire without full SBS coverage."
        )
    wt, covered = _burn_weight_raster(sbs)
    provenance = {"burn_source": burn_source}   # A4: the single stamp, read everywhere
    return wt, covered, provenance


def reproject_dnbr(native_path, dem_profile, resampling):
    """Reproject native dNBR onto the canonical DEM grid (pinned form, pre-reg frozen).
    Snaps via the DEM's EXPLICIT dst_transform/shape -- never the scene's own grid (a half-pixel
    offset is the silent mis-georeference class, FM-15). Returns (float32 RAW dNBR, profile)."""
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
    """Arm A (primary): bin RAW dNBR -> SBS 4-class via the frozen 5->4 collapse, then reuse
    _burn_weight_raster. Returns (wt, covered, cls)."""
    # NaN trap: `NaN < 0.100` is False, so an unmasked NaN would digitize into the TOP bin --
    # invalid cells are replaced with a below-floor sentinel BEFORE np.digitize, set to 15 AFTER.
    safe = np.where(valid, dnbr, -1.0).astype("float64")
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
    """Arm B (companion): linear transfer wt = (clip(dNBR, lo, hi) - lo) / (hi - lo); below-floor
    and invalid -> non-covered, weight 0.0. Returns (wt, covered)."""
    lo, hi = DNBR_CLAMP
    arr = np.asarray(dnbr, dtype="float64")
    b = np.clip(np.where(valid, arr, lo), lo, hi)        # invalid -> lo so the map stays finite
    wt = (b - lo) / (hi - lo)                            # linear [0,1]; lo maps to exactly 0.0
    covered = np.asarray(valid, dtype=bool) & (arr >= DNBR_FLOOR)   # below-floor + invalid -> non-covered
    wt = np.where(covered, wt, 0.0)                      # non-covered cells contribute 0.0 (A17)
    return wt, covered


def ingest_dnbr_both_arms(native_path, dem_profile):
    """The dNBR path end-to-end: reproject both arms (A = nearest, B = bilinear), derive ONE
    shared valid footprint (from Arm A, applied to both -- footprints identical by
    construction), normalize each arm. Same (wt, covered) handoff as the SBS path."""
    dnbr_a, prof_a = reproject_dnbr(native_path, dem_profile, Resampling.nearest)
    dnbr_b, prof_b = reproject_dnbr(native_path, dem_profile, Resampling.bilinear)

    assert_aligned(dem_profile, prof_a, other_name="dNBR-A", expected_crs=dem_profile["crs"])
    assert_aligned(dem_profile, prof_b, other_name="dNBR-B", expected_crs=dem_profile["crs"])

    # One shared valid footprint; no NaN/sentinel may survive into it for either arm (A8).
    valid = (dnbr_a != DNBR_NODATA) & np.isfinite(dnbr_a)
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
