"""P3.4-build-2a ENTRYPOINT lock (A30) -- the per-fire I/O + provenance surface is threaded, and
run.py resolves fires without touching the Montecito byte-for-byte path.

WHAT THIS LOCKS (and what it deliberately does NOT):
  - Test 1 is a DETERMINISM / no-regression check, NOT a threading proof: run_pipeline() and
    run_pipeline(MONTECITO_FIRE) both feed the Montecito config, so it passes even if threading were
    broken. It only guarantees the no-arg default == the explicit-Montecito call.
  - Tests 3, 5, 6 are the real threading proofs -- each fails if the code still opened a hardcoded
    global (SBS_TIF / DEM_TIF / CANONICAL_CRS) instead of the per-fire fire[...] value.
  - Test 2 proves validation_case + out_dir are threaded into the output artifacts (hermetic).
  - Test 4 proves run.py's fire registry resolves + rejects.

COVERAGE BOUNDARY (stated for the record): tests 5-6 dynamically prove dem/sbs consumption and
test 3 proves expected_crs. assets, creeks, and the A27-refusal out_dir threaded through
run_pipeline are NOT dynamically exercised here -- no runnable non-Montecito or incised fire exists
until the ordering build -- so they rest on the adversarial grep for leftover module globals in
run_pipeline's body. That grep is LOAD-BEARING, not incidental.

Run:  pytest tests/test_entrypoint.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
import pytest

# --- load validation/gate.py as an importable module (same cwd-independent pattern as the lock) ---
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_GATE_PATH = _REPO_ROOT / "validation" / "gate.py"
_spec = importlib.util.spec_from_file_location("gate", _GATE_PATH)
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate"] = gate
_spec.loader.exec_module(gate)

from src import outputs


# ---- helpers reused verbatim in spirit from tests/test_output_crs.py -------------------------
def _write_synthetic_dem(path, crs_epsg, x0, y0):
    """A tiny 4x4 metric GeoTIFF in the given UTM CRS, cells = 10 m (CELL_M). Openable by rasterio."""
    transform = from_origin(x0, y0, 10.0, 10.0)   # 10 m cells, dx=dy=CELL_M
    data = np.array([[100, 101, 102, 103],
                     [104, 105, 106, 107],
                     [108, 109, 110, 111],
                     [112, 113, 114, 115]], dtype="float32")   # elevations (m), arbitrary
    with rasterio.open(path, "w", driver="GTiff", height=4, width=4, count=1,
                       dtype="float32", crs=f"EPSG:{crs_epsg}", transform=transform) as d:
        d.write(data, 1)


def _minimal_basins():
    """One scored basin with the exact loose-dict keys write_outputs reads (C9: loose dicts)."""
    mask = np.zeros((4, 4), dtype=bool)
    mask[1:3, 1:3] = True   # a 2x2 block -> a non-empty polygon when vectorised
    return [{
        "basin_id": 0, "rank": 1, "score": 1.234567,
        "mean_burn": 0.5, "mean_slope": 0.3, "area_km2": 0.04,
        "burn_coverage_frac": 0.9, "flowed": True, "matched_creek": "",
        "mask": mask,
    }]


# ---- Test 1: determinism / no-regression (NOT a threading proof) -----------------------------
def test_no_arg_equals_explicit_montecito():
    """run_pipeline() (no-arg -> MONTECITO_FIRE) == run_pipeline(MONTECITO_FIRE), exactly.

    Guarantees the A30 default binding is byte-for-byte the Montecito path. NOTE: both calls feed
    Montecito, so this passes even if threading were broken -- tests 3/5/6 are the threading proofs.
    """
    a = gate.run_pipeline()
    b = gate.run_pipeline(gate.MONTECITO_FIRE)
    assert gate._ranking_signature(a["basins"]) == gate._ranking_signature(b["basins"])
    assert a["metrics"]["auc"] == b["metrics"]["auc"]
    assert a["hydro"]["master_km2"] == b["hydro"]["master_km2"]


# ---- Test 2: validation_case + out_dir threaded into artifacts (hermetic) ---------------------
def test_validation_case_and_out_dir_threaded(tmp_path):
    """write_outputs stamps the passed validation_case (not the Montecito string) and writes to out_dir."""
    dem_tif = tmp_path / "dem.tif"
    _write_synthetic_dem(dem_tif, 32613, 430000.0, 3692000.0)

    csv_path, gj_path, _ = outputs.write_outputs(
        _minimal_basins(), {}, tmp_path, dem_tif, "SBS", validation_case="TEST_FIRE_2099")

    assert csv_path.parent == tmp_path and gj_path.parent == tmp_path
    header = csv_path.read_text()
    assert "validation_case=TEST_FIRE_2099" in header
    assert "Thomas_Fire_2017/Montecito_2018" not in header
    fc = json.loads(gj_path.read_text())
    assert fc["provenance"]["validation_case"] == "TEST_FIRE_2099"


# ---- Test 3: expected_crs threaded (non-vacuous, hermetic) ------------------------------------
class _Sentinel(Exception):
    """Marker raised by the assert_aligned recorder so the test proves the call site, not the guard."""


def test_expected_crs_threaded_into_assert_aligned(tmp_path, monkeypatch):
    """stage_2a_hydrology passes fire['expected_crs'] to assert_aligned (fails if it were hardcoded)."""
    dem_tif = tmp_path / "dem.tif"
    sbs_tif = tmp_path / "sbs.tif"
    _write_synthetic_dem(dem_tif, 32613, 430000.0, 3692000.0)
    _write_synthetic_dem(sbs_tif, 32613, 430000.0, 3692000.0)
    fire = {"dem": dem_tif, "sbs": sbs_tif, "expected_crs": "EPSG:32613"}

    captured = {}

    def _recorder(*args, **kwargs):
        captured["expected_crs"] = kwargs.get("expected_crs")
        raise _Sentinel()

    # stage_2a_hydrology was promoted into src/pipeline.py, so it resolves `assert_aligned` in THAT
    # module's namespace -- patch it there (gate.stage_2a_hydrology is the same object via the shim).
    monkeypatch.setattr("src.pipeline.assert_aligned", _recorder)
    with pytest.raises(_Sentinel):
        gate.stage_2a_hydrology(fire)
    assert captured.get("expected_crs") == "EPSG:32613"


# ---- Test 4: run.py resolves + rejects -------------------------------------------------------
def test_run_resolve_fire():
    """run.resolve_fire finds montecito (== gate.MONTECITO_FIRE) and SystemExits on an unknown name."""
    import run
    assert run.resolve_fire("montecito") == gate.MONTECITO_FIRE
    with pytest.raises(SystemExit) as exc:
        run.resolve_fire("bogus")
    assert "montecito" in str(exc.value)   # the available-fires list is surfaced


# ---- Test 5: fire['dem'] is actually the opened path (dynamic threading proof) ----------------
def test_dem_is_threaded(tmp_path):
    """A bogus fire['dem'] must make run_pipeline raise -- proving it opens fire['dem'], not DEM_TIF.

    Catch BROADLY (Exception): the open goes through rasterio/pysheds and the exact type is unstable.
    If threading were broken (still opens the global DEM_TIF), no exception would raise -> test fails.
    """
    fire = dict(gate.MONTECITO_FIRE)
    fire["dem"] = tmp_path / "does_not_exist.tif"
    with pytest.raises(Exception):
        gate.run_pipeline(fire)


# ---- Test 6: fire['sbs'] is actually the opened path (dynamic threading proof) ----------------
def test_sbs_is_threaded(tmp_path):
    """Real dem (so execution reaches the SBS open) + bogus fire['sbs'] must raise -- proves fire['sbs']."""
    fire = dict(gate.MONTECITO_FIRE)
    fire["sbs"] = tmp_path / "does_not_exist.tif"
    with pytest.raises(Exception):
        gate.run_pipeline(fire)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
