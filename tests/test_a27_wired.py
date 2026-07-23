"""A39-era WIRED-PATH tests for the terrain router + polymorphic dispatch.

Originally P3.4-build-2: proved the A27 refusal was reached through `validation/gate.py`'s live
wiring (the A27-before-A25 seam `_terrain_applicability_gate` -> `write_refusal` -> the polymorphic
`run_pipeline` return contract -> caller-side `dispatch_result`). A39 replaced the refuse-only gate
with a route-not-refuse router (`_terrain_mode`): incised terrain now reaches a RANKED result, not
a refusal (`test_wired_seam_ranks_on_incised_fixture`). `write_refusal` was removed as dead code
(post-review ruling) once terrain shape was its only trigger and stopped calling it; the
`dispatch_result` "refused" branch and the tests below stay live for any FUTURE non-terrain
refusal trigger -- they exercise it with hand-built `{"status": "refused", ...}` dicts, since no
production code currently builds one.

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
