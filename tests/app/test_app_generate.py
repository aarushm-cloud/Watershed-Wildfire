"""The Generate-from-dates approval UI (B4).

Pure-helper + AppTest coverage for app.py's auto-acquire mode: generate_package's F5
error reduction, the deterministic scorecard view (cloud-over-fire headline, tile-cloud
de-emphasized, value-free timing flag), run_generated_screening's contract (A41: one
Approve triggers a bounded sweep over the vetted family, not just the displayed pair;
every failure a legible dict; a sweep GateAbort/aborted/stale-selector-state reduces to
kind=='refused', never a ranking), the F8 staleness key folding mode + dates + the
selected pair, the approve-branch idempotence guard (a queued second click must not
re-run the sweep) + burnmap-preview cleanup, and an AppTest smoke that the toggle
defaults to Upload (existing tests untouched) and the Generate panel renders date
inputs + the Find button.

Run:  pytest tests/app/test_app_generate.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import app  # noqa: E402
from autoacquire import scene_select as ss  # noqa: E402
from autoacquire import sweep  # noqa: E402
from src.grids import GateAbort  # noqa: E402

BBOX = (-122.145, 38.455, -121.985, 38.595)
# app.py's own drawn-box default (main()'s South Fork fallback) -- reused so AppTest render
# tests can stamp a matching `inputs` and avoid a spurious "inputs changed" warning.
_APP_DEFAULT_BBOX = (-105.79156, 33.32552, -105.63614, 33.41352)


def _real_png_bytes() -> bytes:
    """A real, PIL-decodable 1x1 PNG -- st.image() (unlike st.download_button) actually opens
    the bytes, so a magic-number-only fake is not enough once a test drives full AppTest render."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return buf.getvalue()


def _package(status="recommended", *, greenup_days=90, post_date=date(2026, 7, 7)):
    pkg = {
        "status": status,
        "framing": {"screening": "s", "dnbr": "d"},
        "rejected": [],
        "message": "m",
    }
    if status == "recommended":
        pkg["pair"] = {
            "sensor": "S2",
            "pre": {"id": "PRE", "sensor": "S2", "date": date(2026, 6, 4),
                    "tile_cloud_pct": 12.0},
            "post": {"id": "POST", "sensor": "S2", "date": post_date,
                     "tile_cloud_pct": 45.0},
            "metrics": {"pre_valid_frac": 0.98, "post_valid_frac": 0.91,
                        "pair_valid_frac": 0.90},
            "verdict": {"verdict": "good", "summary": "covers ~90% of your fire area."},
        }
        pkg["alternatives"] = {"pre": [{"id": "PRE2"}], "post": []}
        pkg["provenance"] = {"windows": {
            "pre": ("2026-03-10", "2026-06-08"),
            "post": ("2026-06-20", (date(2026, 6, 20)).isoformat()),
            "greenup_days": greenup_days, "widened": False,
        }}
        pkg["provenance"]["windows"]["post"] = ("2026-06-20", "2026-09-18")
    if status == "waiting":
        pkg.update({"passes_tried": 2, "next_overpass_eta": date(2026, 7, 22),
                    "eta_caveat": "not necessarily clear", "greenup_deadline": date(2026, 10, 11)})
    return pkg


# ---- generate_package (F5 reduction) ----


def test_generate_package_happy(monkeypatch):
    monkeypatch.setattr(ss, "select", lambda *a, **k: _package())
    out = app.generate_package(BBOX, date(2026, 6, 8), date(2026, 6, 20))
    assert out["kind"] == "package"
    assert out["package"]["status"] == "recommended"


def test_generate_package_reduces_gateabort_verbatim(monkeypatch):
    def _boom(*a, **k):
        raise GateAbort("STAC search failed: HTTP 503 (A8)")
    monkeypatch.setattr(ss, "select", _boom)
    out = app.generate_package(BBOX, date(2026, 6, 8), date(2026, 6, 20))
    assert out["kind"] == "error" and "503" in out["message"]


def test_generate_package_backstop_names_the_exception(monkeypatch):
    def _boom(*a, **k):
        raise KeyError("weird")
    monkeypatch.setattr(ss, "select", _boom)
    out = app.generate_package(BBOX, date(2026, 6, 8), date(2026, 6, 20))
    assert out["kind"] == "error" and "KeyError" in out["message"]


