"""pipeline.py -- the promoted per-fire screening pipeline (run_pipeline + its stage
wiring + the A27/A31 terrain-applicability refusal), lifted VERBATIM out of
validation/gate.py so the production driver (run.py) and the validation harness
(validation/gate.py) share ONE pipeline definition instead of gate.py owning it.

This is a behavior-neutral promotion (import churn only): every function body, the
frozen order of operations, and every constant are byte-for-byte what gate.py held.
The only edits relocation forced are (a) the module-level import statements and
(b) the ROOT anchor -- see its comment: it still resolves to <repo>/validation, the
Montecito reconstruction data home, even though THIS file lives in src/.

validation/gate.py now re-exports these names (backward-compat shim) so existing
`gate.run_pipeline` / `from validation.gate import ...` call sites are unchanged.

Sub-stages (single pipeline, stages already extracted into src/ modules):
  2a hydrology  -- pysheds fill pits -> depressions -> flats -> D8 dir -> accumulation;
                   inline master-outlet FM-1 check (src/hydrology.py)
  2b outlets    -- channel cells crossing the CONTOUR_M mountain-front (src/delineate.py)
  2c delineate  -- upslope catchment per outlet, INDEX mode (src/delineate.py)
  2d slope      -- mean_slope = tan(theta) (OWNER-CONFIRMED), raw metric DEM (here)
  2e score+rank -- mean_burn x mean_slope x area_km2; within-fire ordinal (src/score.py)
  2f truth+metrics -- creek->outlet match (<=250 m); tercile; rank-AUC; means (here)

All distances are metric. Fail loud, never degrade (FM-10). See DECISIONS A16/A27/A31.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
from shapely.geometry import Point

# Frozen scalar tunables + grid/burn encodings live in src/config.py (P1.1). Imported BY NAME so
# existing bare-name references still resolve; exactly the subset the pipeline body uses.
from src.config import (
    TRUTH_MATCH_M,
    CANONICAL_CRS,
    CELL_M,
    MASTER_PASS_LO,
    MASTER_PASS_HI,
    MASTER_ORDER_LO,
    MASTER_ORDER_HI,
    DIRMAP,
    DNBR_NODATA_FAILLOUD_FRAC,
)
# Shared fail-loud exception + coordinate/CRS helpers (src/grids.py). assert_aligned is the DEM/SBS
# alignment check; test_entrypoint's expected_crs recorder monkeypatches THIS module's binding of it.
from src.grids import GateAbort, _assert_metric_crs, _rc_to_xy, assert_aligned
# P1.2 loaders + the A15 ingest seam (src/ingest.py). ingest_burn SELECTS the burn source (A3
# precedence), remaps to per-cell weights + the A18 coverage mask, and emits the single provenance (A4).
from src.ingest import load_dem, load_assets, load_creeks, ingest_burn, ingest_dnbr_both_arms
# The five-step pysheds flow chain (src/hydrology.py); master-outlet detection + catchment stay here.
from src.hydrology import run_hydrology
# Canyon-mouth outlet detection + catchment delineation (FM-1 index-mode catchment) + the A25/A27
# terrain guards (src/delineate.py). run_pipeline unpacks hydro and passes explicit args.
from src.delineate import (stage_2b_outlets, stage_2c_delineate, assert_contour_in_dem_range,
                           assess_hypsometric_applicability, _valid_dem_mask)
# Frozen scoring + ranking (src/score.py); the frozen formula + per-basin reduction live there.
from src.score import stage_2e_score
# Refusal artifact + human message (src/outputs.py); the A27 gate writes refusal.json via these.
from src.outputs import write_refusal, build_refusal_message

_log = logging.getLogger(__name__)

# CELL_AREA_KM2 is a DERIVATION of CELL_M (m^2 per cell -> km^2), not a standalone tunable;
# per the P1.1 named-binding rule it stays computed here at its use-site from the imported
# CELL_M (= 1e-4 km^2/cell), not extracted into config.py.
CELL_AREA_KM2  = (CELL_M * CELL_M) / 1.0e6 # m^2 per cell -> km^2 (= 1e-4 km^2/cell)

# --- reconstruction I/O anchors. This file lives in src/, but the Montecito reconstruction data still
# lives under <repo>/validation/data/ (A16), so ROOT is re-anchored off the repo root (parent.parent of
# src/pipeline.py) rather than off __file__.parent. VALUE-IDENTICAL to gate.py's old
# ROOT = Path(__file__).resolve().parent (== <repo>/validation); only the expression changed with the move.
_REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = _REPO_ROOT / "validation"
DATA = ROOT / "data"
OUT  = ROOT / "out"
DEM_TIF, SBS_TIF = DATA / "dem.tif", DATA / "sbs.tif"
ASSETS_GJ, CREEKS_GJ = DATA / "assets.geojson", DATA / "creeks.geojson"

# A30: per-fire I/O + provenance surface ONLY -- input paths, out_dir, the expected zone CRS, and the
# validation_case stamp. It carries NO analytical value: every frozen scalar (CONTOUR_M, ACC_THRESHOLD_CELLS,
# MIN_BASIN_KM2, DRAINS_TO_ASSET_M, TRUTH_MATCH_M, BURN_WEIGHTS, DNBR_*, DIRMAP, CELL_M) stays global in
# src/config.py, imported exactly as today. MONTECITO_FIRE == the path/CRS globals above, so it is BOTH
# run_pipeline()'s no-arg default (keeps the behavior lock byte-identical) and run.py's "montecito" entry.
MONTECITO_FIRE = {
    "name": "montecito",
    "dem": DEM_TIF, "sbs": SBS_TIF, "assets": ASSETS_GJ, "creeks": CREEKS_GJ,
    "out_dir": OUT, "expected_crs": CANONICAL_CRS,
    "validation_case": "Thomas_Fire_2017/Montecito_2018",
}

# A31: South Fork Fire 2024 (Colorado, UTM 13N / EPSG:32613) -- the P3 incised-terrain DEMONSTRATION of
# the A27 refusal path THROUGH the pipeline. Its burn products are dNBR-only, so sbs=None BY DESIGN
# (never a missing-file error -- see run._assert_inputs_present); the terrain gate now refuses on the
# DEM alone before any SBS is opened (A31), so --fire southfork runs end-to-end to a refusal.json where
# the (gitignored) data is present. creeks=None: South Fork has no tool-format truth-creek layer AND it
# refuses before creeks are ever loaded. Data lives at repo-root data/southfork/ (NOT validation/data/),
# so paths anchor off _REPO_ROOT. NOT a CI dependency (data gitignored, absent on a clean checkout).
_SOUTHFORK_DATA = _REPO_ROOT / "data" / "southfork"
SOUTHFORK_FIRE = {
    "name": "southfork",
    "dem": _SOUTHFORK_DATA / "dem" / "dem.tif",
    "sbs": None,                                    # dNBR-only fire; no SBS by design (A31, A29)
    "assets": _SOUTHFORK_DATA / "assets" / "osm_buildings_32613.gpkg",
    "creeks": None,                                 # no tool-format creek layer; refuses before creeks load
    "out_dir": OUT / "southfork",
    "expected_crs": "EPSG:32613",
    "validation_case": "South_Fork_Fire_2024",
}

# A34 / P2.2c verification fire: the Montecito case run through the dNBR BOTH-ARMS path (sbs=None), fed
# the committed native dNBR raster (validation/out/montecito_dnbr/dnbr_native.tif -- the P2.3 swap-test
# input). Delineation is burn-independent (DEM + assets only), so this yields the SAME 36 basins as
# MONTECITO_FIRE and must REPRODUCE the P2.3 side-by-side (Arm A -> San Ysidro #1 / Cold Spring #2;
# Arm B -> Cold Spring #1; rank-AUC 0.9722 both arms). creeks present so evaluate() reproduces the
# oracle AUC; sbs=None routes to the dNBR arm (A34). "dnbr" is the new optional per-fire burn-input key.
_MONTECITO_DNBR = OUT / "montecito_dnbr" / "dnbr_native.tif"
MONTECITO_DNBR_FIRE = {
    "name": "montecito_dnbr",
    "dem": DEM_TIF, "sbs": None, "dnbr": _MONTECITO_DNBR,
    "assets": ASSETS_GJ, "creeks": CREEKS_GJ,
    "out_dir": OUT / "montecito_dnbr" / "pipeline",
    "expected_crs": CANONICAL_CRS,
    "validation_case": "Thomas_Fire_2017/Montecito_2018 (dNBR both-arms)",
}


# ---------------------------------------------------------------------------
# A31: DEM load, lifted OUT of stage_2a so the A27 terrain gate can refuse on the DEM ALONE (before any
# SBS is opened or hydrology runs). run_pipeline reads the DEM here ONCE and threads the artifacts into
# both the terrain gate and stage_2a_hydrology (DECISIONS A31).
# ---------------------------------------------------------------------------
def _load_dem_artifacts(fire):
    """Read the DEM ONCE and return every artifact a downstream DEM consumer needs (A31).

    The DEM read that used to live inside stage_2a_hydrology is lifted here so run_pipeline can run the
    A27 terrain-applicability gate on the raw DEM BEFORE opening SBS or running hydrology. The read is
    byte-for-byte the same as before -- the rasterio profile/transform (for assert_aligned + the CELL_M
    check) plus load_dem's pysheds Grid / Raster / raw float64 elevation (m).

    fire -- per-fire I/O dict (A30); reads fire["dem"] only. Returns a bundle:
      grid, dem, dem_raw -- from src.ingest.load_dem (pysheds Grid + Raster + raw elev, m)
      dem_nodata         -- dem.nodata sentinel (pysheds defaults undeclared -> 0, FM-12)
      profile            -- DEM rasterio profile (assert_aligned reads crs/height/width/transform)
      transform          -- DEM affine (m); threaded downstream (delineate, creek match)
    """
    with rasterio.open(fire["dem"]) as dsrc:
        dem_profile = dsrc.profile          # DEM/SBS alignment (assert_aligned); dict, still valid after close
        dem_transform = dsrc.transform      # DEM affine (m); downstream transform of record
    grid, dem, dem_raw = load_dem(fire["dem"])   # pysheds Grid + Raster + raw float64 elev (m); src/ingest.py
    return {"grid": grid, "dem": dem, "dem_raw": dem_raw, "dem_nodata": dem.nodata,
            "profile": dem_profile, "transform": dem_transform}


# ---------------------------------------------------------------------------
# 2a -- hydrology + master-outlet linchpin (FM-1)
# ---------------------------------------------------------------------------
def stage_2a_hydrology(fire, dem_artifacts=None):
    """Condition the DEM and derive D8 flow direction + accumulation (pysheds).

    A31: the DEM is now loaded ONCE upstream (run_pipeline -> _load_dem_artifacts) and passed in via
    dem_artifacts, so this stage opens ONLY the SBS. dem_artifacts=None is a direct-caller fallback
    (it self-loads the DEM); run_pipeline ALWAYS passes the artifacts, so the pipeline opens the DEM
    exactly once and the A27 terrain gate runs before any SBS/hydrology work.

    fire          -- per-fire I/O dict (A30): reads fire["sbs"], validates against fire["expected_crs"].
    dem_artifacts -- the _load_dem_artifacts bundle (grid/dem/dem_raw/dem_nodata/profile/transform).
    CELL_M resolution check, DIRMAP, and the master-outlet block stay global/frozen.
    """
    if dem_artifacts is None:
        dem_artifacts = _load_dem_artifacts(fire)   # direct-caller convenience; run_pipeline passes them in
    grid, dem, dem_raw = dem_artifacts["grid"], dem_artifacts["dem"], dem_artifacts["dem_raw"]
    dem_nodata, dem_transform = dem_artifacts["dem_nodata"], dem_artifacts["transform"]

    # SBS path: open the SBS and align it to the DEM (CRS == expected zone, equal shape, equal affine)
    # -- src/grids.assert_aligned (extracted verbatim, P2.2a). expected_crs threaded per-fire (A30/A25).
    # A dNBR fire (sbs=None, A34) has NO SBS to align here: its dNBR raster is reprojected+aligned onto
    # the DEM grid downstream in ingest_dnbr_both_arms. The DEM-RESOLUTION check is a single-layer DEM
    # property (not pairwise), so it runs either way.
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

    # master-outlet = domain pour-point (max-accumulation cell). Item 1 is [ABSENT] in the
    # report; the prompt-sanctioned structural fallback is used (INFERRED). INDEX mode (FM-1).
    mrow, mcol = np.unravel_index(int(np.argmax(acc_arr)), shape)
    catch = grid.catchment(x=int(mcol), y=int(mrow), fdir=fdir,
                           dirmap=DIRMAP, xytype="index", routing="d8")
    master_km2 = int(np.asarray(catch).sum()) * CELL_AREA_KM2

    return {"grid": grid, "dem_raw": dem_raw, "dem_nodata": dem_nodata, "fdir_raster": fdir,
            "fdir": np.asarray(fdir), "acc": acc_arr, "transform": dem_transform,
            "shape": shape, "master_rowcol": (int(mrow), int(mcol)),
            "master_acc_cells": int(acc_arr[mrow, mcol]), "master_km2": master_km2}


def classify_master_zone(area_km2: float) -> str:
    """Resolve master-outlet area into ABORT / PASS / FINDING (no mushy boundary)."""
    if not np.isfinite(area_km2) or area_km2 <= 0.0:
        return "ABORT"
    if area_km2 < MASTER_ORDER_LO or area_km2 > MASTER_ORDER_HI:
        return "ABORT"
    if MASTER_PASS_LO <= area_km2 <= MASTER_PASS_HI:
        return "PASS"
    return "FINDING"


# ---------------------------------------------------------------------------
# 2d -- slope (OWNER-CONFIRMED: tan theta). 2e score+rank -> src/score.py (P1.5/P2.2a); the A17 weight
# raster + A18 coverage come from the ingest seam (ingest_burn), the slope raster is computed here, and
# all three feed stage_2e_score. mean_slope_tan stays with the pipeline (terrain derivative, passed in).
# ---------------------------------------------------------------------------
def mean_slope_tan(dem_raw: np.ndarray, dem_nodata=None) -> np.ndarray:
    """Per-cell slope as tan(theta) = rise/run gradient magnitude, DIMENSIONLESS.

    OWNER-CONFIRMED transform (reproduces the report mean_slope column to +/-0.01).
    Central differences on the RAW (metric) DEM, dx = dy = CELL_M = 10 m, so the
    gradient components are dimensionless. ("0-1 transport-energy proxy" in
    science_reference s1 is a typical-range description, not a hard bound; tan stays
    < 1 here because mean basin slopes are ~31 deg.)

    A33 (R1 coastal-slope; owner override of the 2026-07-06 deferral, 2026-07-07): np.gradient over a
    DEM whose nodata is clamped to 0 (FM-12) reads a spurious cliff at a VALID land cell adjacent to a
    nodata cell -- the contamination is in the valid cell whose 0-neighbor the gradient consumed, which
    is why masking at the mean does NOT remove it (A33 point 2). So when dem_nodata is given, drop the
    nodata-adjacent RING at SOURCE: an invalid cell OR a valid orthogonal neighbor of one -> NaN, and
    stage_2e_score means over the clean cells only. Validity uses the SAME _valid_dem_mask the A25/A27
    guards use (delineate.py) -- resolving A33's open question (slope no longer bypasses the shared
    valid-mask). dem_nodata=None (legacy callers, finite DEM) -> no drop, byte-identical to before."""
    gy, gx = np.gradient(dem_raw, CELL_M, CELL_M)   # d/d(row), d/d(col) in z per metre
    slope = np.hypot(gx, gy)                          # tan(theta), rise/run
    valid = _valid_dem_mask(dem_raw, dem_nodata)     # shared single-source definition (A27/delineate)
    inv = ~valid
    if inv.any():
        adj = np.zeros_like(inv)                     # valid cells orthogonally adjacent to an invalid cell
        adj[1:, :]  |= inv[:-1, :]                   # neighbor above invalid
        adj[:-1, :] |= inv[1:, :]                    # below
        adj[:, 1:]  |= inv[:, :-1]                   # left
        adj[:, :-1] |= inv[:, 1:]                    # right
        drop = inv | (valid & adj)                   # nodata cells + the valid nodata-adjacent ring
        slope = slope.copy()
        slope[drop] = np.nan                         # dropped at source; per-basin mean skips NaN (A33/score.py)
    return slope


# ---------------------------------------------------------------------------
# 2f -- truth + metrics
# ---------------------------------------------------------------------------
def compute_creek_nearest(basins, creeks, transform):
    """For each creek, the nearest basin outlet and its distance (m).

    Reference geometry (item 8, [VERIFIED]): whole creek LineString, min distance to the
    outlet POINT ("nearest outlet-to-channel", s3.5). Ties -> lowest basin_id (cell index).
    """
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


# ---------------------------------------------------------------------------
# A27 terrain-applicability gate (wired) + caller-side dispatch -- DECISIONS A27/A27.1, P3.4-build-2.
# ---------------------------------------------------------------------------
def _terrain_applicability_gate(dem_raw, dem_nodata, out_dir):
    """A27 terrain-applicability pre-check, WIRED into the live pipeline (DECISIONS A27/A27.1).

    Runs the frozen hypsometric-span detector on the RAW (pre-pit-fill) DEM. If the terrain is
    ill-posed for CONTOUR_M outlet-anchoring (incised: valid-cell (p10 - p1) > 50 m), it writes an
    honest refusal.json to out_dir and returns the FIREWALL-CLEAN refusal-result; otherwise it
    returns None and the pipeline proceeds to the A25 guard unchanged. Montecito (span ~15 m) ->
    None -> falls through, so the behavior lock is untouched.

    FIREWALL (A27): the returned dict carries EXACTLY
    {status, reason_code, span_m, span_threshold_m, message} -- span_m is a DIFFERENCE (a vertical
    extent) and span_threshold_m the frozen 50 m; NO absolute elevation / p1 / p10 / CONTOUR_M
    candidate crosses this boundary. write_refusal may LOG more on disk (refusal.json is a terminal
    artifact crossing no stage boundary); this return must never widen toward that field set. The
    detector's all-nodata GateAbort is deliberately NOT caught here -- a broken/empty DEM is a
    fail-loud input error (A8), not an honest 'wrong terrain' refusal.

    dem_raw    -- raw metric DEM (m), pre-pit-fill; the SAME array the A25 guard reads.
    dem_nodata -- DEM nodata sentinel (FM-12: pysheds defaults an undeclared nodata to 0).
    out_dir    -- per-fire output dir (refusal.json sink); same convention as write_outputs.
    """
    verdict = assess_hypsometric_applicability(dem_raw, dem_nodata)
    if not verdict["refuse"]:
        return None
    write_refusal(verdict, out_dir)        # writes refusal.json only; NO ranking.csv / basins.geojson
    return {
        "status": "refused",
        "reason_code": verdict["reason_code"],
        "span_m": verdict["span_m"],                 # a difference (vertical extent, m), never an absolute elevation
        "span_threshold_m": verdict["span_threshold_m"],
        "message": build_refusal_message(verdict["reason_code"], verdict["span_m"],
                                         verdict["span_threshold_m"]),
    }


def dispatch_result(result):
    """Caller-side dispatch on run_pipeline's polymorphic return (A27). Returns the process EXIT
    CODE (int).

    run_pipeline now returns either a ranked-result ({"status": "ranked", ...}) or a refusal-result
    ({"status": "refused", ...}); every caller must dispatch on that discriminator. On REFUSE this
    emits the human refusal MESSAGE -- the ENTIRE user-facing payload of a refusal, since no
    ranking.csv is written behind it. A refusal is an honest answer, not a crash, so it exits 0; a
    batch caller distinguishes ranked from refused via this return's "status" (in-process) or the
    'TERRAIN-APPLICABILITY REFUSAL' stdout marker + the on-disk refusal.json (CLI). An UNKNOWN
    status RAISES (A8 fail-loud) -- which is exactly what keeps a future third status additive: an
    un-taught caller fails loud rather than silently mishandling it. Dispatch is an explicit match
    on the string, never a boolean flag or a None sentinel.
    """
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


# ---------------------------------------------------------------------------
# dNBR both-arms scoring helpers (A34 / P2.2c). Faithful to the FROZEN P2.3 swap-test machinery
# (validation/p2_3_swap_test._build_arm + validation/p2_run_dnbr helpers), lifted into the production
# pipeline so run_pipeline can score dNBR directly. score.py and the frozen formula are UNTOUCHED.
# ---------------------------------------------------------------------------
def _dnbr_nodata_guard(basins, nodata_mask):
    """P2.1 §4 path 1 (A8): if dNBR NoData/cloud covers > DNBR_NODATA_FAILLOUD_FRAC of a basin, fail
    loud for it -- a clouded scene is a bad scene, not a low-burn finding. Faithful to
    validation/p2_run_dnbr._nodata_fail_loud_guard (guards whatever basin subset it is handed)."""
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
    """F3 (B+): the NON-FATAL companion to _dnbr_nodata_guard. Returns [(basin_id, frac), ...] for basins
    whose dNBR NoData fraction exceeds DNBR_NODATA_FAILLOUD_FRAC, and NEVER raises. Surfaces (loud but
    non-fatal) the non-flowed scored basins the flowed-only guard does not hard-abort on a truth-bearing
    fire, so a clouded basin under-scored as low-burn is never SILENT (A8 spine: the sin is silence)."""
    nd = np.asarray(nodata_mask)
    over = []
    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        frac = float(nd[m].mean()) if ncells else 0.0
        if frac > DNBR_NODATA_FAILLOUD_FRAC:
            over.append((b["basin_id"], frac))
    return over


def _attach_a23_covered_interp(basins, covered_interp):
    """A23 diagnostic: per-basin covered-INTERPRETATION fraction (below-floor counted as covered) --
    READ-ONLY, never fed to low_coverage/score/rank (score.py untouched). Faithful to
    validation/p2_run_dnbr._attach_a23_diagnostic; covered_interp comes from ingest_dnbr_both_arms."""
    ci = np.asarray(covered_interp)
    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        b["burn_coverage_frac_covered_interp"] = float(ci[m].mean()) if ncells else 0.0


def _score_one_arm(basins_src, wt, covered, slope, creek_nearest, covered_interp):
    """Score ONE dNBR arm on an independent copy of the (burn-independent) delineation with the FROZEN
    stage_2e_score, attach the A23 covered-interpretation diagnostic, and -- when a truth-creek layer
    exists -- evaluate the arm (flowed labels + rank-AUC). Mirrors the P2.3 swap-test _build_arm: the
    delineation / slope / area are identical across arms, only mean_burn moves. Returns a per-arm dict."""
    basins = [dict(b) for b in basins_src]                # shallow copy; the read-only 'mask' ndarray is shared
    ranked, n_ties = stage_2e_score(wt, covered, slope, basins)
    _attach_a23_covered_interp(basins, covered_interp)
    metrics = evaluate(basins, ranked, creek_nearest, TRUTH_MATCH_M) if creek_nearest is not None else None
    return {"ranked": ranked, "basins": basins, "n_ties": n_ties, "metrics": metrics}


# ---------------------------------------------------------------------------
# pipeline driver (2a -> 2f) + determinism + perturbation
# ---------------------------------------------------------------------------
def run_pipeline(fire=None):
    """Run 2a -> 2f at the frozen TRUTH_MATCH_M. Returns a results dict.

    fire -- per-fire I/O + provenance dict (A30). None -> MONTECITO_FIRE, so the no-arg call is
    byte-identical to before (behavior lock). Only I/O + provenance is per-fire; every analytical
    scalar stays global/frozen in src/config.py.
    """
    fire = fire if fire is not None else MONTECITO_FIRE

    # A31: load the DEM ONCE, up front -- before the terrain gate and before hydrology. Lifting the DEM
    # read out of stage_2a lets the A27 terrain-applicability gate refuse on the raw DEM ALONE, before
    # any SBS is opened, any hydrology runs, or the master-outlet ABORT is evaluated (DECISIONS A31).
    dem_artifacts = _load_dem_artifacts(fire)

    # A27/A31 terrain-applicability refusal (DECISIONS A27 / A27.1 / A31) -- now FIRST, on the raw DEM.
    # Incised terrain has no mountain-front break, so the CONTOUR_M anchor is ill-posed; emit an honest
    # refusal.json to fire["out_dir"] and return the refusal-result WITHOUT opening SBS, running
    # hydrology, or evaluating the master-outlet ABORT. Montecito (range-front, span ~15 m) -> None ->
    # proceeds. This subsumes the prior "master-outlet-ABORT-before-A27" order (A31): no hydrology work
    # is done for a fire that will refuse on terrain.
    refusal = _terrain_applicability_gate(dem_artifacts["dem_raw"], dem_artifacts["dem_nodata"],
                                          fire["out_dir"])
    if refusal is not None:
        return refusal     # polymorphic refusal-result; SBS never opened, no hydrology run (caller dispatches on status)

    # Hydrology + master outlet run ONLY for terrain that passed the gate. stage_2a opens+aligns SBS and
    # reuses the DEM artifacts loaded above -- the pipeline opens the DEM exactly once (A31).
    hydro = stage_2a_hydrology(fire, dem_artifacts)
    zone = classify_master_zone(hydro["master_km2"])
    if zone == "ABORT":
        raise GateAbort(f"Master outlet {hydro['master_km2']:.2f} km^2 in ABORT zone (FM-1).")

    # A25 carve-out: fail loud if CONTOUR_M is grossly mis-set for this DEM BEFORE detecting outlets
    # (else a wrong-fire contour silently yields zero/wrong canyon mouths). Montecito 150 m is inside
    # [~0, 1199] m -> passes; runs on the same dem_raw the contour test uses. src/delineate.py
    assert_contour_in_dem_range(hydro["dem_raw"], hydro["dem_nodata"])
    # unpack hydro at the call site (dict-key coupling stays here, not in delineate); src/delineate.py
    outlets = stage_2b_outlets(hydro["acc"], hydro["fdir"], hydro["dem_raw"], hydro["shape"])
    assets = load_assets(fire["assets"])     # GeoDataFrame; src/ingest.py
    _assert_metric_crs(assets.crs, "assets.geojson")
    asset_xy = np.column_stack([assets.geometry.x.values, assets.geometry.y.values])
    basins = stage_2c_delineate(hydro["grid"], hydro["acc"], hydro["fdir_raster"],
                                hydro["transform"], hydro["shape"], outlets, asset_xy)

    slope = mean_slope_tan(hydro["dem_raw"], hydro["dem_nodata"])   # tan(theta) raster (2d); A33 drops the nodata ring

    # Truth-creek matching is burn-independent (delineation-based) -- compute it ONCE if this fire carries
    # a truth-creek layer (validation). A real un-assessed fire has none (creeks=None): rank only, no
    # evaluate() (A34/CF-A). The FM-10 geometry abort stays a hard fail-loud whenever creeks ARE present.
    creeks, creek_nearest = None, None
    if fire.get("creeks") is not None:
        creeks = load_creeks(fire["creeks"])     # GeoDataFrame; src/ingest.py
        _assert_metric_crs(creeks.crs, "creeks.geojson")
        if not creeks.geometry.is_valid.all():
            raise GateAbort("Invalid creek geometry -- FM-10 (geometry abort, not a match miss).")
        creek_nearest = compute_creek_nearest(basins, creeks, hydro["transform"])

    # Burn dispatch. Per-fire INPUT routing (A30): an SBS raster present -> the validated single-source
    # SBS path (the A4/A15 coverage SELECTION stays inside ingest_burn, UNCHANGED / byte-identical);
    # otherwise the dNBR BOTH-ARMS path (A34/P2.2c). One source per fire, decided once, stamped once --
    # never re-decided or blended downstream (A4/A15).
    if fire.get("sbs") is not None:
        # ===== SBS path -- UNCHANGED (the Montecito behavior lock is the tripwire) =====
        wt, covered, provenance = ingest_burn(fire["sbs"])            # A15 seam: select + weights + coverage + provenance
        ranked, n_ties = stage_2e_score(wt, covered, slope, basins)  # frozen burn x slope x area; src/score.py
        metrics = evaluate(basins, ranked, creek_nearest, TRUTH_MATCH_M) if creek_nearest is not None else None
        return {"status": "ranked",   # A27 discriminator (P3.4-build-2): ranked-result vs the refusal-result above
                "hydro": hydro, "zone": zone, "outlets": outlets, "basins": basins,
                "ranked": ranked, "n_ties": n_ties, "creeks": creeks,
                "creek_nearest": creek_nearest, "metrics": metrics,
                "provenance": provenance}   # A4/A15: single burn-source stamp from the ingest seam

    # ===== dNBR both-arms path (A34 / P2.2c) =====
    dnbr_path = fire.get("dnbr")
    if dnbr_path is None:
        raise GateAbort("run_pipeline: fire provides neither 'sbs' nor 'dnbr' -- no burn input (A8 fail-loud).")
    D = ingest_dnbr_both_arms(dnbr_path, dem_artifacts["profile"])    # both arms, reprojected+aligned to the DEM grid

    # dNBR NoData/cloud guard (A8; P2.1 §4 path 1). HARD abort on the guarded set: flowed (truth) basins
    # when creeks exist (P2.3-harness parity, byte-identical), else ALL scored basins (a real frontend fire
    # has creeks=None -> every basin guarded). The flowed set is score-independent (creek match <= TRUTH_MATCH_M).
    if creek_nearest is not None:
        flowed_ids = {info["basin_id"] for info in creek_nearest.values() if info["dist_m"] <= TRUTH_MATCH_M}
        guard_basins = [b for b in basins if b["basin_id"] in flowed_ids]
        unguarded_basins = [b for b in basins if b["basin_id"] not in flowed_ids]
    else:
        guard_basins, unguarded_basins = basins, []
    _dnbr_nodata_guard(guard_basins, D["nodata_mask"])
    # F3 (B+): the flowed-only scope leaves non-flowed scored basins un-hard-guarded on a truth-bearing
    # fire; a clouded one is under-scored (NoData -> class 15 -> low burn). Surface it LOUD but NON-FATAL
    # (never silent, A8) so a future P4 truth fire is flagged. Empty on a frontend fire (all guarded above).
    nodata_warn = _dnbr_nodata_flags(unguarded_basins, D["nodata_mask"])
    if nodata_warn:
        _log.warning("dNBR NoData > %.0f%% on %d unguarded non-flowed basin(s) %s -- ranks may be "
                     "under-scored (cloud read as low burn); NOT aborted (flowed-only P2.3 parity). A P4 "
                     "truth fire must widen the guard or pre-screen the scene.",
                     DNBR_NODATA_FAILLOUD_FRAC * 100, len(nodata_warn), [bid for bid, _ in nodata_warn])

    # Score BOTH arms on independent copies of the burn-independent delineation (A34). Arm A (binned) is
    # the pre-registered headline; Arm B (continuous) is the non-gating companion. slope + area are identical
    # across arms; only mean_burn moves. Mirrors the frozen P2.3 swap-test _build_arm machinery.
    arm_a = _score_one_arm(basins, D["arm_a"]["wt"], D["arm_a"]["covered"], slope, creek_nearest, D["covered_interp"])
    arm_b = _score_one_arm(basins, D["arm_b"]["wt"], D["arm_b"]["covered"], slope, creek_nearest, D["covered_interp"])
    provenance = {"burn_source": "dNBR"}   # A4: single burn-source stamp

    return {"status": "ranked",
            "hydro": hydro, "zone": zone, "outlets": outlets,
            "provenance": provenance, "creeks": creeks, "creek_nearest": creek_nearest,
            "arms": {"arm_a": arm_a, "arm_b": arm_b}, "headline_arm": "arm_a",
            "dnbr_diag": {"valid": D["valid"], "nodata_mask": D["nodata_mask"],
                          "covered_interp": D["covered_interp"], "nodata_warn_basins": nodata_warn},
            # Arm A (headline) mirrored at top level so uniform consumers (run.py, viewers) work unchanged:
            "basins": arm_a["basins"], "ranked": arm_a["ranked"],
            "n_ties": arm_a["n_ties"], "metrics": arm_a["metrics"]}
