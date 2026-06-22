"""P3.2 -- ingest the native South Fork dNBR into BOTH arms via the FROZEN seam, persist the
canonical-grid rasters, and run the acquisition-time structural acceptance checks.

⛔ ACQUISITION ONLY (A24 §0 firewall). This calls the frozen src.ingest.ingest_dnbr_both_arms
UNMODIFIED and writes the resulting rasters. It does NOT score, rank, delineate, or touch basins --
the first P3 score does not exist until P3.4. NO gate.run_pipeline, NO stage_2e_score here (that is
the P2.2b harness; P3.2 deliberately stops before it).

The frozen guards run INSIDE ingest_dnbr_both_arms: assert_aligned ×2 (both arms snapped to the DEM
grid) and the non-finite/sentinel guards (ingest.py:264-269). If any fires, ingest raises GateAbort
and this harness aborts loudly (A8) -- that is a correct STOP, not something to paper over.

Writes to data/southfork/burn/:
  dnbr_a.tif (nearest reproject), dnbr_b.tif (bilinear reproject), arm_a_weight.tif, arm_b_weight.tif,
  arm_a_cls.tif (SBS-equivalent class), valid.tif (shared footprint).
Both arms recorded; NEITHER crowned (A24 §4). Writes p3_ingest_diagnostics.json for the manifest.

Run: python validation/p3_ingest_dnbr.py   (after p3_acquire_dnbr.py has produced the native dNBR)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import rasterio

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.grids import GateAbort                              # noqa: E402
from src.ingest import ingest_dnbr_both_arms, DNBR_NODATA, DNBR_FLOOR  # noqa: E402

DEM = _REPO / "data" / "southfork" / "dem" / "dem.tif"
NATIVE_DNBR = _REPO / "data" / "southfork" / "burn" / "southfork_dnbr" / "dnbr_native.tif"
OUT_DIR = _REPO / "data" / "southfork" / "burn"
DIAG = OUT_DIR / "p3_ingest_diagnostics.json"

FROZEN_SHAPE = (966, 1439)   # A24 §3 canonical grid (rows, cols)
FROZEN_CRS = "EPSG:32613"


def _write(path, arr, profile, dtype, nodata=None):
    p = dict(profile)
    p.update(dtype=dtype, count=1, compress="deflate")
    if nodata is not None:
        p["nodata"] = nodata
    else:
        p.pop("nodata", None)
    with rasterio.open(path, "w", **p) as d:
        d.write(np.asarray(arr).astype(dtype), 1)


def main():
    with rasterio.open(DEM) as d:
        dem_profile = d.profile
        dem_shape = (d.height, d.width)
        dem_crs = str(d.crs)
        dem_transform = tuple(d.transform)[:6]

    # ---- the frozen seam (guards run inside; GateAbort on misalignment / non-finite leak) ----
    D = ingest_dnbr_both_arms(NATIVE_DNBR, dem_profile)
    valid = D["valid"]
    prof = D["profile"]                      # canonical dNBR profile (DEM grid, float32, nodata=-9999)

    # ---- persist canonical-grid arm rasters (both arms; neither crowned, A24 §4) ----
    base = {"driver": "GTiff", "height": dem_shape[0], "width": dem_shape[1],
            "crs": dem_profile["crs"], "transform": dem_profile["transform"]}
    _write(OUT_DIR / "dnbr_a.tif", D["dnbr_a"], base, "float32", DNBR_NODATA)
    _write(OUT_DIR / "dnbr_b.tif", D["dnbr_b"], base, "float32", DNBR_NODATA)
    _write(OUT_DIR / "arm_a_weight.tif", D["arm_a"]["wt"], base, "float32")
    _write(OUT_DIR / "arm_b_weight.tif", D["arm_b"]["wt"], base, "float32")
    _write(OUT_DIR / "arm_a_cls.tif", D["arm_a"]["cls"], base, "int16")
    _write(OUT_DIR / "valid.tif", valid, base, "uint8")

    # ---- acquisition-time acceptance diagnostics (§3 b/c/d/e/f) ----
    dnbr_a, dnbr_b = D["dnbr_a"], D["dnbr_b"]
    cov_a, cov_b = D["arm_a"]["covered"], D["arm_b"]["covered"]

    # (d) non-finite / sentinel INSIDE valid, per arm -- must be 0 (mirrors ingest.py:264-269)
    def _leak(a):
        inside = a[valid]
        return int((~np.isfinite(inside)).sum()), int((inside == DNBR_NODATA).sum())
    nf_a, sent_a = _leak(dnbr_a)
    nf_b, sent_b = _leak(dnbr_b)

    # (e) value range over valid (raw scale ~[-0.5, 1.3]; HALT if ~×1000)
    rng_a = [float(np.min(dnbr_a[valid])), float(np.max(dnbr_a[valid]))]
    rng_b = [float(np.min(dnbr_b[valid])), float(np.max(dnbr_b[valid]))]

    diag = {
        "dem_shape_rows_cols": list(dem_shape), "dem_crs": dem_crs, "dem_transform": list(dem_transform),
        "arm_a_shape": list(np.shape(D["arm_a"]["wt"])), "arm_b_shape": list(np.shape(D["arm_b"]["wt"])),
        "canonical_profile_crs": str(prof["crs"]), "canonical_profile_transform": list(prof["transform"])[:6],
        "valid_cells": int(valid.sum()), "total_cells": int(valid.size),
        "valid_frac": round(float(valid.mean()), 4),
        "nodata_cells": int((~valid).sum()),
        "arm_a_covered_cells": int(cov_a.sum()), "arm_b_covered_cells": int(cov_b.sum()),
        "burned_frac_arm_a": round(float(cov_a.sum()) / int(valid.sum()), 4) if valid.sum() else 0.0,
        "burned_frac_arm_b": round(float(cov_b.sum()) / int(valid.sum()), 4) if valid.sum() else 0.0,
        "nonfinite_inside_valid": {"arm_a": nf_a, "arm_b": nf_b},
        "sentinel_inside_valid": {"arm_a": sent_a, "arm_b": sent_b},
        "dnbr_range_valid": {"arm_a": rng_a, "arm_b": rng_b},
        "dnbr_floor": DNBR_FLOOR,
        "arms_differ": bool(not np.array_equal(D["arm_a"]["wt"], D["arm_b"]["wt"])),
        "resampling": {"arm_a": "nearest", "arm_b": "bilinear"},
    }
    DIAG.write_text(json.dumps(diag, indent=2))

    # ---- print acceptance summary (b/c/d/e/f) ----
    print("=" * 78)
    print("P3.2 dNBR ingest (BOTH arms via frozen seam). Acquisition only -- no scoring (A24 §0).")
    print("=" * 78)
    print(f"[ingest] frozen guards passed (assert_aligned ×2, non-finite/sentinel) -- no GateAbort.")
    okb = (tuple(dem_shape) == FROZEN_SHAPE and dem_crs.upper().endswith("32613")
           and np.shape(D["arm_a"]["wt"]) == dem_shape and np.shape(D["arm_b"]["wt"]) == dem_shape)
    print(f"(b) grid alignment: DEM {dem_shape} {dem_crs}; arms match DEM shape -> {'PASS' if okb else 'HALT'}")
    okc = valid.sum() > 0 and (cov_a.sum() > 0 or cov_b.sum() > 0)
    print(f"(c) no degenerate burn: valid={int(valid.sum())} covered A/B={int(cov_a.sum())}/{int(cov_b.sum())}"
          f" -> {'PASS' if okc else 'HALT'}")
    okd = (nf_a == sent_a == nf_b == sent_b == 0)
    print(f"(d) no non-finite/sentinel inside valid: A(nf={nf_a},sent={sent_a}) B(nf={nf_b},sent={sent_b})"
          f" -> {'PASS' if okd else 'HALT'}")
    # (e) lower bound widened -0.6 -> -2.0 to admit the documented native cloud-shadow tail
    # (observed min -1.285) under Decision A (cloud measured, not removed). NOT a x1000 scaling
    # regression (that would be +-1285). This is a QC sanity band, not a scored value.
    oke = (-2.0 <= rng_a[0] and rng_a[1] <= 1.5 and -2.0 <= rng_b[0] and rng_b[1] <= 1.5)
    print(f"(e) dNBR raw range A{[round(x,3) for x in rng_a]} B{[round(x,3) for x in rng_b]}"
          f" -> {'PASS' if oke else 'HALT (scaling regression?)'}")
    okf = diag["arms_differ"]
    print(f"(f) both arms present, differ (neither crowned): arms_differ={okf} -> {'PASS' if okf else 'HALT'}")
    print(f"[wrote] {DIAG.relative_to(_REPO)} + 6 canonical rasters under {OUT_DIR.relative_to(_REPO)}/")
    if not (okb and okc and okd and oke and okf):
        raise SystemExit("HALT: one or more acquisition acceptance checks failed (see above).")


if __name__ == "__main__":
    try:
        main()
    except GateAbort as exc:
        print(f"\nGATE ABORT (fail-loud, A8): {exc}", file=sys.stderr)
        sys.exit(2)
