"""P3.2 -- acquire the South Fork DEM onto the FROZEN canonical grid (the reproject TARGET).

GATE (P3.1 / A24, FROZEN 2026-06-19):
  Produce ONE DEM raster on the frozen canonical grid so the dNBR reproject (src/ingest.py:170-177,
  the P0.5 half-pixel-ghost guard) can snap to the DEM's EXPLICIT dst_transform/dst_shape. The DEM
  must exist BEFORE dNBR is reprojected -- it is the reproject destination, not just an input.
  ACQUISITION ONLY: no slope, no hydrology, no scoring (those are later P3 sub-phases).

NEW-FIRE PATH (DATA_SOURCES.md S1, COG option -- NOT the P0.5 ImageServer reconstruction path):
  Source = USGS 3DEP 1/3 arc-second (~10 m) seamless DEM, public Cloud-Optimized GeoTIFF on AWS
  (anonymous https via /vsicurl/, no token). The AOI (lon -105.79..-105.64, lat 33.33..33.41) falls
  ENTIRELY inside the single 1-degree tile n34w106 -> no mosaic needed. Native CRS EPSG:4269 (NAD83
  geographic) per A24 S3; reproject to the frozen UTM 13N grid.
  Endpoint re-verified reachable 2026-06-19 (DATA_SOURCES last-verified 2026-06-01, was stale).

FROZEN canonical grid (A24 S3 -- the ONE legitimate per-fire change is the grid/CRS):
  CRS EPSG:32613 (UTM 13N) @ 10 m; shape 966 rows x 1439 cols; bbox (EPSG:32613)
  426400.8, 3687653.6 -> 440794.1, 3697312.6. We anchor the grid at the frozen upper-left
  (426400.8, 3697312.6), res 10 m, shape (966, 1439): this reproduces the frozen UL EXACTLY and the
  lower-right within sub-pixel tolerance (right 3.3 m, bottom 1.0 m, both < one 10 m cell -- A24 S3
  records the frozen bbox/dims; the residual is rounding in how the pre-reg recorded bbox vs dims).
  This grid IS the canonical grid for the DEM AND both dNBR arms (ingest reprojects dNBR onto it).

UNITS: elevation in metres (3DEP vertical datum NAVD88); transform in metres (projected CRS);
  resampling = bilinear (continuous elevation surface; nearest would stair-step the terrain).

Run: python validation/p3_acquire_dem.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.warp import reproject, Resampling

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "southfork" / "dem"
OUT_TIF = OUT_DIR / "dem.tif"
OUT_META = OUT_DIR / "dem_source.json"

# Public AWS 3DEP 1/3 arc-second COG (anonymous https). Single tile covers the whole AOI.
COG_URL = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/"
           "n34w106/USGS_13_n34w106.tif")
COG = "/vsicurl/" + COG_URL

# Frozen canonical grid (A24 S3), anchored at the frozen upper-left corner.
DST_CRS = "EPSG:32613"
CELL_M = 10.0
DST_W, DST_H = 1439, 966
DST_LEFT, DST_TOP = 426400.8, 3697312.6
DST_TRANSFORM = Affine(CELL_M, 0.0, DST_LEFT, 0.0, -CELL_M, DST_TOP)
DEM_NODATA = -9999.0


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = np.full((DST_H, DST_W), DEM_NODATA, dtype="float32")
    with rasterio.open(COG) as src:
        src_crs = str(src.crs)
        if src_crs.upper() not in ("EPSG:4269", "EPSG:4326"):
            # A8 fail loud: 3DEP 1/3" arrives NAD83 geographic (EPSG:4269) per A24 S3. A different
            # native CRS means a vintage/product change -> record as a finding, do not silently warp.
            raise SystemExit(f"FAIL: 3DEP COG native CRS {src_crs} not the expected NAD83 geographic "
                             f"(EPSG:4269) -- A24 S3. Halt and record as a finding (A16 precedent).")
        # 3DEP vintage drift is documented (DATA_SOURCES S1): capture whatever the COG exposes.
        tags = dict(src.tags())
        src_meta = {
            "endpoint": COG_URL, "native_crs": src_crs,
            "native_res_deg": [abs(src.transform.a), abs(src.transform.e)],
            "native_shape_rows_cols": [src.height, src.width],
            "native_nodata": src.nodata, "tags": tags,
        }
        reproject(
            source=rasterio.band(src, 1), destination=dst,
            src_transform=src.transform, src_crs=src.crs, src_nodata=src.nodata,
            dst_transform=DST_TRANSFORM, dst_crs=DST_CRS, dst_nodata=DEM_NODATA,
            resampling=Resampling.bilinear,
        )

    valid = dst != DEM_NODATA
    if not valid.any():
        raise SystemExit("FAIL: DEM all-NoData on the canonical grid -- AOI/tile mismatch (A8).")

    profile = {
        "driver": "GTiff", "height": DST_H, "width": DST_W, "count": 1,
        "dtype": "float32", "crs": DST_CRS, "transform": DST_TRANSFORM,
        "nodata": DEM_NODATA, "compress": "deflate",
    }
    with rasterio.open(OUT_TIF, "w", **profile) as d:
        d.write(dst, 1)
        d.update_tags(source="USGS 3DEP 1/3 arc-second COG (AWS, anonymous)",
                      native_crs=src_meta["native_crs"], canonical_crs=DST_CRS,
                      note="P3.2 canonical grid; reproject TARGET for dNBR (A24)")

    src_meta.update({
        "out_tif": str(OUT_TIF.relative_to(REPO)),
        "canonical_crs": DST_CRS, "cell_m": CELL_M,
        "shape_rows_cols": [DST_H, DST_W],
        "transform": list(DST_TRANSFORM)[:6],
        "bbox": [DST_LEFT, DST_TOP - DST_H * CELL_M, DST_LEFT + DST_W * CELL_M, DST_TOP],
        "frozen_bbox_A24": [426400.8, 3687653.6, 440794.1, 3697312.6],
        "elev_min_m": float(np.nanmin(dst[valid])), "elev_max_m": float(np.nanmax(dst[valid])),
        "valid_frac": round(float(valid.mean()), 4),
    })
    OUT_META.write_text(json.dumps(src_meta, indent=2))
    print("WROTE", OUT_TIF)
    print("shape", dst.shape, "| elev m [min,max]:",
          round(src_meta["elev_min_m"], 1), round(src_meta["elev_max_m"], 1),
          "| valid_frac", src_meta["valid_frac"])
    print("native_crs", src_meta["native_crs"], "| transform", list(DST_TRANSFORM)[:6])


if __name__ == "__main__":
    main()
