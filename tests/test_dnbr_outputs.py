"""CF-A (A34) output layer: the dNBR both-arms ranking.csv + basins.geojson.

Arm A (binned) is the headline ranking (rank / score); Arm B (continuous) rides alongside
(rank_b / score_b) with rank_delta = |rankA - rankB| as an honest uncertainty flag (basins where the
two burn methods disagree = rank uncertain). Every artifact carries the screening spine + the n=1
'triage-validated, not exact-rank-validated' dNBR framing.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.pipeline import run_pipeline, MONTECITO_DNBR_FIRE
from src.outputs import write_dnbr_outputs


def _run_and_write(tmp_path):
    R = run_pipeline(MONTECITO_DNBR_FIRE)
    csv_path, gj_path = write_dnbr_outputs(
        R["arms"]["arm_a"], R["arms"]["arm_b"], R["creek_nearest"], tmp_path,
        MONTECITO_DNBR_FIRE["dem"], MONTECITO_DNBR_FIRE["validation_case"])
    return csv_path, gj_path


def test_write_dnbr_outputs_fails_loud_on_zero_basins(tmp_path):
    # F9: a 0-basin result must NOT silently emit an empty CSV/GeoJSON (indistinguishable from a broken
    # delineation) -- refuse loudly (A8). ValueError, not GateAbort, so the pure-serialization sink
    # keeps its no-project-imports design; run_screening + the CLI still surface it legibly.
    empty = {"basins": [], "ranked": []}
    with pytest.raises(ValueError) as e:
        write_dnbr_outputs(empty, empty, None, tmp_path, "/nonexistent/dem.tif", "test-fire")
    assert "0 basins" in str(e.value)


def test_write_outputs_fails_loud_on_zero_basins(tmp_path):
    from src.outputs import write_outputs
    with pytest.raises(ValueError) as e:
        write_outputs([], {}, tmp_path, "/nonexistent/dem.tif", "SBS")
    assert "0 basins" in str(e.value)


def test_dnbr_provenance_has_no_master_zone_field(tmp_path):
    # Drop-entirely contract (scale-free FM-1 guard supersedes PASS/FINDING/ABORT): the retired zone
    # label must NOT travel on the artifact, and no low-confidence master caveat is emitted -- the guard
    # aborts on collapse upstream instead of stamping a Montecito-calibrated band. See DECISIONS
    # (scale-free master-outlet guard) + docs/ALGORITHMS_REVIEW.md T5.
    csv_path, gj_path = _run_and_write(tmp_path)
    assert "low-confidence" not in "".join(_read_rows(csv_path)[0]).lower()
    fc = json.loads(Path(gj_path).read_text())
    assert "master_zone" not in fc["provenance"]


def test_dnbr_geojson_carries_low_coverage(tmp_path):
    # minor: the dNBR GeoJSON omitted the burn low_coverage flag (CSV-only), so a map consumer could
    # not surface the A18 caveat. It must appear in the feature properties.
    _, gj_path = _run_and_write(tmp_path)
    fc = json.loads(Path(gj_path).read_text())
    assert "low_coverage" in fc["features"][0]["properties"]


def _read_rows(csv_path):
    with open(csv_path) as fh:
        lines = list(fh)
    header = [ln for ln in lines if ln.startswith("#")]
    data = [ln for ln in lines if not ln.startswith("#")]
    return header, list(csv.DictReader(data))


def test_dnbr_csv_has_both_arms_and_framing(tmp_path):
    csv_path, gj_path = _run_and_write(tmp_path)
    assert csv_path.exists() and gj_path.exists()
    header, rows = _read_rows(csv_path)
    htext = "".join(header).lower()
    assert "screening" in htext or "not a prediction" in htext        # spine travels
    assert "burn_source=dnbr" in htext                                 # provenance
    assert "triage-validated" in htext and "not exact-rank" in htext   # n=1 framing
    for col in ("basin_id", "rank", "score", "rank_b", "score_b", "rank_delta"):
        assert col in rows[0], f"missing column {col}"


def test_dnbr_csv_headline_is_arm_a_and_rank_delta_flags_disagreement(tmp_path):
    csv_path, _ = _run_and_write(tmp_path)
    _, rows = _read_rows(csv_path)
    by_id = {int(r["basin_id"]): r for r in rows}
    # headline (rank) = Arm A: San Ysidro (b9) #1, Cold Spring (b6) #2; Arm B puts Cold Spring #1
    assert int(by_id[9]["rank"]) == 1
    assert int(by_id[6]["rank"]) == 2
    assert int(by_id[6]["rank_b"]) == 1
    # rank_delta = |rankA - rankB| everywhere; the top two disagree -> the honest uncertainty flag
    for r in rows:
        assert int(r["rank_delta"]) == abs(int(r["rank"]) - int(r["rank_b"]))
    assert int(by_id[6]["rank_delta"]) == 1
    # rows are ordered by the Arm A headline rank
    ranks = [int(r["rank"]) for r in rows]
    assert ranks == sorted(ranks)


def test_dnbr_csv_and_geojson_carry_slope_coverage_flag(tmp_path):
    """F4: the slope-coverage flag (a basin scored on a small non-nodata-ring remnant) travels on the
    dNBR CSV and GeoJSON. Montecito is inland (fully covered, flag False), so this asserts the COLUMN
    is PRESENT -- a coastal fire's flagged basins must be surfaced, never silently ranked."""
    import json
    csv_path, gj_path = _run_and_write(tmp_path)
    _, rows = _read_rows(csv_path)
    assert "low_slope_coverage" in rows[0] and "slope_coverage_frac" in rows[0]
    fc = json.loads(Path(gj_path).read_text())
    props0 = fc["features"][0]["properties"]
    assert "low_slope_coverage" in props0 and "slope_coverage_frac" in props0