def test_generate_package_validates_bbox_first(monkeypatch):
    called = []
    monkeypatch.setattr(ss, "select", lambda *a, **k: called.append(1))
    out = app.generate_package((10, 20, 5, 30), date(2026, 6, 8), date(2026, 6, 20))
    assert out["kind"] == "error" and called == []      # bad bbox never reaches the network


# ---- scorecard view (deterministic; headline = cloud over YOUR fire) ----


def test_scorecard_view_headline_and_tile_deemphasis():
    sc = app.scorecard_view(_package())
    assert sc["verdict"] == "good" and sc["icon"] == "✅"
    assert sc["pair_valid_pct"] == 90.0
    pre, post = sc["scenes"]
    assert pre["role"] == "Pre-fire" and pre["cloud_over_fire_pct"] == pytest.approx(2.0)
    assert post["cloud_over_fire_pct"] == pytest.approx(9.0)
    assert "whole tile" in post["tile_note"]            # tile% shown but de-emphasized
    assert sc["timing_flag"] is None                    # 90 d default window: no flag


def test_scorecard_timing_flag_only_when_operator_extended():
    # Post scene beyond containment+90 d, reachable only via the operator override:
    pkg = _package(greenup_days=180, post_date=date(2026, 10, 20))
    sc = app.scorecard_view(pkg)
    assert sc["timing_flag"] and "green-up" in sc["timing_flag"]


# ---- run_generated_screening (A41: approve triggers a bounded SWEEP, not the single pair) ----

SWEEP_INPUTS = {"ignition": date(2026, 6, 8), "containment": date(2026, 6, 20), "greenup_days": 90}


_SQUARE = [[-105.7, 33.35], [-105.7, 33.36], [-105.69, 33.36], [-105.69, 33.35], [-105.7, 33.35]]


def _fake_run_sweep(status="clean", *, refused_ids=(), chosen=None, attempts=None, message=None,
                    incised=False):
    """A monkeypatch replacement for autoacquire.sweep.run_sweep that WRITES the real artifact
    files run_generated_screening reads back off disk (ranking.csv, basins.geojson, ...) into
    whatever out_dir it is called with -- mirrors the actual sweep.py/write_dnbr_outputs
    contract instead of over-mocking internals. `.calls` records every invocation's kwargs."""
    chosen = chosen or {"sensor": "S2", "pre_id": "PRE", "pre_date": "2026-06-04",
                        "post_id": "POST", "post_date": "2026-07-07", "outcome": "ranked",
                        "refused_count": len(refused_ids),
                        "n_basins_total": 1 + len(refused_ids), "total_nodata_frac": 0.0}
    attempts = attempts if attempts is not None else [chosen]
    calls = []

    def _fn(bbox, *, ignition, containment, out_dir, name="fire", **kw):
        calls.append({"bbox": bbox, "ignition": ignition, "containment": containment,
                      "out_dir": out_dir, "name": name, **kw})
        if status not in ("clean", "degraded"):
            return {"status": status, "package": {}, "attempts": attempts,
                    "message": message or f"selector/sweep: {status}"}
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "ranking.csv").write_bytes(b"# ranking\nbasin_id,rank\nb1,1\n")
        provenance = {}
        if refused_ids:
            provenance["refused_count"] = len(refused_ids)
            provenance["n_basins_total"] = 1 + len(refused_ids)
        if incised:   # F2: mirrors write_dnbr_outputs's real provenance.incised_framing sniff key
            provenance["incised_framing"] = "EXPLORATORY -- incised terrain (fake, test-only text)"
        fc = {"type": "FeatureCollection",
              "features": [{"type": "Feature",
                           "properties": {"basin_id": "b1", "rank": 1, "rank_b": 1, "score": 1.0,
                                         "score_b": 1.0, "rank_delta": 0},
                           "geometry": {"type": "Polygon", "coordinates": [_SQUARE]}}],
              "provenance": provenance}
        (out_dir / "basins.geojson").write_text(json.dumps(fc))
        (out_dir / "sweep_attempts.json").write_text(
            json.dumps({"attempts": attempts, "chosen": chosen}))
        if refused_ids:
            rgj = {"type": "FeatureCollection",
                  "features": [{"type": "Feature", "properties": {"phase1_basin_id": r},
                                "geometry": {"type": "Polygon", "coordinates": [_SQUARE]}}
                               for r in refused_ids]}
            (out_dir / "refused_basins.geojson").write_text(json.dumps(rgj))
            (out_dir / "refused_basins.csv").write_bytes(
                b"phase1_basin_id,nodata_frac,reason\n" +
                "\n".join(f"{r},0.9,cloud" for r in refused_ids).encode())
        dnbr_dir = out_dir / "dnbr"; dnbr_dir.mkdir(exist_ok=True)
        (dnbr_dir / f"dnbr_{name}_quicklook.png").write_bytes(_real_png_bytes())
        (dnbr_dir / f"dnbr_{name}_provenance.json").write_text('{"sensor": "S2"}')
        return {"status": status, "package": {}, "attempts": attempts, "chosen": chosen,
                "refused": [{"phase1_basin_id": r, "nodata_frac": 0.9} for r in refused_ids],
                "result_paths": {"out_dir": str(out_dir)}}
    _fn.calls = calls
    return _fn


