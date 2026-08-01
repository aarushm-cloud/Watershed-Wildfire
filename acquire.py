"""acquire.py -- the network boundary (A35): bbox + uploaded dNBR -> staged DEM/buildings +
the per-fire dict run_pipeline consumes. src/ stays a pure, no-network seam.

Every acquisition precondition raises, never degrades (A8).
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.errors import RasterioError, RasterioIOError   # RasterioIOError subclasses OSError (vsicurl
#                                                               IO); bare RasterioError = merge CRS mismatch
from rasterio.merge import merge
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject
from shapely.geometry import box

# Repo root on sys.path so `from src.config import ...` resolves whether acquire.py is run as a
# script or imported by a test (same cwd-independent pattern as run.py). acquire.py lives at <root>/.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from src.config import DNBR_CLAMP  # frozen RAW-dNBR clamp (0.100, 1.300) -- the guard's reference scale
from src.config import ALLOWED_UTM_ZONES  # A25 ingest allowlist -- the F7 front-door check reads it
from src.grids import GateAbort    # the project's A8 fail-loud contract; app.py catches it uniformly

CELL_M = 10.0   # canonical analysis resolution (m); matches src.config.CELL_M

# 3DEP 1/3 arc-second COG on AWS (anonymous https); one 1-degree tile per name.
_3DEP_COG = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/"
             "{tile}/USGS_13_{tile}.tif")
DEM_NODATA = -9999.0
DNBR_RAW_MAX_ABS = 2.0    # physical dNBR ceiling; a 99th-pct above it = mis-scaled upload -> refuse
DNBR_FILL_ABS = 5000.0    # obvious NoData/fill sentinel, screened BEFORE the scale check
MAX_BBOX_DEG2 = 1.0       # single-fire AOI cap (plumbing bound, owner-raisable)


def _norm_epsg(crs) -> str:
    """Normalize an EPSG spec (int 32613, '32613', or 'EPSG:32613') to 'EPSG:32613'."""
    s = str(crs).strip().upper()
    if s.startswith("EPSG:"):
        return s
    if s.isdigit():
        return f"EPSG:{s}"
    raise ValueError(f"acquire: unrecognized CRS {crs!r}; expected an EPSG code (e.g. 'EPSG:32613').")


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing (lon, lat) degrees; north -> 326xx, south -> 327xx."""
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@dataclass(frozen=True)
class GridSpec:
    """The canonical 10 m analysis grid -- the reproject TARGET for both DEM and dNBR."""
    crs: str
    transform: Affine
    width: int
    height: int
    bounds: tuple


def canonical_grid(west: float, south: float, east: float, north: float, *,
                   src_crs: str = "EPSG:4326", dst_crs: str | None = None,
                   cell_m: float = CELL_M) -> GridSpec:
    """Build the 10 m canonical grid enclosing a bbox, in a metric UTM CRS (auto-derived from
    the centroid when dst_crs is None). Corner-point reprojection + round() shape -- the frozen
    rule that reproduces the committed South Fork grid (see tests/acquire/test_acquire_grid.py)."""
    src = _norm_epsg(src_crs)
    if dst_crs is None:
        clon, clat = (west + east) / 2.0, (south + north) / 2.0
        dst = _norm_epsg(utm_epsg(clon, clat))
    else:
        dst = _norm_epsg(dst_crs)

    if src == dst:
        xs, ys = (west, east), (south, north)
    else:
        tf = Transformer.from_crs(src, dst, always_xy=True)
        corners = [tf.transform(x, y) for x, y in ((west, south), (east, south),
                                                   (east, north), (west, north))]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]

    left, right = min(xs), max(xs)
    bottom, top = min(ys), max(ys)
    width = round((right - left) / cell_m)
    height = round((top - bottom) / cell_m)
    if width <= 0 or height <= 0:
        raise ValueError(f"acquire.canonical_grid: degenerate bbox -> {width}x{height} cells "
                         f"(A8 fail-loud); check bbox ordering (west<east, south<north) and CRS.")

    transform = Affine(cell_m, 0.0, left, 0.0, -cell_m, top)
    bounds = (left, top - height * cell_m, left + width * cell_m, top)
    return GridSpec(crs=dst, transform=transform, width=width, height=height, bounds=bounds)


