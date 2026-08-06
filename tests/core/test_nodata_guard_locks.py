"""Fatal nodata-guard locks (A41 prep). Pin the FROZEN >20% fatal semantics BEFORE the
partition refactor so the refactor provably preserves them (pre-reg P2 §4; A20/A21).
No prior test pinned the fatal arm -- these are the first locks, not a retarget."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import DNBR_NODATA_FAILLOUD_FRAC
from src.grids import GateAbort
from src.pipeline import _dnbr_nodata_guard


def _basin(mask):
    return {"basin_id": 7, "mask": np.asarray(mask, dtype=bool)}


def test_guard_raises_above_bar_with_frozen_message():
    """>20% nodata over a guarded basin -> GateAbort, message text pinned."""
    mask = np.ones((10, 10), dtype=bool)
    nd = np.zeros((10, 10), dtype=bool)
    nd[:3, :] = True                                  # 30% of the basin
    with pytest.raises(GateAbort, match="a clouded scene is a bad scene"):
        _dnbr_nodata_guard([_basin(mask)], nd)


def test_guard_passes_at_exactly_the_bar():
    """Boundary is strict '>': exactly 20% must NOT raise (frozen comparison)."""
    mask = np.ones((10, 10), dtype=bool)
    nd = np.zeros((10, 10), dtype=bool)
    nd[:2, :] = True                                  # exactly 20%
    _dnbr_nodata_guard([_basin(mask)], nd)            # must not raise
    assert DNBR_NODATA_FAILLOUD_FRAC == 0.20          # the frozen constant itself


def test_guard_ignores_nodata_outside_the_basin():
    """Cloud elsewhere in the box never counts against a basin."""
    mask = np.zeros((10, 10), dtype=bool)
    mask[5:, :] = True
    nd = np.zeros((10, 10), dtype=bool)
    nd[:5, :] = True                                  # all cloud OUTSIDE the basin
    _dnbr_nodata_guard([_basin(mask)], nd)            # must not raise
