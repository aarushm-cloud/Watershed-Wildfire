"""CF-B (A33, R1 -- override of the 2026-07-06 deferral, owner 2026-07-07): mean_slope_tan drops the
nodata-adjacent ring so a coastal basin is not silently score-inflated.

A valid land cell next to a 0-clamped nodata cell (FM-12) reads a spurious cliff under np.gradient --
the contamination lives in the VALID cell whose 0-neighbor the gradient consumed, so masking at the
mean does NOT remove it (A33 point 2). The fix drops that valid-but-contaminated ring at SOURCE
(NaN via the shared _valid_dem_mask, answering A33's open question), and stage_2e_score means over
the clean cells only. This synthetic fixture is the testable coastal case A33 said did not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.pipeline import mean_slope_tan


def _coastal_dem():
    """20x20 metric DEM: cols 0-4 = ocean (nodata-as-0, FM-12); cols 5-19 = gentle land (tan 0.2).
    The land edge at col 5 reads a spurious cliff under central-difference gradient (0 -> 100 -> 102)."""
    dem = np.zeros((20, 20), dtype=np.float64)          # ocean = 0
    for c in range(5, 20):
        dem[:, c] = 100.0 + (c - 5) * 2.0               # d/dcol = 2 m per 10 m cell -> tan 0.2
    return dem


def _unmasked_slope(dem):
    gy, gx = np.gradient(dem, 10.0, 10.0)
    return np.hypot(gx, gy)


def test_dropring_removes_coastal_inflation_and_leaves_interior_untouched():
    dem = _coastal_dem()
    old = _unmasked_slope(dem)                     # the pre-fix behavior
    new = mean_slope_tan(dem, dem_nodata=0.0)      # drop-ring fix

    # interior land cell (far from the ocean edge): unchanged, ~0.2 tan
    assert abs(new[10, 12] - old[10, 12]) < 1e-9
    assert abs(new[10, 12] - 0.2) < 1e-6

    # the land cell adjacent to the ocean edge is inflated in `old`, dropped (NaN) in `new`
    assert old[10, 5] > 1.0                        # spurious cliff (tan >> 0.2)
    assert np.isnan(new[10, 5])                     # drop-ring removed it at source

    # a basin straddling the edge: clean interior mean (~0.2), not the inflated old mean
    basin = np.zeros_like(dem, dtype=bool)
    basin[8:12, 5:12] = True                        # contaminated col-5 ring + clean interior
    old_mean = float(np.mean(old[basin]))
    new_mean = float(np.nanmean(new[basin]))
    assert old_mean > new_mean                      # inflation removed
    assert abs(new_mean - 0.2) < 1e-6               # clean slope recovered


def test_mean_slope_tan_backward_compatible_without_nodata():
    """Legacy callers (no dem_nodata) on a finite DEM -> unmasked, identical to np.gradient/hypot."""
    dem = _coastal_dem()
    new = mean_slope_tan(dem)                        # default dem_nodata=None -> no ring drop on finite DEM
    assert np.allclose(new, _unmasked_slope(dem), equal_nan=True)
    assert not np.isnan(new).any()


def test_stage_2e_score_means_over_clean_slope_cells():
    """stage_2e_score excludes NaN (dropped-ring) slope cells from mean_slope; all-NaN basin -> abort."""
    from src.score import stage_2e_score
    from src.grids import GateAbort

    shape = (4, 4)
    wt = np.ones(shape)
    covered = np.ones(shape, dtype=bool)
    slope = np.full(shape, 0.2)
    slope[0, 0] = np.nan                             # one contaminated cell in the basin
    m = np.zeros(shape, dtype=bool)
    m[0:2, 0:2] = True                              # basin: 1 NaN + 3 clean cells
    basins = [{"basin_id": 0, "mask": m, "area_km2": 1.0}]
    stage_2e_score(wt, covered, slope, basins)
    assert abs(basins[0]["mean_slope"] - 0.2) < 1e-9   # NaN excluded, clean mean

    slope_all_nan = np.full(shape, np.nan)
    basins2 = [{"basin_id": 1, "mask": m, "area_km2": 1.0}]
    try:
        stage_2e_score(wt, covered, slope_all_nan, basins2)
        assert False, "expected GateAbort on an all-NaN-slope basin"
    except GateAbort:
        pass


def test_stage_2e_score_flags_low_slope_coverage_when_ring_dominates():
    """F4: a basin mostly on the dropped nodata ring is scored on its small clean remnant -> FLAGGED
    low_slope_coverage, not silently ranked as if fully sampled (mirrors burn's low_coverage). The
    score/mean still come from the clean cells only -- the flag is diagnostic, it never gates the rank."""
    from src.score import stage_2e_score
    shape = (4, 4)
    wt = np.ones(shape)
    covered = np.ones(shape, dtype=bool)
    slope = np.full(shape, 0.2)
    slope[0, :] = np.nan                               # top row = dropped nodata ring
    m = np.zeros(shape, dtype=bool)
    m[0:2, 0:2] = True                                # basin: 2 NaN (ring) + 2 clean -> 50% coverage
    basins = [{"basin_id": 0, "mask": m, "area_km2": 1.0}]
    stage_2e_score(wt, covered, slope, basins)
    assert abs(basins[0]["slope_coverage_frac"] - 0.5) < 1e-9
    assert basins[0]["low_slope_coverage"] is True     # < 0.80 -> flagged
    assert abs(basins[0]["mean_slope"] - 0.2) < 1e-9   # mean still over the clean cells (unchanged)


def test_stage_2e_score_clean_basin_full_slope_coverage():
    """F4: an inland basin (no dropped ring) reports full slope coverage and is NOT flagged -- so the
    Montecito behavior lock is untouched (its basins are all fully covered, frac == 1.0)."""
    from src.score import stage_2e_score
    shape = (4, 4)
    wt = np.ones(shape)
    covered = np.ones(shape, dtype=bool)
    slope = np.full(shape, 0.2)
    m = np.zeros(shape, dtype=bool)
    m[0:2, 0:2] = True                                # all clean
    basins = [{"basin_id": 0, "mask": m, "area_km2": 1.0}]
    stage_2e_score(wt, covered, slope, basins)
    assert basins[0]["slope_coverage_frac"] == 1.0
    assert basins[0]["low_slope_coverage"] is False
