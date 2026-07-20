"""CF-10 (A36) -- the testable core of the Streamlit frontend `app.py`.

Streamlit UIs resist direct unit testing, so `app.py` keeps its logic in pure, importable
helpers (no `st.*` at module top level; the UI lives in `main()` behind an `if __name__ ==
'__main__'` guard). These test that core:
  * validate_bbox   -- legible fail-loud on a malformed bbox BEFORE any network (GateAbort).
  * result_to_view  -- maps run_pipeline's polymorphic result (ranked dNBR / A27 refusal) to a view.
  * basin_rows      -- basins.geojson -> display rows (Arm A rank order + the rank_delta "uncertain" flag).
  * build_basin_map -- smoke: returns a folium.Map (rendering is exercised, internals are not asserted).

The live end-to-end (draw bbox + upload dNBR -> map + CSV, and a clean refusal render) is the
CF-10 gate, verified separately -- it needs the network + a running Streamlit server.

Run:  pytest tests/test_app.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import folium
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import app  # noqa: E402  (repo-root Streamlit module; helpers importable, UI guarded)
from app import validate_bbox, result_to_view, basin_rows, build_basin_map, bbox_from_draw  # noqa: E402
from src.grids import GateAbort  # noqa: E402


SFK_BBOX = (-105.791562, 33.325515, -105.636136, 33.413521)   # W, S, E, N (valid)


def _feature(basin_id, rank, rank_b, score=1.0, mean_burn=0.5, mean_slope=0.3, area_km2=1.2):
    # a 0.01-degree square somewhere in the AOI; geometry only needs to be a valid polygon
    x, y = -105.7 + rank * 0.01, 33.35
    return {"type": "Feature",
            "properties": {"basin_id": basin_id, "rank": rank, "score": score,
                           "rank_b": rank_b, "score_b": score, "rank_delta": abs(rank - rank_b),
                           "mean_burn_a": mean_burn, "mean_slope": mean_slope, "area_km2": area_km2},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[x, y], [x + 0.01, y], [x + 0.01, y + 0.01],
                                          [x, y + 0.01], [x, y]]]}}


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


# ---- validate_bbox -----------------------------------------------------------------------------

def test_validate_bbox_accepts_a_good_box():
    assert validate_bbox(*SFK_BBOX) == pytest.approx(SFK_BBOX)


def test_validate_bbox_rejects_west_ge_east():
    with pytest.raises(GateAbort) as e:
        validate_bbox(-105.6, 33.3, -105.8, 33.4)          # west east swapped
    assert "west" in str(e.value).lower() and "east" in str(e.value).lower()


def test_validate_bbox_rejects_south_ge_north():
    with pytest.raises(GateAbort):
        validate_bbox(-105.8, 33.5, -105.6, 33.3)          # south north swapped


def test_validate_bbox_rejects_out_of_range_lonlat():
    with pytest.raises(GateAbort):
        validate_bbox(-200.0, 33.3, -105.6, 33.4)          # lon < -180
    with pytest.raises(GateAbort):
        validate_bbox(-105.8, 33.3, -105.6, 95.0)          # lat > 90


def test_validate_bbox_rejects_nonnumeric():
    with pytest.raises(GateAbort):
        validate_bbox("west", 33.3, -105.6, 33.4)


# ---- result_to_view ----------------------------------------------------------------------------

def test_result_to_view_refused():
    result = {"status": "refused", "reason_code": "REFUSED_INCISED_TERRAIN",
              "message": "Refused: this fire's terrain is an incised valley ...", "span_m": 77.0}
    view = result_to_view(result)
    assert view["kind"] == "refused"
    assert view["message"] == result["message"]            # passed through verbatim (A11 framing travels)


def test_result_to_view_ranked_dnbr():
    result = {"status": "ranked", "headline_arm": "arm_a",
              "provenance": {"burn_source": "dNBR"},
              "arms": {"arm_a": {"basins": [{"basin_id": "b1"}, {"basin_id": "b2"}]},
                       "arm_b": {"basins": [{"basin_id": "b1"}, {"basin_id": "b2"}]}},
              "creek_nearest": None}
    view = result_to_view(result)
    assert view["kind"] == "ranked"
    assert view["n_basins"] == 2
    assert view["headline_arm"] == "arm_a"


# ---- basin_rows (Arm A order + rank_delta "uncertain" flag) -------------------------------------

def test_basin_rows_sorted_by_arm_a_rank_with_uncertainty_flag():
    # file order is rank 2 then rank 1; must come back rank 1 then rank 2.
    # b_hi has rank_delta 5 (A/B disagree a lot -> uncertain); b_lo has rank_delta 1 (agree -> certain).
    fc = _fc(_feature("b_hi", rank=2, rank_b=7), _feature("b_lo", rank=1, rank_b=2))
    rows = basin_rows(fc)
    assert [r["basin_id"] for r in rows] == ["b_lo", "b_hi"]     # sorted by Arm A rank
    assert rows[0]["rank_delta"] == 1 and rows[0]["uncertain"] is False
    assert rows[1]["rank_delta"] == 5 and rows[1]["uncertain"] is True


def test_basin_rows_surfaces_frozen_formula_terms_in_product_order():
    # B: the table exposes the frozen score's inputs (mean_burn x mean_slope x area_km2 -> score) in
    # that left-to-right order, so a viewer can audit the ranking. Burn is the Arm A binned value.
    fc = _fc(_feature("b1", rank=1, rank_b=1, mean_burn=0.6, mean_slope=0.4, area_km2=2.0))
    row = basin_rows(fc)[0]
    assert (row["mean_burn"], row["mean_slope"], row["area_km2"]) == (0.6, 0.4, 2.0)
    keys = list(row.keys())
    assert keys.index("mean_burn") < keys.index("mean_slope") < keys.index("area_km2") < keys.index("score")


# ---- build_basin_map (smoke) -------------------------------------------------------------------

def test_build_basin_map_returns_a_folium_map():
    fc = _fc(_feature("b1", rank=1, rank_b=1), _feature("b2", rank=2, rank_b=4))
    m = build_basin_map(fc)
    assert isinstance(m, folium.Map)
    html = m.get_root().render()               # renders without error
    assert "b1" in html or "GeoJson" in html   # the basins layer is present


# ---- bbox_from_draw (streamlit-folium draw payload -> bbox) -------------------------------------

def test_bbox_from_draw_extracts_rectangle():
    draw = {"last_active_drawing": {"type": "Feature", "geometry": {"type": "Polygon",
            "coordinates": [[[-105.8, 33.3], [-105.6, 33.3], [-105.6, 33.4],
                             [-105.8, 33.4], [-105.8, 33.3]]]}}}
    assert bbox_from_draw(draw) == (-105.8, 33.3, -105.6, 33.4)
    assert bbox_from_draw(None) is None
    assert bbox_from_draw({}) is None


# ---- AppTest smoke: the guarded main() runs, framing + inputs render, no exception --------------

def test_app_loads_with_framing_and_inputs():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90).run()
    assert not at.exception, at.exception
    assert any("screening" in str(el.value).lower() for el in at.info)   # the A11 spine banner
    assert len(at.number_input) == 5                                     # W/S/E/N bbox + mountain-front contour (B2)
    assert any(b.label == "Run screening" for b in at.button)            # the run button rendered


# ---- F5: run_screening -- EVERY failure reduces to a legible screen dict, never a raise ---------

class _FakeUpload:
    """Stands in for streamlit's UploadedFile (name/size/file_id are instance attrs there too)."""
    def __init__(self, data=b"raster-bytes", name="dnbr.tif", file_id="upload-1"):
        self._data = data
        self.name = name
        self.size = len(data)
        self.file_id = file_id

    def getvalue(self):
        return self._data


