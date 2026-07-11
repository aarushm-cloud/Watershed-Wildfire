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


# ---- F1: scale-guard robustness (physical bound + undeclared-fill screen) ----------------------

def test_assert_raw_dnbr_passes_undeclared_nodata_sentinel(tmp_path):
    # A VALID raw dNBR raster whose fill (-9999) is NOT declared as nodata. rasterio read(masked=True)
    # only masks a DECLARED nodata, so the fill would dominate the 99th percentile and FALSE-REFUSE a
    # good fire. The guard must screen non-physical fill and judge the scale on the real pixels (F1).
    raw = np.linspace(-0.5, 1.2, 400, dtype="float32").reshape(20, 20)
    raw[:, :4] = -9999.0                                    # 20% undeclared fill
    p = _write_raster(tmp_path / "dnbr_undeclared_fill.tif", raw, nodata=None)
    stats = assert_raw_dnbr(p)                              # must NOT raise
    assert stats["p99_abs"] <= 2.0                         # scale judged on the physical pixels only


def test_assert_raw_dnbr_rejects_unreadable_file(tmp_path):
    # F6: a corrupt / renamed non-GeoTIFF upload (the single most likely user mistake) must come back
    # as a legible GateAbort, not a raw RasterioIOError/GDAL trace escaping to the UI.
    p = tmp_path / "not_a_geotiff.tif"
    p.write_bytes(b"This is not a GeoTIFF, it is a renamed text file.")
    with pytest.raises(GateAbort) as e:
        assert_raw_dnbr(p)
    assert "GeoTIFF" in str(e.value)


def test_assert_raw_dnbr_rejects_small_mis_scale(tmp_path):
    # A x3 mis-scale (~ -1.5..3.75) slips a |p99|>5 guard but exceeds the physical dNBR bound [-2,2]
    # (NBR in [-1,1] -> dNBR in [-2,2]); feeding it to the frozen bins saturates moderate pixels to
    # High -> a silent, wrong within-fire ranking. Must fail loud, not rescale.
    raw3 = (np.linspace(-0.5, 1.25, 400, dtype="float32") * 3.0).reshape(20, 20)
    p = _write_raster(tmp_path / "dnbr_x3.tif", raw3, nodata=None)
    with pytest.raises(GateAbort):
        assert_raw_dnbr(p)


# ---- F6: network failures translate to legible GateAbort (the one type app.py catches) ---------

def test_fetch_dem_translates_tile_fetch_failure_to_gateabort(tmp_path, monkeypatch):
    # A 404/timeout/DNS failure on a 3DEP COG currently surfaces as a raw RasterioIOError. It must
    # become a GateAbort naming the tile and chaining the cause (translated loud, never swallowed).
    from rasterio.errors import RasterioIOError
    real_open = rasterio.open

    def _fail_on_vsicurl(path, *a, **k):
        if str(path).startswith("/vsicurl/"):
            raise RasterioIOError("HTTP response code: 404")
        return real_open(path, *a, **k)

    monkeypatch.setattr(rasterio, "open", _fail_on_vsicurl)
    from acquire import canonical_grid, fetch_dem
    bbox = (-105.7916, 33.3255, -105.6361, 33.4135)
    grid = canonical_grid(*bbox)
    with pytest.raises(GateAbort) as e:
        fetch_dem(bbox, grid, tmp_path / "dem.tif")
    msg = str(e.value)
    assert "n34w106" in msg and "404" in msg                    # names the tile + underlying cause
    assert isinstance(e.value.__cause__, RasterioIOError)       # cause chained, never swallowed


def test_fetch_buildings_translates_overpass_failure_to_gateabort(tmp_path, monkeypatch):
    # Overpass rate-limit / connection drop surfaces as a requests/osmnx exception; must become a
    # legible GateAbort naming the failure, with the cause chained.
    import osmnx
    import requests

    def _net_down(*a, **k):
        raise requests.exceptions.ConnectionError("Overpass unreachable")

    monkeypatch.setattr(osmnx, "features_from_polygon", _net_down)
    from acquire import fetch_buildings
    with pytest.raises(GateAbort) as e:
        fetch_buildings((-105.79, 33.33, -105.64, 33.41), "EPSG:32613", tmp_path / "b.gpkg")
    msg = str(e.value)
    assert "Overpass" in msg and "ConnectionError" in msg
    assert e.value.__cause__ is not None                        # cause chained


