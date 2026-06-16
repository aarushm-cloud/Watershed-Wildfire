"""score.py -- the frozen heuristic: score = mean_burn x mean_slope x
contributing_area_km2, then a within-fire ordinal ranking. The formula is
frozen; changing it re-opens validation. See ARCHITECTURE.md.

P1.5 SCOPE (behavior-preserving extract from validation/gate.py stage 2e): the frozen scoring +
ranking stage and its fused helper `_burn_weight_raster` (A17 burn-weight remap + A18 coverage),
lifted VERBATIM. EXPLICIT-ARGS signatures (no dict-bag): the raw SBS raster and the precomputed
per-cell `slope` raster arrive as named args (the SBS load via ingest.load_burn and the
mean_slope_tan computation stay at gate's call site -- score imports neither ingest nor grids).

FROZEN (do NOT touch): the term order AND evaluation order of `mean_burn * mean_slope * area_km2`
(IEEE multiply is non-associative -- re-associating could flip a hair-close pair); the rank /
tercile logic; the A17 direction (class 15 / outside-perimeter -> 0.0, INCLUDED in the burn mean);
A18 coverage = sbs in {1,2,3,4} (excludes Developed=0 + NoData=15), `low_coverage` flag-only (never
excludes a basin from the ranking). No new types (C9).

IMPORT-TIME I/O BAN: nothing executes at module load; imports config + numpy only.
"""
from __future__ import annotations

import numpy as np

from src.config import BURN_WEIGHTS, BURN_LOW_COVERAGE


def _burn_weight_raster(sbs: np.ndarray):
    """Per-cell burn weight (A17, canonical): classes 1-4 -> BURN_WEIGHTS; Developed(0) and
    outside-perimeter/NoData(15) -> 0.0, all INCLUDED in the denominator (coverage-weighted).
    Returns (wt, covered); covered = cells with a real burn assessment, class in {1,2,3,4}
    (excludes Developed=0 and NoData=15) -- the A18/C8 fix; used only for the burn_coverage_frac
    caveat, NOT to gate the mean."""
    wt = np.zeros(sbs.shape, dtype=np.float64)
    for cls, w in BURN_WEIGHTS.items():      # classes 1..4 (0 and 15 stay 0.0)
        wt[sbs == cls] = w
    covered = np.isin(sbs, (1, 2, 3, 4))
    return wt, covered


def stage_2e_score(sbs, slope, basins):
    """mean_burn x mean_slope x area_km2 (science_reference s1), within-fire ordinal rank.

    score = mean_burn [0-1, dimensionless] x mean_slope [tan, dimensionless] x area_km2 [km^2].

    Args (explicit, from gate's call site): sbs -- raw SBS class raster (ingest.load_burn);
    slope -- per-cell tan(theta) raster from mean_slope_tan(dem_raw), computed in gate; basins --
    the delineated basins (masks/areas/basin_id) from delineate."""
    wt, covered = _burn_weight_raster(sbs)   # A17: coverage-weighted (class 15 -> 0.0, included)

    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        ncov = int((m & covered).sum())
        b["burn_coverage_frac"] = ncov / ncells if ncells else 0.0
        # A17: mean over ALL basin cells; outside-perimeter/NoData(15) included as 0.0
        b["mean_burn"] = float(np.mean(wt[m])) if ncells else 0.0
        b["mean_slope"] = float(np.mean(slope[m]))                       # tan(theta), dimensionless
        b["score"] = b["mean_burn"] * b["mean_slope"] * b["area_km2"]    # burn[0-1] x slope[tan] x km^2
        b["low_coverage"] = b["burn_coverage_frac"] < BURN_LOW_COVERAGE

    # ordinal rank: score desc, ties -> ascending basin_id (deterministic)
    order = sorted(basins, key=lambda b: (-b["score"], b["basin_id"]))
    for rank, b in enumerate(order, start=1):
        b["rank"] = rank
    scores = [b["score"] for b in basins]
    n_ties = len(scores) - len(set(round(s, 12) for s in scores))
    return order, n_ties
