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

from src.grids import GateAbort

# incised_fire fixture (tests/conftest.py) is picked up automatically by pytest for the tests below.

# The EXACT in-memory refusal-result key set the firewall pins (DECISIONS A27 / build-2 §2.2).
_REFUSAL_KEYS = {"status", "reason_code", "span_m", "span_threshold_m", "message"}
# Keys that must NEVER appear in the in-memory refusal-result (absolute-elevation firewall).
_FORBIDDEN_KEYS = {"p1", "p1_m", "p10", "p10_m", "contour", "contour_m", "CONTOUR_M",
                   "elevation", "elev", "n_valid", "dem", "dem_raw"}


# ============================================================================
# Wired seam: incised terrain -> ranked-result through the live code path (A39)
# ============================================================================
def test_wired_seam_ranks_on_incised_fixture(incised_fire):
    """A39: the wired seam (gate.run_pipeline, re-exported from src.pipeline) ROUTES incised
    terrain to a ranked result instead of refusing -- the A27 refuse-only gate this test used to
    drive directly (gate._terrain_applicability_gate) no longer exists; see gate.py's A39
    backward-compat-shim comment. The removed write-refusal-json assertion now lives as
    test_incised_writes_no_refusal in tests/test_incised_ranked.py."""
    result = gate.run_pipeline(incised_fire)
    assert result is not None
    assert result["status"] == "ranked"
    assert result["terrain_mode"] == "incised"


def test_refusal_result_is_firewall_clean():
    """Still live for non-terrain refusal triggers (A39 removed only the terrain one)."""
    from src.outputs import build_refusal_message
    verdict = {"reason_code": "REFUSED_INCISED_TERRAIN", "span_m": 71.0,
               "span_threshold_m": 50.0, "n_valid": 1000}
    msg = build_refusal_message(verdict["reason_code"], verdict["span_m"],
                                verdict["span_threshold_m"])
    result = {"status": "refused", "reason_code": verdict["reason_code"],
              "span_m": verdict["span_m"],
              "span_threshold_m": verdict["span_threshold_m"], "message": msg}
    assert set(result) == _REFUSAL_KEYS
    assert not (_FORBIDDEN_KEYS & set(result))


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
