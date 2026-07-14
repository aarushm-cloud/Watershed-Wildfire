"""FM-1 SCALE-FREE anti-collapse guard (supersedes the Montecito-calibrated PASS/FINDING/ABORT
bands). The domain pour-point's catchment must be at least MASTER_MIN_AOI_FRACTION of the AOI's
VALID DEM area, else GateAbort. This freezes TWO things at once:

  (1) the guard still fires on the FM-1 signature (pysheds coordinate-mode returned 0 km^2 and
      silently deleted the two largest flowed basins) -- proven here at several AOI sizes, so the
      protection is SCALE-FREE (a fraction), not tied to Montecito's ~39-45 km^2 absolute band;
  (2) a legitimately LARGE fire (master 300 km^2) is NOT aborted -- the old MASTER_ORDER_HI = 80 km^2
      band would have false-aborted it. That regression direction is the whole point of the change.

Derivation anchor (validation/data/dem.tif, loaded as the pipeline loads it):
  valid AOI = 168.9332 km^2 ; master_km2 = 44.7273 ; master/valid = 0.26476.
  Floor 0.05 = that fraction / ~5 (5x collapse-detection margin). See DECISIONS (scale-free guard)
  + docs/ALGORITHMS_REVIEW.md T5.
"""
import math

import pytest

from src.grids import GateAbort
from src.config import MASTER_MIN_AOI_FRACTION
from src.pipeline import assert_master_outlet_scale

# Montecito reference (derived; == tests/test_behavior_lock.py master_km2 and the valid-cell count).
MONTECITO_MASTER_KM2 = 44.7273
MONTECITO_VALID_KM2 = 168.9332


def test_floor_is_the_locked_value():
    # The floor is a config constant, derived-with-margin from Montecito's 0.2648, not invented here.
    assert MASTER_MIN_AOI_FRACTION == 0.05


def test_montecito_fraction_passes_and_returns_it():
    # Known-answer tie: the validation fire's real numbers clear the floor and yield ~0.265.
    frac = assert_master_outlet_scale(MONTECITO_MASTER_KM2, MONTECITO_VALID_KM2)
    assert abs(frac - 0.26476) < 1e-4


def test_collapse_to_zero_aborts_small_aoi():
    # FM-1 signature: master ~ 0 of a Montecito-sized AOI -> abort.
    with pytest.raises(GateAbort):
        assert_master_outlet_scale(0.0, MONTECITO_VALID_KM2)


def test_collapse_to_near_zero_aborts_large_aoi():
    # SCALE-FREE: the SAME near-0 collapse aborts at a 10x-larger AOI (fraction ~6e-6 << floor),
    # where an absolute-km^2 band centered on ~40 could not generalize.
    with pytest.raises(GateAbort):
        assert_master_outlet_scale(0.01, 1690.0)


def test_large_fire_passes_where_old_absolute_band_would_abort():
    # master 300 km^2 is 26% of an 1150 km^2 AOI -> healthy delineation, NO abort.
    # The retired MASTER_ORDER_HI = 80 km^2 would have false-aborted this legitimate large fire.
    frac = assert_master_outlet_scale(300.0, 1150.0)
    assert frac > MASTER_MIN_AOI_FRACTION


def test_boundary_at_floor_passes_just_below_aborts():
    # >= floor passes; < floor aborts (documented boundary; strict-less-than abort).
    assert assert_master_outlet_scale(5.0, 100.0) == pytest.approx(0.05)   # exactly the floor -> passes
    with pytest.raises(GateAbort):
        assert_master_outlet_scale(4.9, 100.0)                             # 0.049 < 0.05 -> abort


@pytest.mark.parametrize("bad_master", [float("nan"), float("inf"), -float("inf"), 0.0, -3.0])
def test_non_finite_or_nonpositive_master_aborts(bad_master):
    with pytest.raises(GateAbort):
        assert_master_outlet_scale(bad_master, MONTECITO_VALID_KM2)


@pytest.mark.parametrize("bad_valid", [0.0, -10.0])
def test_nonpositive_valid_area_aborts(bad_valid):
    # A degenerate all-nodata AOI (valid area <= 0) aborts instead of dividing by zero.
    with pytest.raises(GateAbort):
        assert_master_outlet_scale(MONTECITO_MASTER_KM2, bad_valid)


def test_retired_band_symbols_are_gone():
    # The Montecito-calibrated bands + the classifier they fed must not linger as dead code.
    import src.config as cfg
    import src.pipeline as pl
    for name in ("MASTER_PASS_LO", "MASTER_PASS_HI", "MASTER_ORDER_LO", "MASTER_ORDER_HI"):
        assert not hasattr(cfg, name), f"retired band constant {name} still in config"
    assert not hasattr(pl, "classify_master_zone"), "classify_master_zone should be replaced, not kept"
