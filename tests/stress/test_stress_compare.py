"""Comparison statistics on synthetic rasters -- no network, no real data."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from validation.stress_compare import (
    compare_aligned,
    compare_regridded,
    compare_same_grid,
    shuffled_null,
)

NODATA = -9999.0


def _write(path, arr, *, transform=None, crs="EPSG:32613"):
    transform = transform if transform is not None else from_origin(400000, 3700000, 30, 30)
    with rasterio.open(
        path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
        count=1, dtype="float32", crs=crs, transform=transform, nodata=NODATA,
    ) as dst:
        dst.write(arr.astype("float32"), 1)
    return path


# --------------------------------------------------------------------------
# compare_same_grid
# --------------------------------------------------------------------------

def test_identical_rasters_score_a_perfect_ceiling(tmp_path):
    rng = np.random.default_rng(0)
    arr = rng.normal(0.3, 0.2, size=(40, 40))
    a = _write(tmp_path / "a.tif", arr)
    b = _write(tmp_path / "b.tif", arr.copy())

    out = compare_same_grid(a, b)

    assert out["n_covalid"] == 1600
    assert out["pearson_r"] == pytest.approx(1.0, abs=1e-6)
    assert out["spearman_rho"] == pytest.approx(1.0, abs=1e-6)
    assert out["max_abs_diff"] == pytest.approx(0.0, abs=1e-6)
    assert out["regridded"] is False


def test_unrelated_rasters_establish_a_near_zero_floor(tmp_path):
    rng = np.random.default_rng(1)
    a = _write(tmp_path / "a.tif", rng.normal(0.3, 0.2, size=(60, 60)))
    b = _write(tmp_path / "b.tif", rng.normal(0.3, 0.2, size=(60, 60)))

    out = compare_same_grid(a, b)

    assert abs(out["spearman_rho"]) < 0.15, "independent noise must not correlate"


def test_nodata_pixels_are_excluded_from_every_statistic(tmp_path):
    arr_a = np.full((10, 10), 0.5)
    arr_b = np.full((10, 10), 0.5)
    arr_a[0, :] = NODATA
    arr_b[:, 0] = NODATA
    a = _write(tmp_path / "a.tif", arr_a)
    b = _write(tmp_path / "b.tif", arr_b)

    out = compare_same_grid(a, b)

    # 100 total, minus row 0 (10) and column 0 (10), plus the double-counted corner
    assert out["n_covalid"] == 81
    assert out["max_abs_diff"] == pytest.approx(0.0)


def test_constant_arrays_report_undefined_correlation_not_zero(tmp_path):
    """Zero variance means correlation is undefined. Reporting 0.0 would read as
    'no agreement' when the rasters are in fact identical."""
    a = _write(tmp_path / "a.tif", np.full((8, 8), 0.4))
    b = _write(tmp_path / "b.tif", np.full((8, 8), 0.4))

    out = compare_same_grid(a, b)

    assert out["pearson_r"] is None
    assert out["spearman_rho"] is None
    assert out["max_abs_diff"] == pytest.approx(0.0)


def test_mismatched_grids_abort_rather_than_silently_resampling(tmp_path):
    a = _write(tmp_path / "a.tif", np.zeros((10, 10)))
    b = _write(tmp_path / "b.tif", np.zeros((10, 10)),
               transform=from_origin(400015, 3700000, 30, 30))   # half-pixel shift

    with pytest.raises(ValueError, match="grid mismatch"):
        compare_same_grid(a, b)


# --------------------------------------------------------------------------
# compare_aligned -- whole-pixel offset, no resampling
# --------------------------------------------------------------------------

def test_aligned_grids_offset_by_whole_pixels_compare_on_their_intersection(tmp_path):
    """Gate 0's real case: same CRS and resolution, window offset by whole pixels."""
    rng = np.random.default_rng(2)
    big = rng.normal(0.3, 0.2, size=(30, 30))
    a = _write(tmp_path / "a.tif", big)
    # b starts 5 px east / 5 px south of a and is smaller -- a strict sub-window.
    b = _write(tmp_path / "b.tif", big[5:25, 5:25],
               transform=from_origin(400000 + 5 * 30, 3700000 - 5 * 30, 30, 30))

    out = compare_aligned(a, b)

    assert out["n_covalid"] == 400
    assert out["pearson_r"] == pytest.approx(1.0, abs=1e-6)
    assert out["max_abs_diff"] == pytest.approx(0.0, abs=1e-6)
    assert out["regridded"] is False
    assert out["offset_px"] == (5, 5)


