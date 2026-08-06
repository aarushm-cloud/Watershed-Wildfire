"""The bounded scene sweep (A41 Task 6): one approval covers the recommended pair -> its
vetted alternate posts -> the other sensor. First zero-refused attempt wins outright; else the
best attempt by the FROZEN score-blind key (fewest refused -> lowest total nodata -> earliest
post). The winner's artifacts are promoted to the fire-level dir; losers stay quarantined.

Fully hermetic: every seam (select/create/stage/attach/pipeline/write) is injected, so no
network, no rasters, no DEM. The fake attach_fn reproduces acquire.attach_dnbr's real
precondition (a manifest must already sit in fire["out_dir"]), which is what locks the
sweep's per-attempt manifest copy.

Run:  pytest tests/acquire/test_sweep.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from autoacquire.sweep import MAX_POST_SWAPS, run_sweep  # noqa: E402
from src.grids import GateAbort  # noqa: E402

BBOX = (-105.7916, 33.3255, -105.6361, 33.4135)
IGNITION, CONTAINMENT = date(2026, 6, 1), date(2026, 6, 20)


def _scene(sensor, i, day=None):
    return {"id": f"{sensor}-{i}", "sensor": sensor,
            "date": f"2026-07-{(day if day is not None else 10 + i):02d}"}


def _pkg(sensor, n_alts=2, alts=None):
    """A canned selector package: recommended pair + pre-vetted alternate posts."""
    posts = alts if alts is not None else [_scene(sensor, 2), _scene(sensor, 3)][:n_alts]
    return {"status": "recommended",
            "pair": {"sensor": sensor, "pre": _scene(sensor, 0), "post": _scene(sensor, 1),
                     "metrics": {}},
            "alternatives": {"pre": [], "post": posts}}


def _result(n_refused, n_clean=3, clean_nodata=0.0, scores=None):
    """A run_pipeline dNBR both-arms return, shaped as pipeline.py:475-490 really returns it."""
    def b(i, f):
        return {"basin_id": i, "nodata_frac": f, "mask": None, "area_km2": 1.0}
    clean = [b(100 + i, clean_nodata) for i in range(n_clean)]
    for rec, s in zip(clean, scores or []):
        rec["score"], rec["rank"] = s, 1               # ranking content: must never be read
    return {"status": "ranked", "terrain_mode": "range_front", "creek_nearest": None,
            "subbasin_meta": None,
            "refused_basins": [b(i, 0.5) for i in range(n_refused)],
            "arms": {"arm_a": {"basins": clean}, "arm_b": {"basins": []}}}


class _Create:
    """Marks an outcome as raised by create_fn (download/403/zone) rather than pipeline_fn."""

    def __init__(self, exc):
        self.exc = exc


class Harness:
    """Scripted seams. `outcomes[i]` is consumed by attempt i: a result dict (pipeline returns
    it), an Exception (pipeline raises it), or _Create(exc) (create_fn raises it)."""

    def __init__(self, tmp_path, outcomes, packages=None):
        self.tmp = Path(tmp_path)
        self.outcomes = list(outcomes)
        self.packages = packages or {}
        self.calls, self.writes = [], []

    def seams(self):
        return {"select_fn": self.select, "create_fn": self.create, "stage_fn": self.stage,
                "attach_fn": self.attach, "pipeline_fn": self.pipeline, "write_fn": self.write}

    def _pop(self):
        assert self.outcomes, "sweep ran more attempts than the script provides"
        return self.outcomes.pop(0)

    def select(self, bbox, *, ignition, containment, sensors=("S2",), **kw):
        self.calls.append(("select", tuple(sensors)))
        pkg = self.packages.get(sensors[0], _pkg(sensors[0]))
        if isinstance(pkg, Exception):
            raise pkg
        return pkg

    def stage(self, bbox, out_dir, *, name="fire", buf_deg=None):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "acquisition_manifest.json").write_text(
            json.dumps({"fire": name, "dnbr_upload": None}))
        self.calls.append(("stage", str(out_dir)))
        return {"name": name, "dem": str(self.tmp / "dem.tif"), "sbs": None, "dnbr": None,
                "assets": None, "creeks": None, "out_dir": out_dir}

    def create(self, pair, bbox, out_dir, *, name="fire"):
        self.calls.append(("create", pair["post"]["id"]))
        if self.outcomes and isinstance(self.outcomes[0], _Create):
            raise self._pop().exc
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tif = out_dir / f"dnbr_{name}_raw.tif"
        tif.write_text(f"dnbr from {pair['post']['id']}")
        return {"dnbr_tif": str(tif)}

    def attach(self, fire, dnbr_path):
        # Mirrors acquire.attach_dnbr: read-modify-WRITE of the manifest in fire["out_dir"],
        # GateAbort(scope="fire") when it is absent.
        mpath = Path(fire["out_dir"]) / "acquisition_manifest.json"
        if not mpath.exists():
            raise GateAbort(f"FAIL: no acquisition manifest at {mpath}", scope="fire")
        manifest = json.loads(mpath.read_text())
        manifest["dnbr_upload"] = {"p99_abs": 1.0, "src": str(dnbr_path)}
        mpath.write_text(json.dumps(manifest))
        self.calls.append(("attach", str(fire["out_dir"])))
        fire["dnbr"] = str(dnbr_path)
        return fire

    def pipeline(self, fire, contour_m=None):
        self.calls.append(("pipeline", str(fire["out_dir"])))
        out = self._pop()
        if isinstance(out, Exception):
            raise out
        return out

    def write(self, arm_a, arm_b, creek_nearest, out_dir, dem_tif, validation_case,
              incised=False, subbasin_meta=None, refused=None, imagery=None):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path, gj_path = out_dir / "ranking.csv", out_dir / "basins.geojson"
        csv_path.write_text(f"# imagery: {imagery['sensor']} pre {imagery['pre_id']} -> "
                            f"post {imagery['post_id']} ({imagery['post_date']})\n")
        gj_path.write_text("{}")
        self.writes.append({"out_dir": str(out_dir), "imagery": imagery,
                            "n_refused": len(refused or [])})
        return csv_path, gj_path


def _sweep(tmp_path, outcomes, packages=None, **kw):
    h = Harness(tmp_path, outcomes, packages)
    out = tmp_path / "fire"
    res = run_sweep(BBOX, ignition=IGNITION, containment=CONTAINMENT, out_dir=out,
                    name="laguna", approve=True, **{**h.seams(), **kw})
    return h, out, res


def _n_attempts(h):
    return sum(1 for c in h.calls if c[0] == "create")


# ---- winning ------------------------------------------------------------------------------

def test_zero_refused_wins_immediately(tmp_path):
    """A clean first attempt stops the sweep: no alternates, no sensor fallback."""
    h, out, res = _sweep(tmp_path, [_result(0), _result(0), _result(0)])
    assert res["status"] == "clean"
    assert res["refused"] == []
    assert _n_attempts(h) == 1
    assert len(res["attempts"]) == 1
    assert res["chosen"]["post_id"] == "S2-1"
    assert ("select", ("Landsat",)) not in h.calls


def test_walks_alternates_then_wins(tmp_path):
    """A degraded first attempt is recorded, and the first vetted alternate post wins."""
    h, out, res = _sweep(tmp_path, [_result(2), _result(0)])
    assert res["status"] == "clean"
    assert _n_attempts(h) == 2
    assert [a["outcome"] for a in res["attempts"]] == ["ranked", "ranked"]
    assert res["attempts"][0]["refused_count"] == 2
    assert res["chosen"]["post_id"] == "S2-2"


def test_sensor_fallback_engages(tmp_path):
    """S2 exhausted without a clean attempt -> select(Landsat) and sweep that package too."""
    h, out, res = _sweep(tmp_path, [_result(2), _result(2), _result(2), _result(0)])
    assert res["status"] == "clean"
    assert res["chosen"]["sensor"] == "Landsat"
    assert [c[1] for c in h.calls if c[0] == "select"] == [("S2",), ("Landsat",)]
    assert _n_attempts(h) == 4


def test_second_sensor_select_failure_is_sensor_scoped(tmp_path):
    """A mid-sweep select() GateAbort (the MPC-403 DoS mode) is recorded, never fatal."""
    boom = GateAbort("MPC 403 during mask reads")
    h, out, res = _sweep(tmp_path, [_result(1), _result(1), _result(1)],
                         packages={"Landsat": boom})
    assert res["status"] == "degraded"
    assert "MPC 403" in res["attempts"][-1]["outcome"]
    assert res["attempts"][-1]["sensor"] == "Landsat"


# ---- the frozen selection key -------------------------------------------------------------

def test_best_attempt_selection_key(tmp_path):
    """Fewest refused first; ties broken by lower total nodata."""
    h, out, res = _sweep(tmp_path,
                         [_result(2), _result(1, clean_nodata=0.1), _result(1, clean_nodata=0.0)],
                         sensors=("S2",))
    assert res["status"] == "degraded"
    assert res["chosen"]["post_id"] == "S2-3"          # 1 refused AND the lower total nodata
    assert res["chosen"]["refused_count"] == 1
    assert res["attempts"][1]["total_nodata_frac"] > res["attempts"][2]["total_nodata_frac"]


def test_selection_key_final_tiebreak_is_earliest_post(tmp_path):
    """Identical coverage -> the earliest post date wins, even when it is not attempted first."""
    early = _scene("S2", 9, day=5)                     # an alternate that predates the base post
    h, out, res = _sweep(tmp_path, [_result(1), _result(1)],
                         packages={"S2": _pkg("S2", alts=[early])}, sensors=("S2",))
    assert res["chosen"]["post_id"] == "S2-9"
    assert res["chosen"]["post_date"] == "2026-07-05"


def test_selection_is_score_blind(tmp_path):
    """Mutating scores/ranks cannot move the winner: the key reads coverage only."""
    chosen = []
    for scores in ([9.9, 9.9, 9.9], [0.001, 0.001, 0.001]):
        # attempt 0: better scores but worse coverage; attempt 1: the coverage winner.
        outcomes = [_result(2, scores=scores), _result(1, scores=list(reversed(scores)))]
        _, _, res = _sweep(tmp_path / f"s{len(chosen)}", outcomes, sensors=("S2",),
                           packages={"S2": _pkg("S2", n_alts=1)})
        chosen.append(res["chosen"]["post_id"])
    assert chosen == ["S2-2", "S2-2"]                  # unchanged under score mutation


# ---- abort classification -----------------------------------------------------------------

def test_fire_scoped_abort_stops_sweep(tmp_path):
    """A DEM-deterministic abort recurs on every attempt -> stop loud, do not retry past it."""
    h = Harness(tmp_path, [GateAbort("contour outside DEM range", scope="fire"), _result(0)])
    with pytest.raises(GateAbort, match="contour outside DEM range"):
        run_sweep(BBOX, ignition=IGNITION, containment=CONTAINMENT, out_dir=tmp_path / "fire",
                  name="laguna", approve=True, **h.seams())
    assert _n_attempts(h) == 1


def test_attempt_scoped_abort_continues(tmp_path):
    """A scene-dependent abort is recorded and the sweep tries the next vetted pair."""
    h, out, res = _sweep(tmp_path, [_Create(GateAbort("MPC 403 on asset download",
                                                      scope="attempt")), _result(0)])
    assert res["status"] == "clean"
    assert res["attempts"][0]["outcome"].startswith("abort: ")
    assert "MPC 403" in res["attempts"][0]["outcome"]
    assert res["attempts"][0]["refused_count"] is None  # never a ranking candidate
    assert len(res["attempts"]) == 2


def test_untagged_create_abort_is_attempt_scoped(tmp_path):
    """PRODUCTION shape: every GateAbort dnbr_create raises carries the DEFAULT "fire" scope
    (it is untouched by this build), so the sweep must re-tag create_fn aborts as per-pair --
    otherwise a single 403 on attempt 1 kills the whole sweep. Literal dnbr_create:259 text."""
    boom = GateAbort("band read failed for scene S2-1 (B8A): RasterioIOError: "
                     "HTTP response code: 403 (A8)")
    assert boom.scope == "fire"                        # the default the fix must handle
    h, out, res = _sweep(tmp_path, [_Create(boom), _result(0)])
    assert res["status"] == "clean"
    assert "band read failed" in res["attempts"][0]["outcome"]
    assert res["chosen"]["post_id"] == "S2-2"          # continued to the next vetted pair
    assert _n_attempts(h) == 2


def test_untagged_abort_outside_create_still_stops_the_sweep(tmp_path):
    """The re-tag is scoped to create_fn alone: a default-scope abort from run_pipeline is
    DEM-deterministic and must still stop the sweep loud (spec 5)."""
    h = Harness(tmp_path, [GateAbort("contour 150 m outside the DEM range"), _result(0)])
    with pytest.raises(GateAbort, match="contour 150 m outside"):
        run_sweep(BBOX, ignition=IGNITION, containment=CONTAINMENT, out_dir=tmp_path / "fire",
                  approve=True, **h.seams())
    assert _n_attempts(h) == 1


def test_missing_nodata_frac_fails_loud(tmp_path):
    """The selection key's coverage input is read directly: a broken upstream invariant must
    raise, never be read as perfect coverage (a silent 0.0 would mis-pick the winner)."""
    broken = _result(0)
    del broken["arms"]["arm_a"]["basins"][0]["nodata_frac"]
    h = Harness(tmp_path, [broken])
    with pytest.raises(KeyError, match="nodata_frac"):
        run_sweep(BBOX, ignition=IGNITION, containment=CONTAINMENT, out_dir=tmp_path / "fire",
                  approve=True, **h.seams())


def test_non_gateabort_propagates(tmp_path):
    """A8: an unclassified failure is never swallowed by the sweep loop."""
    h = Harness(tmp_path, [RuntimeError("rasterio blew up"), _result(0)])
    with pytest.raises(RuntimeError, match="rasterio blew up"):
        run_sweep(BBOX, ignition=IGNITION, containment=CONTAINMENT, out_dir=tmp_path / "fire",
                  approve=True, **h.seams())


def test_zero_clean_everywhere_aborts(tmp_path):
    """Every attempt aborts -> status 'aborted', the trail is returned, and NO fire-level
    ranking artifact is ever written."""
    aborts = [GateAbort("every basin exceeds the NoData bar", scope="attempt") for _ in range(6)]
    h, out, res = _sweep(tmp_path, aborts)
    assert res["status"] == "aborted"
    assert len(res["attempts"]) == 6
    assert "no attempt produced a ranking" in res["message"]
    assert not (out / "ranking.csv").exists()
    assert not (out / "basins.geojson").exists()
    assert "chosen" not in res


# ---- the approval gate --------------------------------------------------------------------

def test_gate_closed_without_approve(tmp_path):
    """approve=False returns the first package untouched: machine proposes, human disposes."""
    h = Harness(tmp_path, [_result(0)])
    pkg = _pkg("S2")
    h.packages["S2"] = pkg
    out = tmp_path / "fire"
    res = run_sweep(BBOX, ignition=IGNITION, containment=CONTAINMENT, out_dir=out,
                    approve=False, **h.seams())
    assert res is pkg
    assert [c[0] for c in h.calls] == ["select"]       # nothing staged, nothing fetched
    assert not out.exists()


def test_selector_failure_state_passes_through(tmp_path):
    """An honest no-pair state is surfaced verbatim, never converted into a sweep."""
    state = {"status": "waiting_for_pass", "message": "next overpass 2026-07-14"}
    h = Harness(tmp_path, [_result(0)], packages={"S2": state})
    res = run_sweep(BBOX, ignition=IGNITION, containment=CONTAINMENT, out_dir=tmp_path / "fire",
                    approve=True, **h.seams())
    assert res is state
    assert [c[0] for c in h.calls] == ["select"]


# ---- artifacts ----------------------------------------------------------------------------

def _assert_scalars(node, where="root"):
    if isinstance(node, dict):
        for k, v in node.items():
            _assert_scalars(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _assert_scalars(v, f"{where}[{i}]")
    else:
        assert node is None or isinstance(node, (str, int, float, bool)), f"{where}={node!r}"


def test_winner_promoted_and_attempts_json(tmp_path):
    """The fire-level dir holds exactly the WINNER's artifacts; losers stay under attempts/."""
    h, out, res = _sweep(tmp_path, [_result(2), _result(0)])
    promoted = (out / "ranking.csv").read_text()
    assert "post S2-2" in promoted                     # the winner's pair, and only it
    assert "post S2-1" not in promoted
    assert (out / "basins.geojson").exists()
    assert (out / "dnbr" / "dnbr_laguna_raw.tif").read_text() == "dnbr from S2-2"
    # losers quarantined, not deleted
    assert (out / "attempts" / "attempt_00" / "ranking.csv").exists()
    assert "post S2-1" in (out / "attempts" / "attempt_00" / "ranking.csv").read_text()

    trail = json.loads((out / "sweep_attempts.json").read_text())
    _assert_scalars(trail)
    assert "array(" not in (out / "sweep_attempts.json").read_text()
    assert len(trail["attempts"]) == 2
    assert trail["chosen"] == {"sensor": "S2", "pre_id": "S2-0", "pre_date": "2026-07-10",
                               "post_id": "S2-2", "post_date": "2026-07-12"}
    assert "ranking content never consulted" in trail["selection"]
    _assert_scalars(res["attempts"])
    _assert_scalars(res["refused"])


