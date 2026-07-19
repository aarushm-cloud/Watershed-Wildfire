"""AA-3 (B4, Auto-Acquire Build-Plan Phase 3) -- thin wiring into the validated pipeline.

autoacquire_run composes selector -> (human approval gate) -> creator ->
acquire.build_fire_config -> src.pipeline.run_pipeline. It adds NO new ingest code
(the frozen both-arms ingest is the one resample) and NO new science. These tests
pin the composition contract: the approval gate defaults CLOSED (machine proposes,
human disposes); honest selector states (waiting / window_closed / no_pre_scene)
pass through untouched with the creator and pipeline NEVER invoked (B1 hard
invariant: no burn-less ranking, no score/rank on any refusal-shaped state);
failures stay loud (GateAbort propagates; a pipeline refusal is passed through
faithfully, never softened -- FM-10).

Run:  pytest tests/test_autoacquire_run.py -v
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from autoacquire import autoacquire_run as ar  # noqa: E402
from autoacquire import scene_select as ss  # noqa: E402
from autoacquire import dnbr_create as dc  # noqa: E402
import acquire  # noqa: E402
from src import pipeline as pl  # noqa: E402
from src.grids import GateAbort  # noqa: E402

BBOX = (-122.145, 38.455, -121.985, 38.595)
DATES = dict(ignition=date(2026, 6, 8), containment=date(2026, 6, 20))


def _package(status="recommended"):
    pkg = {"status": status, "framing": {}, "rejected": []}
    if status == "recommended":
        pkg["pair"] = {
            "sensor": "S2",
            "pre": {"id": "P", "date": date(2026, 6, 4), "sensor": "S2"},
            "post": {"id": "Q", "date": date(2026, 7, 7), "sensor": "S2"},
            "metrics": {"pair_valid_frac": 0.97},
            "verdict": {"verdict": "good", "summary": "s"},
        }
    return pkg


class _Spy:
    def __init__(self, ret=None, exc=None):
        self.calls = []
        self.ret, self.exc = ret, exc

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        if self.exc:
            raise self.exc
        return self.ret


def test_approval_gate_defaults_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "select", _Spy(ret=_package()))
    create = _Spy()
    monkeypatch.setattr(dc, "create_dnbr", create)
    out = ar.run_autoacquire(BBOX, out_dir=tmp_path, **DATES)
    assert out["status"] == "recommended"      # the package, awaiting a human
    assert create.calls == []                  # nothing built without approval


def test_approved_happy_path_wires_created_dnbr_into_pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "select", _Spy(ret=_package()))
    created = {"dnbr_tif": str(tmp_path / "d.tif"), "quicklook_png": "q",
               "provenance_json": "p", "gate_stats": {"p99_abs": 0.5}}
    create = _Spy(ret=created)
    fire = {"name": "x", "dnbr": tmp_path / "d.tif", "out_dir": tmp_path, "dem": "dem.tif"}
    build = _Spy(ret=fire)
    ranked = {"status": "ranked", "arms": {"arm_a": "A", "arm_b": "B"}, "creek_nearest": "C"}
    run = _Spy(ret=ranked)
    write = _Spy(ret=("r.csv", "b.geojson"))
    monkeypatch.setattr(dc, "create_dnbr", create)
    monkeypatch.setattr(acquire, "build_fire_config", build)
    monkeypatch.setattr(pl, "run_pipeline", run)
    monkeypatch.setattr(ar.outputs, "write_dnbr_outputs", write)
    out = ar.run_autoacquire(BBOX, out_dir=tmp_path, approve=True, name="x", **DATES)
    assert out["status"] == "ran"
    assert create.calls[0][0][0]["pre"]["id"] == "P"          # the approved pair
    assert build.calls[0][0][:2] == (BBOX, created["dnbr_tif"])  # created tif handed on
    assert run.calls[0][0][0] is fire                          # unchanged fire dict
    assert out["pipeline"]["status"] == "ranked"
    # Ranked results are persisted via the REUSED validated writer (A34 framing intact).
    assert write.calls and write.calls[0][0][:3] == ("A", "B", "C")
    assert out["outputs"] == ("r.csv", "b.geojson")


def test_refused_pipeline_writes_no_ranked_outputs(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "select", _Spy(ret=_package()))
    monkeypatch.setattr(dc, "create_dnbr", _Spy(ret={"dnbr_tif": "d", "quicklook_png": "q",
                                                     "provenance_json": "p", "gate_stats": {}}))
    monkeypatch.setattr(acquire, "build_fire_config", _Spy(ret={"name": "x"}))
    monkeypatch.setattr(pl, "run_pipeline", _Spy(ret={"status": "refused", "reason_code": "t"}))
    write = _Spy()
    monkeypatch.setattr(ar.outputs, "write_dnbr_outputs", write)
    out = ar.run_autoacquire(BBOX, out_dir=tmp_path, approve=True, **DATES)
    assert write.calls == []                   # no ranking artifacts on a refusal (B1/A28)
    assert "outputs" not in out


@pytest.mark.parametrize("status", ["waiting", "window_closed", "no_pre_scene"])
def test_honest_states_pass_through_with_no_build(monkeypatch, tmp_path, status):
    monkeypatch.setattr(ss, "select", _Spy(ret=_package(status)))
    create, build, run = _Spy(), _Spy(), _Spy()
    monkeypatch.setattr(dc, "create_dnbr", create)
    monkeypatch.setattr(acquire, "build_fire_config", build)
    monkeypatch.setattr(pl, "run_pipeline", run)
    out = ar.run_autoacquire(BBOX, out_dir=tmp_path, approve=True, **DATES)
    assert out["status"] == status
    # B1 hard invariant: no score, no rank, nothing built on any refusal-shaped state.
    assert create.calls == [] and build.calls == [] and run.calls == []


def test_creator_gateabort_propagates(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "select", _Spy(ret=_package()))
    monkeypatch.setattr(dc, "create_dnbr", _Spy(exc=GateAbort("bad artifact (A8)")))
    with pytest.raises(GateAbort):
        ar.run_autoacquire(BBOX, out_dir=tmp_path, approve=True, **DATES)


def test_pipeline_refusal_passed_through_unsoftened(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "select", _Spy(ret=_package()))
    monkeypatch.setattr(dc, "create_dnbr", _Spy(ret={"dnbr_tif": "d", "quicklook_png": "q",
                                                     "provenance_json": "p", "gate_stats": {}}))
    monkeypatch.setattr(acquire, "build_fire_config", _Spy(ret={"name": "x"}))
    refusal = {"status": "refused", "reason_code": "terrain", "message": "no mountain front"}
    monkeypatch.setattr(pl, "run_pipeline", _Spy(ret=refusal))
    out = ar.run_autoacquire(BBOX, out_dir=tmp_path, approve=True, **DATES)
    assert out["status"] == "ran"
    assert out["pipeline"] == refusal          # verbatim, never softened (FM-10)
