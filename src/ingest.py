"""ingest.py -- the front door (the A15 seam): load DEM/burn/assets/creeks, SELECT the one burn
source by precedence, remap its classes to per-cell weights + the coverage mask, and emit the
single burn-source provenance. See ARCHITECTURE.md and DECISIONS A2/A3/A4/A8/A15.

P2.2a SCOPE (behavior-preserving): realises the A15 ingest seam. Burn-source SELECTION (A3
precedence), the class->weight remap + A18 coverage mask (moved here VERBATIM from score.py), and
the single Provenance stamp now live in this one file -- so adding the dNBR arm (P2.2b) is a change
INSIDE this file, not surgery across the pipeline. SBS-only: the dNBR branch is present but inert
(raises NotImplementedError("dNBR arm: P2.2b")). Outputs are bit-identical (Montecito SBS covers
the AOI -> resolves to SBS; new code path, identical values). Deliberately NOT here: the DEM/SBS
alignment check (stays at gate.stage_2a's call site, now via grids.assert_aligned) and the
per-basin mean_burn reduction (stays in score.py -- it needs the delineated basins, which do not
exist at ingest). No Provenance dataclass / no aggregator (C9/A19): the provenance is a loose dict.

IMPORT-TIME I/O BAN: every read lives inside a function; nothing here touches the filesystem at
module load, so the module is importable without any input present (keeps it path-agnostic --
paths are owned by gate.py / run.py and passed as arguments).
"""
from __future__ import annotations

import numpy as np
import geopandas as gpd
import rasterio
from pysheds.grid import Grid

from src.config import BURN_WEIGHTS


# --- BAER SBS thematic codeset: the known class values a valid SBS cell may hold. 1-4 = soil burn
# severity (Unburned/very-low .. High), 0 = Masked (Developed), 15 = outside-perimeter/NoData. A
# cell holding ANY OTHER value is genuinely missing data. sbs.tif declares NO rasterio nodata, so
# the GDAL mask is trivially 100% and cannot define "valid" -- membership in this codeset does
# (owner decision 2026-06-16; class 15 counts as covered, see select_burn_source).
SBS_CODESET = (0, 1, 2, 3, 4, 15)


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
    class->weight remap and A18 coverage masking now live in this module (`_burn_weight_raster`,
    moved from score.py in P2.2a) and are applied by the `ingest_burn` seam. Same `read(1)` call,
    same band, default masked=False -- nodata propagation unchanged."""
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


# ---------------------------------------------------------------------------
# The A15 burn seam: select ONE source -> remap to weights + coverage -> stamp provenance.
# ---------------------------------------------------------------------------
def select_burn_source(sbs: np.ndarray) -> str:
    """A3/A15 precedence: SBS if it covers the WHOLE AOI, else dNBR for the whole AOI (never
    blended). SBS-only for now; the dNBR branch is inert until P2.2b.

    AOI = the analysis grid. The DEM/SBS alignment check (grids.assert_aligned, run UPSTREAM in
    gate.stage_2a) guarantees the SBS raster shares the DEM grid cell-for-cell, so "SBS valid-data
    extent contains the whole AOI" (DATA_SOURCES.md s5) reduces to "every SBS cell holds a valid
    class value." VALID = value in SBS_CODESET {0,1,2,3,4,15}; class 15 (outside-perimeter) COUNTS
    as covered ("assessed: outside the burn"), NOT as missing -- owner decision 2026-06-16 (sbs.tif
    has no declared nodata, so codeset membership, not a GDAL mask, defines validity). "Covers the
    AOI" = EVERY cell is in-codeset; partial coverage -> NOT whole-area -> dNBR (A3)."""
    n_invalid = int((~np.isin(sbs, SBS_CODESET)).sum())   # cells outside the known SBS codeset
    if n_invalid == 0:
        return "SBS"
    # A3: partial SBS -> dNBR for the whole AOI. The dNBR arm is the P2.2b feature; THIS is the seam.
    raise NotImplementedError("dNBR arm: P2.2b")


def _burn_weight_raster(sbs: np.ndarray):
    """Per-cell burn weight (A17, canonical): classes 1-4 -> BURN_WEIGHTS; Developed(0) and
    outside-perimeter/NoData(15) -> 0.0, all INCLUDED in the denominator (coverage-weighted).
    Returns (wt, covered); covered = cells with a real burn assessment, class in {1,2,3,4}
    (excludes Developed=0 and NoData=15) -- the A18/C8 fix; used only for the burn_coverage_frac
    caveat, NOT to gate the mean.

    P2.2a: moved VERBATIM from score.py into the ingest seam so the weighted raster + coverage mask
    are produced once at ingest; score.py now consumes them (the per-basin mean stays in score)."""
    wt = np.zeros(sbs.shape, dtype=np.float64)
    for cls, w in BURN_WEIGHTS.items():      # classes 1..4 (0 and 15 stay 0.0)
        wt[sbs == cls] = w
    covered = np.isin(sbs, (1, 2, 3, 4))
    return wt, covered


def ingest_burn(burn_path):
    """A15 seam: select the one burn source, load it, remap to per-cell weights + coverage mask,
    and emit the single burn-source provenance (A4). SBS-only (the dNBR arm is inert, P2.2b).

    Returns (wt, covered, provenance):
      wt         -- per-cell burn weight raster [0-1, dimensionless], float64 (A17)
      covered    -- per-cell real-assessment mask, bool (A18)
      provenance -- the single loose-dict burn-source stamp every output carries (A4/A19)
    The per-basin mean_burn reduction stays in score.py (it needs the delineated basins)."""
    sbs = load_burn(burn_path)               # raw SBS class raster (band 1)
    burn_source = select_burn_source(sbs)    # A3 precedence -> "SBS" (or NotImplementedError: dNBR P2.2b)
    wt, covered = _burn_weight_raster(sbs)   # A17 weights + A18 coverage, computed once at ingest
    provenance = {"burn_source": burn_source}  # A4/A19: single loose-dict stamp, read everywhere
    return wt, covered, provenance