def test_run_screening_gateabort_message_verbatim(monkeypatch):
    import acquire

    def _abort(*a, **k):
        raise GateAbort("FAIL: uploaded dNBR looks x1000-scaled")

    monkeypatch.setattr(acquire, "build_fire_config", _abort)
    screen = app.run_screening(SFK_BBOX, _FakeUpload())
    assert screen["kind"] == "error"
    assert screen["message"] == "FAIL: uploaded dNBR looks x1000-scaled"   # domain message, verbatim


def test_run_screening_backstops_network_errors_as_legible_error(monkeypatch):
    # RasterioIOError is an OSError, NOT a ValueError -- without the F5 backstop it escapes the
    # except and reaches the user as a raw Streamlit traceback. It must reduce to a named message.
    import acquire
    from rasterio.errors import RasterioIOError

    def _net_down(*a, **k):
        raise RasterioIOError("CURL error: Could not resolve host")

    monkeypatch.setattr(acquire, "build_fire_config", _net_down)
    screen = app.run_screening(SFK_BBOX, _FakeUpload())
    assert screen["kind"] == "error"
    assert "RasterioIOError" in screen["message"]                # the failure is named...
    assert "resolve host" in screen["message"]                   # ...and the detail carried


def test_run_screening_requires_an_upload():
    screen = app.run_screening(SFK_BBOX, None)
    assert screen["kind"] == "error"
    assert "upload" in screen["message"].lower()


