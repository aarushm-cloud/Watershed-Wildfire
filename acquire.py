"""acquire.py -- coordinate-driven acquisition layer (A35), a repo-root peer of run.py.

Turns a bounding box + an uploaded dNBR raster into the staged files + the A30 `fire`
dict that `src.pipeline.run_pipeline` consumes. This is the NETWORK boundary: `src/` is a
pure, no-network seam (`ingest.py` bans import-time I/O), so all fetching lives HERE and
stages files to disk -- network -> staged files -> pure pipeline -- exactly the pattern
the proven `validation/p3_acquire_dem.py` / `p3_acquire_assets.py` scripts already follow.
This module generalizes those two hardcoded-to-South-Fork scripts to an arbitrary bbox.

Guardrail tier (CLAUDE.md): CF-6/7/8 are Tier-2 plumbing (porting proven fetch code).
CF-9's dNBR raw-scale guard is Tier-1-adjacent -- it protects the frozen dNBR bins
(`src.config.DNBR_BIN_EDGES` / `DNBR_CLAMP`, RAW scale), so it READS those, never re-derives.

Fail-loud spine (A8): every acquisition precondition (all-NoData DEM, native-CRS drift,
zero buildings over a populated AOI, an apparent x1000 dNBR upload) raises, never degrades.
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
from src.grids import GateAbort    # the project's A8 fail-loud contract; app.py catches it uniformly

CELL_M = 10.0   # canonical analysis resolution (m); matches src.config.CELL_M (the frozen grid)

# 3DEP 1/3 arc-second COG on AWS (anonymous https; DATA_SOURCES S1 path 1). One 1-degree tile per name.
_3DEP_COG = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/"
             "{tile}/USGS_13_{tile}.tif")
DEM_NODATA = -9999.0
# CF-9 raw-scale guard thresholds (plumbing that PROTECTS the frozen raw bins, NOT frozen-science
# values). raw dNBR is physically bounded: NBR = (NIR-SWIR)/(NIR+SWIR) is a normalized ratio in
# [-1, 1], so dNBR = NBR_pre - NBR_post is in [-2, 2]; the vault's typical raw range is ~[-0.5, 1.3]
# (DATA_SOURCES S2 / science_reference S6). A 99th-pct |dNBR| above the physical ceiling is an apparent
# scale error (x1000, x-N, or an RdNBR upload) -> refuse (never silently rescale).
DNBR_RAW_MAX_ABS = 2.0    # physical dNBR ceiling |dNBR| <= 2 (F1); above this the upload is mis-scaled
# |v| above this is an obvious NoData/FILL sentinel (-9999, 3.4e38, 65535, ...), not dNBR at ANY
# conventional scale (even x1000 dNBR tops out ~1300). Screened BEFORE the scale check so an UNDECLARED
# nodata (read(masked=True) honors only a DECLARED nodata) can't false-refuse a valid raw raster.
DNBR_FILL_ABS = 5000.0


def _norm_epsg(crs) -> str:
    """Normalize an EPSG spec (int 32613, '32613', or 'EPSG:32613') to 'EPSG:32613'."""
    s = str(crs).strip().upper()
    if s.startswith("EPSG:"):
        return s
    if s.isdigit():
        return f"EPSG:{s}"
    raise ValueError(f"acquire: unrecognized CRS {crs!r}; expected an EPSG code (e.g. 'EPSG:32613').")


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing (lon, lat).

    zone = floor((lon + 180) / 6) + 1  (1..60); northern hemisphere -> 326xx, southern -> 327xx.
    The bbox centroid picks the zone for a new fire (DATA_SOURCES S1: reproject to a per-fire
    metric UTM before hydrology). Values are degrees (lon E, lat N).
    """
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@dataclass(frozen=True)
class GridSpec:
    """The canonical 10 m analysis grid -- the reproject TARGET for both DEM and dNBR.

    crs       -- 'EPSG:326xx' (or the caller-forced projected CRS)
    transform -- rasterio Affine, north-up, upper-left anchored (a=cell, e=-cell)
    width     -- columns (int)
    height    -- rows (int)
    bounds    -- (left, bottom, right, top) in the grid CRS, UL-anchored (right/bottom = UL +/- shape*cell)
    """
    crs: str
    transform: Affine
    width: int
    height: int
    bounds: tuple


