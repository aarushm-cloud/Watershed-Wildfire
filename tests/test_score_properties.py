"""CF-12 (A36 build) -- hypothesis property tests locking the FROZEN scoring stage.

These are invariant/regression tests over `src.score.stage_2e_score` (the frozen
`score = mean_burn x mean_slope x area_km2` + within-fire ordinal rank). Unlike the
example-based behavior lock (one Montecito run), hypothesis explores hundreds of random
valid inputs and asserts the invariants hold for ALL of them -- so a future refactor that
silently breaks the formula's term/evaluation order, the rank permutation, or the [0,1]
bounds is caught, not just a change to the one locked example.

They are expected to PASS against the current validated code (they characterise frozen
behavior); a hypothesis-found counterexample would be a real finding, not a flaky test.
Inputs are generated VALID (non-empty masks, finite non-negative slope) -- the empty-mask /
all-NaN fail-loud paths (A32/A33) have their own example tests.

Run:  pytest tests/test_score_properties.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from hypothesis import given, settings, strategies as st
from hypothesis.extra.numpy import arrays

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.score import stage_2e_score  # noqa: E402


@st.composite
def scoring_inputs(draw):
    """A valid (wt, covered, slope, basins) tuple for stage_2e_score on a small grid."""
    h, w = draw(st.integers(3, 6)), draw(st.integers(3, 6))
    finite = dict(allow_nan=False, allow_infinity=False)
    wt = draw(arrays(np.float64, (h, w), elements=st.floats(0.0, 1.0, **finite)))       # burn weights in [0,1]
    covered = draw(arrays(np.bool_, (h, w)))
    slope = draw(arrays(np.float64, (h, w), elements=st.floats(0.0, 5.0, **finite)))    # tan(theta) >= 0, no NaN
    basins = []
    for i in range(draw(st.integers(1, 4))):
        mask = draw(arrays(np.bool_, (h, w)))
        if not mask.any():                        # guarantee non-empty (else A32 fail-loud, tested elsewhere)
            mask[0, 0] = True
        area = draw(st.floats(0.1, 100.0, allow_nan=False, allow_infinity=False))
        basins.append({"basin_id": i, "mask": mask, "area_km2": area})
    return wt, covered, slope, basins


@given(scoring_inputs())
@settings(max_examples=200, deadline=None)
def test_score_is_exactly_burn_times_slope_times_area(inp):
    wt, covered, slope, basins = inp
    stage_2e_score(wt, covered, slope, basins)
    for b in basins:
        # bit-exact: locks the FROZEN term order AND evaluation order (IEEE multiply is
        # non-associative -- re-associating to area*burn*slope could flip a hair-close pair).
        assert b["score"] == b["mean_burn"] * b["mean_slope"] * b["area_km2"]


@given(scoring_inputs())
@settings(max_examples=200, deadline=None)
def test_ranks_are_a_permutation_ordered_by_score_desc_then_id(inp):
    wt, covered, slope, basins = inp
    order, _ = stage_2e_score(wt, covered, slope, basins)
    n = len(basins)
    assert sorted(b["rank"] for b in basins) == list(range(1, n + 1))          # a permutation of 1..n
    # rank order = score DESC, ties broken by ascending basin_id (deterministic)
    expected = sorted(basins, key=lambda b: (-b["score"], b["basin_id"]))
    assert [b["basin_id"] for b in order] == [b["basin_id"] for b in expected]
    assert [b["rank"] for b in expected] == list(range(1, n + 1))


@given(scoring_inputs())
@settings(max_examples=200, deadline=None)
def test_means_and_coverage_stay_in_unit_interval(inp):
    wt, covered, slope, basins = inp
    stage_2e_score(wt, covered, slope, basins)
    for b in basins:
        assert 0.0 <= b["mean_burn"] <= 1.0 + 1e-12          # mean of weights in [0,1]
        assert 0.0 <= b["burn_coverage_frac"] <= 1.0         # ncov/ncells, an exact ratio in [0,1]
        assert b["mean_slope"] >= 0.0                        # tan(theta) of a non-negative slope
        assert isinstance(b["low_coverage"], bool)
