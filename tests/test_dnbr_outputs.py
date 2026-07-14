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

import pytest

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