def test_aligned_rejects_a_sub_pixel_offset(tmp_path):
    """A half-pixel shift is NOT alignment -- comparing it would need resampling."""
    a = _write(tmp_path / "a.tif", np.zeros((10, 10)))
    b = _write(tmp_path / "b.tif", np.zeros((10, 10)),
               transform=from_origin(400015, 3700000, 30, 30))

    with pytest.raises(ValueError, match="not pixel-aligned"):
        compare_aligned(a, b)


def test_aligned_rejects_a_resolution_difference(tmp_path):
    a = _write(tmp_path / "a.tif", np.zeros((10, 10)))
    b = _write(tmp_path / "b.tif", np.zeros((10, 10)),
               transform=from_origin(400000, 3700000, 20, 20))

    with pytest.raises(ValueError, match="resolution"):
        compare_aligned(a, b)


def test_aligned_rejects_disjoint_grids(tmp_path):
    a = _write(tmp_path / "a.tif", np.zeros((10, 10)))
    b = _write(tmp_path / "b.tif", np.zeros((10, 10)),
               transform=from_origin(400000 + 500 * 30, 3700000, 30, 30))

    with pytest.raises(ValueError, match="do not overlap"):
        compare_aligned(a, b)


# --------------------------------------------------------------------------
# compare_regridded
# --------------------------------------------------------------------------

def test_regridded_comparison_flags_its_own_caveat(tmp_path):
    arr = np.linspace(0, 1, 400).reshape(20, 20)
    a = _write(tmp_path / "a.tif", arr)
    b = _write(tmp_path / "b.tif", arr, transform=from_origin(400015, 3700000, 30, 30))

    out = compare_regridded(a, b)

    assert out["regridded"] is True
    assert out["caveat"] is not None
    assert out["n_covalid"] > 0


def test_regridded_across_different_crs(tmp_path):
    """Cooks Peak's reference is 20 m UTM 13N; a Landsat build would be 30 m."""
    rng = np.random.default_rng(3)
    a = _write(tmp_path / "a.tif", rng.normal(0.3, 0.2, size=(30, 30)))
    b = _write(tmp_path / "b.tif", rng.normal(0.3, 0.2, size=(45, 45)),
               transform=from_origin(400000, 3700000, 20, 20))

    out = compare_regridded(a, b)

    assert out["regridded"] is True
    assert out["n_covalid"] > 0


# --------------------------------------------------------------------------
# shuffled_null -- the fallback floor anchor
# --------------------------------------------------------------------------

def test_shuffled_null_is_near_zero(tmp_path):
    """When two fires' scars do not overlap, n_covalid is 0 and a cross-fire floor
    is unobtainable. The within-fire shuffled null substitutes for it."""
    rng = np.random.default_rng(4)
    a = _write(tmp_path / "a.tif", rng.normal(0.3, 0.2, size=(50, 50)))

    out = shuffled_null(a, seed=7)

    assert out["n_covalid"] == 2500
    assert abs(out["spearman_rho"]) < 0.1
    assert "shuffled" in out["caveat"].lower()


def test_shuffled_null_is_deterministic_given_a_seed(tmp_path):
    rng = np.random.default_rng(5)
    a = _write(tmp_path / "a.tif", rng.normal(0.3, 0.2, size=(30, 30)))

    assert shuffled_null(a, seed=11)["spearman_rho"] == \
           shuffled_null(a, seed=11)["spearman_rho"]
