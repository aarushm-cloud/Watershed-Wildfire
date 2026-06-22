"""P3.2 -- acquire the South Fork dNBR onto its NATIVE grid (no reprojection).

REUSE-BY-IMPORT of the frozen P2.0 math (A24 S2; analog of validation/p2_acquire_dnbr.py). p2's
script is hardcoded for Thomas (LC08 042/036, an EPSG:32611 assertion, Montecito AOI) and CANNOT run
as-is on South Fork (LC09, UTM 13N). "Reuse exactly" therefore = reuse the frozen MATH unchanged and
parameterize ONLY the per-fire surface (A24 S2 / build prompt S2.2a):

  IMPORTED VERBATIM from p2_acquire_dnbr (NOT redefined here -> provably unaltered):
    SR_SCALE, SR_OFFSET   -- L8/L9 C2L2 surface-reflectance scaling (DN*2.75e-5 - 0.2)
    _get_item, _sign      -- MPC STAC + SAS plumbing (token-free)
    _qa_cloud_mask        -- QA_PIXEL cloud/cirrus/shadow/dilated bits (1,2,3,4); REPORT-ONLY
    STAC, SIGN, AOI_BUFFER_M

  RE-IMPLEMENTED with the MATH COPIED VERBATIM and the ONLY change being the CRS literal
  (EPSG:32611 -> EPSG:32613): _read_band_window (the scene-CRS assertion) and _nbr (which must call
  the local _read_band_window). The NBR formula, the SR clip-to-[0,1] hygiene, the fill logic, and
  dNBR = NBR_pre - NBR_post are byte-identical to p2.

PARAMETERIZED per-fire (A24 S2.2a) -- and NOTHING ELSE:
  - scene IDs (PRE_ID/POST_ID): South Fork LC09, frozen dates 2024-06-12 (pre) / 2024-07-07 (post).
  - native scene CRS assertion + DEM CRS assertion: EPSG:32611 -> EPSG:32613 (UTM 13N).
  - DEM path / AOI bbox: the frozen canonical DEM (data/southfork/dem/dem.tif).
  - output dir + provenance sensor/event tags (Landsat-9; South Fork; NOT an MTBS selection).

SCENE-FREEZE NOTE (A21, the freeze line): the two scenes are on DIFFERENT WRS-2 paths/rows
  (pre 033/037, post 032/037) because the frozen dates fall on adjacent Landsat-9 overpasses; the AOI
  sits in the path 032/033 sidelap. This is NOT a problem: USGS Collection-2 L2 products in one UTM
  zone share a common 30 m lattice (both UL corners are 15 m mod 30), so over the AOI both scenes
  snap to the IDENTICAL native window_transform -- the dNBR subtraction is pixel-aligned and the
  frozen `tpre == tpost` guard PASSES (it remains the load-bearing fail-loud check, A8). Recorded as a
  characteristic, not a deviation. [VERIFIED 2026-06-19 from proj:transform metadata.]

CLOUD (frozen behavior, Decision A): the product is masked for the QA FILL bit ONLY. _qa_cloud_mask
  (cloud/cirrus/shadow) is used REPORT-ONLY for the AOI-cloud QC and is NOT removed from the dNBR.
  Adding cloud masking would change the validated input method mid-phase -> a P3.5/limitations caveat,
  not a code change. The QC aggregation region is the AOI (P3 has no flowed-basin truth yet -- that is
  P3.4; the per-flowed-basin >20% guard cannot run here, A24 S3c). _qa_cloud_mask itself is unchanged.

GATE: HARD STOP after writing the native dNBR + provenance. NO thresholding / floor / normalization /
  binning / scoring -- those are the frozen ingest (P2.2b) at the run step and P3.4.

Run: python validation/p3_acquire_dnbr.py   (requires MPC STAC reachable)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds, Window

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# --- IMPORTED VERBATIM (frozen math; not redefined here) ---
from p2_acquire_dnbr import (  # noqa: E402
    SR_SCALE, SR_OFFSET, AOI_BUFFER_M, STAC, SIGN,
    _get_item, _sign, _qa_cloud_mask,
)

REPO = _HERE.parent
DEM = REPO / "data" / "southfork" / "dem" / "dem.tif"
OUT_DIR = REPO / "data" / "southfork" / "burn" / "southfork_dnbr"
OUT_TIF = OUT_DIR / "dnbr_native.tif"
OUT_PROV = OUT_DIR / "provenance.json"
REFERENCE_PERIM = REPO / "data" / "southfork" / "reference" / "shp" / "sfk2024-perimeter.shp"

# --- PARAMETERIZED: scene IDs (frozen dates, A24 S2) + native CRS (UTM 13N) ---
PRE_ID = "LC09_L2SP_033037_20240612_02_T1"      # pre  2024-06-12 (path 033/row 037); MPC id omits proc-date
POST_ID = "LC09_L2SP_032037_20240707_02_T1"     # post 2024-07-07 (path 032/row 037); MPC id omits proc-date
NATIVE_CRS = "EPSG:32613"                                # UTM 13N (was 32611 for Thomas)
NODATA = -9999.0


def _read_band_window(signed_href, bounds):
    """Windowed read of a band over `bounds` (EPSG:32613) -- native grid, no resampling.
    COPIED VERBATIM from p2_acquire_dnbr._read_band_window; the ONLY change is the CRS literal
    EPSG:32611 -> EPSG:32613 (A24 S2.2a-permitted CRS parameterization)."""
    with rasterio.open(signed_href) as ds:
        if str(ds.crs).upper() != NATIVE_CRS:
            raise SystemExit(f"FAIL: scene CRS {ds.crs} != {NATIVE_CRS} (expected native UTM 13N)")
        win = from_bounds(*bounds, transform=ds.transform)
        # snap window to whole pixels so pre/post land on the identical native grid
        win = Window(int(round(win.col_off)), int(round(win.row_off)),
                     int(round(win.width)), int(round(win.height)))
        arr = ds.read(1, window=win).astype("float64")
        return arr, ds.window_transform(win), ds.crs


def _nbr(item_id, bounds):
    """Return (NBR array, valid mask, transform, crs, qa, properties) for one scene over the AOI
    window. COPIED VERBATIM from p2_acquire_dnbr._nbr (B5/B7 NBR, SR clip-to-[0,1] hygiene, fill
    logic) -- it only calls the local _read_band_window so the CRS assertion is the 32613 one."""
    it = _get_item(item_id)
    nir_dn, t, crs = _read_band_window(_sign(it["assets"]["nir08"]["href"]), bounds)
    swir_dn, t2, _ = _read_band_window(_sign(it["assets"]["swir22"]["href"]), bounds)
    qa, t3, _ = _read_band_window(_sign(it["assets"]["qa_pixel"]["href"]), bounds)
    assert nir_dn.shape == swir_dn.shape == qa.shape, "band windows misaligned within scene"
    qa_u = qa.astype("uint16")
    fill = (nir_dn <= 0) | (swir_dn <= 0) | ((qa_u & 1).astype(bool))   # DN 0 / QA fill bit 0
    # surface reflectance, clamped to its physical [0,1] range (standard C2L2 hygiene, not a knob).
    nir = np.clip(nir_dn * SR_SCALE + SR_OFFSET, 0.0, 1.0)
    swir = np.clip(swir_dn * SR_SCALE + SR_OFFSET, 0.0, 1.0)
    denom = nir + swir
    valid = (~fill) & (denom > 0)                              # denom==0 only if both refl==0
    nbr = np.where(valid, (nir - swir) / np.where(denom > 0, denom, 1.0), np.nan)
    return nbr, valid, t, crs, qa_u, it["properties"]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with rasterio.open(DEM) as d:
        aoi = d.bounds
        if str(d.crs).upper() != NATIVE_CRS:
            raise SystemExit(f"FAIL: DEM CRS {d.crs} != {NATIVE_CRS}")
    bounds = (aoi.left - AOI_BUFFER_M, aoi.bottom - AOI_BUFFER_M,
              aoi.right + AOI_BUFFER_M, aoi.top + AOI_BUFFER_M)

    nbr_pre, vpre, tpre, crs, qa_pre, p_pre = _nbr(PRE_ID, bounds)
    nbr_post, vpost, tpost, _, qa_post, p_post = _nbr(POST_ID, bounds)
    if nbr_pre.shape != nbr_post.shape or tpre != tpost:
        # A8 fail loud: cross-path pair must still co-register on the common C2 30 m lattice. If this
        # fires, the two scenes are NOT pixel-aligned -> STOP and report (do NOT add a resample).
        raise SystemExit(f"FAIL: pre/post not on identical native grid "
                         f"({nbr_pre.shape} {tpre} vs {nbr_post.shape} {tpost})")

    # dNBR (raw, pre - post). nodata where either scene invalid.
    valid = vpre & vpost & np.isfinite(nbr_pre) & np.isfinite(nbr_post)
    dnbr = np.where(valid, nbr_pre - nbr_post, NODATA).astype("float32")

    with rasterio.open(OUT_TIF, "w", driver="GTiff", height=dnbr.shape[0],
                       width=dnbr.shape[1], count=1, dtype="float32", crs=crs,
                       transform=tpre, nodata=NODATA, compress="deflate") as dst:
        dst.write(dnbr, 1)
        dst.update_tags(burn_source="dNBR", sensor="Landsat-9", assessment_type="self-computed",
                        scale="raw", note="P3.2 native 30m EPSG:32613; reproject deferred to ingest")

    # ---- QC ONLY (not part of the product): cloud via _qa_cloud_mask (A24 S3c basis) ----
    # P3 has no flowed-basin truth (P3.4); we report two bases. _qa_cloud_mask is the frozen bit
    # definition (bits 1,2,3,4). Cloud is NOT removed (Decision A).
    #   (1) PERIMETER basis = the freeze-gate basis: cloud within the USGS fire perimeter. This is the
    #       value the P3.1 freeze-gate's 6.3%/0% were measured on (reproduced to 6.32%/0.0% in build) --
    #       acceptance (a) compares against THIS. [VERIFIED 2026-06-20.]
    #   (2) BBOX basis = cloud over the full frozen AOI bbox (DEM bounds). Context only: it runs higher
    #       (13.62% pre) because cloud in the AOI MARGINS OUTSIDE the burn (below-floor / non-covered)
    #       is counted; those cells carry weight 0 and do not affect screening.
    bbox = (aoi.left, aoi.bottom, aoi.right, aoi.top)
    inner = from_bounds(*bbox, transform=tpre)
    r0, c0 = int(round(inner.row_off)), int(round(inner.col_off))
    r1, c1 = r0 + int(round(inner.height)), c0 + int(round(inner.width))
    sl = (slice(max(0, r0), r1), slice(max(0, c0), c1))
    n_bbox = int(qa_pre[sl].size)
    cloud_pre = _qa_cloud_mask(qa_pre)
    cloud_post = _qa_cloud_mask(qa_post)
    bbox_cloud_pre_pct = round(100 * float(cloud_pre[sl].sum()) / n_bbox, 2)
    bbox_cloud_post_pct = round(100 * float(cloud_post[sl].sum()) / n_bbox, 2)
    cloud_union = cloud_pre | cloud_post | ~valid
    bbox_cloud_union_pct = round(100 * float(cloud_union[sl].sum()) / n_bbox, 2)

    # perimeter basis (the freeze-comparable one). Graceful: if the reference isn't acquired yet, skip.
    perim_cloud_pre_pct = perim_cloud_post_pct = None
    if REFERENCE_PERIM.exists():
        perim = gpd.read_file(REFERENCE_PERIM).to_crs(NATIVE_CRS)
        pmask = geometry_mask([g.__geo_interface__ for g in perim.geometry],
                              out_shape=dnbr.shape, transform=tpre, invert=True)
        n_perim = int(pmask.sum())
        if n_perim:
            perim_cloud_pre_pct = round(100 * float((cloud_pre & pmask).sum()) / n_perim, 2)
            perim_cloud_post_pct = round(100 * float((cloud_post & pmask).sum()) / n_perim, 2)
    # acceptance (a) uses the perimeter basis (freeze-comparable); fall back to bbox only if absent.
    aoi_cloud_pre_pct = perim_cloud_pre_pct if perim_cloud_pre_pct is not None else bbox_cloud_pre_pct
    aoi_cloud_post_pct = perim_cloud_post_pct if perim_cloud_post_pct is not None else bbox_cloud_post_pct
    aoi_cloud_union_pct = bbox_cloud_union_pct

    dvals = dnbr[valid]
    prov = {
        "burn_source": "dNBR",                          # data-record field (Decision A; distinct from
                                                        # the A4 in-pipeline Provenance set by ingest)
        "sensor": "Landsat-9 (OLI-2)",
        "assessment_type": "self-computed (P3.1-frozen scene selection, A24)",
        "source": "self-computed dNBR from P3.1-frozen Landsat C2 L2 scenes via Microsoft Planetary "
                  "Computer (token-free); NBR=(B5-B7)/(B5+B7), dNBR=pre-post, raw scale.",
        "pre_scene": {"id": PRE_ID, "date": p_pre["datetime"][:10],
                      "wrs_path_row": "033/037", "scene_cloud_pct": p_pre.get("eo:cloud_cover"),
                      "aoi_cloud_pct_qa": aoi_cloud_pre_pct,            # freeze-comparable (perimeter)
                      "perimeter_cloud_pct_qa": perim_cloud_pre_pct,   # freeze-gate basis (=6.3% frozen)
                      "bbox_cloud_pct_qa": bbox_cloud_pre_pct},        # full-AOI context (margins incl.)
        "post_scene": {"id": POST_ID, "date": p_post["datetime"][:10],
                       "wrs_path_row": "032/037", "scene_cloud_pct": p_post.get("eo:cloud_cover"),
                       "aoi_cloud_pct_qa": aoi_cloud_post_pct,
                       "perimeter_cloud_pct_qa": perim_cloud_post_pct,
                       "bbox_cloud_pct_qa": bbox_cloud_post_pct},
        "aoi_cloud_union_pct_qa": aoi_cloud_union_pct,
        "aoi_cloud_definition": "_qa_cloud_mask bits (1,2,3,4)=dilated/cirrus/cloud/shadow (frozen p2 "
                                "basis); REPORT-ONLY, NOT removed from product. Two regions reported: "
                                "PERIMETER (within the USGS fire perimeter) is the freeze-gate basis and "
                                "reproduces the frozen 6.3%/0% (build: 6.32%/0.0%); BBOX (full AOI) is "
                                "context only and runs higher because out-of-burn AOI-margin cloud "
                                "(below-floor, non-covered, weight 0) is counted. Acceptance (a) uses "
                                "the PERIMETER value.",
        "bands": {"NIR": "B5 (MPC nir08)", "SWIR2": "B7 (MPC swir22)"},
        "nbr_formula": "(NIR - SWIR2)/(NIR + SWIR2), surface reflectance after DN*2.75e-5-0.2",
        "dnbr_formula": "NBR_pre - NBR_post",
        "scale": "raw (NOT x1000)",
        "native_crs": str(crs), "native_res_m": 30.0,
        "native_transform": list(tpre)[:6],
        "shape_rows_cols": [int(dnbr.shape[0]), int(dnbr.shape[1])],
        "crop": f"DEM(AOI) bbox + {AOI_BUFFER_M:.0f} m buffer in native CRS (faithful subset, no resample)",
        "reproject_status": "DEFERRED to src/ingest.ingest_dnbr_both_arms (canonical grid)",
        "dnbr_stats_raw": {"min": float(np.nanmin(dvals)), "max": float(np.nanmax(dvals)),
                           "mean": float(np.nanmean(dvals)), "valid_frac": round(float(valid.mean()), 4)},
        "cross_path_note": "Pre 033/037, post 032/037 (frozen dates fall on adjacent LC09 overpasses; "
                           "AOI in the 032/033 sidelap). Co-registered on the common C2 30 m lattice "
                           "(both UL 15 m mod 30); the frozen tpre==tpost guard confirms pixel "
                           "alignment. A characteristic, not a deviation. Differs from Thomas (same "
                           "path 042/036). dNBR sensor/processing caveat unchanged (A21/A4).",
        "caveats": [
            "Sensor: Landsat-9 30 m, self-computed dNBR (sensor held fixed vs P2 per A24 S2).",
            "Cloud QC fractions are REPORT-ONLY; cloud is NOT removed from the dNBR (Decision A). "
            "The pre-scene's AOI cloud carries spurious dNBR into both arms -- a P3.5/limitations "
            "caveat, not a code change.",
            "Cross-path pre/post pair, co-registered -- see cross_path_note.",
        ],
    }
    OUT_PROV.write_text(json.dumps(prov, indent=2))
    print("WROTE", OUT_TIF)
    print("WROTE", OUT_PROV)
    print("dNBR raw stats:", prov["dnbr_stats_raw"])
    print(f"cloud%% (QA, report-only) PERIMETER pre/post = {perim_cloud_pre_pct}/{perim_cloud_post_pct} "
          f"(freeze-comparable, frozen 6.3/0); BBOX pre/post/union = "
          f"{bbox_cloud_pre_pct}/{bbox_cloud_post_pct}/{bbox_cloud_union_pct} (context)")


if __name__ == "__main__":
    main()
