"""pipeline.py -- run_pipeline: the single stage wiring (2a hydrology -> 2b outlets ->
2c delineate -> 2d slope -> 2e score -> 2f truth) + the A39 terrain router (incised terrain
routes delineation through src/subbasins.py). Shared by run.py and validation/gate.py.

All distances metric. Fail loud, never degrade.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
from shapely.geometry import Point

from src.config import (
    TRUTH_MATCH_M,
    CANONICAL_CRS,
    CELL_M,
    MASTER_MIN_AOI_FRACTION,
    DIRMAP,
    DNBR_NODATA_FAILLOUD_FRAC,
    CONTOUR_M,
)
# NOTE: tests monkeypatch THIS module's binding of assert_aligned -- keep the by-name import.
from src.grids import GateAbort, _assert_metric_crs, _rc_to_xy, assert_aligned
from src.ingest import load_dem, load_assets, load_creeks, ingest_burn, ingest_dnbr_both_arms
from src.hydrology import run_hydrology
from src.delineate import (stage_2b_outlets, stage_2c_delineate, assert_contour_in_dem_range,
                           assess_hypsometric_applicability, _valid_dem_mask)
from src.score import stage_2e_score

_log = logging.getLogger(__name__)

CELL_AREA_KM2  = (CELL_M * CELL_M) / 1.0e6   # m^2 per cell -> km^2

# Reconstruction I/O anchors: this file lives in src/, but the Montecito data lives under
# <repo>/validation/ (A16), so ROOT anchors off the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = _REPO_ROOT / "validation"
DATA = ROOT / "data"
OUT  = ROOT / "out"
DEM_TIF, SBS_TIF = DATA / "dem.tif", DATA / "sbs.tif"
ASSETS_GJ, CREEKS_GJ = DATA / "assets.geojson", DATA / "creeks.geojson"

# Per-fire dicts carry I/O + provenance ONLY (A30); every analytical scalar stays in config.py.
# MONTECITO_FIRE is run_pipeline()'s no-arg default (the behavior-lock case).
MONTECITO_FIRE = {
    "name": "montecito",
    "dem": DEM_TIF, "sbs": SBS_TIF, "assets": ASSETS_GJ, "creeks": CREEKS_GJ,
    "out_dir": OUT, "expected_crs": CANONICAL_CRS,
    "validation_case": "Thomas_Fire_2017/Montecito_2018",
}

# South Fork Fire 2024 (UTM 13N): the incised-terrain case; dNBR-only by design (sbs=None).
# Data gitignored -- registered but data-absent on a clean checkout (see run._assert_inputs_present).
_SOUTHFORK_DATA = _REPO_ROOT / "data" / "southfork"
SOUTHFORK_FIRE = {
    "name": "southfork",
    "dem": _SOUTHFORK_DATA / "dem" / "dem.tif",
    "sbs": None,                                    # dNBR-only fire; no SBS by design (A31, A29)
    # A39: incised terrain now ROUTES to the WBT sub-basin dNBR both-arms path instead of refusing, so
    # South Fork needs its native dNBR burn input (grid-matched to the DEM).
    "dnbr": _SOUTHFORK_DATA / "burn" / "southfork_dnbr" / "dnbr_native.tif",
    "assets": _SOUTHFORK_DATA / "assets" / "osm_buildings_32613.gpkg",   # loaded only on the range-front path; incised skips it
    "creeks": None,                                 # no tool-format truth-creek layer for South Fork
    "out_dir": OUT / "southfork",
    "expected_crs": "EPSG:32613",
    "validation_case": "South_Fork_Fire_2024",
}

# Montecito through the dNBR both-arms path (sbs=None) -- reproduces the committed swap-test
# oracle (Arm A: San Ysidro #1 / Cold Spring #2; Arm B: Cold Spring #1; AUC 0.9722 both arms).
_MONTECITO_DNBR = OUT / "montecito_dnbr" / "dnbr_native.tif"
MONTECITO_DNBR_FIRE = {
    "name": "montecito_dnbr",
    "dem": DEM_TIF, "sbs": None, "dnbr": _MONTECITO_DNBR,
    "assets": ASSETS_GJ, "creeks": CREEKS_GJ,
    "out_dir": OUT / "montecito_dnbr" / "pipeline",
    "expected_crs": CANONICAL_CRS,
    "validation_case": "Thomas_Fire_2017/Montecito_2018 (dNBR both-arms)",
}


def _load_dem_artifacts(fire):
    """Read the DEM ONCE, before SBS/hydrology, so the terrain gate runs on the raw DEM first
    (A31). Returns {grid, dem, dem_raw (m), dem_nodata, profile, transform}."""
    with rasterio.open(fire["dem"]) as dsrc:
        dem_profile = dsrc.profile
        dem_transform = dsrc.transform
    grid, dem, dem_raw = load_dem(fire["dem"])
    return {"grid": grid, "dem": dem, "dem_raw": dem_raw, "dem_nodata": dem.nodata,
            "profile": dem_profile, "transform": dem_transform}


def stage_2a_hydrology(fire, dem_artifacts=None):
    """Align the SBS (if any) to the DEM grid, run the pysheds flow chain, detect the master
    outlet. dem_artifacts comes from _load_dem_artifacts (None = self-load fallback)."""
    if dem_artifacts is None:
        dem_artifacts = _load_dem_artifacts(fire)
    grid, dem, dem_raw = dem_artifacts["grid"], dem_artifacts["dem"], dem_artifacts["dem_raw"]
    dem_nodata, dem_transform = dem_artifacts["dem_nodata"], dem_artifacts["transform"]

    # A dNBR fire (sbs=None) aligns its raster downstream in ingest_dnbr_both_arms; the DEM
    # resolution check runs either way.
    if fire.get("sbs") is not None:
        with rasterio.open(fire["sbs"]) as ssrc:
            assert_aligned(dem_artifacts["profile"], ssrc.profile, expected_crs=fire["expected_crs"])
            if abs(dem_transform.a - CELL_M) > 1e-6 or abs(dem_transform.e + CELL_M) > 1e-6:
                raise GateAbort(f"DEM resolution {(dem_transform.a, dem_transform.e)} != {CELL_M} m.")
    else:
        if abs(dem_transform.a - CELL_M) > 1e-6 or abs(dem_transform.e + CELL_M) > 1e-6:
            raise GateAbort(f"DEM resolution {(dem_transform.a, dem_transform.e)} != {CELL_M} m.")

    fdir, acc = run_hydrology(grid, dem)   # 5-step pysheds chain (fdir/acc Rasters); src/hydrology.py

    acc_arr = np.asarray(acc)
    shape = acc_arr.shape
    if not np.isfinite(acc_arr).all():
        raise GateAbort("Flow accumulation contains non-finite values.")

    # master-outlet = domain pour-point (max-accumulation cell). INDEX mode (FM-1).
    mrow, mcol = np.unravel_index(int(np.argmax(acc_arr)), shape)
    catch = grid.catchment(x=int(mcol), y=int(mrow), fdir=fdir,
                           dirmap=DIRMAP, xytype="index", routing="d8")
    master_km2 = int(np.asarray(catch).sum()) * CELL_AREA_KM2
    valid_area_km2 = int(_valid_dem_mask(dem_raw, dem_nodata).sum()) * CELL_AREA_KM2

    return {"grid": grid, "dem_raw": dem_raw, "dem_nodata": dem_nodata, "fdir_raster": fdir,
            "fdir": np.asarray(fdir), "acc": acc_arr, "transform": dem_transform,
            "shape": shape, "master_rowcol": (int(mrow), int(mcol)),
            "master_acc_cells": int(acc_arr[mrow, mcol]), "master_km2": master_km2,
            "valid_area_km2": valid_area_km2}


def assert_master_outlet_scale(master_km2: float, valid_area_km2: float) -> float:
    """FM-1 scale-free anti-collapse guard (A38): GateAbort unless master_km2 / valid_area_km2
    (both km^2) clears the floor. Lower-only; a collapse detector, not a quality threshold."""
    if not np.isfinite(master_km2) or master_km2 <= 0.0 or valid_area_km2 <= 0.0:
        raise GateAbort(f"Master outlet {master_km2} km^2 / valid AOI {valid_area_km2} km^2 is "
                        "non-finite or non-positive -- delineation collapse (FM-1).")
    fraction = master_km2 / valid_area_km2
    if fraction < MASTER_MIN_AOI_FRACTION:
        raise GateAbort(f"Master outlet {master_km2:.2f} km^2 = {fraction:.1%} of valid AOI "
                        f"{valid_area_km2:.1f} km^2, below the {MASTER_MIN_AOI_FRACTION:.0%} floor -- "
                        "the whole-AOI pour-point drains too little; delineation collapse (FM-1).")
    return fraction


def mean_slope_tan(dem_raw: np.ndarray, dem_nodata=None) -> np.ndarray:
    """Per-cell slope as tan(theta), dimensionless (OWNER-CONFIRMED transform); central
    differences on the raw metric DEM, dx = dy = CELL_M (m)."""
    gy, gx = np.gradient(dem_raw, CELL_M, CELL_M)
    slope = np.hypot(gx, gy)
    valid = _valid_dem_mask(dem_raw, dem_nodata)
    inv = ~valid
    if inv.any():
        # A33/FM-12: a valid cell next to a 0-clamped nodata cell reads a spurious cliff (the
        # gradient consumed the 0-neighbor), so the nodata-adjacent RING is dropped at source.
        adj = np.zeros_like(inv)
        adj[1:, :]  |= inv[:-1, :]
        adj[:-1, :] |= inv[1:, :]
        adj[:, 1:]  |= inv[:, :-1]
        adj[:, :-1] |= inv[:, 1:]
        drop = inv | (valid & adj)
        slope = slope.copy()
        slope[drop] = np.nan          # per-basin mean skips NaN (score.py)
    return slope


def compute_creek_nearest(basins, creeks, transform):
    """For each creek, the nearest basin outlet and its distance (m); ties -> lowest basin_id."""
    ids = [b["basin_id"] for b in basins]
    pts = [Point(*xy) for xy in
           _rc_to_xy(np.array([b["outlet"][0] for b in basins]),
                     np.array([b["outlet"][1] for b in basins]), transform)]
    nearest = {}
    for _, creek in creeks.iterrows():
        geom = creek.geometry
        dists = np.array([geom.distance(p) for p in pts])
        j = int(np.argmin(dists))            # argmin returns first (lowest id) on tie
        nearest[creek["name"]] = {"basin_id": ids[j], "dist_m": float(dists[j])}
    return nearest


def evaluate(basins, ranked, creek_nearest, match_m):
    """Label flowed (creek match <= match_m), compute tercile / #1 / AUC / means."""
    matched = {}   # basin_id -> matched creek name (nearest creek within match_m)
    unmatched = [] # (creek, dist) beyond match_m
    for creek, info in creek_nearest.items():
        if info["dist_m"] <= match_m:
            bid = info["basin_id"]
            # if a basin is the nearest for >1 creek, keep the closest creek name
            if bid not in matched or info["dist_m"] < creek_nearest[matched[bid]]["dist_m"]:
                matched[bid] = creek
        else:
            unmatched.append((creek, info["dist_m"]))

    flowed_ids = set(matched)
    for b in basins:
        b["flowed"] = b["basin_id"] in flowed_ids
        b["matched_creek"] = matched.get(b["basin_id"], "")

    n = len(basins)
    tercile_k = n // 3                                   # floor(n/3); 36 -> 12
    top = [b for b in ranked if b["rank"] <= tercile_k]
    flowed_in_top = sum(1 for b in top if b["flowed"])
    rank1 = ranked[0]
    flowed = [b for b in basins if b["flowed"]]
    nonflowed = [b for b in basins if not b["flowed"]]

    # rank-AUC: strict pairwise concordance (tie -> 0), over the ACTUAL set
    n_pairs = len(flowed) * len(nonflowed)
    concordant, discordant = 0, []
    for f in flowed:
        for nf in nonflowed:
            if f["score"] > nf["score"]:
                concordant += 1
            else:
                discordant.append((f, nf))
    auc = concordant / n_pairs if n_pairs else float("nan")
    # FM-3 signature: every discordant pair is a SMALLER flowed basin outranked by a LARGER one
    disc_fm3 = bool(discordant) and all(f["area_km2"] < nf["area_km2"] for f, nf in discordant)

    return {
        "matched": matched, "unmatched": unmatched,
        "matched_flowed_count": len(flowed_ids), "tercile_k": tercile_k,
        "flowed_in_top": flowed_in_top, "n_flowed": len(flowed),
        "rank1_is_flowed": bool(rank1["flowed"]), "rank1_creek": rank1["matched_creek"] or None,
        "rank1_id": rank1["basin_id"],
        "auc": auc, "n_pairs": n_pairs, "n_discordant": len(discordant),
        "discordant_are_fm3": disc_fm3,
        "discordant": [(f["basin_id"], f["matched_creek"], f["area_km2"], f["score"],
                        nf["basin_id"], nf["area_km2"], nf["score"]) for f, nf in discordant],
        "flowed_mean_score": float(np.mean([b["score"] for b in flowed])) if flowed else float("nan"),
        "nonflowed_mean_score": float(np.mean([b["score"] for b in nonflowed])) if nonflowed else float("nan"),
        "low_coverage_basins": sum(1 for b in basins if b["low_coverage"]),
    }


