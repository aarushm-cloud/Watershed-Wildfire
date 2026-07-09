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


def _feature(basin_id, rank, rank_b, score=1.0):
    # a 0.01-degree square somewhere in the AOI; geometry only needs to be a valid polygon
    x, y = -105.7 + rank * 0.01, 33.35
    return {"type": "Feature",
            "properties": {"basin_id": basin_id, "rank": rank, "score": score,
                           "rank_b": rank_b, "score_b": score, "rank_delta": abs(rank - rank_b)},
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
    assert len(at.number_input) == 4                                     # W/S/E/N bbox fields
    assert any(b.label == "Run screening" for b in at.button)            # the run button rendered


def test_ranked_results_persist_across_reruns():
    """Regression: st_folium (and the download button) trigger reruns on which the run button reads
    False; results must be held in session_state and re-rendered, not vanish. Seeds a ranked screen
    and asserts the table survives a second run() (a rerun)."""
    from streamlit.testing.v1 import AppTest
    fc = _fc(_feature("b1", rank=1, rank_b=1), _feature("b2", rank=2, rank_b=6))
    at = AppTest.from_file(str(_REPO_ROOT / "app.py"), default_timeout=90)
    at.session_state["screen"] = {"kind": "ranked", "fc": fc,
                                  "csv": b"# ranking\nbasin_id,rank\nb1,1\nb2,2\n", "n": 2}
    at.run()                                     # render from session_state (a rerun, run button False)
    assert not at.exception, at.exception
    assert len(at.dataframe) >= 1                 # the ranking table rendered
    at.run()                                     # ANOTHER rerun (as st_folium/download would cause)
    assert not at.exception, at.exception
    assert len(at.dataframe) >= 1                 # ...and it did NOT disappear
