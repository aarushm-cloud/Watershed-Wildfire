"""AA-2 (B4, Auto-Acquire Build-Plan Phase 2) -- the unified dNBR creator.

Turns a HUMAN-APPROVED scene pair (from scene_select) into a raw native-grid dNBR
GeoTIFF + quicklook + provenance, satisfying the existing input gate
acquire.assert_raw_dnbr and feeding the UNCHANGED validated ingest
(build_fire_config -> ingest_dnbr_both_arms -> run_pipeline, A34). Lives in the autoacquire/ package
alongside scene_select.py, outside src/ (A35): a network boundary; src/ stays pure.

Design frozen by the Feature Spec section 6B + the RATIFIED pre-registration
(2026-07-17): ONE module = p2_acquire_dnbr's windowed-NATIVE-read fetch skeleton
+ per-sensor band adapters. NO reprojection here -- the single resample in the
whole pathway is the frozen both-arms ingest (avoids double-resample smoothing of
the burn signal). Cross-UTM-zone fires and pre/post lattice mismatches ABORT LOUD;
the mandated response to a grid mismatch is STOP, never resample (A8, p2 pattern).

Frozen science (transcribed verbatim -- DATA_SOURCES section 2, pre-reg D, and the
validated working code putah_dnbr.py / p2_acquire_dnbr.py; never reconstructed):
  NBR  = (NIR - SWIR) / (NIR + SWIR)           [dimensionless surface reflectance]
  dNBR = NBR_pre - NBR_post                    [raw scale ~ -0.5..+1.3, NEVER x1000;
                                                positive = burned]
  Sentinel-2: NIR = B8A (nir08), SWIR = B12 (swir22), both 20 m.
      SR = (DN - 1000) / 10000  -- BOA_ADD_OFFSET -1000 applies to processing
      baseline >= 04.00 (in operations since 25 Jan 2022); the baseline is ASSERTED
      numerically (the STAC field is a string -- verified live 2026-07-17) and older
      products fail loud: this tool screens recent fires; pre-2022 is unsupported.
  Landsat 8/9: NIR = B5 (nir08), SWIR2 = B7 (swir22), both 30 m.
      SR = DN * 0.0000275 - 0.2  (Collection-2 Level-2; the additive offset means a
      ratio on raw DN is WRONG -- scale before any ratio). Fill: DN 0 / QA bit 0.
  Masking (in-product, union of bad pixels -> NoData; RATIFIED 2026-07-17 -- a
  knowing divergence from the p2/p3 validation lineage's fill-bit-only handling):
      S2: SCL classes [0,1,3,6,8,9,10,11]; Landsat: QA_PIXEL bits 1-4 + fill.

Output contract (already satisfied by putah/p2): single-band float32 GeoTIFF,
RAW scale, |dNBR| <= 2 (F1 physical bound), nodata -9999, on the scene's NATIVE
grid. The artifact is gated through acquire.assert_raw_dnbr before this function
returns -- a gate-failing artifact raises GateAbort (fail loud, unlike putah's
non-fatal print).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.grids import GateAbort  # noqa: E402
from autoacquire.scene_select import (  # noqa: E402  (single source for the frozen mask sets)
    S2_BAD_SCL,
    landsat_valid_mask,
    _sign_mpc,
)

# Frozen adapter constants (transcribed from the validated working code).
S2_MIN_BASELINE = 4.0        # assert processing baseline >= 04.00, numeric (pre-reg D)
S2_SR_CLIP = (0.0, 1.6)      # putah_dnbr L2A SR hygiene clip [dimensionless]
S2_DEN_GUARD = 0.02          # putah_dnbr: tiny-denominator NBR blowup guard
LS_SR_SCALE, LS_SR_OFFSET = 0.0000275, -0.2   # p2_acquire_dnbr C2L2 SR scaling
LS_SR_CLIP = (0.0, 1.0)      # p2_acquire_dnbr: standard C2L2 hygiene, not a knob
NODATA = -9999.0             # matches the pipeline's DNBR_NODATA
DNBR_PHYS_CLIP = 2.0         # |dNBR| <= 2 physical bound (F1; NBR in [-1,1])

# Quicklook color ramp (putah_dnbr colorize, transcribed): 6 bins, upper edges.
_QL_EDGES = [-0.10, 0.10, 0.27, 0.44, 0.66, 10]
_QL_COLORS = np.array(
    [[90, 140, 60], [140, 190, 90], [255, 235, 120],
     [240, 150, 45], [210, 60, 30], [140, 20, 20]], dtype="uint8"
)
_QL_NODATA_RGB = (235, 235, 235)


def create_dnbr(pair, bbox, out_dir, *, name="fire"):
    """Approved pair -> raw native-grid dNBR + quicklook + provenance (fail loud).

    pair: {"sensor": "S2"|"Landsat", "pre": candidate, "post": candidate} -- the
    scene_select shapes (grouped candidates supported; members mosaic first-valid-
    wins on the shared native lattice). bbox: (W, S, E, N) lon/lat. Returns
    {"dnbr_tif", "quicklook_png", "provenance_json", "gate_stats"}.
    """
    import rasterio

    sensor = pair["sensor"]
    pre_c, post_c = pair["pre"], pair["post"]
    if pre_c["sensor"] != post_c["sensor"]:
        raise GateAbort(
            f"mixed-sensor pair ({pre_c['sensor']} pre + {post_c['sensor']} post) -- "
            "a pair is internally ONE sensor, never blended (A2/A3)."
        )
    zones = _zones(pre_c) | _zones(post_c)
    if len(zones) > 1:
        raise GateAbort(
            f"pair spans multiple UTM zones {sorted(zones)} -- a cross-UTM-zone fire "
            "is unsupported in v1; aborting loud rather than resampling across zones "
            "(Feature Spec 6B)."
        )

    pre = _read_scene(pre_c, bbox, sensor)
    post = _read_scene(post_c, bbox, sensor)

    # A8 fail loud: pre/post must land on the IDENTICAL native lattice. If this
    # fires the mandated response is STOP and report -- do NOT add a resample
    # (p2_acquire_dnbr pattern; the one resample lives in the frozen ingest).
    if pre["nbr"].shape != post["nbr"].shape or not np.allclose(
        tuple(pre["transform"])[:6], tuple(post["transform"])[:6], atol=1e-6
    ) or pre["crs"] != post["crs"]:
        raise GateAbort(
            "pre/post scenes are not on an identical native grid "
            f"(pre {pre['nbr'].shape} {pre['crs']}, post {post['nbr'].shape} "
            f"{post['crs']}) -- STOP; do NOT resample in creation (A8). The single "
            "resample in this pathway is the frozen both-arms ingest."
        )

    # dNBR = NBR_pre - NBR_post, raw scale, positive = burned (frozen).
    dnbr = (pre["nbr"] - post["nbr"]).astype("float32")
    dnbr = np.clip(dnbr, -DNBR_PHYS_CLIP, DNBR_PHYS_CLIP)  # NaN survives the clip
    bad = pre["bad"] | post["bad"] | ~np.isfinite(dnbr)    # union -> NoData
    arr = np.where(~bad, dnbr, NODATA).astype("float32")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tif = out_dir / f"dnbr_{name}_raw.tif"
    profile = dict(
        driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
        dtype="float32", crs=pre["crs"], transform=pre["transform"],
        nodata=NODATA, compress="deflate",
    )
    with rasterio.open(tif, "w", **profile) as ds:
        ds.write(arr, 1)
        ds.update_tags(
            product=f"{sensor} dNBR (raw)",
            fire=name,
            pre=f"{pre_c['date'].isoformat()};{pre_c['id']}",
            post=f"{post_c['date'].isoformat()};{post_c['id']}",
            method=(
                "dNBR=NBR_pre-NBR_post; NBR=(NIR-SWIR)/(NIR+SWIR); "
                + ("L2A SR (DN-1000)/10000, baseline>=04.00; SCL-masked; raw scale"
                   if sensor == "S2"
                   else "C2L2 SR DN*2.75e-5-0.2; QA fill+cloud-masked; raw scale")
            ),
            note="dNBR UNVALIDATED for ranking (A34); within-fire relative only; "
                 f"nodata={NODATA:.0f}",
        )

    quicklook = out_dir / f"dnbr_{name}_quicklook.png"
    _write_quicklook(np.where(bad, np.nan, dnbr), quicklook)

    # The pipeline's own F1 input gate, run here so a bad artifact can NEVER be
    # handed onward (putah printed failures; the creator raises them).
    import acquire

    gate_stats = acquire.assert_raw_dnbr(str(tif))  # GateAbort on violation

    prov = _provenance(pair, bbox, sensor, pre_c, post_c, pre, arr, gate_stats, name)
    prov_path = out_dir / f"dnbr_{name}_provenance.json"
    prov_path.write_text(json.dumps(prov, indent=2))

    return {
        "dnbr_tif": str(tif),
        "quicklook_png": str(quicklook),
        "provenance_json": str(prov_path),
        "gate_stats": gate_stats,
    }


def _zones(candidate):
    members = candidate.get("items") or [candidate]
    return {m.get("epsg") for m in members if m.get("epsg") is not None}


def _read_scene(candidate, bbox, sensor):
    """One scene (possibly a same-day tile group) -> NBR + bad-pixel mask on its
    NATIVE windowed grid. Whole-pixel window snap per member; every member/band
    must share the base lattice or the read aborts (never resampled)."""
    if sensor == "S2":
        _assert_s2_baseline(candidate)
        band_keys, mask_key = ("nir08", "swir22"), "scl"
        mask_fill = 0        # SCL 0 = nodata class -> bad
    else:
        band_keys, mask_key = ("nir08", "swir22"), "qa_pixel"
        mask_fill = 1        # QA bit 0 set = fill -> bad

    members = candidate.get("items") or [candidate]
    base = None  # (transform, crs, height, width)
    nir_dn = swir_dn = mask = None
    for m in members:
        got = {}
        for key in (*band_keys, mask_key):
            arr, transform, crs = _read_band_window(m, bbox, key, candidate["id"])
            if base is None:
                base = (transform, crs, arr.shape[0], arr.shape[1])
            else:
                if crs != base[1] or arr.shape != (base[2], base[3]) or not np.allclose(
                    tuple(transform)[:6], tuple(base[0])[:6], atol=1e-6
                ):
                    raise GateAbort(
                        f"scene {candidate['id']}: asset {key} of member {m['id']} is "
                        "not on the scene's shared native lattice -- grid contract "
                        "violation; STOP, do NOT resample (A8)."
                    )
            got[key] = arr
        # First-valid-wins mosaic across same-day member tiles (DN 0 = fill).
        if nir_dn is None:
            nir_dn, swir_dn = got[band_keys[0]], got[band_keys[1]]
            mask = got[mask_key]
        else:
            have = nir_dn > 0
            take = ~have & (got[band_keys[0]] > 0)
            nir_dn = np.where(take, got[band_keys[0]], nir_dn)
            swir_dn = np.where(take, got[band_keys[1]], swir_dn)
            mask_have = (mask != mask_fill) if sensor == "S2" else ((mask & 1) == 0)
            mask = np.where(~mask_have, got[mask_key], mask)

    nir_dn = nir_dn.astype("float64")
    swir_dn = swir_dn.astype("float64")

    if sensor == "S2":
        # Fill first: DN 0 predates the -1000 offset math (putah src_nodata=0).
        fill = (nir_dn <= 0) | (swir_dn <= 0)
        nir = np.clip((nir_dn - 1000.0) / 10000.0, *S2_SR_CLIP)
        swir = np.clip((swir_dn - 1000.0) / 10000.0, *S2_SR_CLIP)
        den = nir + swir
        with np.errstate(invalid="ignore", divide="ignore"):
            nbr = np.where(den > S2_DEN_GUARD, (nir - swir) / den, np.nan)
        bad = fill | np.isin(mask.astype(np.uint8), np.array(S2_BAD_SCL, dtype=np.uint8))
    else:
        qa_u = mask.astype(np.uint16)
        fill = (nir_dn <= 0) | (swir_dn <= 0) | ((qa_u & 1).astype(bool))
        nir = np.clip(nir_dn * LS_SR_SCALE + LS_SR_OFFSET, *LS_SR_CLIP)
        swir = np.clip(swir_dn * LS_SR_SCALE + LS_SR_OFFSET, *LS_SR_CLIP)
        den = nir + swir
        valid = (~fill) & (den > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            nbr = np.where(valid, (nir - swir) / np.where(den > 0, den, 1.0), np.nan)
        # In-product cloud mask (RATIFIED divergence from p2/p3 fill-bit-only QC).
        bad = fill | ~landsat_valid_mask(qa_u)

    return {
        "nbr": nbr.astype("float32"),
        "bad": bad,
        "transform": base[0],
        "crs": base[1],
    }


def _assert_s2_baseline(candidate):
    """Numeric baseline >= 04.00 for every member; fail loud on older (or absent).
    The -1000 BOA offset attaches to the PROCESSING BASELINE, not the acquisition
    date -- reading the field is the robust check (pre-reg D)."""
    members = candidate.get("items") or [candidate]
    for m in members:
        raw = m.get("processing_baseline")
        try:
            num = float(raw)
        except (TypeError, ValueError):
            num = None
        if num is None or num < S2_MIN_BASELINE:
            raise GateAbort(
                f"scene {m.get('id', candidate['id'])} has Sentinel-2 processing "
                f"baseline {raw!r} -- the frozen SR offset (-1000) requires baseline "
                ">= 04.00. This tool screens recent fires; pre-2022 baselines are "
                "unsupported (fail loud, not silently wrong)."
            )


def _read_band_window(member, bbox, key, group_id):
    """One member's band, windowed on ITS native grid (whole-pixel snap, boundless
    fill 0 = both sensors' fill DN). Returns (array, window_transform, crs_str)."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    href = (member.get("assets") or {}).get(key)
    if not href:
        raise GateAbort(f"scene {group_id} has no {key} asset -- cannot build a dNBR (A8).")
    if member["sensor"] != "S2" and not Path(str(href)).exists():
        href = _sign_mpc(href)  # MPC assets need SAS signing (local test files don't)
    try:
        with rasterio.open(href) as ds:
            wsen = transform_bounds("EPSG:4326", ds.crs, *bbox, densify_pts=21)
            win = from_bounds(*wsen, transform=ds.transform)
            # Snap to whole pixels so pre/post land on the identical native grid
            # (p2_acquire_dnbr pattern, verbatim rounding rule).
            win = Window(
                int(round(win.col_off)), int(round(win.row_off)),
                int(round(win.width)), int(round(win.height)),
            )
            arr = ds.read(1, window=win, boundless=True, fill_value=0)
            return arr, ds.window_transform(win), ds.crs.to_string()
    except (rasterio.errors.RasterioError, OSError) as e:
        raise GateAbort(
            f"band read failed for scene {group_id} ({key}): {type(e).__name__}: {e} (A8)"
        ) from e