def _terrain_mode(dem_raw, dem_nodata):
    """A39 -- classify terrain to ROUTE, not to refuse: the A27 detector's verdict selects the
    engine (range-front canyon-mouth vs incised WBT sub-basin)."""
    verdict = assess_hypsometric_applicability(dem_raw, dem_nodata)
    return ("incised" if verdict["refuse"] else "range_front"), verdict


def dispatch_result(result):
    """Dispatch run_pipeline's polymorphic return -> process exit code. "ranked" -> 0;
    "refused" -> print the refusal message, 0 (an honest answer, not a crash); anything
    else RAISES (an un-taught status must never be silently mishandled)."""
    status = result.get("status")
    if status == "ranked":
        return 0
    if status == "refused":
        print("\n" + "=" * 74)
        print("TERRAIN-APPLICABILITY REFUSAL (DECISIONS A27/A27.1) -- no ranking produced")
        print("=" * 74)
        print(result["message"])
        return 0
    raise GateAbort(f"run_pipeline returned unknown status {status!r} -- caller cannot dispatch "
                    "(A8 fail-loud; an un-taught status must never be silently mishandled).")


def _dnbr_nodata_guard(basins, nodata_mask):
    """Fail loud if dNBR NoData/cloud covers > DNBR_NODATA_FAILLOUD_FRAC of any handed basin --
    a clouded scene is a bad scene, not a low-burn finding (A8)."""
    nd = np.asarray(nodata_mask)
    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        frac = float(nd[m].mean()) if ncells else 0.0
        if frac > DNBR_NODATA_FAILLOUD_FRAC:
            raise GateAbort(
                f"dNBR NoData covers {frac:.1%} of basin {b['basin_id']} (> {DNBR_NODATA_FAILLOUD_FRAC:.0%}) "
                "-- a clouded scene is a bad scene, not a low-burn finding (P2.1 §4 path 1, A8).")