def test_run_screening_corrupt_upload_is_a_legible_geotiff_error():
    # End-to-end through the REAL acquire scale guard (no monkeypatch): garbage bytes renamed .tif
    # must come back as a legible "not a readable GeoTIFF" error dict -- the most likely user mistake.
    screen = app.run_screening(SFK_BBOX, _FakeUpload(data=b"definitely not a geotiff"))
    assert screen["kind"] == "error"
    assert "GeoTIFF" in screen["message"]


# ---- F8: stale-result detection ------------------------------------------------------------------

_APP_DEFAULT_BBOX = (-105.79156, 33.32552, -105.63614, 33.41352)   # app.py's South Fork default form


def test_screen_inputs_key_distinguishes_bbox_and_upload():
    k1 = app.screen_inputs_key(*SFK_BBOX, _FakeUpload(file_id="A"))
    same = app.screen_inputs_key(*SFK_BBOX, _FakeUpload(file_id="A"))
    other_box = app.screen_inputs_key(-105.8, 33.3, -105.6, 33.4, _FakeUpload(file_id="A"))
    other_file = app.screen_inputs_key(*SFK_BBOX, _FakeUpload(file_id="B"))
    assert k1 == same
    assert k1 != other_box and k1 != other_file
    assert app.screen_inputs_key(*SFK_BBOX, None) != k1            # upload removed -> different


def test_stale_results_are_flagged_after_inputs_change():
    """F8: a stored result whose inputs no longer match the current form must render WITH an
    'inputs changed' warning (still visible, clearly labeled stale) -- never silently posing as
    the current box's screening."""
    from streamlit.testing.v1 import AppTest
    fc = _fc(_feature("b1", rank=1, rank_b=1))
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {"kind": "ranked", "fc": fc, "csv": b"x", "n": 1,
                                  "inputs": ("some", "other", "bbox")}   # produced by DIFFERENT inputs
    at.run()
    assert not at.exception, at.exception
    assert any("inputs changed" in str(w.value).lower() for w in at.warning)
    assert len(at.dataframe) >= 1                                  # stale result still visible, labeled


def test_matching_results_show_no_stale_warning():
    """F8 negative control: a stored result stamped with the CURRENT form inputs renders clean."""
    from streamlit.testing.v1 import AppTest
    fc = _fc(_feature("b1", rank=1, rank_b=1))
    current = app.screen_inputs_key(*_APP_DEFAULT_BBOX, None)      # the app's default form state
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {"kind": "ranked", "fc": fc, "csv": b"x", "n": 1,
                                  "inputs": current}
    at.run()
    assert not at.exception, at.exception
    assert not any("inputs changed" in str(w.value).lower() for w in at.warning)
    assert len(at.dataframe) >= 1


# ---- [2]: run_screening SUCCESS paths (the F5 backstop otherwise hides success-branch bugs) --------

def test_run_screening_ranked_success_path(monkeypatch, tmp_path):
    # Drive run_screening to kind=='ranked' so a bug in the success branch (a typo, a wrong writer
    # arg) fails a test LOUDLY instead of shipping green behind the F5 `except Exception` backstop.
    import acquire
    from src import pipeline as pl
    from src import outputs as outs
    ranked = {"status": "ranked", "headline_arm": "arm_a", "provenance": {"burn_source": "dNBR"},
              "arms": {"arm_a": {"basins": [{"basin_id": 0}, {"basin_id": 1}]}, "arm_b": {"basins": []}},
              "creek_nearest": None}
    gj = tmp_path / "basins.geojson"; gj.write_text(json.dumps(_fc(_feature("b0", 1, 1))))
    cp = tmp_path / "ranking.csv"; cp.write_bytes(b"# spine\nbasin_id,rank\n0,1\n")
    monkeypatch.setattr(acquire, "build_fire_config",
                        lambda bbox, path, out_dir, **k: {"name": "t", "out_dir": out_dir, "dem": "x"})
    monkeypatch.setattr(pl, "run_pipeline", lambda fire, **k: ranked)   # **k absorbs the B2 contour_m kwarg
    monkeypatch.setattr(outs, "write_dnbr_outputs", lambda *a, **k: (cp, gj))
    screen = app.run_screening(SFK_BBOX, _FakeUpload())
    assert screen["kind"] == "ranked" and screen["n"] == 2
    assert screen["fc"]["type"] == "FeatureCollection" and screen["csv"].startswith(b"# spine")


