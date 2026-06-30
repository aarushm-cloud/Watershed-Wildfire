"""P3.4-build-2 WIRED-PATH tests for the A27 terrain-applicability refusal.

Build-1 tested the detector (`assess_hypsometric_applicability`) and the artifact writer
(`write_refusal` / `build_refusal_message`) in ISOLATION. This file proves the refusal is reached
through `validation/gate.py`'s live wiring: the A27-before-A25 seam (`_terrain_applicability_gate`)
-> `write_refusal` -> the polymorphic `run_pipeline` return contract -> caller-side `dispatch_result`.
A test that only re-called the detector would NOT exercise build-2.

STRATEGY (b): drive the wired refusal seam directly with the committed synthetic incised fixture's
RAW DEM (the same `dem_raw` / `dem_nodata` `run_pipeline` feeds the seam), instead of parameterizing
the Montecito-hardcoded `run_pipeline` (option (a), higher blast radius -- D0). Every refusal/wired
test writes to an ISOLATED tmp_path (pytest), NEVER out/montecito or validation/out: `write_refusal`
does not delete stale ranking.csv/basins.geojson, so the 'ranking absent' assertion is only
meaningful in a fresh dir (FM-9 output-clobbering avoidance).

The behavior lock (tests/test_behavior_lock.py) is deliberately NOT extended to assert `status`;
the Montecito-returns-`ranked` invariant lives HERE (new file) to keep the lock byte-frozen.

Run:  pytest tests/test_a27_wired.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import rasterio  # noqa: F401  (kept for parity / future raster asserts)

# --- load validation/gate.py as an importable module (same cwd-independent pattern as the lock) ---
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_GATE_PATH = _REPO_ROOT / "validation" / "gate.py"
_spec = importlib.util.spec_from_file_location("gate", _GATE_PATH)
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate"] = gate
_spec.loader.exec_module(gate)

from src.ingest import load_dem                       # exact wired-path DEM read (gives dem_raw / nodata)
from src.delineate import HYPSOMETRIC_SPAN_THRESHOLD_M
from src.grids import GateAbort

_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "incised_synthetic.tif"

# The EXACT in-memory refusal-result key set the firewall pins (DECISIONS A27 / build-2 §2.2).
_REFUSAL_KEYS = {"status", "reason_code", "span_m", "span_threshold_m", "message"}
# Keys that must NEVER appear in the in-memory refusal-result (absolute-elevation firewall).
_FORBIDDEN_KEYS = {"p1", "p1_m", "p10", "p10_m", "contour", "contour_m", "CONTOUR_M",
                   "elevation", "elev", "n_valid", "dem", "dem_raw"}


def _incised_inputs():
    """The fixture's RAW pre-fill DEM + nodata, read exactly as run_pipeline feeds the seam."""
    grid, dem, dem_raw = load_dem(str(_FIXTURE))
    return dem_raw, dem.nodata


# ============================================================================
# Wired seam: incised terrain -> refusal-result through the live code path
# ============================================================================
def test_wired_seam_refuses_on_incised_fixture(tmp_path):
    """The wired A27 seam classifies the incised fixture REFUSE and returns a refusal-result."""
    dem_raw, dem_nodata = _incised_inputs()
    result = gate._terrain_applicability_gate(dem_raw, dem_nodata, tmp_path)
    assert result is not None, "incised fixture must produce a refusal-result, not None"
    assert result["status"] == "refused"
    assert result["reason_code"] == "REFUSED_INCISED_TERRAIN"
    assert result["span_m"] > HYPSOMETRIC_SPAN_THRESHOLD_M    # incised: span exceeds the frozen 50 m
    assert result["span_threshold_m"] == 50.0                 # frozen A27 threshold, propagated verbatim
    # message is the whole user-facing payload of a refusal -- assert it is the readable refusal prose
    assert "no ranking is produced" in result["message"]
    assert "incised valley" in result["message"]


