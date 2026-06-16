"""hydrology.py -- pysheds flow modelling: fill pits -> fill depressions ->
resolve flats -> D8 flow direction -> flow accumulation. Pure terrain
processing; knows nothing of outlets or scores. See ARCHITECTURE.md.

P1.3 SCOPE (behavior-preserving extract from validation/gate.py stage_2a): the five-step
pysheds flow chain, lifted verbatim as ONE function. It receives the already-loaded,
already-aligned pysheds `grid` + `Raster` `dem` (from ingest.load_dem) and returns the two
flow products downstream consumes. Deliberately NOT here (stay in gate): the DEM/SBS alignment
block, slope/contour (derive from dem_raw), and master-outlet detection + catchment (delineate,
P1.4). The conditioned DEM (resolve_flats output) feeds flowdir and is not consumed downstream,
so it is not returned (fixed arity 2).

IMPORT-TIME I/O BAN: nothing executes at module load; the chain is `grid` METHODS on the passed
grid, so this module needs no pysheds import -- only DIRMAP from config (one binding, no literal).
"""
from __future__ import annotations

from src.config import DIRMAP


def run_hydrology(grid, dem):
    """Run the five-step pysheds chain on the passed `grid`, return (fdir, acc) as pysheds Rasters.

    Args:
      grid -- the pysheds Grid from ingest.load_dem (the SAME instance the caller holds; threaded
              through and mutated in place by these methods, never re-instantiated here)
      dem  -- the pysheds Raster from grid.read_raster (NOT dem_raw); the fill_pits input
    Returns (fdir, acc):
      fdir -- D8 flow direction, pysheds Raster (delineate's grid.catchment needs the Raster type)
      acc  -- flow accumulation (cell counts), pysheds Raster (gate converts via np.asarray)
    Step order and every kwarg are preserved verbatim; dirmap=DIRMAP (config) at flowdir + accumulation."""
    pit_filled = grid.fill_pits(dem)
    flooded    = grid.fill_depressions(pit_filled)
    inflated   = grid.resolve_flats(flooded)            # conditioned DEM for routing (chain-internal)
    fdir = grid.flowdir(inflated, dirmap=DIRMAP, routing="d8")
    acc  = grid.accumulation(fdir, dirmap=DIRMAP, routing="d8")
    return fdir, acc
