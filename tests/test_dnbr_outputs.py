"""CF-A (A34) output layer: the dNBR both-arms ranking.csv + basins.geojson.

Arm A (binned) is the headline ranking (rank / score); Arm B (continuous) rides alongside
(rank_b / score_b) with rank_delta = |rankA - rankB| as an honest uncertainty flag (basins where the
two burn methods disagree = rank uncertain). Every artifact carries the screening spine + the n=1
'triage-validated, not exact-rank-validated' dNBR framing.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

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