def tiles_for_bbox(west: float, south: float, east: float, north: float) -> list[str]:
    """3DEP 1-degree COG tile names covering a lon/lat bbox, e.g. ['n34w106']."""
    lat_lo = int(math.floor(min(south, north))) + 1
    lat_hi = int(math.ceil(max(south, north)))
    lonmag_lo = int(math.floor(-max(west, east))) + 1   # eastmost tile; floor()+1 (not ceil) so an exact
    #                                                     integer east meridian doesn't pull a spurious tile
    lonmag_hi = int(math.floor(1.0 - min(west, east)))
    tiles = [f"n{lat:02d}w{lonmag:03d}"
             for lat in range(lat_lo, lat_hi + 1)
             for lonmag in range(lonmag_lo, lonmag_hi + 1)]
    if not tiles:
        raise ValueError(f"acquire.tiles_for_bbox: no 3DEP tile covers bbox {(west, south, east, north)} "
                         f"(A8); expected US extent (north lat, west lon).")
    return tiles


def fetch_dem(bbox, grid: GridSpec, out_path, *, dem_nodata: float = DEM_NODATA):
    """Fetch 3DEP tiles over bbox, mosaic, reproject bilinear onto the canonical grid, stage a
    GeoTIFF. Elevation in metres. Fails loud on native-CRS drift or all-NoData (A8)."""
    west, south, east, north = bbox
    tiles = tiles_for_bbox(west, south, east, north)
    urls = ["/vsicurl/" + _3DEP_COG.format(tile=t) for t in tiles]
    srcs = []
    try:
        for u, tile in zip(urls, tiles):
            try:
                ds = rasterio.open(u)
            except RasterioIOError as e:   # 404/DNS/timeout/truncated COG (F6); local OSErrors fall
                raise GateAbort(           # through to the F5 backstop rather than mislabel as a bucket outage
                    f"FAIL: 3DEP tile {tile} fetch failed ({e}) -- most commonly the endpoint is "
                    "unreachable/rate-limited or the tile is missing from the bucket (see the error "
                    "above for the exact cause). Record as a finding and retry; acquire does NOT "
                    "silently proceed on a partial mosaic (A8; DATA_SOURCES S1).") from e
            srcs.append(ds)   # [C3]: owned by `finally` immediately, before the CRS check below can raise
            native = str(ds.crs).upper()
            if native not in ("EPSG:4269", "EPSG:4326"):
                raise GateAbort(f"FAIL: 3DEP tile native CRS {native} is not NAD83/WGS84 geographic "
                                 f"(expected EPSG:4269) -- vintage/product drift; record as a finding, "
                                 f"do NOT silently warp (A8, A24 S3 precedent).")
        try:
            mosaic, mosaic_transform = merge(srcs, bounds=(west, south, east, north))  # windowed read
        except (RasterioIOError, RasterioError) as e:   # F6: RasterioIOError = connection drop / corrupt
            # tile mid-read; a BARE RasterioError = merge's own inter-tile CRS-mismatch raise. Translate
            # both so neither escapes a direct caller as a raw traceback.
            raise GateAbort(
                f"FAIL: 3DEP mosaic read failed mid-fetch ({e}) -- a connection dropped, a tile is "
                "corrupt, or the tiles' CRSs differ; record as a finding and retry (A8; DATA_SOURCES "
                "S1).") from e
        src_crs, src_nodata = srcs[0].crs, srcs[0].nodata
        dst = np.full((grid.height, grid.width), dem_nodata, dtype="float32")
        reproject(source=mosaic[0], destination=dst,
                  src_transform=mosaic_transform, src_crs=src_crs, src_nodata=src_nodata,
                  dst_transform=grid.transform, dst_crs=grid.crs, dst_nodata=dem_nodata,
                  resampling=Resampling.bilinear)
    finally:
        for ds in srcs:
            ds.close()

    if not (dst != dem_nodata).any():
        raise GateAbort("FAIL: DEM all-NoData on the canonical grid -- AOI/tile mismatch (A8).")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(driver="GTiff", height=grid.height, width=grid.width, count=1, dtype="float32",
                   crs=grid.crs, transform=grid.transform, nodata=dem_nodata, compress="deflate")
    with rasterio.open(out_path, "w", **profile) as d:
        d.write(dst, 1)
        d.update_tags(source="USGS 3DEP 1/3 arc-second COG (AWS, anonymous)",
                      tiles=",".join(tiles),
                      canonical_crs=grid.crs, note="acquire.py CF-7; reproject TARGET for dNBR (A35)")
    return out_path