def _write_quicklook(dnbr_nan, out_path):
    """putah_dnbr's 6-bin quicklook (transcribed): NaN -> light gray."""
    from PIL import Image

    a = dnbr_nan
    out = np.zeros((*a.shape, 3), dtype="uint8")
    prev = -10.0
    for e, col in zip(_QL_EDGES, _QL_COLORS):
        sel = np.isfinite(a) & (a > prev) & (a <= e)
        out[sel] = col
        prev = e
    out[~np.isfinite(a)] = _QL_NODATA_RGB
    Image.fromarray(out, "RGB").save(out_path)


def _provenance(pair, bbox, sensor, pre_c, post_c, pre_read, arr, gate_stats, name):
    from src.outputs import DNBR_FRAMING, SCREENING_STATEMENT

    valid = arr != NODATA
    vals = arr[valid]
    if sensor == "S2":
        bands = {"NIR": "B8A (nir08, 20 m)", "SWIR": "B12 (swir22, 20 m)"}
        scaling = "(DN - 1000)/10000 -- BOA offset -1000, baseline >= 04.00 asserted"
        maskdesc = f"SCL union {list(S2_BAD_SCL)} -> NoData (in-product)"
    else:
        bands = {"NIR": "B5 (nir08, 30 m)", "SWIR2": "B7 (swir22, 30 m)"}
        scaling = "DN * 0.0000275 - 0.2 (C2 L2 surface reflectance)"
        maskdesc = ("QA_PIXEL fill bit 0 + bits 1-4 union -> NoData "
                    "(in-product; ratified pre-reg 2026-07-17)")
    return {
        "created_by": "dnbr_create.create_dnbr (AA-2, pre-reg RATIFIED 2026-07-17)",
        "fire": name,
        "sensor": sensor,
        "bbox_lonlat": list(bbox),
        "scenes": {
            "pre": _scene_entry(pre_c),
            "post": _scene_entry(post_c),
        },
        "bands": bands,
        "sr_scaling": scaling,
        "nbr_formula": "(NIR - SWIR)/(NIR + SWIR), on scaled surface reflectance",
        "dnbr_formula": "NBR_pre - NBR_post",
        "scale": "raw (NOT x1000)",
        "mask": maskdesc,
        "native_crs": pre_read["crs"],
        "native_res_m": abs(pre_read["transform"].a),
        "native_transform": list(pre_read["transform"])[:6],
        "nodata": NODATA,
        "dnbr_stats_raw": {
            "min": float(vals.min()) if vals.size else None,
            "max": float(vals.max()) if vals.size else None,
            "mean": float(vals.mean()) if vals.size else None,
            "valid_frac": round(float(valid.sum()) / valid.size, 4),
        },
        "gate_stats": gate_stats,
        "framing": {"screening": SCREENING_STATEMENT, "dnbr": DNBR_FRAMING},
    }


def _scene_entry(c):
    members = c.get("items") or [c]
    return {
        "id": c["id"],
        "date": c["date"].isoformat(),
        "tile_cloud_pct": c.get("tile_cloud_pct"),
        "processing_baseline": c.get("processing_baseline"),
        "member_items": [m["id"] for m in members],
    }