def test_run_generated_screening_clean_sweep_is_ranked(monkeypatch):
    fake = _fake_run_sweep("clean")
    monkeypatch.setattr(sweep, "run_sweep", fake)
    out = app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert out["kind"] == "ranked" and out["n"] == 1
    assert out["sweep_status"] == "clean"
    assert out["chosen"]["sensor"] == "S2" and out["chosen"]["post_id"] == "POST"
    assert out["chosen"]["pre_date"] == "2026-06-04"      # carried by the chosen record itself
    assert out["quicklook"].startswith(b"\x89PNG")
    assert out["dnbr_provenance"] == {"sensor": "S2"}
    assert "refused_geojson" not in out                  # clean run: no sidecar to surface
    assert len(fake.calls) == 1
    assert fake.calls[0]["ignition"] == SWEEP_INPUTS["ignition"]
    assert fake.calls[0]["containment"] == SWEEP_INPUTS["containment"]


def test_run_generated_screening_degraded_sweep_carries_refused_geojson(monkeypatch):
    fake = _fake_run_sweep("degraded", refused_ids=["p1", "p2"])
    monkeypatch.setattr(sweep, "run_sweep", fake)
    out = app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert out["kind"] == "ranked" and out["sweep_status"] == "degraded"
    assert out["fc"]["provenance"]["refused_count"] == 2
    assert out["fc"]["provenance"]["n_basins_total"] == 3
    assert len(out["refused_geojson"]["features"]) == 2


def test_run_generated_screening_degraded_sweep_carries_refused_csv_bytes(monkeypatch):
    """Review fix F3: refused_basins.csv must be read into the payload BEFORE the sweep's
    tempdir is rmtree'd -- the degraded banner tells the user to consult that exact file."""
    fake = _fake_run_sweep("degraded", refused_ids=["p1", "p2"])
    monkeypatch.setattr(sweep, "run_sweep", fake)
    out = app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert out["refused_csv"].startswith(b"phase1_basin_id")
    assert b"p1" in out["refused_csv"] and b"p2" in out["refused_csv"]


def test_run_generated_screening_reads_incised_framing_sniff_true(monkeypatch):
    """Review fix F2: screen['incised'] is sniffed off basins.geojson's
    provenance.incised_framing key (the sweep-backed path has no terrain_mode of its own to
    read directly). Positive case."""
    fake = _fake_run_sweep("clean", incised=True)
    monkeypatch.setattr(sweep, "run_sweep", fake)
    out = app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert out["incised"] is True


def test_run_generated_screening_reads_incised_framing_sniff_false(monkeypatch):
    """Review fix F2: negative case -- no incised_framing key -> incised is False, not truthy
    by accident (e.g. from a stray None/empty-string key)."""
    fake = _fake_run_sweep("clean", incised=False)
    monkeypatch.setattr(sweep, "run_sweep", fake)
    out = app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert out["incised"] is False