def test_wired_refusal_writes_only_refusal_json(tmp_path):
    """REFUSE writes refusal.json to the (isolated) out_dir and NO ranking.csv / basins.geojson."""
    dem_raw, dem_nodata = _incised_inputs()
    gate._terrain_applicability_gate(dem_raw, dem_nodata, tmp_path)
    assert (tmp_path / "refusal.json").exists(), "refusal.json must be written on REFUSE"
    assert not (tmp_path / "ranking.csv").exists(), "no ranking.csv on REFUSE (no basins, no scores)"
    assert not (tmp_path / "basins.geojson").exists(), "no basins.geojson on REFUSE"


def test_refusal_result_is_firewall_clean(tmp_path):
    """FIREWALL (A27): the in-memory refusal-result is the exact 5-field subset, no absolute
    elevation, and never widens toward refusal.json's larger on-disk field set."""
    dem_raw, dem_nodata = _incised_inputs()
    result = gate._terrain_applicability_gate(dem_raw, dem_nodata, tmp_path)

    # exact key set -- a superset (an elevation leak) fails here
    assert set(result.keys()) == _REFUSAL_KEYS, (
        f"refusal-result keys {sorted(result)} != frozen {sorted(_REFUSAL_KEYS)}")
    # belt-and-suspenders: no absolute-elevation / contour / p1 / p10 / n_valid key
    assert not (set(result.keys()) & _FORBIDDEN_KEYS), (
        f"absolute-elevation/contour leak into refusal-result: {set(result) & _FORBIDDEN_KEYS}")

    # consistency (not identity) with refusal.json: the SHARED fields carry the same values; the
    # JSON may persist MORE on disk (terminal artifact) but the return must not be widened toward it.
    disk = json.loads((tmp_path / "refusal.json").read_text())
    for shared in ("reason_code", "span_m", "span_threshold_m"):
        assert disk[shared] == result[shared], f"{shared} differs between return and refusal.json"
    # the on-disk artifact, too, leaks no absolute percentile elevation (only span_m, a difference)
    assert "p1" not in disk and "p10" not in disk, "refusal.json must not persist absolute p1/p10"


# ============================================================================
# Caller-side dispatch (the polymorphic-return discriminator)
# ============================================================================
def test_dispatch_refused_emits_message_and_exits_zero(capsys):
    """A 'refused' return: dispatch emits the human message, returns exit 0, does NOT raise."""
    msg = "Refused: this fire's terrain is an incised valley; no ranking is produced."
    code = gate.dispatch_result({"status": "refused", "reason_code": "REFUSED_INCISED_TERRAIN",
                                 "span_m": 70.6, "span_threshold_m": 50.0, "message": msg})
    assert code == 0                                  # honest answer, not a crash
    out = capsys.readouterr().out
    assert msg in out                                 # the message IS the user-facing payload
    assert "REFUSAL" in out                           # parseable stdout marker for a batch caller


def test_dispatch_ranked_returns_zero_silently(capsys):
    """A 'ranked' return dispatches to exit 0 and emits no refusal banner."""
    code = gate.dispatch_result({"status": "ranked"})
    assert code == 0
    assert "REFUSAL" not in capsys.readouterr().out


def test_dispatch_unknown_status_fails_loud():
    """An UNKNOWN status RAISES (A8 fail-loud). This is also the extensibility guarantee: a future
    third status fails loud in an un-taught caller rather than being silently mishandled."""
    with pytest.raises(GateAbort):
        gate.dispatch_result({"status": "caveated_ranking_future"})


# ============================================================================
# Montecito ranked invariant (lives here, NOT in the frozen lock)
# ============================================================================
def test_montecito_run_pipeline_returns_ranked():
    """The Montecito (range-front) path still returns a ranked-result -- catches a future regression
    that flips Montecito to a refusal. Kept OUT of test_behavior_lock.py to keep the lock frozen."""
    R = gate.run_pipeline()
    assert R["status"] == "ranked"
    assert "ranked" in R and "metrics" in R           # ranked payload intact alongside the discriminator
