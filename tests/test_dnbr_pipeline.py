"""CF-A (A34 / P2.2c): run_pipeline scores a dNBR fire through BOTH arms and reproduces the committed
P2.3 Montecito swap-test oracle.

The dNBR scoring path was built + unit-tested (src.ingest.ingest_dnbr_both_arms) but UNWIRED --
`ingest_burn` hard-refused any non-SBS selection (A29), so production could not score dNBR at all.
This locks the production wiring: a dNBR fire (sbs=None, dnbr=<native raster>) runs end-to-end, scores
Arm A (binned, primary) and Arm B (continuous companion), and returns both.

Oracle (frozen; validation/out/montecito_dnbr/p2_3_side_by_side + the dNBR input-swap finding):
  Arm A (primary):   #1 = San Ysidro Creek (basin 9, score 3.314); Cold Spring (basin 6) = rank 2 (3.280).
  Arm B (companion): #1 = Cold Spring (basin 6).
  rank-AUC = 0.9722 under BOTH arms (identical to the SBS control -- triage preserved).

Delineation is burn-independent (DEM + assets only), so the dNBR fire yields the SAME 36 basins /
slope / area as the SBS control; only mean_burn moves. The SBS behavior lock (test_behavior_lock.py)
is the regression guard that this wiring leaves the SBS path byte-identical.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.pipeline import run_pipeline, MONTECITO_DNBR_FIRE

EXPECTED_AUC = 0.9722222222222222
COLD_SPRING_ID = 6
SAN_YSIDRO_ID = 9


def _by_id(basins):
    return {int(b["basin_id"]): b for b in basins}


def test_dnbr_fire_scores_both_arms_and_reproduces_oracle():
    """The load-bearing CF-A lock: production dNBR both-arms == the P2.3 swap-test oracle."""
    R = run_pipeline(MONTECITO_DNBR_FIRE)

    assert R["status"] == "ranked"
    assert R["provenance"]["burn_source"] == "dNBR"
    assert R["headline_arm"] == "arm_a"

    A, B = R["arms"]["arm_a"], R["arms"]["arm_b"]
    assert len(A["basins"]) == 36 and len(B["basins"]) == 36
    a_by, b_by = _by_id(A["basins"]), _by_id(B["basins"])

    # Arm A (binned, pre-registered primary): San Ysidro (b9) #1, Cold Spring (b6) #2 -- the documented
    # 1.03% burn-driven transposition that failed the pre-registered exact-#1 criterion.
    assert A["ranked"][0]["basin_id"] == SAN_YSIDRO_ID
    assert a_by[COLD_SPRING_ID]["rank"] == 2
    assert abs(a_by[SAN_YSIDRO_ID]["score"] - 3.314) < 0.01
    assert abs(a_by[COLD_SPRING_ID]["score"] - 3.280) < 0.01

    # Arm B (continuous companion): reproduces Cold Spring (b6) at #1.
    assert B["ranked"][0]["basin_id"] == COLD_SPRING_ID

    # Triage AUC identical across arms (the finding's headline: dNBR finds the flow basins as well as SBS).
    assert abs(A["metrics"]["auc"] - EXPECTED_AUC) < 1e-3
    assert abs(B["metrics"]["auc"] - EXPECTED_AUC) < 1e-3


def test_dnbr_headline_mirrors_arm_a():
    """Uniform-consumer contract: top-level ranked/metrics mirror the Arm A headline."""
    R = run_pipeline(MONTECITO_DNBR_FIRE)
    assert R["ranked"][0]["basin_id"] == SAN_YSIDRO_ID
    assert R["metrics"]["auc"] == R["arms"]["arm_a"]["metrics"]["auc"]


def test_dnbr_nodata_flags_surfaces_over_threshold_without_raising():
    """F3 (B+): _dnbr_nodata_flags is the loud-but-NON-FATAL companion to the hard NoData guard. It
    returns the (basin_id, frac) of basins whose dNBR NoData exceeds the fail-loud fraction and NEVER
    raises -- the surfacing that keeps the flowed-only guard from silently under-scoring a clouded
    (non-flowed) basin (NoData -> class 15 -> low burn)."""
    import numpy as np
    from src.pipeline import _dnbr_nodata_flags
    from src.config import DNBR_NODATA_FAILLOUD_FRAC
    shape = (4, 4)
    nodata_mask = np.zeros(shape, dtype=bool)
    nodata_mask[0, :] = True                                 # top row is NoData
    clean = np.zeros(shape, dtype=bool); clean[2:4, 0:2] = True      # 0% nodata
    clouded = np.zeros(shape, dtype=bool); clouded[0:2, 0:2] = True  # 2 of 4 cells NoData -> 50% (> 20%)
    basins = [{"basin_id": 5, "mask": clean}, {"basin_id": 7, "mask": clouded}]
    over = _dnbr_nodata_flags(basins, nodata_mask)           # must NOT raise
    ids = [bid for bid, frac in over]
    assert 7 in ids and 5 not in ids                         # only the clouded basin is flagged
    assert all(frac > DNBR_NODATA_FAILLOUD_FRAC for _, frac in over)


def test_montecito_dnbr_run_surfaces_nodata_warn_list():
    """F3 (B+): the run threads the non-fatal NoData warning into the result diag. Montecito's raster is
    NoData-clean, so the list is empty -- but the KEY must exist so a future clouded fire is surfaced."""
    R = run_pipeline(MONTECITO_DNBR_FIRE)
    assert R["dnbr_diag"]["nodata_warn_basins"] == []