def test_run_generated_screening_non_recommended_status_is_refused(monkeypatch):
    # The selector's own honest states (waiting/window_closed/no_pre_scene) can resurface
    # if conditions changed between the scorecard render and the Approve click.
    fake = _fake_run_sweep("window_closed", message="past the greenup deadline")
    monkeypatch.setattr(sweep, "run_sweep", fake)
    out = app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert out["kind"] == "refused" and "greenup" in out["message"]


def test_run_generated_screening_aborted_sweep_is_refused_not_a_ranking(monkeypatch):
    attempts = [{"sensor": "S2", "post_id": "P1", "outcome": "abort: MPC 403"}]
    fake = _fake_run_sweep("aborted", message="no attempt produced a ranking; see attempts.",
                          attempts=attempts)
    monkeypatch.setattr(sweep, "run_sweep", fake)
    out = app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert out["kind"] == "refused" and "no attempt" in out["message"]
    assert out["attempts"] == attempts                    # the trail survives even on a refusal


def test_run_generated_screening_gateabort_from_sweep_is_refused_not_error(monkeypatch):
    # Fire-scoped GateAborts (e.g. stage_fire failing before any attempt) propagate out of
    # run_sweep; they must reduce to a legible refusal, never a raw error or a ranking.
    def _boom(*a, **k):
        raise GateAbort("no acquisition manifest -- fire-scoped")
    monkeypatch.setattr(sweep, "run_sweep", _boom)
    out = app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert out["kind"] == "refused" and "manifest" in out["message"]


def test_run_generated_screening_bad_bbox_is_error_not_refused(monkeypatch):
    """A malformed bbox is an input error, not a science refusal -- rendering it as
    'Screening refused' dilutes the Tier-1 vocabulary. Validated before the sweep starts."""
    fake = _fake_run_sweep("clean")
    monkeypatch.setattr(sweep, "run_sweep", fake)
    out = app.run_generated_screening((10, 20, 5, 30), SWEEP_INPUTS)
    assert out["kind"] == "error" and "West" in out["message"]
    assert fake.calls == []                             # nothing staged, nothing swept


def test_chosen_is_the_return_value_never_a_disk_reread(monkeypatch):
    """run_sweep's chosen record carries pre_date itself now; the old workaround (re-reading
    sweep_attempts.json for the richer copy) is gone -- the return value is the single source."""
    fake = _fake_run_sweep("clean")

    def _tampered(bbox, **kw):
        out = fake(bbox, **kw)
        (Path(kw["out_dir"]) / "sweep_attempts.json").write_text(
            json.dumps({"attempts": [], "chosen": {"pre_date": "9999-01-01"}}))
        return out
    monkeypatch.setattr(sweep, "run_sweep", _tampered)
    out = app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert out["chosen"]["pre_date"] == "2026-06-04"    # the return's, not the tampered file's


def test_contour_m_threads_through_to_run_sweep(monkeypatch):
    # Regression lock (B2): the operator's per-fire mountain-front contour must reach the
    # sweep in the Generate path too, not just Upload.
    fake = _fake_run_sweep("clean")
    monkeypatch.setattr(sweep, "run_sweep", fake)
    app.run_generated_screening(BBOX, SWEEP_INPUTS, contour_m=1900.0)  # Cooks Peak
    assert fake.calls[0]["contour_m"] == 1900.0


def test_greenup_days_threads_through_to_run_sweep(monkeypatch):
    """Review fix F1: the sweep re-runs the selector internally, so a dropped greenup_days
    would re-select over a DIFFERENT post-window than the one the human actually approved
    (operator-extended 180d silently narrowed back to the 90d default -> the just-recommended
    pair can vanish). Mirrors the contour_m lock's pattern."""
    fake = _fake_run_sweep("clean")
    monkeypatch.setattr(sweep, "run_sweep", fake)
    inputs = {**SWEEP_INPUTS, "greenup_days": 180}
    app.run_generated_screening(BBOX, inputs)
    assert fake.calls[0]["greenup_days"] == 180