def _buildings_to_points(gdf, dst_crs):
    """OSM footprints -> one representative POINT each (the pipeline's asset layer must be
    Points). Centroids computed in the projected metric CRS, never lon/lat."""
    gdf = gdf.set_crs("EPSG:4326") if gdf.crs is None else gdf
    # GPKG can't store OSM list-valued tag columns; keep geometry only (assets are presence, not attrs).
    proj = gdf[[c for c in gdf.columns if c == "geometry"]].copy().to_crs(_norm_epsg(dst_crs))
    proj["geometry"] = proj.geometry.centroid   # Polygon/MultiPolygon/Point -> POINT (metric CRS)
    return proj


def fetch_buildings(bbox, dst_crs, out_path, *, buf_deg: float = 0.012):
    """Fetch OSM buildings over bbox (buffered ~1 km) via Overpass, stage a GeoPackage. Fails
    loud on 0 buildings over a populated AOI (A8). Returns (out_path, n_buildings)."""
    import osmnx as ox   # lazy: heavy import, only paid when actually fetching
    # osmnx 2.x RAISES on an empty Overpass result (it does not return an empty gdf), so the
    # len()==0 guard below never fires on that path; the private-module import is pinned-safe
    # (osmnx==2.1.0, A10/A13 lockfile) and verified in-env 2026-07-09.
    from osmnx._errors import InsufficientResponseError

    west, south, east, north = bbox
    poly = box(west - buf_deg, south - buf_deg, east + buf_deg, north + buf_deg)  # lon/lat (EPSG:4326)
    try:
        gdf = ox.features_from_polygon(poly, tags={"building": True})
    except InsufficientResponseError as e:
        raise GateAbort("FAIL: Overpass returned 0 buildings over the AOI (A8) -- unexpected for a "
                         "populated area; treat as a source/endpoint problem, not 'no assets'.") from e
    except Exception as e:   # F6: osmnx's exception surface is version-dependent (requests errors + its
        # own ValueError subclasses), so TRANSLATE anything the network call raises into the one loud
        # type app.py catches. Not a swallow: the type is NAMED + cause chained. The cause is phrased
        # "most commonly" network so a non-network failure (e.g. MemoryError on a huge urban AOI, a
        # post-rebuild API change) surfaces its real type instead of a wrong "just retry" prescription.
        raise GateAbort(
            f"FAIL: OSM/Overpass buildings fetch failed ({type(e).__name__}: {e}) -- most commonly "
            "Overpass rate-limiting or downtime (DATA_SOURCES S4 flags it as flaky); see the named "
            "error above for the actual cause. acquire does NOT silently proceed without an asset "
            "layer (A8).") from e
    if gdf is None or len(gdf) == 0:
        raise GateAbort("FAIL: Overpass returned 0 buildings over the AOI (A8) -- unexpected for a "
                         "populated area; treat as a source/endpoint problem, not 'no assets'.")
    pts = _buildings_to_points(gdf, dst_crs)   # footprints -> representative POINTS (pipeline contract)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pts.to_file(out_path, driver="GPKG")
    return out_path, int(len(pts))


