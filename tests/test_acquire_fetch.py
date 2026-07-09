"""CF-7/CF-8/CF-9 (A35) -- the hermetic parts of the acquisition layer:

  * tiles_for_bbox   -- 3DEP 1-degree COG tile enumeration (generalizes South Fork's ONE
                        hardcoded tile n34w106 to any bbox). Pure; no network.
  * assert_raw_dnbr  -- CF-9 Tier-1-adjacent guard: the uploaded dNBR must be RAW scale
                        (~-2..2), because the frozen bins (src.config.DNBR_BIN_EDGES /
                        DNBR_CLAMP) are defined on raw dNBR. An apparent x1000 upload is
                        REFUSED (never silently rescaled). Hermetic (tiny in-memory rasters).
  * build_fire_config-- CF-9 orchestrator: assembles the A30 `fire` dict (sbs=None, dnbr set).
                        Tested with the two network fetchers monkeypatched -> hermetic shape test.

The live network fetch (CF-7 fetch_dem / CF-8 fetch_buildings against USGS 3DEP + Overpass)
is verified separately against South Fork's committed artifacts (see the CF-C verification run),
not in this suite -- its inputs are gitignored and it needs the network.

Run:  pytest tests/test_acquire_fetch.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import acquire  # noqa: E402
from acquire import tiles_for_bbox, assert_raw_dnbr, build_fire_config, _buildings_to_points  # noqa: E402
from src.grids import GateAbort  # noqa: E402  (the A8 fail-loud contract acquire raises)


def _write_raster(path, array, *, crs="EPSG:32613", nodata=-9999.0,
                  transform=from_origin(426400.8, 3697312.6, 10.0, 10.0)):
    array = np.asarray(array, dtype="float32")
    profile = dict(driver="GTiff", height=array.shape[0], width=array.shape[1], count=1,
                   dtype="float32", crs=crs, transform=transform, nodata=nodata)
    with rasterio.open(path, "w", **profile) as d:
        d.write(array, 1)
    return path


# ---- CF-7: 3DEP tile enumeration --------------------------------------------------------------

def test_tiles_for_bbox_southfork_single_tile():
    # South Fork's whole AOI falls inside the single 1-degree tile n34w106 (per the proven script).
    assert tiles_for_bbox(-105.7916, 33.3255, -105.6361, 33.4135) == ["n34w106"]


def test_tiles_for_bbox_spans_multiple_tiles():
    # A bbox straddling two integer lon AND two integer lat lines -> the 2x2 tile block.
    got = tiles_for_bbox(-106.5, 33.5, -105.5, 34.5)
    assert sorted(got) == ["n34w106", "n34w107", "n35w106", "n35w107"]


# ---- CF-8: building footprints -> POINT assets (the pipeline contract) -------------------------

def test_buildings_to_points_converts_footprints_to_points():
    # pipeline.py reads assets.geometry.x/.y -> the asset layer MUST be Point geoms (validated
    # Montecito assets are 12,221 Points). OSM building=* returns Polygons (+ the odd node Point);
    # fetch_buildings must reduce every footprint to a representative point in the canonical CRS.
    import geopandas as gpd
    from shapely.geometry import Polygon, Point
    gdf = gpd.GeoDataFrame(
        {"building": ["yes", "house"]},
        geometry=[Polygon([(-105.70, 33.40), (-105.70, 33.41), (-105.69, 33.41), (-105.69, 33.40)]),
                  Point(-105.68, 33.40)],
        crs="EPSG:4326")
    pts = _buildings_to_points(gdf, "EPSG:32613")
    assert set(pts.geom_type) == {"Point"}                 # Point-only (pipeline contract)
    assert pts.crs.to_epsg() == 32613                      # reprojected to the canonical CRS
    assert len(pts) == 2                                   # one point per input footprint


# ---- CF-9: dNBR raw-scale guard (Tier-1-adjacent: protects the frozen raw-dNBR bins) -----------

def test_assert_raw_dnbr_accepts_raw_scale(tmp_path):
    # Raw dNBR lives ~ -1..1.3 (South Fork's real range was -1.285..1.238). Must pass.
    rng = np.linspace(-1.2, 1.25, 400, dtype="float32").reshape(20, 20)
    p = _write_raster(tmp_path / "dnbr_raw.tif", rng)
    stats = assert_raw_dnbr(p)                    # returns stats, does not raise
    assert stats["p99_abs"] <= 2.0


def test_assert_raw_dnbr_rejects_x1000_scale(tmp_path):
    # The classic ingestion gotcha (DATA_SOURCES S2): dNBR distributed x1000 (~ -1285..1238).
    # Feeding it to the raw-scale bins would misclassify every pixel -> must FAIL LOUD, not rescale.
    rng = np.linspace(-1285.0, 1238.0, 400, dtype="float32").reshape(20, 20)
    p = _write_raster(tmp_path / "dnbr_x1000.tif", rng)
    with pytest.raises(GateAbort) as exc:
        assert_raw_dnbr(p)
    msg = str(exc.value).lower()
    assert "1000" in msg or "scale" in msg or "raw" in msg   # a clear, actionable message


def test_assert_raw_dnbr_rejects_all_nodata(tmp_path):
    # No valid dNBR at all -> fail loud (A8), don't proceed on an empty burn input.
    p = _write_raster(tmp_path / "dnbr_empty.tif", np.full((20, 20), -9999.0, dtype="float32"))
    with pytest.raises(GateAbort):
        assert_raw_dnbr(p)


# ---- CF-9: fire-config assembly (A30 dict), fetchers monkeypatched (hermetic) ------------------

def test_build_fire_config_assembles_a30_dict(tmp_path, monkeypatch):
    """build_fire_config wires bbox + uploaded dNBR -> the A30 fire dict, staging DEM+buildings via
    the (here monkeypatched) fetchers. Asserts the exact seam run_pipeline consumes: sbs=None,
    dnbr set, expected_crs = the derived UTM zone, creeks=None (a new fire has no truth layer)."""
    # a real (tiny, raw-scale) uploaded dNBR file -> exercises the real CF-9 scale guard
    dnbr = _write_raster(tmp_path / "upload_dnbr.tif",
                         np.linspace(-1.0, 1.2, 400, dtype="float32").reshape(20, 20))

    staged = {}
    def _fake_fetch_dem(bbox, grid, out_path, **kw):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        _write_raster(out_path, np.ones((grid.height, grid.width), dtype="float32"))
        staged["dem"] = out_path
        return out_path
    def _fake_fetch_buildings(bbox, dst_crs, out_path, **kw):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("stub")   # count returned, file staged
        staged["assets"] = out_path
        return out_path, 633
    monkeypatch.setattr(acquire, "fetch_dem", _fake_fetch_dem)
    monkeypatch.setattr(acquire, "fetch_buildings", _fake_fetch_buildings)

    fire = build_fire_config((-105.7916, 33.3255, -105.6361, 33.4135), dnbr,
                             out_dir=tmp_path / "out", name="testfire")

    assert fire["name"] == "testfire"
    assert fire["sbs"] is None                       # dNBR-only fire (A34/A29)
    assert Path(fire["dnbr"]) == dnbr                 # the uploaded raster, unmodified
    assert fire["creeks"] is None                     # new fire: no ground-truth creeks
    assert fire["expected_crs"] == "EPSG:32613"       # zone derived from the bbox centroid
    assert Path(fire["dem"]).exists() and Path(fire["assets"]).exists()   # both staged
    assert "out" in str(fire["out_dir"])
