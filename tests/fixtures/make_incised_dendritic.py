"""make_incised_dendritic.py -- generate the committed DENDRITIC incised-terrain DEM fixture.

WHAT THIS IS (read before trusting the numbers it makes): a larger, fully-deterministic,
SYNTHETIC DEM used to exercise the A39 sub-basin ROUTER end-to-end (segmentation -> phase-1
geometry -> phase-2 burn/slope filter -> ranked result). `incised_synthetic.tif` (see
make_incised_fixture.py) is a single triangular channel built only to trip the A27 *detector*;
it has no tributaries, so WhiteboxTools' `subbasins` returns all-zero labels at the FROZEN
SUBBASIN_ACC_THRESHOLD_CELLS = 3000 and cannot back a *ranked* fixture. This fixture is simply
LARGE ENOUGH, with real branching tributaries, that a dendritic network forms multiple
3000-cell accumulation streams at that same frozen threshold. TUNES NOTHING FROZEN: the
segmentation threshold, burn-fraction floor, slope floor, and minimum basin area all stay at
their src/config.py values; only this synthetic terrain (test data) is sized to exercise them.

Construction: a plateau that falls gently along the row axis, V-walls down to a central trunk
(so cross-sections look like a valley, not a flat plain), an incised trunk channel, and eight
tributary notches alternating left/right off the trunk (a simple dendritic pattern) so
segmentation has real confluences to split on. Fully analytic, NO randomness, so the written
GeoTIFF is byte-reproducible and its SHA256 is stable.

Validated by the controller (A39 Task 8 fixture recipe) at the frozen thresholds:
  incised detection: refuse=True, span_m ~= 132 m (> the frozen 50 m HYPSOMETRIC_SPAN_THRESHOLD_M)
  segment_subbasins (frozen SUBBASIN_ACC_THRESHOLD_CELLS=3000) -> 23 raw sub-basins
  build_geometry_records -> 9 phase-1 records (>= MIN_BASIN_KM2, interior only)
  phase-2 filter_burned_steep (with a synthetic dNBR burning the upper half) -> 4 phase-2 basins

Run:  python tests/fixtures/make_incised_dendritic.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

# --- fixed grid (metric CRS so the array reads as real terrain; values are what matter) ----------
CRS = "EPSG:32613"              # matches conftest's incised_fire expected_crs
CELL_M = 10.0                   # 10 m cells, matches the pipeline's CELL_M
NROWS, NCOLS = 300, 240         # large enough for a real dendritic 3000-cell network
X0, Y0 = 400000.0, 3700000.0    # fixed UTM origin (upper-left)
NODATA = -9999.0

OUT_TIF = Path(__file__).resolve().parent / "incised_dendritic.tif"


def build_elevation():
    """Analytic dendritic-incised elevation field (m), float32, with a 1-cell nodata border.

    A plateau falling gently down-row, V-walls in from both sides toward a central trunk
    column, the trunk itself incised well below the walls, and eight tributary notches
    (alternating side, each stepping diagonally in from the wall toward the trunk) so the
    trunk + tributaries form a real branching (dendritic) network rather than one channel.
    """
    rows = np.arange(NROWS)[:, None].astype(float)
    cols = np.arange(NCOLS)[None, :].astype(float)
    mc = NCOLS / 2.0
    elev = 1700.0 - rows * 1.6 + np.abs(cols - mc) * 3.0   # plateau, V-walls to the trunk, axial fall
    tc = int(mc)
    elev[:, tc - 1:tc + 2] -= 90.0                          # trunk incision
    for k, r0 in enumerate(range(24, NROWS - 24, 20)):      # dendritic tributaries, alternating sides
        side = 1 if k % 2 == 0 else -1
        length = int(mc) - 4
        for t in range(length):
            rr = int(r0 + t * 0.6)
            cc = tc - side * (length - t)
            if 0 <= rr < NROWS and 0 <= cc < NCOLS:
                elev[rr, cc] -= 55.0
                if 0 <= cc - 1 < NCOLS:
                    elev[rr, cc - 1] -= 25.0
                if 0 <= cc + 1 < NCOLS:
                    elev[rr, cc + 1] -= 25.0
    elev = elev.astype(np.float32)

    # 1-cell nodata border to exercise the valid-cell masking (FM-12 / _valid_dem_mask).
    elev[0, :] = NODATA
    elev[-1, :] = NODATA
    elev[:, 0] = NODATA
    elev[:, -1] = NODATA
    return elev


def write_fixture():
    elev = build_elevation()
    transform = from_origin(X0, Y0, CELL_M, CELL_M)
    profile = {
        "driver": "GTiff", "dtype": "float32", "count": 1,
        "height": NROWS, "width": NCOLS, "crs": CRS,
        "transform": transform, "nodata": NODATA,
    }
    with rasterio.open(OUT_TIF, "w", **profile) as dst:
        dst.write(elev, 1)
    return elev


def report(elev):
    valid = np.isfinite(elev) & (elev != NODATA)
    vals = elev[valid].astype(np.float64)
    p1, p10 = np.percentile(vals, [1, 10], method="linear")
    span = float(p10 - p1)
    sha = hashlib.sha256(OUT_TIF.read_bytes()).hexdigest()
    print(f"wrote {OUT_TIF}")
    print(f"  shape={elev.shape}  n_valid={int(valid.sum())}  nodata={NODATA}")
    print(f"  p1={p1:.4f} m  p10={p10:.4f} m  span_m={span:.4f} m  (> 50 m: {span > 50.0})")
    print(f"  sha256={sha}")


if __name__ == "__main__":
    report(write_fixture())
