"""score.py -- the FROZEN heuristic: score = mean_burn x mean_slope x area_km2, then a
within-fire ordinal ranking. Changing the formula re-opens validation (DECISIONS).
"""
from __future__ import annotations

import numpy as np

from src.config import BURN_LOW_COVERAGE, SLOPE_LOW_COVERAGE
from src.grids import GateAbort


def stage_2e_score(wt, covered, slope, basins):
    """score = mean_burn [0-1] x mean_slope [tan] x area_km2 [km^2]; within-fire ordinal rank."""
    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        ncov = int((m & covered).sum())
        b["burn_coverage_frac"] = ncov / ncells if ncells else 0.0
        # A17: mean over ALL basin cells; outside-perimeter/NoData(15) included as 0.0
        b["mean_burn"] = float(np.mean(wt[m])) if ncells else 0.0
        # A32: an empty mask = a broken delineation premise -> fail loud, never nan.
        if not ncells:
            raise GateAbort(
                f"basin {b['basin_id']}: mean_slope on empty mask (ncells=0) -- violates "
                f"delineate's MIN_BASIN_KM2 guarantee, so the run's premises are broken (A32). "
                f"Refusing to emit a nan score."
            )
        # A33: slope carries NaN on the nodata-adjacent ring (FM-12 spurious cliffs); mean over
        # clean cells only, fail loud if a basin has none.
        sl = slope[m]
        sl = sl[~np.isnan(sl)]
        if sl.size == 0:
            raise GateAbort(
                f"basin {b['basin_id']}: every slope cell is nodata-contaminated (dropped ring) -- the "
                f"basin sits entirely on the FM-12 nodata edge; refusing a nan score (A33/A8 fail-loud)."
            )
        b["mean_slope"] = float(np.mean(sl))                             # tan(theta), clean cells
        b["slope_coverage_frac"] = sl.size / ncells                      # diagnostic; never gates score/rank
        b["low_slope_coverage"] = b["slope_coverage_frac"] < SLOPE_LOW_COVERAGE
        # FROZEN term + evaluation order (IEEE multiply is non-associative; do not re-associate).
        b["score"] = b["mean_burn"] * b["mean_slope"] * b["area_km2"]
        b["low_coverage"] = b["burn_coverage_frac"] < BURN_LOW_COVERAGE

    # ordinal rank: score desc, ties -> ascending basin_id (deterministic)
    order = sorted(basins, key=lambda b: (-b["score"], b["basin_id"]))
    for rank, b in enumerate(order, start=1):
        b["rank"] = rank
    scores = [b["score"] for b in basins]
    n_ties = len(scores) - len(set(round(s, 12) for s in scores))
    return order, n_ties


def add_intensity_rank(basins):
    """intensity = mean_burn * mean_slope, the area-independent companion column (incised only, A39).
    Additional column; `score`/`rank` untouched. Promotion to range-front fires requires C1."""
    for b in basins:
        b["intensity"] = b["mean_burn"] * b["mean_slope"]
    order = sorted(basins, key=lambda b: (-b["intensity"], b["basin_id"]))
    for i, b in enumerate(order, 1):
        b["intensity_rank"] = i
    return order
