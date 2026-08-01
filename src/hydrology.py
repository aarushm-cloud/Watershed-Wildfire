"""hydrology.py -- pysheds flow chain: fill pits -> depressions -> flats -> D8 -> accumulation.

Pure terrain processing; knows nothing of outlets, scores, or the burn source.
"""
from __future__ import annotations

from src.config import DIRMAP


def run_hydrology(grid, dem):
    """Five-step pysheds chain on the passed grid -> (fdir, acc) Rasters. Order frozen (behavior-locked)."""
    pit_filled = grid.fill_pits(dem)
    flooded    = grid.fill_depressions(pit_filled)
    inflated   = grid.resolve_flats(flooded)            # conditioned DEM for routing (chain-internal)
    fdir = grid.flowdir(inflated, dirmap=DIRMAP, routing="d8")
    acc  = grid.accumulation(fdir, dirmap=DIRMAP, routing="d8")
    return fdir, acc