# ---- Task 7: INCISED_FRAMING, conditional intensity columns, ordering, engine provenance -------

def test_accepted_fire_schema_is_unchanged(tmp_path):
    """REGRESSION LOCK: the incised path must not alter accepted-fire output."""
    csv_path, _ = _run_and_write(tmp_path)
    header, rows = _read_rows(csv_path)
    assert list(rows[0].keys()) == [
        "basin_id", "rank", "score", "rank_b", "score_b", "rank_delta",
        "mean_burn_a", "mean_burn_b", "mean_slope", "slope_coverage_frac",
        "low_slope_coverage", "area_km2", "burn_coverage_frac", "low_coverage",
        "flowed", "matched_creek", "nearest_outlet_dist_m",
    ]
    assert not any("EXPLORATORY" in h for h in header), \
        "an accepted fire must never carry the incised disclaimer"


def _write_fake_dem(tmp_path, n=4):
    """Tiny synthetic GeoTIFF (mirrors tests/test_entrypoint.py::_write_synthetic_dem) -- adaptation:
    write_dnbr_outputs unconditionally opens dem_tif to build the GeoJSON, real fire or not, so the
    fake-arm tests below need an openable raster rather than the brief's literal "dem.tif" string."""
    path = tmp_path / "fake_dem.tif"
    transform = from_origin(500000.0, 3800000.0, 10.0, 10.0)
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1,
                       dtype="float32", crs="EPSG:32611", transform=transform) as d:
        d.write(np.full((n, n), 100.0, dtype="float32"), 1)
    return str(path)


def _fake_arm(n=3, incised=True):
    basins = []
    for i in range(n):
        mask = np.zeros((4, 4), dtype=bool)
        mask[1:3, 1:3] = True   # a 2x2 block -> a non-empty polygon when vectorised (adaptation:
        #                         the brief's basins had no "mask"; write_dnbr_outputs needs one for
        #                         every basin to build the GeoJSON, incised or not).
        b = {"basin_id": i, "rank": n - i, "score": 0.1 * (n - i),
             "mean_burn": 0.4, "mean_slope": 0.3 + 0.1 * i, "area_km2": 1.0,
             "burn_coverage_frac": 1.0, "low_coverage": False,
             "slope_coverage_frac": 1.0, "low_slope_coverage": False,
             "mask": mask}
        if incised:
            b["intensity"] = b["mean_burn"] * b["mean_slope"]
            # adaptation: NOT "n - i" (== rank) -- that would make the rows already come out of the
            # rank-ordered loop in intensity_rank order too, so the ordering test below would pass
            # even if the code never sorted by intensity_rank. i + 1 decouples the two orderings.
            b["intensity_rank"] = i + 1
        basins.append(b)
    ranked = sorted(basins, key=lambda b: b["rank"])
    return {"basins": basins, "ranked": ranked, "n_ties": 0, "metrics": {}}


