"""CF-11 -- pyflwdir independent-engine cross-check, locked as a confidence test.

Asserts the substantive agreement `validation/cf11_pyflwdir_crosscheck.py` documents: on Montecito,
pysheds and pyflwdir (Deltares) produce per canyon-mouth-outlet catchment areas that correlate
near-perfectly (Pearson >= 0.99) and match to a few percent on the large (>= 1 km^2) basins that
drive the ranking. The known divergences -- tiny-basin pour-point sensitivity and the whole-grid
coastal-ocean master -- are documented there and deliberately NOT gated (they don't affect scores).

Skips when the (gitignored) Montecito DEM is absent, so a clean checkout stays green.

Run:  pytest tests/test_pyflwdir_crosscheck.py -v
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_DEM = _REPO_ROOT / "validation" / "data" / "dem.tif"


@pytest.mark.skipif(not _DEM.exists(), reason="Montecito DEM absent (gitignored); cross-check skipped")
def test_pyflwdir_confirms_pysheds_catchment_areas():
    spec = importlib.util.spec_from_file_location(
        "cf11_crosscheck", str(_REPO_ROOT / "validation" / "cf11_pyflwdir_crosscheck.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    m = mod.crosscheck(_DEM)
    assert m["n_large"] >= 5                        # enough substantive basins for the check to mean something
    assert m["pearson_area"] >= 0.99               # near-perfect per-outlet area agreement (observed 0.9994)
    assert 0.95 <= m["median_ratio_large"] <= 1.05  # large basins match closely (observed 0.999)
    assert m["max_abs_dev_large"] <= 0.10          # no large basin off by >10% (observed max dev 3.0%)
