"""autoacquire_run (B4) -- thin wiring into the validated pipeline.

autoacquire_run composes selector -> (human approval gate) -> creator ->
acquire.build_fire_config -> src.pipeline.run_pipeline. It adds NO new ingest code
(the frozen both-arms ingest is the one resample) and NO new science. These tests
pin the composition contract: the approval gate defaults CLOSED (machine proposes,
human disposes); honest selector states (waiting / window_closed / no_pre_scene)
pass through untouched with the creator and pipeline NEVER invoked (B1 hard
invariant: no burn-less ranking, no score/rank on any refusal-shaped state);
failures stay loud (GateAbort propagates; a pipeline refusal is passed through
faithfully, never softened -- FM-10).

Run:  pytest tests/acquire/test_autoacquire_run.py -v
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from autoacquire import autoacquire_run as ar  # noqa: E402
from autoacquire import scene_select as ss  # noqa: E402
from autoacquire import dnbr_create as dc  # noqa: E402
import acquire  # noqa: E402
from src import pipeline as pl  # noqa: E402
from src.grids import GateAbort  # noqa: E402

ARGV_COMMON = ["--bbox", "-122.145", "38.455", "-121.985", "38.595",
               "--ignition", "2026-06-08", "--containment", "2026-06-20"]

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


def test_ran_path_passes_refused_basins_to_writer(monkeypatch, tmp_path):
    """A41 fix-wave CRITICAL 1: the legacy run_autoacquire ('ran') path must forward
    result['refused_basins'] into write_dnbr_outputs -- omitting it on a partly-clouded fire
    would write clean-looking artifacts with the refused basins silently absent (no banner,
    no sidecars, no counts)."""
    monkeypatch.setattr(ss, "select", _Spy(ret=_package()))
    created = {"dnbr_tif": str(tmp_path / "d.tif"), "quicklook_png": "q",
               "provenance_json": "p", "gate_stats": {"p99_abs": 0.5}}
    monkeypatch.setattr(dc, "create_dnbr", _Spy(ret=created))
    fire = {"name": "x", "dnbr": tmp_path / "d.tif", "out_dir": tmp_path, "dem": "dem.tif"}
    monkeypatch.setattr(acquire, "build_fire_config", _Spy(ret=fire))
    refused = [{"basin_id": 7, "nodata_frac": 0.4}]
    ranked = {"status": "ranked", "arms": {"arm_a": "A", "arm_b": "B"}, "creek_nearest": "C",
             "refused_basins": refused}
    monkeypatch.setattr(pl, "run_pipeline", _Spy(ret=ranked))
    write = _Spy(ret=("r.csv", "b.geojson"))
    monkeypatch.setattr(ar.outputs, "write_dnbr_outputs", write)
    out = ar.run_autoacquire(BBOX, out_dir=tmp_path, approve=True, name="x", **DATES)
    assert out["status"] == "ran"
    assert write.calls[0][1]["refused"] == refused    # non-empty, reaches the writer


def test_incised_sbs_abort_writes_no_ranked_outputs(monkeypatch, tmp_path, incised_fire):
    """A39: incised terrain no longer refuses (the old REFUSED-status fake this test drove can no
    longer happen), so this locks the B1/A28 no-artifacts invariant against the real, reachable
    failure instead -- incised+SBS is a hard GateAbort (Task 8)."""
    fire = dict(incised_fire)
    fire["sbs"] = "data/southfork/burn/arm_a_cls.tif"   # any real path -- never opened before the abort
    fire["dnbr"] = None
    monkeypatch.setattr(ss, "select", _Spy(ret=_package()))
    monkeypatch.setattr(dc, "create_dnbr", _Spy(ret={"dnbr_tif": "d", "quicklook_png": "q",
                                                     "provenance_json": "p", "gate_stats": {}}))
    monkeypatch.setattr(acquire, "build_fire_config", _Spy(ret=fire))
    write = _Spy()
    monkeypatch.setattr(ar.outputs, "write_dnbr_outputs", write)
    with pytest.raises(GateAbort):
        ar.run_autoacquire(BBOX, out_dir=tmp_path, approve=True, **DATES)
    assert write.calls == []                   # no ranking artifacts on the abort (B1/A28)


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


def test_incised_sbs_abort_propagates_unsoftened(monkeypatch, tmp_path, incised_fire):
    """A39: run_autoacquire does not catch/rewrap the pipeline's GateAbort -- it propagates raw with
    its real message intact, exactly like the creator's (test_creator_gateabort_propagates).
    Retargeted from the old REFUSED-status dict-passthrough test: the failure is now an exception,
    not a returned dict, so there is no return value left to soften."""
    fire = dict(incised_fire)
    fire["sbs"] = "data/southfork/burn/arm_a_cls.tif"   # any real path -- never opened before the abort
    fire["dnbr"] = None
    monkeypatch.setattr(ss, "select", _Spy(ret=_package()))
    monkeypatch.setattr(dc, "create_dnbr", _Spy(ret={"dnbr_tif": "d", "quicklook_png": "q",
                                                     "provenance_json": "p", "gate_stats": {}}))
    monkeypatch.setattr(acquire, "build_fire_config", _Spy(ret=fire))
    with pytest.raises(GateAbort, match="incised"):
        ar.run_autoacquire(BBOX, out_dir=tmp_path, approve=True, **DATES)


# ---------------------------------------------------------------------------
# Task 8: `--max-swaps` CLI routing + JSON slim (`main()`, patching `ar.sweep.run_sweep`
# the same way the tests above patch `ss.select` / `dc.create_dnbr` -- the module attribute,
# not the name `main()` binds locally, so a plain `import autoacquire.sweep` inside
# autoacquire_run.py is required for this style to actually intercept the call).
# ---------------------------------------------------------------------------

def _degraded_sweep_result(tmp_path, leak_result=False):
    ret = {
        "status": "degraded",
        "package": {"status": "recommended"},
        "attempts": [{"sensor": "S2", "pre_id": "P", "pre_date": "2026-06-04", "post_id": "Q1",
                     "post_date": "2026-07-01", "outcome": "ranked", "refused_count": 1,
                     "n_basins_total": 5, "total_nodata_frac": 0.12}],
        "chosen": {"sensor": "S2", "pre_id": "P", "pre_date": "2026-06-04", "post_id": "Q1",
                  "post_date": "2026-07-01", "outcome": "ranked", "refused_count": 1,
                  "n_basins_total": 5, "total_nodata_frac": 0.12},
        "refused": [{"phase1_basin_id": 7, "nodata_frac": 0.4}],
        "result_paths": {"out_dir": str(tmp_path),
                         "ranking_csv": str(tmp_path / "ranking.csv"),
                         "basins_geojson": str(tmp_path / "basins.geojson")},
    }
    if leak_result:
        # Defensive case: a hypothetical future caller leaks the raw pipeline result
        # (with an ndarray mask inside) into the returned dict -- the slim filter must
        # strip it before it ever reaches json.dumps.
        ret["result"] = {"arms": {"arm_a": {"basins": [{"mask": np.zeros((2, 2))}]}}}
    return ret


def test_max_swaps_zero_routes_to_legacy_path_and_sweep_is_never_called(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "select", _Spy(ret=_package("waiting")))
    sweep_spy = _Spy()
    monkeypatch.setattr(ar.sweep, "run_sweep", sweep_spy)
    out = ar.main(ARGV_COMMON + ["--out", str(tmp_path), "--max-swaps", "0"])
    assert out["status"] == "waiting"
    assert sweep_spy.calls == []


def test_max_swaps_zero_calls_legacy_with_no_new_kwargs(monkeypatch, tmp_path):
    legacy_spy = _Spy(ret=_package("waiting"))
    monkeypatch.setattr(ar, "run_autoacquire", legacy_spy)
    ar.main(ARGV_COMMON + ["--out", str(tmp_path), "--max-swaps", "0"])
    assert legacy_spy.calls
    _, kwargs = legacy_spy.calls[0]
    assert "contour_m" not in kwargs
    assert "max_post_swaps" not in kwargs


def test_negative_max_swaps_rejected():
    """A41 fix-wave T8: --max-swaps is a budget, not a signed offset -- argparse must reject a
    negative value loudly instead of routing to the sweep with a nonsensical budget."""
    with pytest.raises(SystemExit):
        ar._parse_args(ARGV_COMMON + ["--out", "x", "--max-swaps", "-1"])


def test_default_max_swaps_routes_to_sweep_with_default_budget_and_contour(monkeypatch, tmp_path):
    sweep_spy = _Spy(ret={"status": "waiting"})
    monkeypatch.setattr(ar.sweep, "run_sweep", sweep_spy)
    ar.main(ARGV_COMMON + ["--out", str(tmp_path)])
    assert sweep_spy.calls
    _, kwargs = sweep_spy.calls[0]
    assert kwargs["max_post_swaps"] == 6
    assert kwargs["contour_m"] == 150.0


def test_custom_max_swaps_and_contour_m_pass_through(monkeypatch, tmp_path):
    sweep_spy = _Spy(ret={"status": "waiting"})
    monkeypatch.setattr(ar.sweep, "run_sweep", sweep_spy)
    ar.main(ARGV_COMMON + ["--out", str(tmp_path), "--max-swaps", "3", "--contour-m", "75.5"])
    _, kwargs = sweep_spy.calls[0]
    assert kwargs["max_post_swaps"] == 3
    assert kwargs["contour_m"] == 75.5


def test_relative_out_reaches_the_sweep_absolute(monkeypatch, tmp_path):
    """A relative --out reaches WBT's breach step on incised fires and dies with a misleading
    'returned 0 but did not write' GateAbort -- absolutized once at the CLI boundary."""
    monkeypatch.chdir(tmp_path)
    sweep_spy = _Spy(ret={"status": "waiting"})
    monkeypatch.setattr(ar.sweep, "run_sweep", sweep_spy)
    ar.main(ARGV_COMMON + ["--out", "rel_out"])
    _, kwargs = sweep_spy.calls[0]
    assert kwargs["out_dir"].is_absolute()
    assert kwargs["out_dir"] == Path(tmp_path).resolve() / "rel_out"


def test_relative_out_reaches_the_legacy_path_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    legacy_spy = _Spy(ret=_package("waiting"))
    monkeypatch.setattr(ar, "run_autoacquire", legacy_spy)
    ar.main(ARGV_COMMON + ["--out", "rel_out", "--max-swaps", "0"])
    _, kwargs = legacy_spy.calls[0]
    assert kwargs["out_dir"].is_absolute()


def test_approve_flag_passes_through_to_sweep(monkeypatch, tmp_path):
    sweep_spy = _Spy(ret={"status": "waiting"})
    monkeypatch.setattr(ar.sweep, "run_sweep", sweep_spy)
    ar.main(ARGV_COMMON + ["--out", str(tmp_path), "--approve"])
    _, kwargs = sweep_spy.calls[0]
    assert kwargs["approve"] is True


def test_unapproved_sweep_prints_recommendation_like_legacy(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ar.sweep, "run_sweep", _Spy(ret=_package()))
    ar.main(ARGV_COMMON + ["--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert "pre : P" in out
    assert "Re-run with --approve" in out


def test_degraded_sweep_prints_chosen_and_refused_count(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ar.sweep, "run_sweep",
                        _Spy(ret=_degraded_sweep_result(tmp_path)))
    ar.main(ARGV_COMMON + ["--out", str(tmp_path), "--approve"])
    out = capsys.readouterr().out
    assert "pre : P (2026-06-04)" in out        # dated, from the chosen record itself
    assert "Q1" in out
    assert "1 basin(s) refused" in out
    assert "refused_basins.csv" in out          # hazard-unknown pointer (degraded only)


def test_degraded_sweep_prints_refused_phase1_ids(monkeypatch, tmp_path, capsys):
    """Degraded runs name the refused basins on stdout, not just a count + sidecar pointer."""
    monkeypatch.setattr(ar.sweep, "run_sweep",
                        _Spy(ret=_degraded_sweep_result(tmp_path)))
    ar.main(ARGV_COMMON + ["--out", str(tmp_path), "--approve"])
    out = capsys.readouterr().out
    assert "refused (phase-1 basin ids): 7" in out


def test_clean_sweep_prints_chosen_without_hazard_pointer(monkeypatch, tmp_path, capsys):
    clean = _degraded_sweep_result(tmp_path)
    clean["status"] = "clean"
    clean["chosen"]["refused_count"] = 0
    clean["refused"] = []
    monkeypatch.setattr(ar.sweep, "run_sweep", _Spy(ret=clean))
    ar.main(ARGV_COMMON + ["--out", str(tmp_path), "--approve"])
    out = capsys.readouterr().out
    assert "0 basin(s) refused" in out
    assert "refused_basins.csv" not in out


def test_aborted_sweep_prints_message_and_attempt_count(monkeypatch, tmp_path, capsys):
    aborted = {"status": "aborted", "package": {"status": "recommended"},
              "attempts": [{"sensor": "S2"}, {"sensor": "S2"}],
              "message": "no attempt produced a ranking; see attempts."}
    monkeypatch.setattr(ar.sweep, "run_sweep", _Spy(ret=aborted))
    ar.main(ARGV_COMMON + ["--out", str(tmp_path), "--approve"])
    out = capsys.readouterr().out
    assert "no attempt produced a ranking" in out
    assert "2" in out


def test_degraded_sweep_result_json_round_trips_with_no_result_key(monkeypatch, tmp_path):
    monkeypatch.setattr(ar.sweep, "run_sweep",
                        _Spy(ret=_degraded_sweep_result(tmp_path, leak_result=True)))
    ar.main(ARGV_COMMON + ["--out", str(tmp_path), "--approve"])
    text = (tmp_path / "autoacquire_result.json").read_text()
    assert "array(" not in text
    parsed = json.loads(text)               # must not raise
    assert "result" not in parsed
    assert "pipeline" not in parsed
    assert "masks" not in parsed
    assert parsed["status"] == "degraded"
    assert isinstance(parsed["chosen"]["post_date"], str)
