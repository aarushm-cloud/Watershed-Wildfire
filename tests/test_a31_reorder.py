"""A31 REORDER + South Fork wiring tests.

A31 moves the A27 terrain-applicability gate AHEAD of stage_2a_hydrology in run_pipeline, so a fire
refuses on the raw DEM ALONE -- before any SBS is opened or hydrology runs. This file locks the three
DISTINCT outcomes of the input-shape matrix (do not conflate them):

  1. INCISED-RANKS (incised DEM, sbs=None) -> run_pipeline ROUTES (A39) to a ranked-result, WITHOUT
                                          opening SBS; no refusal.json is written.
                                          (test_hermetic_end_to_end_incised_ranks)
  2. BAD-PATH (dem = missing file)     -> run_pipeline RAISES (library contract), NOT a SystemExit.
                                          (test_bad_dem_path_raises_not_systemexit)
  3. DATA-ABSENT (driver layer)        -> run.run_fire / _assert_inputs_present exits with a clean
                                          SystemExit naming the missing input. (test_clean_exit_*)

test_behavior_lock.py (the oracle) and test_B_southfork_corroboration are deliberately NOT touched.

Run:  pytest tests/test_a31_reorder.py -v
"""
from __future__ import annotations

import importlib.util
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

# incised_fire fixture (tests/conftest.py) is picked up automatically by pytest below.


# ============================================================================
# 1. INCISED-RANKS -- hermetic end-to-end through run_pipeline (the A31 guarantee, A39 outcome)
# ============================================================================
def test_hermetic_end_to_end_incised_ranks(incised_fire):
    """An incised-DEM fire with NO SBS runs end-to-end through run_pipeline to a RANKED result.

    The A31 guarantee (the terrain gate precedes stage_2a, so classification runs on the DEM alone
    before any SBS is opened) is unchanged; A39 changed only the OUTCOME -- incised terrain now
    ROUTES to a ranked sub-basin result instead of refusing, so no refusal.json is written.
    result["terrain_mode"] being present proves classification ran.

    Uses the incised_fire fixture (tests/conftest.py): the old inline fire config here had no
    'dnbr' key and assets=None, which only worked because the gate refused before either input was
    needed. Execution now continues past the gate, so a real dnbr is required (incised terrain still
    skips assets -- A39 -- so assets=None stays valid for this fixture).
    """
    result = gate.run_pipeline(incised_fire)

    assert result is not None
    assert result["status"] == "ranked", f"expected a ranked-result, got {result.get('status')!r}"
    assert result["terrain_mode"] == "incised"

    assert not (incised_fire["out_dir"] / "refusal.json").exists()


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
