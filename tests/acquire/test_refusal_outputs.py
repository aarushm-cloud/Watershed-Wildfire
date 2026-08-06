"""Refusal rendering in the dNBR both-arms writer (A41 Task 3): refused_basins.csv/geojson
sidecars, the ranking.csv banner + imagery header lines, provenance counts, and the dual-rank
map's hatched refused layer. A zero-refused write stays projection-identical to Task 2's output
except for the always-on nodata_frac column (locked exhaustively in test_dnbr_outputs.py's
test_accepted_fire_schema_is_unchanged, the one sanctioned edit there).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.outputs import write_dnbr_outputs, write_dual_rank_map
from tests.acquire.test_dnbr_outputs import _fake_arm, _write_fake_dem, _read_rows


def ranking_header(out_dir):
    header, _ = _read_rows(Path(out_dir) / "ranking.csv")
    return "".join(header)


def _refused_record(bid, nodata_frac, mean_slope=0.4, area_km2=1.0):
    mask = np.zeros((4, 4), dtype=bool)
    mask[1:3, 1:3] = True
    return {"basin_id": bid, "mask": mask, "nodata_frac": nodata_frac,
            "area_km2": area_km2, "mean_slope": mean_slope}


@pytest.fixture
def small_run(tmp_path):
    """Mirrors test_dnbr_outputs.py's _fake_arm/_write_fake_dem fixture pattern: a small
    synthetic non-incised both-arms run, args pre-bound for write_dnbr_outputs(*args, ...)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dem = _write_fake_dem(tmp_path)
    args = (_fake_arm(incised=False), _fake_arm(incised=False), None, out_dir, dem, "refusal_test")
    return SimpleNamespace(out_dir=out_dir, args=args)


@pytest.fixture
def refused_two():
    return [_refused_record(100, 0.35), _refused_record(101, 0.55, mean_slope=float("nan"))]


# ---- projection-identity (Step 1) ----------------------------------------------------------

def test_zero_refused_is_projection_identical(tmp_path, small_run):
    """With refused=None (the default): no banner, no imagery line, no sidecars, no provenance
    refusal counts."""
    write_dnbr_outputs(*small_run.args, refused=None)
    header, rows = _read_rows(Path(small_run.out_dir) / "ranking.csv")
    assert not any("UNKNOWN" in h or "imagery" in h for h in header)
    assert "nodata_frac" in rows[0]
    assert not (Path(small_run.out_dir) / "refused_basins.csv").exists()
    assert not (Path(small_run.out_dir) / "refused_basins.geojson").exists()
    gj = json.loads((Path(small_run.out_dir) / "basins.geojson").read_text())
    assert "refused_count" not in gj["provenance"] and "n_basins_total" not in gj["provenance"]

    # determinism: a second identical write produces byte-identical header + rows.
    write_dnbr_outputs(*small_run.args, refused=None)
    header2, rows2 = _read_rows(Path(small_run.out_dir) / "ranking.csv")
    assert header2 == header and rows2 == rows


def test_refused_sidecars_and_banner(tmp_path, small_run, refused_two):
    write_dnbr_outputs(*small_run.args, refused=refused_two)
    csv_text = (Path(small_run.out_dir) / "refused_basins.csv").read_text()
    assert "phase1_basin_id" in csv_text and "score" not in csv_text and "rank" not in csv_text
    header = ranking_header(small_run.out_dir)
    assert "hazard is UNKNOWN -- not low" in header
    assert "2 of 5 basins could not be assessed (insufficient cloud-free imagery)" in header
    gj = json.loads((Path(small_run.out_dir) / "basins.geojson").read_text())
    assert gj["provenance"]["refused_count"] == 2
    assert gj["provenance"]["n_basins_total"] == 5
    rj = json.loads((Path(small_run.out_dir) / "refused_basins.geojson").read_text())
    assert all("rank" not in f["properties"] and "score" not in f["properties"]
               for f in rj["features"])
    assert set(rj["features"][0]["properties"].keys()) == {
        "phase1_basin_id", "nodata_frac", "reason"}
    assert {f["properties"]["phase1_basin_id"] for f in rj["features"]} == {100, 101}


def test_refused_is_falsy_empty_list_behaves_like_none(tmp_path, small_run):
    """refused=[] (a real ranked-with-zero-refused pipeline result) must render nothing -- an
    empty list is falsy, same as None."""
    write_dnbr_outputs(*small_run.args, refused=[])
    assert not (Path(small_run.out_dir) / "refused_basins.csv").exists()
    assert "UNKNOWN" not in ranking_header(small_run.out_dir)


def test_stale_refused_sidecars_are_purged(tmp_path, small_run, refused_two):
    """Mirrors test_dnbr_outputs.py's refusal.json/map_dual_rank.png purge tests: a stale
    refused sidecar from an earlier degraded run must not survive a fresh clean write."""
    write_dnbr_outputs(*small_run.args, refused=refused_two)
    assert (Path(small_run.out_dir) / "refused_basins.csv").exists()
    assert (Path(small_run.out_dir) / "refused_basins.geojson").exists()

    write_dnbr_outputs(*small_run.args, refused=None)
    assert not (Path(small_run.out_dir) / "refused_basins.csv").exists()
    assert not (Path(small_run.out_dir) / "refused_basins.geojson").exists()


