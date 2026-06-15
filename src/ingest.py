"""ingest.py -- the front door: load DEM/burn/assets/creeks and carry the burn-source
provenance stamp. See ARCHITECTURE.md and DECISIONS A2/A3/A4/A8/A15.

P1.2 SCOPE (behavior-preserving extract from validation/gate.py): the RAW input reads and
the `BURN_SOURCE` provenance string, lifted verbatim. Deliberately NOT here yet (stay in gate,
unchanged): the DEM/SBS alignment-validation block + transform extraction (cross-input checks,
beyond a raw read), the burn-class->weight remap + A18 coverage masking, the `_assert_metric_crs`
guards on assets/creeks, and burn-SOURCE SELECTION (SBS-only; the SBS-else-dNBR precedence is P2,
A12). No Provenance dataclass / no aggregator (C9, D0): one function per input, paths passed in.

IMPORT-TIME I/O BAN: every read lives inside a function; nothing here touches the filesystem at
module load, so the module is importable without any input present (keeps it path-agnostic --
paths are owned by gate.py / run.py and passed as arguments).
"""
from __future__ import annotations

import numpy as np
import geopandas as gpd
import rasterio
from pysheds.grid import Grid


# --- provenance (A4/A11): the single burn-source stamp every output carries. SBS-only for the
# validation fire; no selection logic here (P2/A12). Loose string form preserved (no dataclass, C9).
BURN_SOURCE = "SBS"  # validation input (BAER Soil Burn Severity)


def load_dem(path):
    """Load the DEM into a pysheds Grid + raw elevation array (metres). Raw read only.

    Returns (grid, dem, dem_raw) -- the FULL consumed surface of this read:
      grid    -- pysheds Grid built from the DEM (downstream: conditioning, flow, catchments)
      dem     -- the pysheds Raster from read_raster (downstream: grid.fill_pits input)
      dem_raw -- float64 copy of the elevation raster (metres), used for slope + contour tests
    The rasterio metadata read (CRS/shape/transform) for DEM/SBS alignment stays in gate -- it is
    cross-input validation, not a raw read. `read_raster` is called exactly as the gate did it
    (no nodata/masked args; pysheds defaults unchanged)."""
    grid = Grid.from_raster(str(path))
    dem = grid.read_raster(str(path))
    dem_raw = np.asarray(dem, dtype=np.float64).copy()  # raw terrain elevation (m)
    return grid, dem, dem_raw


def load_burn(path):
    """Load the burn raster band 1 as the RAW SBS class array (no remap). Raw read only.

    Returns the integer SBS class raster (classes per config.BURN_WEIGHTS encoding). The
    class->weight remap and A18 coverage masking stay in gate (`_burn_weight_raster`). Same
    `read(1)` call, same band, default masked=False -- nodata propagation unchanged."""
    with rasterio.open(path) as s:
        return s.read(1)


def load_assets(path):
    """Load the asset (building) layer as a GeoDataFrame. Raw read only.

    Returns the GeoDataFrame verbatim from `gpd.read_file`; the gate still does the
    `_assert_metric_crs(.crs)` guard and the x/y coordinate extraction downstream."""
    return gpd.read_file(path)


def load_creeks(path):
    """Load the truth creek/channel layer as a GeoDataFrame. Raw read only.

    Returns the GeoDataFrame verbatim from `gpd.read_file`; the gate still does the
    `_assert_metric_crs(.crs)` guard and the geometry-validity check downstream."""
    return gpd.read_file(path)