def test_run_screening_threads_operator_contour_to_pipeline(monkeypatch):
    # Regression lock (B2): the per-fire mountain-front contour must reach run_pipeline
    # (a bad merge once dropped the whole contour input; this pins the Upload-path threading).
    import acquire
    from src import pipeline as pl
    calls = []
    monkeypatch.setattr(acquire, "build_fire_config",
                        lambda *a, **k: {"name": "t", "out_dir": ".", "dem": "x"})
    monkeypatch.setattr(pl, "run_pipeline",
                        lambda fire, **k: calls.append(k) or {"status": "refused",
                        "reason_code": "R", "message": "m"})
    app.run_screening(SFK_BBOX, _FakeUpload(), contour_m=1900.0)   # Cooks Peak
    assert calls and calls[0].get("contour_m") == 1900.0


def test_run_screening_incised_sbs_abort_is_not_softened(monkeypatch, incised_fire):
    """A39: incised terrain no longer refuses (the old REFUSED_INCISED_TERRAIN fake this test drove
    can no longer happen), so this locks the invariant against the real, reachable failure instead --
    incised+SBS is a hard GateAbort (Task 8). run_screening must still reduce it to a legible error,
    never a ranking."""
    import acquire
    fire = dict(incised_fire)
    fire["sbs"] = "data/southfork/burn/arm_a_cls.tif"   # any real path -- never opened before the abort
    fire["dnbr"] = None
    monkeypatch.setattr(acquire, "build_fire_config", lambda *a, **k: fire)
    screen = app.run_screening(SFK_BBOX, _FakeUpload())
    assert screen["kind"] == "error"
    assert "incised" in screen["message"].lower()
    assert "fc" not in screen and "csv" not in screen      # no ranking artifact shipped


def test_run_screening_logs_traceback_to_stderr_on_backstop(monkeypatch, capsys):
    # [4]: the backstop must preserve the developer debugging channel -- emit the full traceback to
    # stderr (the local `streamlit run` console) even though the user sees only the one-line message.
    import acquire
    def _boom(*a, **k):
        raise RuntimeError("internal boom")
    monkeypatch.setattr(acquire, "build_fire_config", _boom)
    screen = app.run_screening(SFK_BBOX, _FakeUpload())
    assert screen["kind"] == "error" and "RuntimeError" in screen["message"]
    err = capsys.readouterr().err
    assert "Traceback" in err and "internal boom" in err


# ---- [7]/[8]: staleness key precision + fallback --------------------------------------------------

def test_screen_inputs_key_ignores_sub_display_precision_jitter():
    # Widgets display+submit at %.5f; the key must round at the SAME precision so a 6th-decimal-only
    # difference (a drawn full-precision box vs its re-typed 5-dp value) does not spuriously flag stale.
    k1 = app.screen_inputs_key(-105.7915643, 33.3255156, -105.6361360, 33.4135210, None)
    k2 = app.screen_inputs_key(-105.79156, 33.32552, -105.63614, 33.41352, None)   # equal at 5 dp
    assert k1 == k2


def test_screen_inputs_key_falls_back_to_name_size_without_file_id():
    class _NoId:
        def __init__(self, name, size): self.name, self.size = name, size
        def getvalue(self): return b"x"
    a = app.screen_inputs_key(*SFK_BBOX, _NoId("a.tif", 100))
    b = app.screen_inputs_key(*SFK_BBOX, _NoId("b.tif", 100))
    assert a != b and a is not None                       # distinct by name when file_id absent


# ---- [3]/[9]: staleness through the real widget seam + unstamped-is-stale -------------------------

def test_stale_flagged_when_a_coordinate_is_edited():
    """[3] the killing test: actually CHANGE a form input and assert the banner appears -- pins
    inputs_key to the live number_input values (a `drawn`-sourced key would miss a typed edit)."""
    from streamlit.testing.v1 import AppTest
    fc = _fc(_feature("b1", rank=1, rank_b=1))
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {"kind": "ranked", "fc": fc, "csv": b"x", "n": 1,
                                  "inputs": app.screen_inputs_key(*_APP_DEFAULT_BBOX, None)}
    at.run()
    assert not any("inputs changed" in str(w.value).lower() for w in at.warning)   # matches default form
    at.number_input[0].set_value(-106.0)                 # user edits West -> stored result now stale
    at.run()
    assert not at.exception, at.exception
    assert any("inputs changed" in str(w.value).lower() for w in at.warning)