def test_fetch_buildings_maps_empty_overpass_result_to_zero_buildings_abort(tmp_path, monkeypatch):
    # osmnx 2.x RAISES InsufficientResponseError on an empty Overpass result (it does not return an
    # empty gdf), so the len()==0 guard never fires on that path -- the raise must map to the same
    # legible 0-buildings abort, not the generic network-failure message.
    import osmnx
    from osmnx._errors import InsufficientResponseError

    def _empty(*a, **k):
        raise InsufficientResponseError("No matching features. Check query location/tags.")

    monkeypatch.setattr(osmnx, "features_from_polygon", _empty)
    from acquire import fetch_buildings
    with pytest.raises(GateAbort) as e:
        fetch_buildings((-105.79, 33.33, -105.64, 33.41), "EPSG:32613", tmp_path / "b.gpkg")
    assert "0 buildings" in str(e.value)


def test_assert_raw_dnbr_rejects_renamed_image(tmp_path):
    # F6/[13]: GDAL sniffs CONTENT not extension, so a colorized dNBR PNG/JPEG renamed .tif OPENS
    # (via the PNG driver) -- the OSError path never fires, and a dark uint8 image could even slip the
    # scale guard and be silently scored. The driver check must refuse a non-GeoTIFF raster outright.
    Image = pytest.importorskip("PIL.Image")
    img = np.zeros((20, 20), dtype="uint8")            # a dark single-band "export"
    p = tmp_path / "colorized_dnbr.tif"                # renamed image (uploader filters on extension)
    Image.fromarray(img).save(p, format="PNG")         # PNG bytes in a .tif file
    with pytest.raises(GateAbort) as e:
        assert_raw_dnbr(p)
    assert "GeoTIFF" in str(e.value)                   # legible: "upload the raw dNBR GeoTIFF, not an image"


def test_fetch_dem_translates_merge_failure_to_gateabort(tmp_path, monkeypatch):
    # F6/[10]: the connection-drop / corrupt-tile-mid-read case (the OSError around merge()) -- the
    # harder-to-reproduce field failure -- must translate to a legible GateAbort, cause chained.
    from rasterio.errors import RasterioIOError

    class _FakeDS:
        crs = "EPSG:4269"
        nodata = None
        def close(self):
            pass

    def _boom(*a, **k):
        raise RasterioIOError("Connection reset by peer mid-read")

    monkeypatch.setattr(rasterio, "open", lambda *a, **k: _FakeDS())   # tiles "open" with a valid CRS
    monkeypatch.setattr(acquire, "merge", _boom)                        # ...then merge drops mid-fetch
    from acquire import canonical_grid, fetch_dem
    bbox = (-105.7916, 33.3255, -105.6361, 33.4135)
    grid = canonical_grid(*bbox)
    with pytest.raises(GateAbort) as e:
        fetch_dem(bbox, grid, tmp_path / "dem.tif")
    assert "mid-fetch" in str(e.value)
    assert isinstance(e.value.__cause__, RasterioIOError)


def test_fetch_dem_translates_crs_mismatch_to_gateabort(tmp_path, monkeypatch):
    # round-2/[2]: merge() raises a BARE RasterioError (not a RasterioIOError) on an inter-tile CRS
    # mismatch -- both a 4269 and a 4326 tile pass the per-tile allowlist, then merge rejects the mix.
    # The merge wrap must catch RasterioError too, not let it escape as a raw traceback.
    from rasterio.errors import RasterioError

    class _FakeDS:
        crs = "EPSG:4269"
        nodata = None
        def close(self):
            pass

    def _mismatch(*a, **k):
        raise RasterioError("CRS mismatch with source: <tile>")

    monkeypatch.setattr(rasterio, "open", lambda *a, **k: _FakeDS())
    monkeypatch.setattr(acquire, "merge", _mismatch)
    from acquire import canonical_grid, fetch_dem
    bbox = (-105.7916, 33.3255, -105.6361, 33.4135)
    grid = canonical_grid(*bbox)
    with pytest.raises(GateAbort) as e:
        fetch_dem(bbox, grid, tmp_path / "dem.tif")
    assert isinstance(e.value.__cause__, RasterioError)


