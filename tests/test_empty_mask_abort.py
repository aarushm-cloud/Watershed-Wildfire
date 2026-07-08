"""A32 empty-mask abort: stage_2e_score raises GateAbort when a basin mask has zero cells.

mean_slope (src/score.py) is the one per-basin reduction WITHOUT an `if ncells else 0.0`
fallback -- and deliberately so. Its two siblings (burn_coverage_frac, mean_burn) return 0.0
on an empty mask because a zero there is a MEANINGFUL value (a genuinely unburned / uncovered
basin). An empty *mask* is not a meaningful zero: it can only occur if delineate's
MIN_BASIN_KM2 guarantee (every retained basin >= min area) has been violated upstream -- i.e.
the run's own premises are broken. So mean_slope FAILS LOUD (GateAbort) rather than emit
np.mean([]) -> nan -> nan score (A8/A29 fail-loud; ratified A32).

This state cannot occur on Montecito or through the current pipeline, so the fixture builds it
by hand -- a guard no fixture can trip is dead code by this project's own standard.

Run:  pytest tests/test_empty_mask_abort.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.grids import GateAbort
from src.score import stage_2e_score


def _rasters(shape=(4, 4)):
    """Minimal per-cell inputs stage_2e_score consumes: burn weight, covered mask, slope."""
    wt = np.full(shape, 0.5, dtype="float64")        # burn WEIGHT [0-1]
    covered = np.ones(shape, dtype=bool)             # A18 real-assessment mask
    slope = np.full(shape, 0.3, dtype="float64")     # tan(theta), dimensionless
    return wt, covered, slope


def test_empty_mask_raises_gateabort():
    """ncells == 0 -> GateAbort naming the basin + the violated MIN_BASIN_KM2 invariant (not nan)."""
    wt, covered, slope = _rasters()
    empty = {"basin_id": 7, "mask": np.zeros((4, 4), dtype=bool), "area_km2": 0.05}
    with pytest.raises(GateAbort, match="MIN_BASIN_KM2"):
        stage_2e_score(wt, covered, slope, [empty])


def test_nonempty_basin_scores_without_abort():
    """Non-vacuous guard: a basin with >= 1 cell scores normally and mean_slope stays finite."""
    wt, covered, slope = _rasters()
    mask = np.zeros((4, 4), dtype=bool)
    mask[0, 0] = True
    basin = {"basin_id": 1, "mask": mask, "area_km2": 0.10}
    ranked, _ = stage_2e_score(wt, covered, slope, [basin])
    assert ranked[0]["mean_slope"] == pytest.approx(0.3)
    assert np.isfinite(basin["score"])
