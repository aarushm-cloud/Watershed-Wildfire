"""T3 back-port lock: the SBS writer surfaces the same per-basin diagnostics the dNBR writer
does -- slope_coverage_frac / low_slope_coverage (F4) + low_coverage (A18) -- in ranking.csv AND
basins.geojson. score.py computes them for every basin regardless of burn source; only the
serialization differed (algorithms-review T3, owner-bumped HIGH: "publish all basin data").
Additive columns only -- score/rank/ordering untouched.

Run:  pytest tests/core/test_outputs_columns.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src import outputs


def _dem(path):
    transform = from_origin(250000.0, 3810000.0, 10.0, 10.0)   # 10 m cells (CELL_M), Montecito zone
    data = (np.arange(16, dtype="float32") + 100.0).reshape(4, 4)
    with rasterio.open(path, "w", driver="GTiff", height=4, width=4, count=1,
                       dtype="float32", crs="EPSG:32611", transform=transform) as d:
        d.write(data, 1)


def _basins():
    mask = np.zeros((4, 4), dtype=bool)
    mask[1:3, 1:3] = True
    return [{
        "basin_id": 0, "rank": 1, "score": 1.234567,
        "mean_burn": 0.5, "mean_slope": 0.3,
        "slope_coverage_frac": 0.9375, "low_slope_coverage": False,
        "area_km2": 0.04, "burn_coverage_frac": 0.7, "low_coverage": True,
        "flowed": True, "matched_creek": "", "mask": mask,
    }]


def test_sbs_csv_surfaces_coverage_diagnostics(tmp_path):
    """ranking.csv schema: F4 slope-coverage pair + A18 low_coverage, dNBR-writer placement."""
    dem_tif = tmp_path / "dem.tif"
    _dem(dem_tif)
    csv_path, _, _ = outputs.write_outputs(_basins(), {}, tmp_path, dem_tif, "SBS")
    df = pd.read_csv(csv_path, comment="#")
    assert list(df.columns) == [
        "basin_id", "rank", "score", "mean_burn", "mean_slope",
        "slope_coverage_frac", "low_slope_coverage",
        "area_km2", "burn_coverage_frac", "low_coverage",
        "drains_to_asset", "flowed", "matched_creek", "nearest_outlet_dist_m",
    ]
    row = df.iloc[0]
    assert row["slope_coverage_frac"] == pytest.approx(0.9375)
    assert bool(row["low_slope_coverage"]) is False
    assert bool(row["low_coverage"]) is True


def test_sbs_geojson_surfaces_coverage_diagnostics(tmp_path):
    """basins.geojson properties carry the same three fields (GIS-consumer parity)."""
    dem_tif = tmp_path / "dem.tif"
    _dem(dem_tif)
    _, gj_path, _ = outputs.write_outputs(_basins(), {}, tmp_path, dem_tif, "SBS")
    props = json.loads(gj_path.read_text())["features"][0]["properties"]
    assert props["slope_coverage_frac"] == pytest.approx(0.9375)
    assert props["low_slope_coverage"] is False
    assert props["low_coverage"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
