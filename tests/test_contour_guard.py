"""P3.3 CONTOUR_M guard known-answer tests (A25 carve-out, council Q3).

The contour test in delineate.stage_2b_outlets keys off CONTOUR_M (config, 150 m for Montecito):
a channel cell at elev >= CONTOUR_M whose downstream neighbour is < CONTOUR_M is a canyon mouth.
If CONTOUR_M is the WRONG fire's value for this DEM -- e.g. the 150 m Montecito contour on South
Fork's 1976-3312 m DEM -- the contour sits entirely below the terrain, NO cell straddles it, and
stage_2b_outlets either finds zero outlets (confusing "zero outlets" abort) or wrong ones. This
guard turns that gross mis-set into a CLEAR fail-loud abort.

SCOPE (honest): catches a contour ENTIRELY OUTSIDE the DEM's valid elevation range only -- NOT
geomorphic correctness. An in-range-but-wrong contour still passes (out of A25 scope).

THE DECISIVE CASE is the nodata-as-0 trap (FM-12): pysheds defaults an undeclared nodata to 0, so
if 0-fill cells are counted as valid terrain the min collapses to 0 and `0 <= 150 <= 3312` passes
-- the guard silently never fires. The guard MUST compute min/max over genuinely valid cells only.

Run:  pytest tests/test_contour_guard.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.delineate import assert_contour_in_dem_range
from src.grids import GateAbort


def test_in_range_contour_passes():
    """Montecito-like: terrain spans 0..1199 m; 150 m contour is in range -> no abort."""
    dem = np.linspace(0.0, 1199.0, 100).reshape(10, 10).astype("float64")
    assert_contour_in_dem_range(dem, dem_nodata=0, contour_m=150)   # must NOT raise


def test_contour_below_dem_min_fires():
    """South Fork-like: terrain 1976..3312 m; the 150 m contour is below the DEM minimum -> abort."""
    dem = np.linspace(1976.0, 3312.0, 100).reshape(10, 10).astype("float64")
    with pytest.raises(GateAbort, match="CONTOUR_M"):
        assert_contour_in_dem_range(dem, dem_nodata=-9999.0, contour_m=150)


def test_contour_above_dem_max_fires():
    """A contour above the highest terrain also fails loud (symmetry)."""
    dem = np.linspace(0.0, 1199.0, 100).reshape(10, 10).astype("float64")
    with pytest.raises(GateAbort, match="CONTOUR_M"):
        assert_contour_in_dem_range(dem, dem_nodata=None, contour_m=5000)


def test_nodata_as_zero_trap_does_not_defeat_the_guard():
    """FM-12 DECISIVE CASE: real terrain 1976..3312 m with a block of nodata-as-0 fill.

    With nodata=0 correctly excluded, the valid min is 1976 -> the 150 m contour is below it ->
    the guard FIRES. (If the 0-fill leaked in, min would be 0 and the guard would silently pass.)
    """
    dem = np.full((10, 10), 2500.0, dtype="float64")   # real terrain (m)
    dem[0:3, 0:3] = 0.0                                 # pysheds nodata-as-0 fill (FM-12)
    with pytest.raises(GateAbort, match="CONTOUR_M"):
        assert_contour_in_dem_range(dem, dem_nodata=0, contour_m=150)


def test_mask_is_load_bearing_negative_control():
    """Non-vacuous control: the SAME array, but if the 0-fill is NOT masked (nodata=None), min
    collapses to 0 and the guard would WRONGLY pass -- proving the nodata mask is what makes the
    guard bite. This documents the trap; it is not the production call (which passes the sentinel)."""
    dem = np.full((10, 10), 2500.0, dtype="float64")
    dem[0:3, 0:3] = 0.0
    assert_contour_in_dem_range(dem, dem_nodata=None, contour_m=150)   # passes ONLY because mask defeated


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