def test_each_attempt_completes_its_own_manifest_copy(tmp_path):
    """attach_dnbr completes the manifest in fire["out_dir"], so each attempt dir needs its own
    copy of the staged manifest -- and the winner's completed copy is promoted back."""
    h, out, res = _sweep(tmp_path, [_result(1), _result(0)])
    for n in ("attempt_00", "attempt_01"):
        m = json.loads((out / "attempts" / n / "acquisition_manifest.json").read_text())
        assert m["dnbr_upload"] is not None
    fire_level = json.loads((out / "acquisition_manifest.json").read_text())
    assert "attempt_01" in fire_level["dnbr_upload"]["src"]   # the winner's, not attempt 0's


def test_refused_metadata_and_degraded_status(tmp_path):
    """A degraded winner reports its refused basins in the phase-1 id space (A41/A39)."""
    h, out, res = _sweep(tmp_path, [_result(2)], sensors=("S2",),
                         packages={"S2": _pkg("S2", n_alts=0)})
    assert res["status"] == "degraded"
    assert res["refused"] == [{"phase1_basin_id": 0, "nodata_frac": 0.5},
                              {"phase1_basin_id": 1, "nodata_frac": 0.5}]
    assert h.writes[-1]["n_refused"] == 2               # the writer got the full records
    assert res["attempts"][0]["n_basins_total"] == 5    # 2 refused + 3 clean (phase-1 total)