def test_run_generated_screening_cleans_up_its_temp_dir(monkeypatch):
    made = {}
    real_mkdtemp = tempfile.mkdtemp

    def _spy(*a, **k):
        d = real_mkdtemp(*a, **k)
        made["d"] = d
        return d
    monkeypatch.setattr(tempfile, "mkdtemp", _spy)
    monkeypatch.setattr(sweep, "run_sweep", _fake_run_sweep("clean"))
    app.run_generated_screening(BBOX, SWEEP_INPUTS)
    assert "d" in made and not Path(made["d"]).exists()


# ---- F8 staleness key: mode + dates + pair fold in; upload identity unchanged ----


def test_inputs_key_upload_mode_is_legacy_5_tuple():
    k = app.screen_inputs_key(*BBOX, None)
    assert len(k) == 5 and k[4] is None


def test_inputs_key_generate_mode_folds_dates_and_pair():
    gen = ("2026-06-08", "2026-06-20", 90, "PRE", "POST")
    k1 = app.screen_inputs_key(*BBOX, None, mode="generate", gen=gen)
    k2 = app.screen_inputs_key(*BBOX, None, mode="generate",
                               gen=("2026-06-08", "2026-06-20", 90, "PRE2", "POST"))
    assert k1 != k2                                     # swapping a scene flags stale
    k3 = app.screen_inputs_key(*BBOX, None, mode="generate",
                               gen=("2026-06-09", "2026-06-20", 90, "PRE", "POST"))
    assert k1 != k3                                     # editing a date flags stale
    assert k1 != app.screen_inputs_key(*BBOX, None)     # mode switch flags stale


# ---- Approve-click AppTest: sweep wiring, idempotence, burnmap cleanup ----------------------