def assert_raw_dnbr(dnbr_path) -> dict:
    """Refuse a non-raw-scale dNBR upload: the frozen bins are defined on RAW dNBR (physically
    bounded [-2, 2]), so a 99th-pct |dNBR| above the ceiling = apparent x1000/RdNBR -> fail loud,
    never silently rescale (A8). Fill sentinels screened first. Returns stats for the manifest."""
    try:
        with rasterio.open(dnbr_path) as ds:
            driver = ds.driver                  # GDAL sniffs CONTENT, not the .tif extension
            band = ds.read(1, masked=True)      # masks only a DECLARED nodata
            total = ds.width * ds.height        # captured here -- no second open (was a duplicate)
    except RasterioIOError as e:   # F6: unreadable / corrupt / truncated upload -> legible, not a GDAL trace
        raise GateAbort(
            f"FAIL: uploaded dNBR {Path(dnbr_path).name} could not be read as a raster ({e}). If it "
            "opens in GIS locally this may be a local disk/staging problem; otherwise upload the raw "
            "dNBR GeoTIFF itself, not a truncated or non-raster file (A8).") from e
    if driver != "GTiff":   # [13]: a colorized dNBR PNG/JPEG renamed .tif OPENS via its own driver, so
        # the read above does not fail -- and a dark one (values ~0) would slip the scale guard below and
        # be SILENTLY scored. Refuse a non-GeoTIFF raster outright.
        raise GateAbort(
            f"FAIL: uploaded dNBR {Path(dnbr_path).name} is a {driver} raster, not a GeoTIFF -- this "
            "looks like a colorized/exported image, not raw dNBR. Upload the raw dNBR GeoTIFF (A8).")
    finite = np.asarray(band.compressed(), dtype="float64")
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        raise GateAbort(f"FAIL: uploaded dNBR {Path(dnbr_path).name} has no valid pixels (A8).")
    # Screen obvious fill sentinels (undeclared -9999 / 3.4e38 / 65535 ...) BEFORE judging the scale --
    # not dNBR at any conventional scale (even x1000 tops out ~1300). Excluded from the scale stats but
    # COUNTED (they lower valid_frac); never silently treated as burn data.
    sentinel = np.abs(finite) > DNBR_FILL_ABS
    n_sentinel = int(sentinel.sum())
    physical = finite[~sentinel]
    if physical.size == 0:
        raise GateAbort(f"FAIL: uploaded dNBR {Path(dnbr_path).name}: every valid pixel is a fill "
                        f"sentinel (|dNBR| > {DNBR_FILL_ABS:.0f}); no real dNBR data (A8).")
    p99_abs = float(np.percentile(np.abs(physical), 99))
    if p99_abs > DNBR_RAW_MAX_ABS:
        lo, hi = DNBR_CLAMP
        detail = (f"looks x1000-scaled (99th-pct |dNBR| = {p99_abs:.0f}); divide by 1000 and re-upload"
                  if p99_abs > 50.0 else
                  f"exceeds the physical dNBR range [-2, 2] (99th-pct |dNBR| = {p99_abs:.2f}); check the "
                  f"scale/units -- is this raw dNBR, not an RdNBR or otherwise-scaled product?")
        raise GateAbort(
            f"FAIL: uploaded dNBR {detail}. The pipeline's frozen bins are RAW dNBR (clamp {lo}..{hi}); "
            f"acquire will NOT silently rescale (A8; DATA_SOURCES S2 scale gotcha).")
    return {"p99_abs": round(p99_abs, 4), "min": float(physical.min()), "max": float(physical.max()),
            "valid_frac": round(float(physical.size / total), 4), "n_fill_sentinel": n_sentinel}