# ---- F7: front-door bounds -- refuse BEFORE any network work ------------------------------------

def _raw_dnbr(tmp_path):
    return _write_raster(tmp_path / "raw.tif",
                         np.linspace(-1.0, 1.2, 400, dtype="float32").reshape(20, 20))


def test_build_fire_config_refuses_unonboarded_utm_zone_before_any_fetch(tmp_path, monkeypatch):
    # An Oregon bbox resolves to UTM zone 10 (EPSG:32610), not in ALLOWED_UTM_ZONES {32611, 32613}
    # (A25). Today the pipeline aborts only AFTER the full DEM+buildings fetch, mislabeled as an
    # assets-CRS error. Must refuse at the front door with the onboarding pointer, zero network work.
    called = []
    monkeypatch.setattr(acquire, "fetch_dem", lambda *a, **k: called.append("dem"))
    monkeypatch.setattr(acquire, "fetch_buildings", lambda *a, **k: called.append("bld"))
    with pytest.raises(GateAbort) as e:
        build_fire_config((-123.8, 43.6, -123.6, 43.8), _raw_dnbr(tmp_path), out_dir=tmp_path / "out")
    msg = str(e.value)
    assert "32610" in msg and "ALLOWED_UTM_ZONES" in msg
    assert called == []                                          # zero fetches before the refusal


def test_build_fire_config_refuses_oversized_bbox_before_any_fetch(tmp_path, monkeypatch):
    # A CONUS-scale mis-draw passes lon/lat validation but would enumerate hundreds of 3DEP tiles
    # and hang the app in the spinner. The single-fire AOI cap must refuse before ANY work.
    called = []
    monkeypatch.setattr(acquire, "fetch_dem", lambda *a, **k: called.append("dem"))
    monkeypatch.setattr(acquire, "fetch_buildings", lambda *a, **k: called.append("bld"))
    with pytest.raises(GateAbort) as e:
        build_fire_config((-120.0, 32.0, -114.0, 38.0), _raw_dnbr(tmp_path), out_dir=tmp_path / "out")
    assert "deg^2" in str(e.value)                               # names the cap
    assert called == []


def test_build_fire_config_area_cap_boundary(tmp_path, monkeypatch):
    # [11]: pin the strict-`>` boundary so a mutation (cap value, `>` vs `>=`, a units slip) can't
    # survive the suite. Exactly 1.0 deg^2 in an onboarded zone is ACCEPTED and reaches the fetchers;
    # 1.01 deg^2 is REFUSED before any fetch.
    staged = {}
    monkeypatch.setattr(acquire, "fetch_dem",
                        lambda bbox, grid, out_path, **k: (Path(out_path).parent.mkdir(parents=True, exist_ok=True),
                                                           _write_raster(out_path, np.ones((grid.height, grid.width), "float32")),
                                                           staged.setdefault("dem", out_path))[-1])
    monkeypatch.setattr(acquire, "fetch_buildings",
                        lambda bbox, dst, out_path, **k: (Path(out_path).parent.mkdir(parents=True, exist_ok=True),
                                                          Path(out_path).write_text("stub"),
                                                          (out_path, 633))[-1])
    # exactly 1.0 deg^2, centroid lon -105.5 -> zone 13 (onboarded): accepted
    fire = build_fire_config((-106.0, 33.0, -105.0, 34.0), _raw_dnbr(tmp_path), out_dir=tmp_path / "ok")
    assert "dem" in staged and fire["sbs"] is None
    # 1.01 deg^2: refused before any fetch
    with pytest.raises(GateAbort) as e:
        build_fire_config((-106.01, 33.0, -105.0, 34.0), _raw_dnbr(tmp_path), out_dir=tmp_path / "over")
    assert "deg^2" in str(e.value)