def _seeded_generate_apptest(monkeypatch, fake_sweep):
    """A Generate-mode AppTest with a recommended package pre-seeded (skips the Find click) and
    the scene preview short-circuited (no network in tests). Returns the AppTest after the
    radio has been switched to Generate but BEFORE any Approve click."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(sweep, "run_sweep", fake_sweep)
    monkeypatch.setattr(ss, "render_rgb_preview",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in tests")))
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["gen"] = {"outcome": {"kind": "package", "package": _package()}}
    at.run()
    radio = next(r for r in at.radio if "Burn severity" in (r.label or ""))
    radio.set_value("Generate from dates")
    at.run()
    assert not at.exception, at.exception
    return at


def test_approve_click_runs_the_sweep_once_and_stores_a_ranked_result(monkeypatch):
    fake = _fake_run_sweep("clean")
    at = _seeded_generate_apptest(monkeypatch, fake)
    approve = next(b for b in at.button if "Approve" in b.label)
    approve.set_value(True)
    at.run()
    assert not at.exception, at.exception
    assert len(fake.calls) == 1
    stored = at.session_state["screen"]
    assert stored["kind"] == "ranked" and "inputs" in stored


def test_second_queued_approve_click_does_not_rerun_the_sweep(monkeypatch):
    """Self-review focus (a): a queued second Approve click for the SAME inputs (the
    Streamlit double-submit race the idempotence guard exists for) must be a no-op."""
    fake = _fake_run_sweep("clean")
    at = _seeded_generate_apptest(monkeypatch, fake)
    approve = next(b for b in at.button if "Approve" in b.label)
    approve.set_value(True)
    at.run()
    assert len(fake.calls) == 1

    approve_again = next(b for b in at.button if "Approve" in b.label)
    approve_again.set_value(True)
    at.run()
    assert not at.exception, at.exception
    assert len(fake.calls) == 1                            # still just once
    assert any("already ran" in str(i.value).lower() for i in at.info)


def test_error_result_offers_retry_that_reruns_the_sweep(monkeypatch):
    """A transient failure (kind='error') must not dead-end behind the idempotence guard:
    the error branch renders a Retry button that discards the failed result and re-runs the
    screening with the same inputs -- no input perturbation required."""
    real = _fake_run_sweep("clean")
    state = {"n": 0}

    def _flaky(bbox, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("transient network hiccup")
        return real(bbox, **kw)

    at = _seeded_generate_apptest(monkeypatch, _flaky)
    approve = next(b for b in at.button if "Approve" in b.label)
    approve.set_value(True)
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["screen"]["kind"] == "error"

    retry = next(b for b in at.button if b.label == "Retry")
    retry.set_value(True)
    at.run()
    assert not at.exception, at.exception
    assert state["n"] == 2                              # the sweep actually re-ran
    assert at.session_state["screen"]["kind"] == "ranked"


def test_burnmap_preview_popped_after_a_completed_sweep(monkeypatch):
    """Self-review focus (b): the pre-approval quicklook (a losing pair's, possibly) must not
    linger above the winner's re-read quicklook once the sweep has stored its result."""
    fake = _fake_run_sweep("clean")
    at = _seeded_generate_apptest(monkeypatch, fake)
    at.session_state["gen"]["burnmap"] = _real_png_bytes()
    approve = next(b for b in at.button if "Approve" in b.label)
    approve.set_value(True)
    at.run()
    assert not at.exception, at.exception
    assert "burnmap" not in at.session_state["gen"]


# ---- Independent pre/post swap (spec 7): the re-gate is enforced, not just computed (R1) ----


def _swap_apptest(monkeypatch, evaluate_pair_result):
    """A Generate-mode AppTest seeded with a recommended pair carrying BOTH a pre and a post
    alternative, so the independent double-swap (spec 7) is reachable. evaluate_pair is stubbed
    (the real one reads rasters) to return the given re-gate result; render_rgb_preview is
    short-circuited (no network)."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(ss, "render_rgb_preview",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in tests")))
    monkeypatch.setattr(ss, "evaluate_pair", lambda *a, **k: evaluate_pair_result)
    pkg = _package()
    # Full-field alternatives so a swapped pair can render through scorecard_view.
    pkg["alternatives"] = {
        "pre": [{"id": "PRE2", "sensor": "S2", "date": date(2026, 6, 3), "tile_cloud_pct": 20.0}],
        "post": [{"id": "POST2", "sensor": "S2", "date": date(2026, 7, 9), "tile_cloud_pct": 60.0}],
    }
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["gen"] = {"outcome": {"kind": "package", "package": pkg}}
    at.run()
    radio = next(r for r in at.radio if "Burn severity" in (r.label or ""))
    radio.set_value("Generate from dates")
    at.run()
    assert not at.exception, at.exception
    return at


def test_below_floor_swapped_pair_is_not_offered_for_build(monkeypatch):
    """R1 (2026-07-18 review): the independent pre/post double-swap can construct a pair below
    the box-gate floor -- a combination never gated together. evaluate_pair returns
    passes_gate=False for it; the panel MUST honor that and refuse the pair (spec 7 below-bar ->
    Mode B), never silently leave a sub-floor pair approvable."""
    below = {
        "metrics": {"pre_valid_frac": 0.70, "post_valid_frac": 0.60, "pair_valid_frac": 0.40},
        "verdict": {"verdict": "below_bar", "summary": "below the bar"},
        "passes_gate": False,
    }
    at = _swap_apptest(monkeypatch, below)
    pre_sel = next(s for s in at.selectbox if "Pre-fire scene" in (s.label or ""))
    post_sel = next(s for s in at.selectbox if "Post-fire scene" in (s.label or ""))
    pre_sel.set_value("PRE2")
    post_sel.set_value("POST2")
    next(b for b in at.button if b.label == "Use this pair").set_value(True)
    at.run()
    assert not at.exception, at.exception
    # The sub-floor pair was NOT adopted -- the recommended pair still stands.
    adopted = at.session_state["gen"]["outcome"]["package"]["pair"]
    assert adopted["pre"]["id"] == "PRE" and adopted["post"]["id"] == "POST"
    # ...and the operator is told why, in the clean-gate's own terms.
    assert any("floor" in str(e.value).lower() for e in at.error)


def test_passing_swapped_pair_is_adopted(monkeypatch):
    """The other side of R1: a swap whose re-gate PASSES must still go through -- the fix gates
    on passes_gate, it does not freeze the swap path."""
    ok = {
        "metrics": {"pre_valid_frac": 0.95, "post_valid_frac": 0.92, "pair_valid_frac": 0.88},
        "verdict": {"verdict": "good", "summary": "covers ~88% of your fire area."},
        "passes_gate": True,
    }
    at = _swap_apptest(monkeypatch, ok)
    next(s for s in at.selectbox if "Pre-fire scene" in (s.label or "")).set_value("PRE2")
    next(s for s in at.selectbox if "Post-fire scene" in (s.label or "")).set_value("POST2")
    next(b for b in at.button if b.label == "Use this pair").set_value(True)
    at.run()
    assert not at.exception, at.exception
    adopted = at.session_state["gen"]["outcome"]["package"]["pair"]
    assert adopted["pre"]["id"] == "PRE2" and adopted["post"]["id"] == "POST2"


def test_degraded_sweep_result_renders_and_persists_through_a_rerun(monkeypatch):
    fake = _fake_run_sweep("degraded", refused_ids=["p1", "p2"])
    at = _seeded_generate_apptest(monkeypatch, fake)
    approve = next(b for b in at.button if "Approve" in b.label)
    approve.set_value(True)
    at.run()
    assert not at.exception, at.exception
    assert any("2 of 3 basins could not be assessed" in str(w.value) for w in at.warning)
    assert any("UNKNOWN" in str(w.value) for w in at.warning)
    at.run()                                                # a rerun (e.g. st_folium) must not drop it
    assert not at.exception, at.exception
    assert any("2 of 3 basins could not be assessed" in str(w.value) for w in at.warning)


def test_degraded_sweep_result_offers_refused_csv_download(monkeypatch):
    """Review fix F3: the degraded banner tells the user to consult refused_basins.csv --
    the download button must actually exist, with real (non-empty) bytes behind it."""
    fake = _fake_run_sweep("degraded", refused_ids=["p1", "p2"])
    at = _seeded_generate_apptest(monkeypatch, fake)
    approve = next(b for b in at.button if "Approve" in b.label)
    approve.set_value(True)
    at.run()
    assert not at.exception, at.exception
    stored = at.session_state["screen"]
    assert stored["refused_csv"]                           # non-empty bytes reached the store
    labels = [d.label for d in at.get("download_button")]
    assert any("refused_basins.csv" in lbl for lbl in labels), labels


def test_clean_sweep_result_offers_no_refused_csv_download(monkeypatch):
    fake = _fake_run_sweep("clean")
    at = _seeded_generate_apptest(monkeypatch, fake)
    approve = next(b for b in at.button if "Approve" in b.label)
    approve.set_value(True)
    at.run()
    assert not at.exception, at.exception
    labels = [d.label for d in at.get("download_button")]
    assert not any("refused_basins.csv" in lbl for lbl in labels), labels


def test_incised_sweep_result_renders_the_exploratory_disclaimer(monkeypatch):
    """Review fix F2: screen['incised'] (sniffed from basins.geojson provenance) must actually
    drive the exploratory-terrain warning at render time, not just sit unused in the dict."""
    fake = _fake_run_sweep("clean", incised=True)
    at = _seeded_generate_apptest(monkeypatch, fake)
    approve = next(b for b in at.button if "Approve" in b.label)
    approve.set_value(True)
    at.run()
    assert not at.exception, at.exception
    assert any("Exploratory result" in str(w.value) for w in at.warning)


# ---- Render-only: seed session_state["screen"] directly (test_app.py's established idiom) ----


def _fc_one_ranked_basin(*, refused_count=None, n_basins_total=None):
    prov = {}
    if refused_count is not None:
        prov["refused_count"] = refused_count
        prov["n_basins_total"] = n_basins_total
    return {"type": "FeatureCollection", "provenance": prov,
            "features": [{"type": "Feature",
                         "properties": {"basin_id": "b1", "rank": 1, "rank_b": 1, "score": 1.0,
                                       "score_b": 1.0, "rank_delta": 0, "mean_burn_a": 0.5,
                                       "mean_slope": 0.3, "area_km2": 1.2},
                         "geometry": {"type": "Polygon",
                                     "coordinates": [[[-105.7, 33.35], [-105.7, 33.36],
                                                      [-105.69, 33.36], [-105.7, 33.35]]]}}]}


def test_degraded_banner_text_is_verbatim_and_path_agnostic():
    """Step 2(d): the banner renders off basins.geojson provenance -- exercised here directly
    via session_state (test_app.py's own AppTest idiom), independent of the sweep mechanics."""
    from streamlit.testing.v1 import AppTest
    fc = _fc_one_ranked_basin(refused_count=2, n_basins_total=3)
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {
        "kind": "ranked", "fc": fc, "csv": b"x", "n": 1,
        "inputs": app.screen_inputs_key(*_APP_DEFAULT_BBOX, None),
    }
    at.run()
    assert not at.exception, at.exception
    banner = ("2 of 3 basins could not be assessed (insufficient cloud-free imagery). "
             "Their hazard is UNKNOWN -- not low. Any refused basin could rank high if "
             "data existed; see refused_basins.csv.")
    assert any(banner in str(w.value) for w in at.warning)


def test_clean_result_renders_no_degraded_banner():
    from streamlit.testing.v1 import AppTest
    fc = _fc_one_ranked_basin()
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {
        "kind": "ranked", "fc": fc, "csv": b"x", "n": 1,
        "inputs": app.screen_inputs_key(*_APP_DEFAULT_BBOX, None),
    }
    at.run()
    assert not at.exception, at.exception
    assert not any("could not be assessed" in str(w.value) for w in at.warning)


def test_sweep_trail_expander_renders_attempts_table():
    from streamlit.testing.v1 import AppTest
    fc = _fc_one_ranked_basin()
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {
        "kind": "ranked", "fc": fc, "csv": b"x", "n": 1,
        "attempts": [{"sensor": "S2", "post_id": "POST", "outcome": "ranked"}],
        "chosen": {"sensor": "S2", "post_id": "POST"},
        "inputs": app.screen_inputs_key(*_APP_DEFAULT_BBOX, None),
    }
    at.run()
    assert not at.exception, at.exception
    assert any("Sweep: 1 attempt(s), chose S2 POST" in (e.label or "") for e in at.expander)
    assert len(at.dataframe) >= 1


def test_aborted_sweep_renders_attempts_trail_not_just_message():
    """A41 fix-wave IMPORTANT 3: an aborted sweep's message says 'see attempts.' but the trail
    was never rendered on the refused branch (and the sweep's tempdir is already gone by then) --
    the attempts expander/dataframe must render off the surviving `screen["attempts"]` list.
    No `chosen` key exists on this branch (an abort never picks a winner), unlike the ranked
    branch's expander."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {
        "kind": "refused", "message": "no attempt produced a ranking; see attempts.",
        "attempts": [{"sensor": "S2", "post_id": "POST", "outcome": "abort: MPC 403"}],
        "inputs": app.screen_inputs_key(*_APP_DEFAULT_BBOX, None),
    }
    at.run()
    assert not at.exception, at.exception
    assert any("Sweep: 1 attempt(s)" in (e.label or "") for e in at.expander)
    assert len(at.dataframe) >= 1


def test_build_basin_map_hatches_refused_features_without_error():
    fc = _fc_one_ranked_basin()
    refused_fc = {"type": "FeatureCollection",
                  "features": [{"type": "Feature", "properties": {"phase1_basin_id": "p1"},
                               "geometry": {"type": "Polygon",
                                            "coordinates": [[[-105.71, 33.34], [-105.71, 33.35],
                                                             [-105.70, 33.35], [-105.71, 33.34]]]}}]}
    m = app.build_basin_map(fc, refused_fc=refused_fc)
    assert m is not None                                    # smoke: no exception, a real folium.Map


# ---- AppTest smoke: Upload stays the default; Generate renders its panel ----


def test_apptest_upload_default_and_generate_panel():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.run()
    assert not at.exception, at.exception
    assert any(b.label == "Run screening" for b in at.button)   # Upload default intact
    assert len(at.number_input) == 5                            # W/S/E/N bbox + mountain-front contour (B2)

    radio = next(r for r in at.radio if "Burn severity" in (r.label or ""))
    radio.set_value("Generate from dates")
    at.run()
    assert not at.exception, at.exception
    assert len(at.date_input) == 2                              # ignition + containment
    assert any(b.label == "Find scene pair" for b in at.button)
    assert not any(b.label == "Run screening" for b in at.button)  # one panel at a time