def _dnbr_nodata_flags(basins, nodata_mask):
    """Non-fatal companion to _dnbr_nodata_guard: returns [(basin_id, frac), ...] over the
    threshold, never raises -- an under-scored clouded basin is surfaced, never silent."""
    nd = np.asarray(nodata_mask)
    over = []
    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        frac = float(nd[m].mean()) if ncells else 0.0
        if frac > DNBR_NODATA_FAILLOUD_FRAC:
            over.append((b["basin_id"], frac))
    return over


def _partition_refused(basins, nodata_mask):
    """A41: split basins at the FROZEN bar (DNBR_NODATA_FAILLOUD_FRAC, strictly '>');
    attaches b["nodata_frac"] to every record. Refused are never scored/ranked/renumbered."""
    nd = np.asarray(nodata_mask)
    clean, refused = [], []
    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        b["nodata_frac"] = float(nd[m].mean()) if ncells else 0.0
        (refused if b["nodata_frac"] > DNBR_NODATA_FAILLOUD_FRAC else clean).append(b)
    return clean, refused


def _attach_a23_covered_interp(basins, covered_interp):
    """Per-basin covered-interpretation fraction (A23 diagnostic) -- never fed to score/rank."""
    ci = np.asarray(covered_interp)
    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        b["burn_coverage_frac_covered_interp"] = float(ci[m].mean()) if ncells else 0.0


