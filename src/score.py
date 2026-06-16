"""score.py -- the frozen heuristic: score = mean_burn x mean_slope x
contributing_area_km2, then a within-fire ordinal ranking. The formula is
frozen; changing it re-opens validation. See ARCHITECTURE.md.

P1.5 / P2.2a SCOPE (behavior-preserving): the frozen scoring + ranking stage (the A17 mean_burn
reduction + the A18 coverage flag). EXPLICIT-ARGS signature (no dict-bag): the precomputed per-cell
burn WEIGHT raster `wt` (A17) and `covered` mask (A18) -- produced ONCE by the ingest seam
(ingest.ingest_burn, where `_burn_weight_raster` moved in P2.2a) -- plus the per-cell `slope` raster
arrive as named args. score imports neither ingest nor grids; the per-basin mean over basin masks
(which needs the delineated basins) stays here.

FROZEN (do NOT touch): the term order AND evaluation order of `mean_burn * mean_slope * area_km2`
(IEEE multiply is non-associative -- re-associating could flip a hair-close pair); the rank /
tercile logic; the A17 direction (class 15 / outside-perimeter -> 0.0, INCLUDED in the burn mean --
now applied in the weight raster at ingest); A18 coverage = sbs in {1,2,3,4} (excludes Developed=0
+ NoData=15), `low_coverage` flag-only (never excludes a basin from the ranking). No new types (C9).

IMPORT-TIME I/O BAN: nothing executes at module load; imports config + numpy only.
"""
from __future__ import annotations

import numpy as np

from src.config import BURN_LOW_COVERAGE


def stage_2e_score(wt, covered, slope, basins):
    """mean_burn x mean_slope x area_km2 (science_reference s1), within-fire ordinal rank.

    score = mean_burn [0-1, dimensionless] x mean_slope [tan, dimensionless] x area_km2 [km^2].

    Args (explicit, from gate's call site): wt -- per-cell burn WEIGHT raster (A17) and covered --
    per-cell real-assessment mask (A18), both from the ingest seam (ingest.ingest_burn); slope --
    per-cell tan(theta) raster from mean_slope_tan(dem_raw), computed in gate; basins -- the
    delineated basins (masks/areas/basin_id) from delineate."""
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