# ---- refused_basins.csv column contract ------------------------------------------------------

def test_refused_csv_columns_and_nan_mean_slope(tmp_path, small_run, refused_two):
    write_dnbr_outputs(*small_run.args, refused=refused_two)
    with open(Path(small_run.out_dir) / "refused_basins.csv") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == [
        "phase1_basin_id", "nodata_frac", "reason", "area_km2", "mean_slope"]
    by_id = {int(r["phase1_basin_id"]): r for r in rows}
    assert by_id[100]["reason"] == "dNBR NoData 35% > 20% (cloud/scene-edge)"
    assert float(by_id[100]["nodata_frac"]) == pytest.approx(0.35)
    assert float(by_id[100]["mean_slope"]) == pytest.approx(0.4)
    assert by_id[101]["mean_slope"] == ""          # all-NaN mean_slope -> "" (nan-safe)


def test_refused_csv_missing_area_km2_fails_loud(tmp_path, small_run):
    """area_km2 is attached before the partition (stage_2c_delineate / build_geometry_records)
    -- every real refused record carries it. A record missing it is a broken invariant, not a
    gap to silently fill: read it directly and let a missing key KeyError loudly."""
    r = _refused_record(200, 0.5)
    del r["area_km2"]
    with pytest.raises(KeyError):
        write_dnbr_outputs(*small_run.args, refused=[r])


# ---- ranking.csv: always-on nodata_frac, imagery line, incised column order -------------------

def test_ranking_csv_nodata_frac_defaults_empty_when_absent(tmp_path, small_run):
    write_dnbr_outputs(*small_run.args)
    _, rows = _read_rows(Path(small_run.out_dir) / "ranking.csv")
    assert rows[0]["nodata_frac"] == ""            # _fake_arm basins carry no nodata_frac


def test_ranking_csv_nodata_frac_uses_real_value_when_present(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dem = _write_fake_dem(tmp_path)
    arm_a, arm_b = _fake_arm(incised=False), _fake_arm(incised=False)
    for b in arm_a["basins"]:
        b["nodata_frac"] = 0.15
    write_dnbr_outputs(arm_a, arm_b, None, out_dir, dem, "nd_value_test")
    _, rows = _read_rows(out_dir / "ranking.csv")
    assert float(rows[0]["nodata_frac"]) == pytest.approx(0.15)


def test_incised_row_column_order_places_nodata_frac_before_intensity(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dem = _write_fake_dem(tmp_path)
    csv_path, _ = write_dnbr_outputs(_fake_arm(incised=True), _fake_arm(incised=True), None,
                                     out_dir, dem, "order_test", incised=True)
    _, rows = _read_rows(csv_path)
    keys = list(rows[0].keys())
    assert keys[-2:] == ["intensity", "intensity_rank"]     # test_dnbr_outputs.py's existing lock
    assert keys.index("nodata_frac") < keys.index("intensity")


def test_imagery_header_line_only_when_passed(tmp_path, small_run):
    imagery = {"sensor": "S2", "pre_id": "PRE1", "pre_date": "2024-01-01",
              "post_id": "POST1", "post_date": "2024-02-15"}
    write_dnbr_outputs(*small_run.args, imagery=imagery)
    header = ranking_header(small_run.out_dir)
    assert "# imagery: S2 pre PRE1 (2024-01-01) -> post POST1 (2024-02-15)" in header

    write_dnbr_outputs(*small_run.args, imagery=None)
    assert "imagery:" not in ranking_header(small_run.out_dir)


# ---- dual-rank map: hatched refused layer ----------------------------------------------------

def test_dual_rank_map_hatches_refused_when_sidecar_present(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dem = _write_fake_dem(tmp_path)
    refused = [_refused_record(100, 0.4)]
    write_dnbr_outputs(_fake_arm(incised=True), _fake_arm(incised=True), None, out_dir, dem,
                       "incised_refusal_test", incised=True, refused=refused)
    png = out_dir / "map_dual_rank.png"
    assert png.exists()
    data = png.read_bytes()
    assert data[:4] == b"\x89PNG"
    assert len(data) > 5000
    assert (out_dir / "refused_basins.geojson").exists()


def test_write_dual_rank_map_tolerates_missing_refused_sidecar(tmp_path):
    """A refused_gj_path pointing at a file that was never written (the clean-run case, or a
    stale/removed path) must not error -- just skip the hatch layer."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dem = _write_fake_dem(tmp_path)
    csv_path, gj_path = write_dnbr_outputs(_fake_arm(incised=True), _fake_arm(incised=True), None,
                                           out_dir, dem, "no_refusal_test", incised=True)
    out_png = out_dir / "standalone_map.png"
    write_dual_rank_map(gj_path, dem, out_png, "no_refusal_test",
                        refused_gj_path=out_dir / "does_not_exist.geojson")
    assert out_png.exists()
    assert out_png.read_bytes()[:4] == b"\x89PNG"
