"""make_incised_fixture.py -- generate the committed synthetic incised-terrain DEM fixture.

WHAT THIS IS (read before trusting the number it makes): a tiny, fully-deterministic, SYNTHETIC
DEM that illustrates incised-valley terrain so the A27 refusal can be exercised HERMETICALLY by any
reviewer and by CI -- the real South Fork DEM is gitignored and cannot be assumed present on a clean
checkout. The fixture's valid-cell (p10 - p1) span is a PROPERTY OF THIS CONSTRUCTED PROFILE; it is
NOT a stand-in for South Fork's measured span and it TUNES NOTHING. The A27 trigger (50 m) is frozen
in src/delineate.py and is not touched here. This file plays the same role as Test C's synthetic
arrays, persisted as a raster.

Construction: a high plateau with a deep, narrow channel incised into it (the channel floor also
tilts gently along the axis). Most cells sit on the high plateau; only the few cells in the narrow
incision reach far below it -- the bottom-heavy-sparse hypsometry of dissected upland. So the valid
low tail climbs steeply and the valid-cell (p10 - p1) span lands unambiguously above the frozen 50 m
threshold (it comes out ~70.6 m here; the exact value is whatever the analytic field yields). No flat
depositional plain anywhere. Fully analytic, NO randomness, so the written GeoTIFF is byte-reproducible
and its SHA256 is stable (pinned in the A27 test).

Run:  python tests/fixtures/make_incised_fixture.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

# --- fixed grid (metric CRS so the array reads as real terrain; values are what matter) ----------
CRS = "EPSG:32613"          # a valid UTM (metres); arbitrary but real
CELL_M = 10.0               # 10 m cells
NROWS, NCOLS = 40, 80       # tiny -> a few KB
X0, Y0 = 400000.0, 3700000.0   # fixed UTM origin (upper-left)
NODATA = -9999.0

OUT_TIF = Path(__file__).resolve().parent / "incised_synthetic.tif"


def build_elevation():
    """Analytic incised-upland elevation field (m), float32, with a 1-cell nodata border.

    elevation(i, j) = PLATEAU + AXIAL * j - notch(i), where
      notch(i) = DEPTH * max(0, 1 - d/HALFWIDTH),  d = |i - center_row|
    i.e. a high plateau (gently tilted along the axis by AXIAL) with a deep, narrow channel of
    half-width HALFWIDTH incised DEPTH metres into it. Most cells stay on the plateau; only the few
    channel cells reach far below -- the bottom-heavy-sparse hypsometry of dissected upland, so the
    valid low tail climbs steeply and (p10 - p1) is wide. Constants chosen so the valid-cell span
    sits comfortably above 50 m (~70.6 m; illustration only, tunes nothing frozen).
    """
    PLATEAU = 1600.0        # plateau elevation (m)
    AXIAL = 0.3             # m per column -- gentle axial tilt of the plateau + channel floor
    DEPTH = 270.0           # m the channel is incised below the plateau at its axis
    HALFWIDTH = 5.0         # rows from the channel axis to the plateau rim (narrow incision)

    rows = np.arange(NROWS, dtype=np.float64)[:, None]      # (NROWS, 1)
    cols = np.arange(NCOLS, dtype=np.float64)[None, :]      # (1, NCOLS)
    center_row = (NROWS - 1) / 2.0
    d = np.abs(rows - center_row)                          # rows from the channel axis (runs along cols)
    notch = DEPTH * np.maximum(0.0, 1.0 - d / HALFWIDTH)   # (NROWS, 1) triangular incision
    elev = (PLATEAU + AXIAL * cols - notch).astype(np.float32)   # broadcast -> (NROWS, NCOLS)

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
