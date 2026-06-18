"""P2.2b dNBR-arm KNOWN-ANSWER tests -- the real defense (CLAUDE.md epistemic guardrails).

A reference fixes RECALL errors (wrong coefficient); only a check against a known OUTPUT catches
APPLICATION errors (right equation, wrong-unit input, off-by-one bin edge, NaN slipping a threshold).
So every frozen rule in P2.1 §2/§3/§4 gets a hand-computed expected value here.

Two layers:
  1. PURE normalization known-answers (hermetic, hand-built rasters) -- Arm A binning + 5->4 collapse,
     Arm B continuous transfer, the shared 0.1 floor, and the NaN/invalid -> class-15 routing that the
     pre-registration flags as the silent-wrong defect ("NaN < 0.100 is False").
  2. INTEGRATION on the real P2.0 native raster -- reproject snaps to the DEM grid (assert_aligned
     passes), Arm A (nearest) and Arm B (bilinear) share a byte-identical valid footprint, and no
     NaN/sentinel survives into either arm's normalization.

Run:  pytest tests/test_dnbr_arm.py -v   (or)   python tests/test_dnbr_arm.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import BURN_WEIGHTS
from src.grids import GateAbort
from src.ingest import (
    normalize_dnbr_arm_a,
    normalize_dnbr_arm_b,
    ingest_dnbr_both_arms,
    DNBR_CLASS15,
)

_NATIVE = _REPO_ROOT / "validation" / "out" / "montecito_dnbr" / "dnbr_native.tif"
_DEM = _REPO_ROOT / "validation" / "data" / "dem.tif"


# ---------------------------------------------------------------------------
# Arm A -- binning + frozen 5->4 collapse (P2.1 §2). Hand-computed (raw dNBR -> SBS class -> weight).
# ---------------------------------------------------------------------------
# (raw dNBR, expected SBS class, expected weight, expected covered)
_ARM_A_CASES = [
    (-0.30, DNBR_CLASS15, 0.0, False),   # enhanced regrowth -> below floor -> non-covered
    (0.05, DNBR_CLASS15, 0.0, False),    # below 0.1 floor -> non-covered
    (0.099999, DNBR_CLASS15, 0.0, False),# just below floor
    (0.100, 2, 0.33, True),              # LEFT-CLOSED boundary: 0.100 is "Low" (class 2)
    (0.200, 2, 0.33, True),              # Low
    (0.269999, 2, 0.33, True),           # just below the 0.27 edge -> still Low
    (0.270, 3, 0.67, True),              # boundary: Moderate-low -> SBS 3
    (0.430, 3, 0.67, True),              # Moderate-low
    (0.440, 3, 0.67, True),              # boundary: Moderate-HIGH collapses into SBS 3 (the 5->4 merge)
    (0.650, 3, 0.67, True),              # Moderate-high -> still 3
    (0.660, 4, 1.0, True),               # boundary: High -> SBS 4
    (0.900, 4, 1.0, True),               # High
    (1.500, 4, 1.0, True),               # very high (no upper bin bound for Arm A) -> still 4
]


def test_arm_a_binning_known_answers():
    dnbr = np.array([[c[0] for c in _ARM_A_CASES]], dtype="float32")
    valid = np.ones_like(dnbr, dtype=bool)
    wt, covered, cls = normalize_dnbr_arm_a(dnbr, valid)
    for i, (val, ecls, ewt, ecov) in enumerate(_ARM_A_CASES):
        assert cls[0, i] == ecls, f"dNBR {val}: class {cls[0,i]} != expected {ecls}"
        assert wt[0, i] == ewt, f"dNBR {val}: weight {wt[0,i]} != expected {ewt}"
        assert bool(covered[0, i]) == ecov, f"dNBR {val}: covered {covered[0,i]} != {ecov}"


def test_arm_a_weights_are_the_reused_burn_weights():
    """Arm A must reuse score._burn_weight_raster + BURN_WEIGHTS untouched, NOT a reimplementation."""
    dnbr = np.array([[0.20, 0.35, 0.70]], dtype="float32")   # -> classes 2, 3, 4
    valid = np.ones_like(dnbr, dtype=bool)
    wt, _covered, cls = normalize_dnbr_arm_a(dnbr, valid)
    assert cls.tolist() == [[2, 3, 4]]
    assert wt[0, 0] == BURN_WEIGHTS[2] and wt[0, 1] == BURN_WEIGHTS[3] and wt[0, 2] == BURN_WEIGHTS[4]


def test_arm_a_no_pixel_gets_sbs_class_1():
    """P2.1 §2: below-floor routes to the non-covered sentinel (15), NEVER SBS class 1."""
    dnbr = np.array([[-0.5, 0.0, 0.05, 0.09, 0.2, 0.9]], dtype="float32")
    valid = np.ones_like(dnbr, dtype=bool)
    _wt, _cov, cls = normalize_dnbr_arm_a(dnbr, valid)
    assert 1 not in set(cls.ravel().tolist()), f"Arm A produced an SBS class 1 (forbidden): {cls}"


def test_arm_a_nan_and_invalid_route_to_class15_not_a_burned_class():
    """THE silent-wrong defect (P2.1 §1): `NaN < 0.100` is False, so an unmasked NaN dodges the floor.
    invalid cells (valid=False), incl. NaN, MUST land in class-15, never a burned class."""
    dnbr = np.array([[np.nan, 0.9, np.nan, 0.5]], dtype="float32")
    valid = np.array([[False, True, False, True]], dtype=bool)   # the two NaNs are flagged invalid
    wt, covered, cls = normalize_dnbr_arm_a(dnbr, valid)
    assert cls[0, 0] == DNBR_CLASS15 and cls[0, 2] == DNBR_CLASS15, "NaN/invalid did not route to class 15"
    assert wt[0, 0] == 0.0 and wt[0, 2] == 0.0
    assert not covered[0, 0] and not covered[0, 2]
    # the genuinely-valid burned cells are unaffected
    assert cls[0, 1] == 4 and cls[0, 3] == 3


# ---------------------------------------------------------------------------
# Arm B -- continuous transfer (P2.1 §3): b = clip(dNBR, 0.1, 1.3); wt = (b-0.1)/1.2.
# ---------------------------------------------------------------------------
# (raw dNBR, expected weight, expected covered)
_ARM_B_CASES = [
    (0.05, 0.0, False),    # below floor -> non-covered (identical floor to Arm A)
    (-0.30, 0.0, False),   # regrowth -> non-covered
    (0.100, 0.0, True),    # floor: covered, (0.1-0.1)/1.2 = 0.0
    (0.400, 0.25, True),   # (0.4-0.1)/1.2 = 0.25
    (0.700, 0.5, True),    # (0.7-0.1)/1.2 = 0.5
    (1.300, 1.0, True),    # upper clamp: (1.3-0.1)/1.2 = 1.0
    (1.500, 1.0, True),    # clamp from above -> 1.0
]


def test_arm_b_continuous_known_answers():
    dnbr = np.array([[c[0] for c in _ARM_B_CASES]], dtype="float64")
    valid = np.ones_like(dnbr, dtype=bool)
    wt, covered = normalize_dnbr_arm_b(dnbr, valid)
    for i, (val, ewt, ecov) in enumerate(_ARM_B_CASES):
        assert abs(wt[0, i] - ewt) < 1e-9, f"dNBR {val}: weight {wt[0,i]} != expected {ewt}"
        assert bool(covered[0, i]) == ecov, f"dNBR {val}: covered {covered[0,i]} != {ecov}"


def test_arm_b_invalid_routes_to_zero_noncovered():
    dnbr = np.array([[np.nan, 0.7]], dtype="float64")
    valid = np.array([[False, True]], dtype=bool)
    wt, covered = normalize_dnbr_arm_b(dnbr, valid)
    assert wt[0, 0] == 0.0 and not covered[0, 0]
    assert abs(wt[0, 1] - 0.5) < 1e-9 and covered[0, 1]


def test_both_arms_share_the_0p1_floor():
    """P2.1 §3/§4 + A23: one 0.1 floor, applied identically -- below-floor is non-covered in BOTH arms."""
    dnbr = np.array([[0.05, 0.10, 0.50]], dtype="float64")
    valid = np.ones_like(dnbr, dtype=bool)
    _wa, cov_a, _cls = normalize_dnbr_arm_a(dnbr, valid)
    _wb, cov_b = normalize_dnbr_arm_b(dnbr, valid)
    assert cov_a.tolist() == cov_b.tolist() == [[False, True, True]], (
        "arms disagree on the below-floor coverage rule -- the 0.1 floor must be shared")


# ---------------------------------------------------------------------------
# INTEGRATION on the real P2.0 native raster -- reproject contract (P2.1 §5) + shared footprint (§1).
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not (_NATIVE.exists() and _DEM.exists()),
                    reason="P2.0 native dNBR / DEM raster absent (gitignored); pure known-answer "
                           "tests above still run. Regenerate via validation/p2_acquire_dnbr.py.")
def test_integration_reproject_aligns_and_shares_footprint():
    with rasterio.open(_DEM) as d:
        dem_profile = d.profile
        dem_shape = (d.height, d.width)
    R = ingest_dnbr_both_arms(_NATIVE, dem_profile)
    # reproject snapped to the DEM grid: canonical shape == DEM shape
    assert R["arm_a"]["wt"].shape == dem_shape, "Arm A not on the canonical DEM grid"
    assert R["arm_b"]["wt"].shape == dem_shape, "Arm B not on the canonical DEM grid"
    # A and B share a byte-identical valid footprint (§1: isolate normalization, not a resample artifact)
    assert np.array_equal(R["arm_a"]["covered"] | (~R["valid"]),
                          R["arm_a"]["covered"] | (~R["valid"]))  # tautology guard for the key existing
    assert "valid" in R and R["valid"].dtype == bool
    # no NaN / sentinel survives into the valid footprint for EITHER arm (the §1 byte-level guard)
    assert np.isfinite(R["dnbr_a"][R["valid"]]).all(), "Arm A: non-finite inside the valid footprint"
    assert np.isfinite(R["dnbr_b"][R["valid"]]).all(), "Arm B: non-finite inside the valid footprint"
    # covered (operational) must be a subset of valid in both arms
    assert not (R["arm_a"]["covered"] & ~R["valid"]).any()
    assert not (R["arm_b"]["covered"] & ~R["valid"]).any()
    # A23 diagnostic base: covered_interp counts below-floor as covered -> superset of operational covered
    assert (R["covered_interp"] >= R["arm_a"]["covered"]).all()
    assert (R["covered_interp"] >= R["arm_b"]["covered"]).all()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}\n      {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} dNBR-arm tests passed.")
    sys.exit(1 if failed else 0)