def _score_one_arm(basins_src, wt, covered, slope, creek_nearest, covered_interp):
    """Score one dNBR arm on an independent copy of the shared delineation; only mean_burn
    differs across arms. Returns {ranked, basins, n_ties, metrics}."""
    basins = [dict(b) for b in basins_src]                # shallow copy; the read-only 'mask' ndarray is shared
    ranked, n_ties = stage_2e_score(wt, covered, slope, basins)
    _attach_a23_covered_interp(basins, covered_interp)
    metrics = evaluate(basins, ranked, creek_nearest, TRUTH_MATCH_M) if creek_nearest is not None else None
    return {"ranked": ranked, "basins": basins, "n_ties": n_ties, "metrics": metrics}


def run_pipeline(fire=None, contour_m=None):
    """Run 2a -> 2f for one fire; returns a results dict. fire None -> MONTECITO_FIRE (the
    behavior-lock default); contour_m None -> the frozen config default."""
    fire = fire if fire is not None else MONTECITO_FIRE
    contour_m = contour_m if contour_m is not None else CONTOUR_M

    # DEM loads once, up front, so the terrain router classifies on the raw DEM alone (A31).
    dem_artifacts = _load_dem_artifacts(fire)
    terrain_mode, terrain_verdict = _terrain_mode(dem_artifacts["dem_raw"], dem_artifacts["dem_nodata"])
    incised = (terrain_mode == "incised")
    if incised and fire.get("sbs") is not None:
        # A39: incised+SBS would emit an UNDISCLAIMED ranking (no both-arms shape) -- fail loud.
        raise GateAbort(
            "FAIL: incised terrain with an SBS burn input is not supported in v1 (A39). "
            "The SBS path does not carry the both-arms shape the disclaimer and UI require, "
            "so it would emit an UNDISCLAIMED ranking. Supply a dNBR input, or run the "
            "range-front path on terrain that fits it.")
    if incised and fire.get("creeks") is not None:
        # Renumbering trap: phase-2 filtering renumbers basins AFTER creek distances would be
        # computed, so a creek match would silently attach to the wrong basin -- fail loud.
        raise GateAbort(
            f"FAIL: creek/truth matching is not supported on the incised tier in v1 (A39) for "
            f"fire {fire.get('name')!r}. Phase-2 filtering (filter_burned_steep) renumbers "
            "surviving sub-basins from 0 AFTER creek distances are computed against the phase-1 "
            "numbering, so a creek match would silently attach to the WRONG basin. Do NOT wire a "
            "creeks layer through the incised path -- set fire['creeks'] = None, or run the "
            "range-front path on terrain that fits it.")

    # Hydrology + the master-outlet guard run for BOTH terrain modes.
    hydro = stage_2a_hydrology(fire, dem_artifacts)
    assert_master_outlet_scale(hydro["master_km2"], hydro["valid_area_km2"])   # FM-1

    if incised:
        # No mountain front: contour guard + canyon-mouth stage are ill-posed; assets skipped
        # (no drains-to-asset filter on incised terrain, A39).
        from src.subbasins import segment_subbasins, build_geometry_records
        labels, subbasin_meta = segment_subbasins(fire["dem"], str(Path(fire["out_dir"]) / "_wbt"))
        if labels.shape != hydro["dem_raw"].shape:
            # A mis-aligned label grid would silently index the wrong cells -- raise, never skip.
            raise GateAbort(
                f"FAIL: subbasin labels shape {labels.shape} != pipeline DEM grid "
                f"{hydro['dem_raw'].shape} (A39, subbasin labels vs pipeline DEM grid).")
        basins = build_geometry_records(labels, hydro["dem_raw"], hydro["dem_nodata"],
                                        subbasin_meta.pop("_acc"))
        if not basins:
            raise GateAbort(
                "FAIL: no sub-basins survive geometry filtering on incised terrain (A39). "
                "Most likely the DEM does not cover the drainage network, or every basin is "
                "truncated at the data footprint. Do NOT emit an empty ranking.")
        outlets = [b["outlet"] for b in basins]
    else:
        subbasin_meta = None
        assert_contour_in_dem_range(hydro["dem_raw"], hydro["dem_nodata"], contour_m=contour_m)
        outlets = stage_2b_outlets(hydro["acc"], hydro["fdir"], hydro["dem_raw"], hydro["shape"], contour_m=contour_m)
        assets = load_assets(fire["assets"])
        _assert_metric_crs(assets.crs, "assets.geojson")
        asset_xy = np.column_stack([assets.geometry.x.values, assets.geometry.y.values])
        basins = stage_2c_delineate(hydro["grid"], hydro["acc"], hydro["fdir_raster"],
                                    hydro["transform"], hydro["shape"], outlets, asset_xy)

    slope = mean_slope_tan(hydro["dem_raw"], hydro["dem_nodata"])   # tan(theta) raster (2d)

    # Truth-creek matching is burn-independent; a real un-assessed fire has creeks=None.
    creeks, creek_nearest = None, None
    if fire.get("creeks") is not None:
        creeks = load_creeks(fire["creeks"])
        _assert_metric_crs(creeks.crs, "creeks.geojson")
        if not creeks.geometry.is_valid.all():
            raise GateAbort("Invalid creek geometry -- FM-10 (geometry abort, not a match miss).")
        creek_nearest = compute_creek_nearest(basins, creeks, hydro["transform"])

    # Burn dispatch (A30): SBS present -> the validated SBS path; else the dNBR both-arms path.
    # One source per fire, decided once, stamped once (A4/A15).
    if fire.get("sbs") is not None:
        wt, covered, provenance = ingest_burn(fire["sbs"])
        ranked, n_ties = stage_2e_score(wt, covered, slope, basins)
        metrics = evaluate(basins, ranked, creek_nearest, TRUTH_MATCH_M) if creek_nearest is not None else None
        return {"status": "ranked",
                "hydro": hydro, "outlets": outlets, "basins": basins,
                "ranked": ranked, "n_ties": n_ties, "creeks": creeks,
                "creek_nearest": creek_nearest, "metrics": metrics,
                "provenance": provenance}

    # dNBR both-arms path (A34)
    dnbr_path = fire.get("dnbr")
    if dnbr_path is None:
        raise GateAbort("run_pipeline: fire provides neither 'sbs' nor 'dnbr' -- no burn input (A8 fail-loud).")
    D = ingest_dnbr_both_arms(dnbr_path, dem_artifacts["profile"])    # both arms, reprojected+aligned to the DEM grid

    # A41 dispatch: flowed basins (creeks present) keep the FROZEN fatal guard verbatim
    # (pre-reg P2 §4); creeks=None partitions per-basin instead of aborting the run.
    refused_basins, nodata_warn = [], []
    if creek_nearest is not None:
        flowed_ids = {info["basin_id"] for info in creek_nearest.values() if info["dist_m"] <= TRUTH_MATCH_M}
        guard_basins = [b for b in basins if b["basin_id"] in flowed_ids]
        unguarded_basins = [b for b in basins if b["basin_id"] not in flowed_ids]
        _partition_refused(basins, D["nodata_mask"])   # attach nodata_frac only; this path never refuses
        _dnbr_nodata_guard(guard_basins, D["nodata_mask"])
        nodata_warn = _dnbr_nodata_flags(unguarded_basins, D["nodata_mask"])   # loud, non-fatal
        if nodata_warn:
            _log.warning("dNBR NoData > %.0f%% on %d unguarded non-flowed basin(s) %s -- ranks may be "
                         "under-scored (cloud read as low burn); NOT aborted (flowed-only P2.3 parity). A P4 "
                         "truth fire must widen the guard or pre-screen the scene.",
                         DNBR_NODATA_FAILLOUD_FRAC * 100, len(nodata_warn), [bid for bid, _ in nodata_warn])
    else:
        # Partition on the scene-INDEPENDENT geometry, BEFORE the burn filter -- a clouded
        # burned basin must be REFUSED, not silently dropped by filter_burned_steep (A41).
        basins, refused_basins = _partition_refused(basins, D["nodata_mask"])
        # A41: refused basins are never scored, so mean_slope must be attached here for
        # the refused_basins.csv sidecar (nan-safe; all-NaN -> nan, rendered "" by outputs.py).
        for b in refused_basins:
            b["mean_slope"] = float(np.nanmean(slope[b["mask"]]))
        if refused_basins:
            # A refused basin is ABSENT from the ranking -- never let that shortening be silent.
            _log.warning("dNBR NoData > %.0f%% on %d basin(s) %s -- REFUSED, not ranked: a clouded "
                         "basin is a hazard-UNKNOWN basin, not a low-hazard one (A41). They are "
                         "excluded from the ranking and rendered as refused in outputs from A41 Task 3.",
                         DNBR_NODATA_FAILLOUD_FRAC * 100, len(refused_basins),
                         [b["basin_id"] for b in refused_basins])
        if not basins:
            raise GateAbort(
                "FAIL: every basin exceeds the frozen dNBR NoData bar "
                f"({DNBR_NODATA_FAILLOUD_FRAC:.0%}) -- no clean basin to rank (B1/A41). "
                "Do NOT emit an empty ranking.", scope="attempt")

    if incised:
        # Phase 2 (A39): burn + slope exist only now, so the phase-1 geometry basins are
        # filtered here -- on the CLEAN set only (A41). Arm A's weight raster defines the set;
        # Arm B scores that identical set.
        from src.subbasins import filter_burned_steep
        basins = filter_burned_steep(basins, D["arm_a"]["wt"], slope)
        if not basins:
            raise GateAbort(
                "FAIL: no sub-basins are both sufficiently burned and steep on incised "
                "terrain (A39). The burn may not intersect mapped drainage. Do NOT emit an "
                "empty ranking.", scope="attempt")   # A41: cloud -> wt 0 can empty the set; scene-dependent
        outlets = [b["outlet"] for b in basins]   # rebuild from the FINAL (phase-2) basin set

    # Score BOTH arms on independent copies (A34): Arm A headline, Arm B companion.
    arm_a = _score_one_arm(basins, D["arm_a"]["wt"], D["arm_a"]["covered"], slope, creek_nearest, D["covered_interp"])
    arm_b = _score_one_arm(basins, D["arm_b"]["wt"], D["arm_b"]["covered"], slope, creek_nearest, D["covered_interp"])
    provenance = {"burn_source": "dNBR"}

    if incised:
        # A39: area has no anchored meaning on segmentation basins -> add the intensity companion.
        from src.score import add_intensity_rank
        add_intensity_rank(arm_a["basins"])
        add_intensity_rank(arm_b["basins"])

    result = {"status": "ranked",
              "hydro": hydro, "outlets": outlets,
              "provenance": provenance, "creeks": creeks, "creek_nearest": creek_nearest,
              "arms": {"arm_a": arm_a, "arm_b": arm_b}, "headline_arm": "arm_a",
              "refused_basins": refused_basins,   # A41: never scored/ranked; [] on the flowed path
              "dnbr_diag": {"valid": D["valid"], "nodata_mask": D["nodata_mask"],
                            "covered_interp": D["covered_interp"], "nodata_warn_basins": nodata_warn},
              # Arm A (headline) mirrored at top level so uniform consumers (run.py, viewers) work unchanged:
              "basins": arm_a["basins"], "ranked": arm_a["ranked"],
              "n_ties": arm_a["n_ties"], "metrics": arm_a["metrics"]}
    result["terrain_mode"] = terrain_mode
    result["terrain_span_m"] = terrain_verdict["span_m"]
    if incised:
        result["basin_engine"] = subbasin_meta["engine"]
        result["subbasin_meta"] = subbasin_meta
    return result