def test_stale_conditional_artifact_is_purged_on_promote(tmp_path):
    """A previous run's refusal sidecar must not survive beside a clean winner's artifacts."""
    out = tmp_path / "fire"
    out.mkdir(parents=True)
    (out / "refused_basins.csv").write_text("phase1_basin_id\n7\n")
    h = Harness(tmp_path, [_result(0)])
    run_sweep(BBOX, ignition=IGNITION, containment=CONTAINMENT, out_dir=out, approve=True,
              **h.seams())
    assert not (out / "refused_basins.csv").exists()


def test_real_seam_signatures_bind():
    """Every test above runs on fakes, so this is the one guard that the sweep's real call
    shapes still match the real seams (signature drift would otherwise surface only live)."""
    import inspect

    import acquire
    from autoacquire import dnbr_create, scene_select
    from src import outputs, pipeline

    sig = inspect.signature
    sig(scene_select.select).bind(BBOX, ignition=IGNITION, containment=CONTAINMENT,
                                  sensors=("S2",), greenup_days=90)
    sig(acquire.stage_fire).bind(BBOX, "out", name="laguna")
    sig(acquire.attach_dnbr).bind({}, "dnbr.tif")
    sig(dnbr_create.create_dnbr).bind({}, BBOX, "out/dnbr", name="laguna")
    sig(pipeline.run_pipeline).bind({}, contour_m=150.0)
    sig(outputs.write_dnbr_outputs).bind(
        {}, {}, None, "out", "dem.tif", validation_case="v", incised=False,
        subbasin_meta=None, refused=[], imagery={})


def test_max_post_swaps_bounds_the_sweep(tmp_path):
    """The cap is honored per sensor, and the owned A41 default is 6."""
    assert MAX_POST_SWAPS == 6
    h, out, res = _sweep(tmp_path, [_result(1)] * 4, sensors=("S2",), max_post_swaps=1)
    assert _n_attempts(h) == 2                          # base pair + one alternate only
