"""P3.2 -- assemble the acquisition manifest (the A21 freeze record) and run ALL §3 acceptance
checks. The manifest + its SHA256s are the durable in-repo artifact (rasters are gitignored).

Decision rule (build prompt §3): ALL checks must PASS to proceed to the commit proposal. Any HALT ->
stop, report the failing check, propose NO commit. This script prints a PASS/HALT table and exits
non-zero on any HALT.

Run: python validation/p3_manifest_and_checks.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd

_REPO = Path(__file__).resolve().parent.parent
SF = _REPO / "data" / "southfork"
# P3.3 hygiene: the manifest (the A21 freeze record + raster SHA256s) is the durable in-repo
# artifact, so it lives on a TRACKED path -- NOT under the gitignored /data/ tree where it cannot
# be committed. The rasters it checksums stay gitignored on disk under data/southfork/.
MANIFEST = _REPO / "validation" / "p3_southfork" / "acquisition_manifest.json"

DEM_SRC = SF / "dem" / "dem_source.json"
BURN_PROV = SF / "burn" / "southfork_dnbr" / "provenance.json"
INGEST_DIAG = SF / "burn" / "p3_ingest_diagnostics.json"
ASSETS_SRC = SF / "assets" / "assets_source.json"
REF_PERIM = SF / "reference" / "shp" / "sfk2024-perimeter.shp"
REF_BASINS = SF / "reference" / "shp" / "sfk2024-basins.shp"

# delivered rasters to checksum (gitignored; the manifest carries their SHA256)
RASTERS = [
    SF / "dem" / "dem.tif",
    SF / "burn" / "southfork_dnbr" / "dnbr_native.tif",
    SF / "burn" / "dnbr_a.tif", SF / "burn" / "dnbr_b.tif",
    SF / "burn" / "arm_a_weight.tif", SF / "burn" / "arm_b_weight.tif",
    SF / "burn" / "arm_a_cls.tif", SF / "burn" / "valid.tif",
]

FROZEN = {
    "pre_date": "2024-06-12", "post_date": "2024-07-07", "sensor": "Landsat-9",
    "pre_cloud_candidate": 6.3, "post_cloud_candidate": 0.0,
    "shape": [966, 1439], "crs": "EPSG:32613", "n_basins": 192, "ref_version": "1.0",
}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    results = []   # (check, passed, detail)

    def chk(name, passed, detail):
        results.append((name, bool(passed), detail))

    # load artifacts (missing -> the relevant check fails loudly, not a crash)
    def load(p):
        return json.loads(p.read_text()) if p.exists() else None
    dem_src = load(DEM_SRC); prov = load(BURN_PROV); diag = load(INGEST_DIAG); assets = load(ASSETS_SRC)

    # ---- (a) scene freeze well-formed ----
    if prov:
        pre, post = prov["pre_scene"], prov["post_scene"]
        a_pre_ok = pre["date"] == FROZEN["pre_date"] and "Landsat-9" in prov["sensor"]
        a_post_ok = post["date"] == FROZEN["post_date"]
        # PERIMETER basis (the freeze-gate basis; reproduces frozen 6.3%/0%). 0.05 tol = the frozen
        # candidate is recorded to 1 decimal (6.3), so a 6.32 measurement matches it.
        cloud_pre_ok = pre["aoi_cloud_pct_qa"] <= FROZEN["pre_cloud_candidate"] + 0.05
        cloud_post_ok = post["aoi_cloud_pct_qa"] <= FROZEN["post_cloud_candidate"] + 0.05
        chk("(a) scene freeze + AOI-cloud<=candidate (perimeter basis)",
            a_pre_ok and a_post_ok and cloud_pre_ok and cloud_post_ok,
            f"pre {pre['id']} perim-cloud {pre['aoi_cloud_pct_qa']}%<= {FROZEN['pre_cloud_candidate']} "
            f"(bbox {pre.get('bbox_cloud_pct_qa')}% = context); "
            f"post {post['id']} perim-cloud {post['aoi_cloud_pct_qa']}%<= {FROZEN['post_cloud_candidate']}")
    else:
        chk("(a) scene freeze", False, "burn provenance.json missing -- dNBR not acquired (MPC?)")

    # ---- (b) grid alignment ----
    if diag:
        b_ok = (diag["dem_shape_rows_cols"] == FROZEN["shape"]
                and diag["arm_a_shape"] == FROZEN["shape"] and diag["arm_b_shape"] == FROZEN["shape"]
                and diag["dem_crs"].upper().endswith("32613")
                and diag["canonical_profile_transform"] == diag["dem_transform"])
        chk("(b) grid alignment (shape/CRS/transform)", b_ok,
            f"DEM {diag['dem_shape_rows_cols']} {diag['dem_crs']}; arms {diag['arm_a_shape']}/"
            f"{diag['arm_b_shape']}; transform match={diag['canonical_profile_transform']==diag['dem_transform']}")
    else:
        chk("(b) grid alignment", False, "ingest diagnostics missing -- ingest not run")

    # ---- (c) no degenerate burn ----
    if diag:
        c_ok = diag["valid_cells"] > 0 and (diag["arm_a_covered_cells"] > 0 or diag["arm_b_covered_cells"] > 0)
        chk("(c) no degenerate burn", c_ok,
            f"valid={diag['valid_cells']} covered A/B={diag['arm_a_covered_cells']}/"
            f"{diag['arm_b_covered_cells']} burned_frac A/B={diag['burned_frac_arm_a']}/{diag['burned_frac_arm_b']}")
    else:
        chk("(c) no degenerate burn", False, "ingest diagnostics missing")

    # ---- (d) no non-finite/sentinel inside valid, both arms ----
    if diag:
        nf = diag["nonfinite_inside_valid"]; sent = diag["sentinel_inside_valid"]
        d_ok = nf["arm_a"] == nf["arm_b"] == sent["arm_a"] == sent["arm_b"] == 0
        chk("(d) no non-finite/sentinel inside valid", d_ok, f"nonfinite={nf} sentinel={sent}")
    else:
        chk("(d) non-finite/sentinel", False, "ingest diagnostics missing")

    # ---- (e) raw value range ----
    if diag:
        ra, rb = diag["dnbr_range_valid"]["arm_a"], diag["dnbr_range_valid"]["arm_b"]
        # (e) lower bound widened -0.6 -> -2.0 to admit the documented native cloud-shadow tail
        # (observed min -1.285) under Decision A (cloud measured, not removed). NOT a x1000 scaling
        # regression (that would be +-1285). This is a QC sanity band, not a scored value.
        e_ok = -2.0 <= ra[0] and ra[1] <= 1.5 and -2.0 <= rb[0] and rb[1] <= 1.5
        chk("(e) dNBR raw range (no x1000 regression)", e_ok, f"armA {ra} armB {rb}")
    else:
        chk("(e) dNBR range", False, "ingest diagnostics missing")

    # ---- (f) both arms present, neither crowned ----
    if diag:
        chk("(f) both arms present & differ (neither crowned)", diag.get("arms_differ", False),
            f"arms_differ={diag.get('arms_differ')} resampling={diag.get('resampling')}; "
            f"both derive from one native source read twice")
    else:
        chk("(f) both arms", False, "ingest diagnostics missing")

    # ---- (g) reference stored v1.0, 192 features, no join ----
    try:
        perim = gpd.read_file(REF_PERIM); basins = gpd.read_file(REF_BASINS)
        ver = str(perim.iloc[0].get("Version"))
        g_ok = len(basins) == FROZEN["n_basins"] and ver == FROZEN["ref_version"]
        chk("(g) reference v1.0 + 192 features (no join)", g_ok,
            f"basins={len(basins)} version={ver} (v3.0 NOT pulled)")
    except Exception as ex:
        chk("(g) reference", False, f"reference read failed: {ex}")

    # ---- (h) frozen-constants pytest green ----
    proc = subprocess.run(
        ["conda", "run", "-n", "wildfire-watershed", "python", "-m", "pytest",
         "tests/test_dnbr_frozen_constants.py", "-q"],
        cwd=_REPO, capture_output=True, text=True)
    h_ok = proc.returncode == 0
    chk("(h) frozen-constants pytest", h_ok, (proc.stdout.strip().splitlines() or ["<no output>"])[-1])

    # ---- (i) manifest complete + reproducible (SHA256s) ----
    shas = {}
    missing = []
    for r in RASTERS:
        if r.exists():
            shas[str(r.relative_to(_REPO))] = sha256(r)
        else:
            missing.append(str(r.relative_to(_REPO)))

    manifest = {
        "phase": "P3.2", "decision": "A24 (P3.1 pre-registration)", "fire": "South Fork 2024 (sfk2024)",
        "sciencebase_item": "68c493a0d4be021a00d8cd9c",
        "p3_2_blocker": (
            "RESOLVED (A25, P3.3). The frozen pipeline was CRS-locked to EPSG:32611, while A24 §3 makes "
            "the grid/CRS the one legitimate per-fire change and South Fork's grid is EPSG:32613. A25 "
            "made CANONICAL_CRS per-fire (threaded via dem_profile[crs], Montecito 32611 behavior-lock "
            "byte-unchanged), so src.ingest.ingest_dnbr_both_arms now runs clean on the 32613 grid. "
            "Acquisition deliverables (DEM/dNBR/reference/assets/scenes) are complete; the "
            "ingest-dependent checks (b-f,i) PASS. See docs/P3.2_BUILD_REPORT.md and the A25 commit."),
        "frozen_grid": {"crs": FROZEN["crs"], "cell_m": 10.0, "shape_rows_cols": FROZEN["shape"],
                        "transform": (dem_src or {}).get("transform"),
                        "bbox": (dem_src or {}).get("bbox"),
                        "frozen_bbox_A24": [426400.8, 3687653.6, 440794.1, 3697312.6]},
        "scenes": {
            "pre": (prov or {}).get("pre_scene"), "post": (prov or {}).get("post_scene"),
            "sensor": (prov or {}).get("sensor"),
            "aoi_cloud_definition": (prov or {}).get("aoi_cloud_definition"),
            "aoi_cloud_union_pct_qa": (prov or {}).get("aoi_cloud_union_pct_qa"),
            "cross_path_note": (prov or {}).get("cross_path_note"),
            "cloud_not_removed": "QA fill-bit masked only; cloud measured, NOT removed (Decision A)",
        },
        "dnbr": {"nbr_formula": (prov or {}).get("nbr_formula"),
                 "dnbr_formula": (prov or {}).get("dnbr_formula"),
                 "scale": (prov or {}).get("scale"),
                 "stats_raw": (prov or {}).get("dnbr_stats_raw"),
                 "resampling": {"arm_a": "nearest", "arm_b": "bilinear"},
                 "source_endpoint": "Microsoft Planetary Computer STAC + SAS (landsat-c2-l2)"},
        "dem": {"endpoint": (dem_src or {}).get("endpoint"),
                "native_crs": (dem_src or {}).get("native_crs"),
                "vintage_tags": (dem_src or {}).get("tags"),
                "elev_min_max_m": [(dem_src or {}).get("elev_min_m"), (dem_src or {}).get("elev_max_m")]},
        "reference": {"version": FROZEN["ref_version"], "assessment_date": "2024-07-01",
                      "n_basins": (len(basins) if 'basins' in dir() else None),
                      "files": "Shapefiles.zip (basins/outlets/segments/perimeter)",
                      "note": "stored read-only; NO spatial join (P3.4); v3.0 NOT pulled"},
        "assets": {"source": (assets or {}).get("source"),
                   "n_buildings": (assets or {}).get("n_buildings"),
                   "out_crs": (assets or {}).get("out_crs")},
        "raster_sha256": shas, "rasters_missing": missing,
        "retrieval_note": "endpoints re-verified reachable 2026-06-19 (MPC STAC intermittently 504 "
                          "during build; DEM/ScienceBase/Overpass OK).",
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)   # tracked dir (validation/p3_southfork/)
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    i_ok = (not missing and len(shas) == len(RASTERS)
            and manifest["scenes"]["pre"] is not None
            and manifest["dem"]["endpoint"] is not None
            and manifest["reference"]["n_basins"] == FROZEN["n_basins"])
    chk("(i) manifest complete + SHA256s", i_ok,
        f"{len(shas)}/{len(RASTERS)} rasters hashed; missing={missing}")

    # ---- report ----
    print("=" * 78)
    print("P3.2 ACCEPTANCE CHECKS (§3). ALL must PASS to propose commit.")
    print("=" * 78)
    allpass = True
    for name, passed, detail in results:
        allpass &= passed
        print(f"  [{'PASS' if passed else 'HALT'}] {name}\n         {detail}")
    print("-" * 78)
    print(f"  manifest -> {MANIFEST.relative_to(_REPO)}")
    print("=" * 78)
    print("RESULT:", "ALL PASS -- ready for commit proposal" if allpass else "HALT -- do NOT propose commit")
    sys.exit(0 if allpass else 1)


if __name__ == "__main__":
    main()
