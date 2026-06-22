"""P3.2 -- acquire OSM building assets over the South Fork / Ruidoso AOI.

GATE (P3.1 / A24): acquisition ONLY. Pull OSM buildings via Overpass (osmnx, which sets correct
headers + rate-limit handling per DATA_SOURCES.md S4), cache the raw 4326 pull, reproject to the
frozen canonical CRS (EPSG:32613). NO drains-to-asset logic here (DRAINS_TO_ASSET_M=600 is applied
in delineate/score, a later P3 sub-phase). Assets are stored, not processed.

AOI: the frozen 32613 bbox (A24 S3) buffered out by ~1 km in lon/lat so a basin whose channel
reaches within DRAINS_TO_ASSET_M=600 m of a just-outside-AOI building is not silently dropped at the
edge. Buffer is generous and asset-presence is not score-affecting at P3.2 -- it only seeds the later
keep/drop test.

Run: python validation/p3_acquire_assets.py
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import osmnx as ox
from shapely.geometry import box

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "southfork" / "assets"
RAW_GPKG = OUT_DIR / "osm_buildings_4326.gpkg"      # cached raw pull (DATA_SOURCES S4: cache per fire)
OUT_GPKG = OUT_DIR / "osm_buildings_32613.gpkg"     # reprojected to the canonical CRS
OUT_META = OUT_DIR / "assets_source.json"

# frozen 32613 AOI bbox (A24 S3) -> lon/lat, then ~1 km buffer (deg) for edge assets.
AOI_LONLAT = (-105.7916, 33.3255, -105.6361, 33.4135)   # (W, S, E, N)
BUF_DEG = 0.012   # ~1.1 km at this latitude
DST_CRS = "EPSG:32613"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    w, s, e, n = AOI_LONLAT
    poly = box(w - BUF_DEG, s - BUF_DEG, e + BUF_DEG, n + BUF_DEG)   # lon/lat (EPSG:4326)

    gdf = ox.features_from_polygon(poly, tags={"building": True})    # Overpass via osmnx
    if gdf is None or len(gdf) == 0:
        raise SystemExit("FAIL: Overpass returned 0 buildings over the Ruidoso AOI (A8) -- unexpected "
                         "for a populated town; treat as a source/endpoint problem, not 'no assets'.")
    gdf = gdf.set_crs("EPSG:4326") if gdf.crs is None else gdf
    # GPKG can't store list-valued OSM tag columns; keep only geometry + a couple of scalar id cols.
    keep = [c for c in gdf.columns if c == "geometry"]
    raw = gdf[keep].copy()
    raw.to_file(RAW_GPKG, driver="GPKG")

    proj = raw.to_crs(DST_CRS)
    proj.to_file(OUT_GPKG, driver="GPKG")

    meta = {
        "source": "OpenStreetMap buildings via Overpass (osmnx)",
        "osmnx_version": ox.__version__,
        "aoi_lonlat_buffered": [w - BUF_DEG, s - BUF_DEG, e + BUF_DEG, n + BUF_DEG],
        "buffer_deg": BUF_DEG,
        "n_buildings": int(len(proj)),
        "raw_crs": "EPSG:4326", "out_crs": DST_CRS,
        "raw_gpkg": str(RAW_GPKG.relative_to(REPO)),
        "out_gpkg": str(OUT_GPKG.relative_to(REPO)),
        "note": "acquisition only; DRAINS_TO_ASSET_M=600 applied later (delineate/score)",
    }
    OUT_META.write_text(json.dumps(meta, indent=2))
    print("WROTE", OUT_GPKG, "| n_buildings", len(proj), "| CRS", DST_CRS)


if __name__ == "__main__":
    main()
