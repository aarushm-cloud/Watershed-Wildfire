"""AA-4 (B4, Auto-Acquire Build-Plan Phase 4) -- the Generate-from-dates approval UI.

Pure-helper + AppTest coverage for app.py's auto-acquire mode: generate_package's F5
error reduction, the deterministic scorecard view (cloud-over-fire headline, tile-cloud
de-emphasized, value-free timing flag), run_generated_screening's contract (creator ->
the SAME validated downstream as an upload; every failure a legible dict; refusal
unsoftened), the F8 staleness key folding mode + dates + the selected pair, and an
AppTest smoke that the toggle defaults to Upload (existing tests untouched) and the
Generate panel renders date inputs + the Find button.

Run:  pytest tests/test_app_generate.py -v
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import app  # noqa: E402
from autoacquire import dnbr_create as dc  # noqa: E402
from autoacquire import scene_select as ss  # noqa: E402
from src.grids import GateAbort  # noqa: E402

BBOX = (-122.145, 38.455, -121.985, 38.595)


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


# ---- run_generated_screening (creator -> the SAME validated downstream) ----


def _wire_downstream(monkeypatch, tmp_path, pipeline_result):
    ql = tmp_path / "q.png"; ql.write_bytes(b"\x89PNG\r\n\x1a\nx")
    prov = tmp_path / "p.json"; prov.write_text('{"sensor": "S2"}')
    created = {"dnbr_tif": str(tmp_path / "d.tif"), "quicklook_png": str(ql),
               "provenance_json": str(prov), "gate_stats": {"p99_abs": 0.5}}
    calls = {"create": [], "build": [], "run": [], "write": []}
    monkeypatch.setattr(dc, "create_dnbr",
                        lambda *a, **k: calls["create"].append((a, k)) or created)
    import acquire
    fire = {"name": "frontend", "out_dir": tmp_path, "dem": "dem.tif"}
    monkeypatch.setattr(acquire, "build_fire_config",
                        lambda *a, **k: calls["build"].append((a, k)) or fire)
    from src import pipeline as pl, outputs as outs
    monkeypatch.setattr(pl, "run_pipeline",
                        lambda *a, **k: calls["run"].append((a, k)) or pipeline_result)
    csvp = tmp_path / "r.csv"; csvp.write_bytes(b"csv")
    gjp = tmp_path / "b.geojson"; gjp.write_text('{"type": "FeatureCollection", "features": []}')
    monkeypatch.setattr(outs, "write_dnbr_outputs",
                        lambda *a, **k: calls["write"].append((a, k)) or (csvp, gjp))
    return calls


def test_run_generated_screening_ranked(monkeypatch, tmp_path):
    ranked = {"status": "ranked", "arms": {"arm_a": {"basins": [1, 2]}, "arm_b": {}},
              "creek_nearest": None, "headline_arm": "arm_a"}
    calls = _wire_downstream(monkeypatch, tmp_path, ranked)
    pair = _package()["pair"]
    out = app.run_generated_screening(BBOX, pair)
    assert out["kind"] == "ranked" and out["n"] == 2
    assert out["quicklook"].startswith(b"\x89PNG")
    assert out["dnbr_provenance"] == {"sensor": "S2"}
    assert calls["create"][0][0][0] is pair             # the approved pair, untouched
    assert calls["write"]                               # persisted via the reused writer


def test_contour_m_threads_through_generate_path_to_pipeline(monkeypatch, tmp_path):
    # Regression lock (B2): the operator's per-fire mountain-front contour must reach
    # run_pipeline in the Generate path too, not just Upload. A bad merge once dropped
    # the contour input entirely; this pins the threading so it can't silently vanish.
    ranked = {"status": "ranked", "arms": {"arm_a": {"basins": [1]}, "arm_b": {}},
              "creek_nearest": None, "headline_arm": "arm_a"}
    calls = _wire_downstream(monkeypatch, tmp_path, ranked)
    app.run_generated_screening(BBOX, _package()["pair"], contour_m=1900.0)  # Cooks Peak
    assert calls["run"][0][1].get("contour_m") == 1900.0


def test_run_generated_screening_refusal_unsoftened(monkeypatch, tmp_path):
    refused = {"status": "refused", "message": "no mountain front", "reason_code": "terrain"}
    calls = _wire_downstream(monkeypatch, tmp_path, refused)
    out = app.run_generated_screening(BBOX, _package()["pair"])
    assert out["kind"] == "refused" and "mountain front" in out["message"]
    assert calls["write"] == []                          # no ranking artifacts on a refusal


def test_run_generated_screening_creator_abort_is_legible(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "create_dnbr",
                        lambda *a, **k: (_ for _ in ()).throw(GateAbort("baseline 03.01 < 04.00")))
    out = app.run_generated_screening(BBOX, _package()["pair"])
    assert out["kind"] == "error" and "04.00" in out["message"]


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
