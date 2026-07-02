"""A31 REORDER + South Fork wiring tests.

A31 moves the A27 terrain-applicability gate AHEAD of stage_2a_hydrology in run_pipeline, so a fire
refuses on the raw DEM ALONE -- before any SBS is opened or hydrology runs. This file locks the three
DISTINCT outcomes of the input-shape matrix (do not conflate them):

  1. REFUSAL (incised DEM, sbs=None)   -> run_pipeline returns a refusal-result + writes refusal.json,
                                          WITHOUT opening SBS. (test_hermetic_end_to_end_refusal)
  2. BAD-PATH (dem = missing file)     -> run_pipeline RAISES (library contract), NOT a SystemExit.
                                          (test_bad_dem_path_raises_not_systemexit)
  3. DATA-ABSENT (driver layer)        -> run.run_fire / _assert_inputs_present exits with a clean
                                          SystemExit naming the missing input. (test_clean_exit_*)

test_behavior_lock.py (the oracle) and test_B_southfork_corroboration are deliberately NOT touched.

Run:  pytest tests/test_a31_reorder.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# --- load validation/gate.py as an importable module (same cwd-independent pattern as the other tests) ---
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_GATE_PATH = _REPO_ROOT / "validation" / "gate.py"
_spec = importlib.util.spec_from_file_location("gate", _GATE_PATH)
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate"] = gate
_spec.loader.exec_module(gate)

import run  # the production driver (imports gate; no side effects at import -- argparse is inside main())

_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "incised_synthetic.tif"


# ============================================================================
# 1. REFUSAL -- hermetic end-to-end through run_pipeline (the A31 guarantee)
# ============================================================================
def test_hermetic_end_to_end_refusal(tmp_path):
    """An incised-DEM fire with NO SBS runs end-to-end through run_pipeline to a refusal.

    This is the A31 GUARANTEE: because the terrain gate now precedes stage_2a, a fire with sbs=None
    refuses on the DEM alone -- the SBS is never opened. Uses the committed incised fixture and an
    ISOLATED tmp out_dir (a shared OUT could pass vacuously off a stale refusal.json).

    NON-VACUITY: if the A31 reorder were reverted (gate back AFTER stage_2a), stage_2a would run first
    and call rasterio.open(fire["sbs"]) with sbs=None -- which raises BEFORE any refusal.json is
    written. run_pipeline would then raise instead of returning a refusal, so both the returned-status
    assertion and the on-disk content assertion below would fail. This test therefore fails if the
    ordering regresses.
    """
    fire = {
        "name": "hermetic_incised",
        "dem": _FIXTURE,
        "sbs": None,                 # no SBS: must never be opened before the terrain gate refuses
        "assets": None,              # nothing between DEM-load and the gate consumes assets/creeks
        "creeks": None,
        "out_dir": tmp_path,         # isolated: refusal.json content assertion must be non-vacuous
        "expected_crs": "EPSG:32613",
        "validation_case": "hermetic_incised_refusal",
    }

    result = gate.run_pipeline(fire)

    # (a) run_pipeline returns the polymorphic refusal-result (lowercase 'refused' discriminator)
    assert result is not None
    assert result["status"] == "refused", f"expected a refusal-result, got {result.get('status')!r}"
    assert result["reason_code"] == "REFUSED_INCISED_TERRAIN"

    # (b) refusal.json exists in the tmp out_dir AND its CONTENT proves a real refusal. NOTE: the on-disk
    # schema uses "status": "REFUSED" / "ranking_produced": false (write_refusal); there is NO "refuse"
    # boolean key in refusal.json (that key lives only on the in-memory verdict), so we assert the real
    # persisted fields rather than a nonexistent "refuse == true".
    refusal_json = tmp_path / "refusal.json"
    assert refusal_json.exists(), "refusal.json must be written to the fire's out_dir on REFUSE"
    disk = json.loads(refusal_json.read_text())
    assert disk["status"] == "REFUSED"
    assert disk["ranking_produced"] is False
    assert disk["reason_code"] == "REFUSED_INCISED_TERRAIN"

    # (c) the SBS was never produced: no ranking artifacts behind a refusal
    assert not (tmp_path / "ranking.csv").exists()
    assert not (tmp_path / "basins.geojson").exists()


# ============================================================================
# 2. BAD-PATH -- run_pipeline is a library function: it RAISES (not SystemExit)
# ============================================================================
def test_bad_dem_path_raises_not_systemexit(tmp_path):
    """run_pipeline with a dem path to a nonexistent file RAISES -- proving fire['dem'] is threaded.

    This is the library contract (NOT the driver's clean SystemExit -- see test_clean_exit_* below).
    Deliberately NOT a parity test against run_pipeline(MONTECITO_FIRE): both would feed Montecito and
    pass even if threading were broken (the false-coverage trap). The bad dem is the whole point.
    """
    fire = dict(gate.MONTECITO_FIRE)
    fire["dem"] = str(tmp_path / "does_not_exist.tif")   # non-None path to a missing file

    with pytest.raises(Exception) as ei:                 # SystemExit is NOT an Exception subclass, so a
        gate.run_pipeline(fire)                           # driver-style SystemExit here would FAIL to match
    assert not isinstance(ei.value, SystemExit), "run_pipeline must RAISE, not SystemExit (library contract)"


# ============================================================================
# 3. DATA-ABSENT -- the DRIVER exits cleanly (distinct from the library raise above)
# ============================================================================
def test_clean_exit_when_input_absent(tmp_path):
    """run.run_fire on a fire whose dem path is missing exits CLEANLY (SystemExit + acquisition pointer).

    Same underlying condition as test 2 (a missing input path) but at the DRIVER layer: run_fire calls
    _assert_inputs_present BEFORE run_pipeline, so a registered-but-data-absent fire (data gitignored on
    a clean checkout) exits gracefully instead of crashing deep in rasterio.open/load_dem.
    """
    fire = dict(gate.MONTECITO_FIRE)                     # a registered fire...
    fire["name"] = "southfork"                            # ...standing in for the data-absent case
    fire["dem"] = str(tmp_path / "missing_dem.tif")

    with pytest.raises(SystemExit) as exc:
        run.run_fire(fire)
    msg = str(exc.value)
    assert "data not present" in msg
    assert "acquisition_manifest.json" in msg
    assert "dem" in msg                                   # names the missing input


def test_none_input_paths_are_skipped(tmp_path):
    """A None input path (sbs=None, by design) is NEVER treated as a missing file by the driver guard.

    Encodes the matrix rule: only a NON-None path to a nonexistent file triggers the data-absence exit.
    A fire whose real inputs all exist and whose sbs is None passes _assert_inputs_present cleanly.
    """
    dem = tmp_path / "dem.tif"
    dem.write_bytes(b"not a real tif, but the guard only checks existence")
    fire = {"name": "none_ok", "dem": dem, "sbs": None, "assets": None, "creeks": None,
            "out_dir": tmp_path, "expected_crs": "EPSG:32613", "validation_case": "x"}
    run._assert_inputs_present(fire)   # must NOT raise: sbs/assets/creeks are None (absent by design)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