def build_fire_config(bbox, dnbr_path, out_dir, name: str = "fire", *, buf_deg: float = 0.012) -> dict:
    """(bbox lon/lat + uploaded dNBR) -> staged files + the fire dict run_pipeline consumes
    (sbs=None -> the dNBR both-arms path). Scale guard first, then fetches; writes a manifest."""
    west, south, east, north = bbox
    out_dir = Path(out_dir)
    # F7 front-door AOI cap -- cheapest check first, before even the dNBR read. A mis-drawn
    # state/CONUS-scale box passes lon/lat validation but would enumerate hundreds of 3DEP tiles.
    area_deg2 = (east - west) * (north - south)
    if area_deg2 > MAX_BBOX_DEG2:
        raise GateAbort(
            f"FAIL: bounding box covers {area_deg2:.2f} deg^2 (> the {MAX_BBOX_DEG2:.0f} deg^2 "
            f"single-fire cap): {len(tiles_for_bbox(west, south, east, north))} 3DEP tiles would be "
            "fetched. Draw a box around ONE fire's burn area -- or, if this genuinely is a single "
            "megafire scar, raising acquire.MAX_BBOX_DEG2 is an owner decision (plumbing bound, not "
            "science) (A8/F7).")
    dnbr_stats = assert_raw_dnbr(dnbr_path)                     # CF-9 guard before any fetch
    grid = canonical_grid(west, south, east, north)            # CF-6: lon/lat -> UTM 10 m grid
    # F7 front-door zone check -- the pipeline ingests only ALLOWED_UTM_ZONES: now the whole CONUS
    # coverage (UTM 10N-19N, A37). Without this, an out-of-coverage bbox pays the full DEM+buildings
    # fetch and THEN aborts deep in ingest with an assets-CRS message far from the cause. Refuse here.
    zone = int(grid.crs.split(":")[1])
    if zone not in ALLOWED_UTM_ZONES:
        raise GateAbort(
            f"FAIL: this bbox resolves to UTM zone {grid.crs}, outside the tool's CONUS coverage "
            f"(UTM 10N-19N = EPSG 32610-32619, A37). It screens contiguous-US fires on 3DEP terrain; "
            "Alaska/Hawaii/non-US is out of coverage -- extending it is an owner edit to "
            "src/config.ALLOWED_UTM_ZONES (A8/F7).")
    stage = out_dir / "inputs"
    dem_path = fetch_dem(bbox, grid, stage / "dem.tif")        # CF-7 (module-level -> monkeypatchable)
    assets_path, n_buildings = fetch_buildings(bbox, grid.crs, stage / "buildings.gpkg", buf_deg=buf_deg)  # CF-8

    fire = {
        "name": name,
        "dem": dem_path,
        "sbs": None,                     # dNBR-only fire (A34/A29): no BAER SBS for an un-assessed fire
        "dnbr": Path(dnbr_path),         # the uploaded raster, carried unmodified (raw scale, guarded)
        "assets": assets_path,
        "creeks": None,                  # a new un-assessed fire has no ground-truth creek layer
        "out_dir": out_dir,
        "expected_crs": grid.crs,        # per-fire UTM zone derived from the bbox (A25)
        "validation_case": None,         # not a validation reconstruction (A30)
    }
    _write_manifest(out_dir, name, bbox, grid, dnbr_stats, n_buildings)
    return fire


def _write_manifest(out_dir: Path, name, bbox, grid: GridSpec, dnbr_stats, n_buildings):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "acquired_by": "acquire.build_fire_config (A35, CF-C)",
        "fire": name,
        "bbox_lonlat": list(bbox),
        "canonical_grid": {"crs": grid.crs, "cell_m": CELL_M,
                           "shape_rows_cols": [grid.height, grid.width],
                           "transform": list(grid.transform)[:6], "bounds": list(grid.bounds)},
        "dem": {"source": "USGS 3DEP 1/3\" COG (AWS)", "tiles": tiles_for_bbox(*bbox)},
        "assets": {"source": "OSM buildings via Overpass (osmnx)", "n_buildings": n_buildings},
        "dnbr_upload": dnbr_stats,
        "burn_source": "dNBR (both arms, A34)",
        "screening_note": "within-fire relative ranking, never a prediction; dNBR triage-validated, "
                          "not exact-rank-validated, n=1 (A34).",
    }
    (out_dir / "acquisition_manifest.json").write_text(json.dumps(manifest, indent=2))

