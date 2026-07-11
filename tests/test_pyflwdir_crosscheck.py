"""CF-11 -- pyflwdir independent-engine cross-check, locked as a confidence test.

F10: this file used to be a SINGLE test gated `skipif(DEM absent)` -- and the Montecito DEM is
gitignored, so on a clean checkout / CI runner it skipped and the suite went green having NEVER run
the independent-engine check (a pyflwdir break or a crosscheck() regression would not be caught).
Split into two:

  * test_pyflwdir_engine_runs_hermetically -- a HERMETIC smoke test (tiny synthetic DEM, no external
    data) that ALWAYS runs. A HARD `import pyflwdir` means a broken/renamed engine errors loudly here
    instead of silently skipping; exercises its core API (from_dem / upstream_area / basins).
  * test_pyflwdir_confirms_pysheds_catchment_areas -- the full-fidelity Montecito comparison (needs the
    gitignored DEM). A NAMED integration lane: it skips with a pointer on a clean checkout, but in a
    data-staged lane (env WWS_REQUIRE_INTEGRATION=1) a missing DEM is a HARD FAILURE, so the check can
    never be a silent green when it is meant to run.

Run:  pytest tests/test_pyflwdir_crosscheck.py -v
      WWS_REQUIRE_INTEGRATION=1 pytest tests/test_pyflwdir_crosscheck.py -v   (data-staged lane)
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_DEM = _REPO_ROOT / "validation" / "data" / "dem.tif"
_REQUIRE_INTEGRATION = os.environ.get("WWS_REQUIRE_INTEGRATION") == "1"


def test_pyflwdir_engine_runs_hermetically():
    # Always-run guard against the cross-check's rot-prone external dependency. HARD import (a broken
    # pyflwdir errors, never a silent skip) + its core API on a synthetic cone DEM draining to (0,0).
    import pyflwdir
    from rasterio.transform import from_origin

    yy, xx = np.mgrid[0:24, 0:24]
    dem = np.hypot(xx, yy).astype("float32")               # cone: min at (0,0); everything drains there
    flw = pyflwdir.from_dem(data=dem, nodata=-9999.0,
                            transform=from_origin(0, 240, 10, 10), latlon=False)
    ua = flw.upstream_area(unit="cell")
    assert ua.shape == dem.shape and float(np.nanmax(ua)) >= 50   # a real drainage network formed
    basins = flw.basins(idxs=np.array([0], dtype="int64"))       # catchment of cell (0,0)
    assert basins.shape == dem.shape and int((basins == 1).sum()) >= 50


def test_pyflwdir_confirms_pysheds_catchment_areas():
    # Full-fidelity Montecito cross-check: on Montecito, pysheds and pyflwdir (Deltares) give per
    # canyon-mouth-outlet catchment areas that correlate near-perfectly (Pearson >= 0.99) and match to
    # a few percent on the large (>= 1 km^2) basins that drive the ranking. Needs the gitignored DEM.
    if not _DEM.exists():
        msg = ("Montecito DEM absent (gitignored); stage validation/data/dem.tif to run the pyflwdir "
               "cross-check.")
        if _REQUIRE_INTEGRATION:
            pytest.fail(msg + " (WWS_REQUIRE_INTEGRATION=1 -> hard failure, not a silent skip -- F10)")
        pytest.skip(msg + " Set WWS_REQUIRE_INTEGRATION=1 in a data-staged lane to enforce it.")

    spec = importlib.util.spec_from_file_location(
        "cf11_crosscheck", str(_REPO_ROOT / "validation" / "cf11_pyflwdir_crosscheck.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    m = mod.crosscheck(_DEM)
    assert m["n_large"] >= 5                        # enough substantive basins for the check to mean something
    assert m["pearson_area"] >= 0.99               # near-perfect per-outlet area agreement (observed 0.9994)
    assert 0.95 <= m["median_ratio_large"] <= 1.05  # large basins match closely (observed 0.999)
    assert m["max_abs_dev_large"] <= 0.10          # no large basin off by >10% (observed max dev 3.0%)