def test_unstamped_result_is_treated_as_stale():
    # [9]/[12]: a result lacking the inputs stamp (a pre-F8 result surviving a dev hot-reload) must
    # render FLAGGED, not silently clean -- unknown provenance = stale (fail-loud).
    from streamlit.testing.v1 import AppTest
    fc = _fc(_feature("b1", rank=1, rank_b=1))
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {"kind": "ranked", "fc": fc, "csv": b"x", "n": 1}   # NO inputs stamp
    at.run()
    assert not at.exception, at.exception
    assert any("inputs changed" in str(w.value).lower() for w in at.warning)


def test_run_button_stores_and_stamps_result_in_the_holder():
    # round-2/[1]: pin the `if run:` glue that stores a completed run into the persistent holder
    # (box.clear/update inside the spinner) AND stamps it -- previously untested end-to-end, so a
    # revert/break of the store path shipped green. Drives the REAL button (no upload -> a legible
    # error screen), and asserts the store executed, stamped, and rendered. (The rerun-RACE the store
    # placement guards against is not unit-testable in synchronous AppTest -- it is mechanism-verified.)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90).run()
    btn = [b for b in at.button if b.label == "Run screening"][0]
    btn.set_value(True)
    at.run()
    assert not at.exception, at.exception
    stored = at.session_state["screen"]
    assert stored["kind"] == "error" and "upload" in stored["message"].lower()   # ran + stored
    assert "inputs" in stored                                                     # stamped (F8)
    assert any("upload" in str(e.value).lower() for e in at.error)                # rendered legibly


def test_run_screening_cleans_up_its_temp_dir(monkeypatch):
    # minor: run_screening mkdtemp'd a fresh dir per run and never removed it (a leak on a long-lived
    # server). Outputs are read into memory before return, so the dir must be cleaned in a finally.
    import tempfile
    import acquire
    made = {}
    real = tempfile.mkdtemp

    def _spy(*a, **k):
        d = real(*a, **k)
        made["d"] = d
        return d

    def _boom(*a, **k):
        raise GateAbort("boom after staging")

    monkeypatch.setattr(tempfile, "mkdtemp", _spy)
    monkeypatch.setattr(acquire, "build_fire_config", _boom)
    app.run_screening(SFK_BBOX, _FakeUpload())
    assert "d" in made and not Path(made["d"]).exists()   # created, then cleaned up


def test_result_to_view_ranked_without_arms_is_unknown_not_keyerror():
    # minor: a ranked result lacking the dNBR 'arms' shape (e.g. an SBS-shaped result, if ever wired to
    # the UI) must degrade to kind='unknown', not raise KeyError.
    view = app.result_to_view({"status": "ranked", "basins": [{"basin_id": 0}]})   # no "arms"
    assert view["kind"] == "unknown"


def test_ranked_results_persist_across_reruns():
    """Regression: st_folium (and the download button) trigger reruns on which the run button reads
    False; results must be held in session_state and re-rendered, not vanish. Seeds a ranked screen
    and asserts the table survives a second run() (a rerun)."""
    from streamlit.testing.v1 import AppTest
    fc = _fc(_feature("b1", rank=1, rank_b=1), _feature("b2", rank=2, rank_b=6))
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {"kind": "ranked", "fc": fc,
                                  "csv": b"# ranking\nbasin_id,rank\nb1,1\nb2,2\n", "n": 2,
                                  "inputs": app.screen_inputs_key(*_APP_DEFAULT_BBOX, None)}   # stamped: not stale
    at.run()                                     # render from session_state (a rerun, run button False)
    assert not at.exception, at.exception
    assert len(at.dataframe) >= 1                 # the ranking table rendered
    at.run()                                     # ANOTHER rerun (as st_folium/download would cause)
    assert not at.exception, at.exception
    assert len(at.dataframe) >= 1                 # ...and it did NOT disappear


# ---- A39: result_to_view flags incised-terrain ranked results -----------------------------------

def test_result_to_view_incised_is_ranked_and_flagged():
    from app import result_to_view
    result = {"status": "ranked", "terrain_mode": "incised",
              "arms": {"arm_a": {"ranked": [], "basins": []},
                       "arm_b": {"ranked": [], "basins": []}},
              "headline_arm": "arm_a"}
    view = result_to_view(result)
    assert view["kind"] == "ranked" and view["incised"] is True


def test_result_to_view_range_front_not_flagged():
    from app import result_to_view
    result = {"status": "ranked", "terrain_mode": "range_front",
              "arms": {"arm_a": {"ranked": [], "basins": []},
                       "arm_b": {"ranked": [], "basins": []}},
              "headline_arm": "arm_a"}
    assert result_to_view(result)["incised"] is False