def test_incised_appends_intensity_and_stamps_disclaimer(tmp_path):
    from src.outputs import write_dnbr_outputs, INCISED_FRAMING
    csv_path, _ = write_dnbr_outputs(
        _fake_arm(), _fake_arm(), None, tmp_path, _write_fake_dem(tmp_path), "incised_test",
        incised=True, subbasin_meta={"engine": "whiteboxtools", "wbt_version": "v2.4.0",
                                     "acc_threshold_cells": 3000, "breach_dist_cells": 100})
    header, rows = _read_rows(csv_path)
    assert list(rows[0].keys())[-2:] == ["intensity", "intensity_rank"]
    assert any(INCISED_FRAMING in h for h in header)
    assert "EXPLORATORY" in INCISED_FRAMING


def test_incised_rows_are_ordered_by_intensity(tmp_path):
    """A39: intensity is the HEADLINE ordering -- a practitioner reading top-down must
    read it, not the frozen score order."""
    from src.outputs import write_dnbr_outputs
    csv_path, _ = write_dnbr_outputs(_fake_arm(), _fake_arm(), None, tmp_path,
                                     _write_fake_dem(tmp_path), "incised_test", incised=True)
    _, rows = _read_rows(csv_path)
    assert [int(r["intensity_rank"]) for r in rows] == sorted(
        int(r["intensity_rank"]) for r in rows)


def test_incised_stamps_engine_provenance(tmp_path):
    from src.outputs import write_dnbr_outputs
    _, gj_path = write_dnbr_outputs(
        _fake_arm(), _fake_arm(), None, tmp_path, _write_fake_dem(tmp_path), "incised_test",
        incised=True, subbasin_meta={"engine": "whiteboxtools", "wbt_version": "v2.4.0",
                                     "acc_threshold_cells": 3000, "breach_dist_cells": 100})
    import json
    prov = json.loads(gj_path.read_text())["provenance"]
    assert prov["basin_engine"] == "whiteboxtools"
    assert prov["wbt_version"] == "v2.4.0"
    assert prov["acc_threshold_cells"] == 3000


def test_write_dnbr_outputs_purges_stale_refusal_json(tmp_path):
    """Owner ruling (out_dir staleness hygiene): a fresh ranked write must not leave a stale
    refusal.json from a superseded run sitting alongside the new ranking.csv/basins.geojson --
    Task 14 had to hand-delete South Fork's stale one; this makes the writer do it."""
    from src.outputs import write_dnbr_outputs
    stale = tmp_path / "refusal.json"
    stale.write_text('{"status": "REFUSED"}')

    write_dnbr_outputs(_fake_arm(), _fake_arm(), None, tmp_path,
                       _write_fake_dem(tmp_path), "purge_test")

    assert not stale.exists()


# ---- Dual-rank map PNG (owner-requested product artifact; incised-gated) ------------------------

def test_incised_write_produces_dual_rank_map_png(tmp_path):
    """A39 dual-rank map: an incised write must also land map_dual_rank.png (SIZE rank panel +
    INTENSITY rank panel over a hillshade) -- a real PNG, not a stub."""
    from src.outputs import write_dnbr_outputs
    write_dnbr_outputs(_fake_arm(), _fake_arm(), None, tmp_path,
                       _write_fake_dem(tmp_path), "incised_test", incised=True)
    png = tmp_path / "map_dual_rank.png"
    assert png.exists(), "incised write must produce map_dual_rank.png"
    data = png.read_bytes()
    assert data[:4] == b"\x89PNG"
    assert len(data) > 5000, "a rendered two-panel figure, not a stub"


def test_accepted_write_produces_no_dual_rank_map(tmp_path):
    """Incised-gated: intensity must NEVER appear on accepted-fire output -- so the intensity-
    bearing map file must not exist after an accepted (incised=False) write. Pre-seeds a STALE
    map_dual_rank.png (mirroring the neighboring refusal-purge test, test_write_dnbr_outputs_purges_
    stale_refusal_json above) so this proves the writer REMOVES superseded-run debris left by an
    earlier incised run into the same persistent out_dir -- not merely that it never creates the
    file itself (map-export review Fix 1: a live-reproduced gap, the writer purged refusal.json
    unconditionally but never map_dual_rank.png)."""
    from src.outputs import write_dnbr_outputs, DUAL_RANK_MAP_NAME
    stale = tmp_path / DUAL_RANK_MAP_NAME
    stale.write_bytes(b"\x89PNG\r\n\x1a\nfake-stale-map")
    write_dnbr_outputs(_fake_arm(incised=False), _fake_arm(incised=False), None, tmp_path,
                       _write_fake_dem(tmp_path), "accepted_test")
    assert not (tmp_path / DUAL_RANK_MAP_NAME).exists()