def canonical_grid(west: float, south: float, east: float, north: float, *,
                   src_crs: str = "EPSG:4326", dst_crs: str | None = None,
                   cell_m: float = CELL_M) -> GridSpec:
    """Build the 10 m canonical grid enclosing a bbox, in a metric UTM CRS.

    The bbox is (west, south, east, north) in `src_crs` (lon/lat by default). If `dst_crs` is
    None it is auto-derived from the bbox centroid via `utm_epsg` (the coordinate-frontend path).

    Reprojection uses the 4 CORNER POINTS (exact inverses -> no edge bowing), taking min/max --
    NOT rasterio.warp.transform_bounds, whose edge densification returns the outward-bowing
    enclosing box and inflates a UTM round-trip by ~1-2% (see tests/test_acquire_grid.py). Shape
    uses round() -- the unique rule reproducing South Fork's frozen 1439 x 966 from its bbox.
    The grid is upper-left anchored so an arbitrary UTM corner (e.g. 426400.8) is preserved exactly.
    """
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


# ---- CF-7: DEM auto-fetch (generalizes validation/p3_acquire_dem.py) ---------------------------

def tiles_for_bbox(west: float, south: float, east: float, north: float) -> list[str]:
    """3DEP 1-degree COG tile names covering a lon/lat bbox (generalizes the single hardcoded
    `n34w106`). A tile `n{NN}w{WWW}` covers lat [NN-1, NN] and lon [-WWW, -WWW+1] -- the US 3DEP
    convention (northern lat, western lon). Returns e.g. ['n34w106'] for South Fork.
    """
    lat_lo = int(math.floor(min(south, north))) + 1
    lat_hi = int(math.ceil(max(south, north)))
    lonmag_lo = int(math.ceil(-max(west, east)))     # least-negative lon -> smallest west magnitude
    lonmag_hi = int(math.floor(1.0 - min(west, east)))
    tiles = [f"n{lat:02d}w{lonmag:03d}"
             for lat in range(lat_lo, lat_hi + 1)
             for lonmag in range(lonmag_lo, lonmag_hi + 1)]
    if not tiles:
        raise ValueError(f"acquire.tiles_for_bbox: no 3DEP tile covers bbox {(west, south, east, north)} "
                         f"(A8); expected US extent (north lat, west lon).")
    return tiles


def fetch_dem(bbox, grid: GridSpec, out_path, *, dem_nodata: float = DEM_NODATA):
    """Fetch USGS 3DEP 1/3" tiles over `bbox`, mosaic (windowed to the AOI), reproject bilinear onto
    the canonical `grid`, and stage a single GeoTIFF. Fails loud on native-CRS drift or all-NoData
    (A8), exactly as the proven single-tile `p3_acquire_dem.py` does -- generalized to N tiles.

    UNITS: elevation in metres (3DEP NAVD88); transform in metres (projected grid CRS). Resampling
    is bilinear (continuous surface; nearest would stair-step terrain).
    """
    west, south, east, north = bbox
    tiles = tiles_for_bbox(west, south, east, north)
    urls = ["/vsicurl/" + _3DEP_COG.format(tile=t) for t in tiles]
    srcs = []
    try:
        for u in urls:
            ds = rasterio.open(u)
            native = str(ds.crs).upper()
            if native not in ("EPSG:4269", "EPSG:4326"):
                raise GateAbort(f"FAIL: 3DEP tile native CRS {native} is not NAD83/WGS84 geographic "
                                 f"(expected EPSG:4269) -- vintage/product drift; record as a finding, "
                                 f"do NOT silently warp (A8, A24 S3 precedent).")
            srcs.append(ds)
        mosaic, mosaic_transform = merge(srcs, bounds=(west, south, east, north))  # windowed read
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


# ---- CF-8: buildings auto-fetch (generalizes validation/p3_acquire_assets.py) ------------------

