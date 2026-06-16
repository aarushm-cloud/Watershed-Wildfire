"""P2.0 -- acquire the Thomas Fire dNBR onto its NATIVE grid (no reprojection).

GATE (frozen by P2.1 `validation/P2_PREREGISTRATION.md`, amended by A21):
  Produce a single RAW, continuous dNBR raster on disk + a Provenance stamp.
  HARD STOP here: NO thresholding, NO 0.1 floor, NO class-15 mask, NO normalization,
  NO mean_burn, NO binning, NO scoring. Those are P2.2b / P2.3.

WHY THIS PATH (owner decision, 2026-06-16):
  The frozen extended-assessment scene-date policy (A21) is physically satisfiable only
  off-season: spring-2017 Sentinel-2 pre scenes are buried under marine-layer cloud over
  the flowed basins (Cold Spring 85% bad; 4-6/6 basins exceed the §4 20% fail-loud floor).
  That met the pre-registered MTBS fallback trigger. MTBS's published continuous-dNBR
  raster is not token-free downloadable (portal rebuilt; ZipServlet 503; IIPP serves only
  the firewall-forbidden THEMATIC product). So we reuse MTBS's *validated scene selection*
  -- the hard part -- and compute the continuous dNBR ourselves from the exact Landsat-8
  scenes MTBS used for its Extended assessment, with the frozen NBR formula. This is
  recorded honestly as a sensor+processing caveat (A4/A8/A16), never a silent swap.

MTBS Thomas Fire record (authoritative, MTBS national FOD dataset):
  event_id   = CA3442911910020171205   asmnt_type = Extended   ig_date = 2017-12-04
  pre  scene = L8 042/036 2017-06-16    post scene = L8 042/036 2018-06-19  (phenology-matched)

SCIENCE / UNITS (transcribed, not reconstructed):
  - Landsat-8 Collection-2 Level-2 surface reflectance is SCALED: reflectance (dimensionless,
    0..1) = DN * 0.0000275 - 0.2.  The additive -0.2 offset means a ratio on raw DN is WRONG,
    so we apply scale+offset BEFORE forming any ratio. Fill DN = 0 -> nodata.
  - NBR (Normalized Burn Ratio, dimensionless -1..1) = (NIR - SWIR2)/(NIR + SWIR2).
    Landsat-8: NIR = band 5 (MPC asset `nir08`), SWIR2 = band 7 (MPC asset `swir22`).
  - dNBR (raw, dimensionless, ~ -0.5..+1.3; POSITIVE = burned) = NBR_pre - NBR_post.
  - Output stays on the Landsat NATIVE grid (EPSG:32611, 30 m). NOT resampled to the 10 m
    canonical grid -- that reprojection + the per-arm nearest/bilinear split is P2.2b (§5).
    We crop to AOI + buffer in the native CRS (a faithful subset, no resampling) to keep the
    artifact small; the buffer leaves margin for P2.2b's resample at the AOI edge.

OWNER-ACCEPTED DEVIATIONS (2026-06-16) -- knowing acceptances, not silent pass-throughs and not
new amendments. Both clear the A21 correction-test because they are INHERITED from MTBS's
analyst-validated extended-assessment selection, not agent-chosen to move a number (result-
independent: no dNBR score existed when the choices were made). Committed here so they persist
(provenance.json is a gitignored regenerated output). Also stamped into provenance `accepted_deviations`.

  Note A -- post-scene is mid-June (2018-06-19), later than A21's "late spring":
    A21 froze the post window as "late spring, NOT late summer 2018" to predate significant chaparral
    green-up. The acquired post-scene is 2018-06-19 (early summer). ACCEPTED because it is MTBS's own
    analyst-validated Extended-assessment selection for the Thomas Fire (event CA3442911910020171205),
    not an agent-chosen date -- a field-validated inherited choice, stronger provenance than an arbitrary
    "late spring" pick. Consequence: the green-up under-read risk (FM-11 direction) is somewhat more live
    than a May scene would make it, concentrated in low-severity MARGINS, not the flowed basins (dNBR
    +0.31...+0.43, well above the 0.1 floor). The per-arm flowed-basin coverage-fraction check at P2.3 is
    the load-bearing guard -- confirm coverage there, do not assume.

  Note B -- self-computed dNBR, NOT MTBS's published raster (MUST travel into the P2.4/O4 claim):
    MTBS's published dNBR raster proved non-downloadable token-free (EROS bundle 404, ZipServlet 503,
    IIPP thematic-only/firewall-forbidden). This is a self-computed Landsat-8 dNBR using the standard
    NBR = (B5-B7)/(B5+B7), dNBR = pre-post, raw scale, over MTBS's validated Extended scene selection
    (the two scenes MTBS chose). ACCEPTED: arguably cleaner than the published product (exact formula +
    scale under our control) and it inherits MTBS's hard part -- the analyst scene selection.
    P2.4 CLAIM PRECISION (mandatory): the honest line is "dNBR computed from MTBS's validated
    Extended-assessment scene selection," NOT "MTBS's dNBR product." Do not let the looser phrasing into
    the O4 conversation.

Run: python validation/p2_acquire_dnbr.py
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds, Window
from rasterio.features import geometry_mask
import geopandas as gpd

REPO = Path(__file__).resolve().parent.parent
DEM = REPO / "validation" / "data" / "dem.tif"
BASINS = REPO / "validation" / "out" / "basins.geojson"          # SBS control delineation
OUT_DIR = REPO / "validation" / "out" / "montecito_dnbr"          # FM-9: namespaced, not flat
OUT_TIF = OUT_DIR / "dnbr_native.tif"
OUT_PROV = OUT_DIR / "provenance.json"

PRE_ID = "LC08_L2SP_042036_20170616_02_T1"
POST_ID = "LC08_L2SP_042036_20180619_02_T1"
SR_SCALE, SR_OFFSET = 0.0000275, -0.2     # L8 C2L2 surface-reflectance scaling
AOI_BUFFER_M = 5000.0                      # native-CRS crop buffer (m); ~167 px @ 30 m

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign?href="


def _get_item(item_id: str) -> dict:
    body = {"collections": ["landsat-c2-l2"], "ids": [item_id], "limit": 1}
    req = urllib.request.Request(STAC, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        feats = json.load(r)["features"]
    if not feats:
        raise SystemExit(f"FAIL: MPC returned no item for {item_id}")
    return feats[0]


def _sign(href: str) -> str:
    with urllib.request.urlopen(SIGN + urllib.request.quote(href, safe=""), timeout=60) as r:
        return json.load(r)["href"]


def _read_band_window(signed_href: str, bounds) -> tuple[np.ndarray, object, object]:
    """Windowed read of a band over `bounds` (EPSG:32611) -- native grid, no resampling."""
    with rasterio.open(signed_href) as ds:
        if str(ds.crs).upper() != "EPSG:32611":
            raise SystemExit(f"FAIL: scene CRS {ds.crs} != EPSG:32611 (expected native UTM 11N)")
        win = from_bounds(*bounds, transform=ds.transform)
        # snap window to whole pixels so pre/post land on the identical native grid
        win = Window(int(round(win.col_off)), int(round(win.row_off)),
                     int(round(win.width)), int(round(win.height)))
        arr = ds.read(1, window=win).astype("float64")
        return arr, ds.window_transform(win), ds.crs


def _nbr(item_id: str, bounds):
    """Return (NBR array, valid mask, transform, crs) for one scene over the AOI window."""
    it = _get_item(item_id)
    nir_dn, t, crs = _read_band_window(_sign(it["assets"]["nir08"]["href"]), bounds)
    swir_dn, t2, _ = _read_band_window(_sign(it["assets"]["swir22"]["href"]), bounds)
    qa, t3, _ = _read_band_window(_sign(it["assets"]["qa_pixel"]["href"]), bounds)
    assert nir_dn.shape == swir_dn.shape == qa.shape, "band windows misaligned within scene"
    qa_u = qa.astype("uint16")
    fill = (nir_dn <= 0) | (swir_dn <= 0) | ((qa_u & 1).astype(bool))   # DN 0 / QA fill bit 0
    # surface reflectance, clamped to its physical [0,1] range (negative = atm-correction
    # overshoot on dark pixels; >1 = saturation) -- standard C2L2 hygiene, not a firewall knob.
    nir = np.clip(nir_dn * SR_SCALE + SR_OFFSET, 0.0, 1.0)
    swir = np.clip(swir_dn * SR_SCALE + SR_OFFSET, 0.0, 1.0)
    denom = nir + swir
    valid = (~fill) & (denom > 0)                              # denom==0 only if both refl==0
    nbr = np.where(valid, (nir - swir) / np.where(denom > 0, denom, 1.0), np.nan)
    return nbr, valid, t, crs, qa_u, it["properties"]


def _qa_cloud_mask(qa: np.ndarray) -> np.ndarray:
    """QA_PIXEL cloud/shadow/cirrus/dilated -> True (QC only; NOT the §4 class-15 mask)."""
    bits = (1, 2, 3, 4)        # dilated cloud, cirrus, cloud, cloud shadow
    m = np.zeros(qa.shape, bool)
    for b in bits:
        m |= ((qa >> b) & 1).astype(bool)
    return m


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with rasterio.open(DEM) as d:
        aoi = d.bounds
        if str(d.crs).upper() != "EPSG:32611":
            raise SystemExit(f"FAIL: DEM CRS {d.crs} != EPSG:32611")
    bounds = (aoi.left - AOI_BUFFER_M, aoi.bottom - AOI_BUFFER_M,
              aoi.right + AOI_BUFFER_M, aoi.top + AOI_BUFFER_M)

    nbr_pre, vpre, tpre, crs, qa_pre, p_pre = _nbr(PRE_ID, bounds)
    nbr_post, vpost, tpost, _, qa_post, p_post = _nbr(POST_ID, bounds)
    if nbr_pre.shape != nbr_post.shape or tpre != tpost:
        raise SystemExit(f"FAIL: pre/post not on identical native grid "
                         f"({nbr_pre.shape} {tpre} vs {nbr_post.shape} {tpost})")

    # dNBR (raw, pre - post). nodata where either scene invalid.
    valid = vpre & vpost & np.isfinite(nbr_pre) & np.isfinite(nbr_post)
    NODATA = -9999.0
    dnbr = np.where(valid, nbr_pre - nbr_post, NODATA).astype("float32")

    with rasterio.open(OUT_TIF, "w", driver="GTiff", height=dnbr.shape[0],
                       width=dnbr.shape[1], count=1, dtype="float32", crs=crs,
                       transform=tpre, nodata=NODATA, compress="deflate") as dst:
        dst.write(dnbr, 1)
        dst.update_tags(burn_source="dNBR", sensor="Landsat-8", assessment_type="extended",
                        scale="raw", note="P2.0 native 30m EPSG:32611; reproject deferred to P2.2b")

    # ---- QC ONLY (not part of the product): cloud fraction over the 6 flowed basins ----
    cloud = _qa_cloud_mask(qa_pre) | _qa_cloud_mask(qa_post) | ~valid
    g = gpd.read_file(BASINS).to_crs("EPSG:32611")
    flowed = g[g["flowed"] == True].sort_values("basin_id")
    qc = {}
    for _, row in flowed.iterrows():
        bm = geometry_mask([row.geometry.__geo_interface__], out_shape=dnbr.shape,
                           transform=tpre, invert=True)
        n = int(bm.sum())
        qc[int(row.basin_id)] = round(100 * float(cloud[bm].sum()) / n, 1) if n else None

    valid_aoi = g.to_crs("EPSG:32611")
    dvals = dnbr[valid]
    prov = {
        "burn_source": "dNBR",
        "sensor": "Landsat-8 (OLI)",
        "assessment_type": "extended",
        "source": "self-computed dNBR from MTBS-selected Landsat C2 L2 scenes via "
                  "Microsoft Planetary Computer (token-free); MTBS published raster "
                  "not token-free downloadable -- see acquisition note",
        "mtbs_event_id": "CA3442911910020171205",
        "mtbs_asmnt_type": "Extended",
        "pre_scene": {"id": PRE_ID, "date": p_pre["datetime"][:10],
                      "scene_cloud_pct": p_pre.get("eo:cloud_cover")},
        "post_scene": {"id": POST_ID, "date": p_post["datetime"][:10],
                       "scene_cloud_pct": p_post.get("eo:cloud_cover")},
        "bands": {"NIR": "B5 (MPC nir08)", "SWIR2": "B7 (MPC swir22)"},
        "nbr_formula": "(NIR - SWIR2)/(NIR + SWIR2), surface reflectance after DN*2.75e-5-0.2",
        "dnbr_formula": "NBR_pre - NBR_post",
        "scale": "raw (NOT x1000); range observed below. No offset applied (we computed SR "
                 "directly, so MTBS dnbr_offst=6.0 is N/A to this self-computed product).",
        "native_crs": str(crs),
        "native_res_m": 30.0,
        "native_transform": list(tpre)[:6],
        "shape_rows_cols": [int(dnbr.shape[0]), int(dnbr.shape[1])],
        "crop": f"AOI bbox + {AOI_BUFFER_M:.0f} m buffer in native CRS (faithful subset, no resampling)",
        "reproject_status": "DEFERRED to P2.2b (native-grid storage per owner design call)",
        "dnbr_stats_raw": {"min": float(np.nanmin(dvals)), "max": float(np.nanmax(dvals)),
                           "mean": float(np.nanmean(dvals)),
                           "valid_frac": round(float(valid.mean()), 4)},
        "qc_flowed_basin_cloud_pct": qc,
        # Owner-accepted deviations from the frozen P2.1/A21 plan (recorded verbatim, 2026-06-16).
        # These are the two notes that must survive into P2.4; Note B governs the O4 claim wording.
        "accepted_deviations": {
            "A_post_scene_mid_june":
                "Accepted deviation (owner, 2026-06-16): A21 froze the post-scene window as \"late "
                "spring, NOT late summer 2018\" to predate significant chaparral green-up. The acquired "
                "post-scene is 2018-06-19 (early summer). ACCEPTED because the scene is MTBS's own "
                "analyst-validated Extended-assessment selection for the Thomas Fire (event "
                "CA3442911910020171205), not an agent-chosen date -- i.e. a field-validated inherited "
                "choice, stronger provenance than an arbitrary \"late spring\" pick. Clears the A21 "
                "correction-test (the choice is result-independent: no dNBR score existed when it was "
                "made). Consequence: the green-up under-read risk (FM-11 direction) is somewhat more "
                "live than a May scene would make it, concentrated in low-severity margins, not the "
                "flowed basins (which sit at dNBR +0.31...+0.43, well above the 0.1 floor). The per-arm "
                "flowed-basin coverage-fraction check at P2.3 is the load-bearing guard -- confirm "
                "coverage there, do not assume.",
            "B_self_computed_not_mtbs_published":
                "Accepted method note (owner, 2026-06-16): This is NOT MTBS's published dNBR raster "
                "(which proved non-downloadable token-free -- EROS bundle 404, ZipServlet 503, IIPP "
                "thematic-only/firewall-forbidden). It is a self-computed Landsat-8 dNBR using the "
                "standard NBR = (B5-B7)/(B5+B7), dNBR = pre-post, raw scale, over MTBS's validated "
                "Extended scene selection (the two scenes MTBS chose). ACCEPTED: arguably cleaner than "
                "the published product (exact formula + scale under our control), and it inherits "
                "MTBS's hard part -- the analyst scene selection. P2.4 claim precision (mandatory): the "
                "honest line is \"dNBR computed from MTBS's validated Extended-assessment scene "
                "selection,\" NOT \"MTBS's dNBR product.\" Do not let the looser phrasing into the O4 "
                "conversation.",
        },
        "p2_4_claim_precision": "dNBR computed from MTBS's validated Extended-assessment scene "
                                "selection (NOT 'MTBS's dNBR product').",
        "caveats": [
            "Sensor caveat (A21/A4): Landsat-8 30 m, NOT Sentinel-2 20 m -- co-varies sensor+resolution.",
            "Green-up caveat (A21): extended assessment can under-read low-severity margins; confirm "
            "per-flowed-basin coverage in P2.3. See accepted_deviations.A (mid-June makes this more live).",
            "Self-computed, not MTBS-published: see accepted_deviations.B (governs the P2.4/O4 claim).",
            "QC cloud fractions above are REPORT-ONLY. The §4 NoData/cloud -> class-15 mapping and the "
            ">20% fail-loud guard are P2.2b, NOT applied here.",
        ],
    }
    OUT_PROV.write_text(json.dumps(prov, indent=2))
    print("WROTE", OUT_TIF)
    print("WROTE", OUT_PROV)
    print("dNBR raw stats:", prov["dnbr_stats_raw"])
    print("QC flowed-basin cloud%:", qc)


if __name__ == "__main__":
    main()
