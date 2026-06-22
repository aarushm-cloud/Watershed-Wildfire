"""P3.3 OUTPUT-CRS lock (A25) -- the single most dangerous site: outputs.py must label the
basins GeoDataFrame with the PER-FIRE CRS (read off the DEM the run is scoped to) BEFORE the
WGS84 reprojection -- never the hardcoded validation-zone CANONICAL_CRS (EPSG:32611).

WHY THIS MATTERS: if a 32613 fire's basins are constructed with crs=32611 and then
.to_crs("EPSG:4326")'d, every polygon is silently mis-georeferenced -- South Fork (UTM 13N, New
Mexico) meter-coordinates get interpreted as UTM 11N (California) and reprojected into the wrong
hemisphere-quadrant with NO abort. A loud-failure project must not emit a confident, wrongly-placed
ranking. This is a SILENT-WRONG defect, exactly the class the guardrails exist to catch.

MECHANISM (verified on current code, P3.3): write_outputs() opens the dem_tif it is handed and
reads s.transform off it (outputs.py:72-74); the SAME open handle exposes s.crs -- the per-fire
decided CRS (== dem_profile["crs"], == gate.py's validated DEM CRS). The A25 fix reads s.crs there
and uses it at the GeoDataFrame construction site (outputs.py:87) instead of CANONICAL_CRS.

HOW THE TEST CATCHES IT (no value-substitution): the GeoDataFrame is built and immediately
.to_crs("EPSG:4326")'d on one chained line, so the pre-reprojection CRS is never returned. We
therefore SPY on the gpd.GeoDataFrame constructor inside outputs and RECORD the `crs=` it is
actually passed -- we do NOT monkeypatch the value outputs.py reads (that would bypass the very
crs=CANONICAL_CRS hardcode under test and pass falsely). Against current code the spy records
EPSG:32611 -> the 32613 case FAILS RED; after the A25 fix it records the DEM's CRS for both fires.

Run:  pytest tests/test_output_crs.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src import outputs


def _write_synthetic_dem(path, crs_epsg, x0, y0):
    """A tiny 4x4 metric GeoTIFF in the given UTM CRS, cells = 10 m (CELL_M).

    x0/y0 are the upper-left corner in that CRS's metres (chosen to sit inside the fire's real
    UTM zone, so a wrong source-CRS label reprojects the basins into the wrong place). The CRS is
    what write_outputs reads off the DEM handle (s.crs) -- the per-fire value the A25 fix uses."""
    transform = from_origin(x0, y0, 10.0, 10.0)   # 10 m cells, dx=dy=CELL_M
    data = np.array([[100, 101, 102, 103],
                     [104, 105, 106, 107],
                     [108, 109, 110, 111],
                     [112, 113, 114, 115]], dtype="float32")   # elevations (m), arbitrary
    with rasterio.open(path, "w", driver="GTiff", height=4, width=4, count=1,
                       dtype="float32", crs=f"EPSG:{crs_epsg}", transform=transform) as d:
        d.write(data, 1)


def _minimal_basins():
    """One scored basin with the exact loose-dict keys write_outputs reads (C9: loose dicts)."""
    mask = np.zeros((4, 4), dtype=bool)
    mask[1:3, 1:3] = True   # a 2x2 block -> a non-empty polygon when vectorised
    return [{
        "basin_id": 0, "rank": 1, "score": 1.234567,
        "mean_burn": 0.5, "mean_slope": 0.3, "area_km2": 0.04,
        "burn_coverage_frac": 0.9, "flowed": True, "matched_creek": "",
        "mask": mask,
    }]


# (epsg, UL-x, UL-y) -- South Fork UTM 13N (NM) and Montecito UTM 11N (CA) corners.
_CASES = [
    pytest.param(32613, 430000.0, 3692000.0, id="southfork_32613"),   # RED vs current (records 32611)
    pytest.param(32611, 250000.0, 3810000.0, id="montecito_32611"),   # regression: always green
]


@pytest.mark.parametrize("epsg,x0,y0", _CASES)
def test_output_gdf_uses_per_fire_crs(tmp_path, monkeypatch, epsg, x0, y0):
    """The basins GeoDataFrame is constructed with the PER-FIRE CRS (the DEM's CRS), not 32611.

    Exercises the real write_outputs construction path against a synthetic DEM whose CRS is the
    fire's CRS, and asserts the crs the GeoDataFrame is built with equals it -- BEFORE to_crs(4326).
    """
    dem_tif = tmp_path / "dem.tif"
    _write_synthetic_dem(dem_tif, epsg, x0, y0)

    # SPY: record (do NOT substitute) the crs passed to each gpd.GeoDataFrame construction.
    seen_crs = []
    real_gdf = gpd.GeoDataFrame

    def _spy(*args, **kwargs):
        seen_crs.append(kwargs.get("crs"))
        return real_gdf(*args, **kwargs)

    monkeypatch.setattr(outputs.gpd, "GeoDataFrame", _spy)

    outputs.write_outputs(_minimal_basins(), {}, tmp_path, dem_tif, "dNBR")

    assert seen_crs, "write_outputs never constructed a GeoDataFrame"
    construction_crs = seen_crs[0]   # the outputs.py:87 construction, before .to_crs(4326)
    assert str(construction_crs).upper() == f"EPSG:{epsg}", (
        f"output GeoDataFrame built with crs={construction_crs!r}, expected the per-fire "
        f"EPSG:{epsg} (read off the DEM). A 32613 fire labelled 32611 is silently "
        f"mis-georeferenced into California."
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