def _buildings_to_points(gdf, dst_crs):
    """Reduce OSM building footprints to one representative POINT each, in `dst_crs`.

    The pipeline's asset layer MUST be Point geometries -- `stage_2c` reads `assets.geometry.x/.y`
    (the validated Montecito assets are 12,221 Points). OSM `building=*` returns Polygons (plus the
    odd node Point), so every footprint is reduced to its centroid; a building's *presence*, not its
    outline, is what the drains-to-asset screening test (DRAINS_TO_ASSET_M) needs. Centroids are
    computed in the projected (metric) CRS, never in lon/lat.
    """
    gdf = gdf.set_crs("EPSG:4326") if gdf.crs is None else gdf
    # GPKG can't store OSM list-valued tag columns; keep geometry only (assets are presence, not attrs).
    proj = gdf[[c for c in gdf.columns if c == "geometry"]].copy().to_crs(_norm_epsg(dst_crs))
    proj["geometry"] = proj.geometry.centroid   # Polygon/MultiPolygon/Point -> POINT (metric CRS)
    return proj


def fetch_buildings(bbox, dst_crs, out_path, *, buf_deg: float = 0.012):
    """Fetch OSM building footprints over `bbox` (buffered ~1 km for edge assets) via osmnx/Overpass,
    reproject to `dst_crs`, and stage a GeoPackage. Fails loud on 0 buildings over a populated AOI
    (A8) -- a source/endpoint problem, not 'no assets'. Returns (out_path, n_buildings).
    DATA_SOURCES S4: cache per fire; DRAINS_TO_ASSET_M is applied later (delineate/score), not here.
    """
    import osmnx as ox   # lazy: heavy import, only paid when actually fetching

    west, south, east, north = bbox
    poly = box(west - buf_deg, south - buf_deg, east + buf_deg, north + buf_deg)  # lon/lat (EPSG:4326)
    gdf = ox.features_from_polygon(poly, tags={"building": True})
    if gdf is None or len(gdf) == 0:
        raise GateAbort("FAIL: Overpass returned 0 buildings over the AOI (A8) -- unexpected for a "
                         "populated area; treat as a source/endpoint problem, not 'no assets'.")
    pts = _buildings_to_points(gdf, dst_crs)   # footprints -> representative POINTS (pipeline contract)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pts.to_file(out_path, driver="GPKG")
    return out_path, int(len(pts))


# ---- CF-9: dNBR raw-scale guard + A30 fire-config assembly -------------------------------------

def assert_raw_dnbr(dnbr_path) -> dict:
    """Guard (Tier-1-adjacent): the uploaded dNBR MUST be raw scale, because the pipeline's frozen bins
    (src.config.DNBR_BIN_EDGES / DNBR_CLAMP) are defined on RAW dNBR. dNBR is physically bounded to
    [-2, 2] (NBR = (NIR-SWIR)/(NIR+SWIR) in [-1,1]); the vault's typical raw range is ~[-0.5, 1.3]. So a
    99th-pct |dNBR| above DNBR_RAW_MAX_ABS (=2.0) is an apparent scale error (x1000, x-N, or an RdNBR
    upload) -> fail loud (A8); acquire NEVER silently rescales (a wrong-scale input would misclassify
    every pixel). Obvious NoData/fill sentinels (|v| > DNBR_FILL_ABS) are SCREENED first, so an
    UNDECLARED nodata (which read(masked=True) does NOT mask) can't false-refuse a valid raw raster.
    Returns stats for the manifest. Raises on all-NoData / all-sentinel too.
    """
    with rasterio.open(dnbr_path) as ds:
        band = ds.read(1, masked=True)          # masks only a DECLARED nodata
        total = ds.width * ds.height            # captured here -- no second open (was a duplicate)
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
    """Coordinate frontend seam: (bbox lon/lat + uploaded dNBR) -> staged files + the A30 `fire` dict
    that `run_pipeline` consumes (sbs=None -> the A34 dNBR both-arms path). The dNBR scale guard runs
    FIRST (cheap, fail-fast, before any network). Writes an acquisition manifest alongside the inputs.
    """
    west, south, east, north = bbox
    out_dir = Path(out_dir)
    dnbr_stats = assert_raw_dnbr(dnbr_path)                     # CF-9 guard before any fetch
    grid = canonical_grid(west, south, east, north)            # CF-6: lon/lat -> UTM 10 m grid
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

