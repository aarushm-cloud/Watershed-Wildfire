"""gate.py -- the Week-0 validation gate, reconstructed in P0.5 from
VALIDATION_REPORT.md (the behavior oracle) using the same data sources and
parameters. Must reproduce the documented 32-basin ranking, top-tercile 6/6,
#1 = Cold Spring (flowed), rank-AUC 0.987, and the 39.19 km2 master-outlet
check. Not to be edited to make a run pass. See DECISIONS A16.

Sub-stages, single script (P1 modularises into src/):
  2a hydrology  -- pysheds fill pits -> fill depressions -> resolve flats ->
                   D8 flow dir -> accumulation; inline master-outlet FM-1 check
  2b outlets    -- channel cells (acc > thresh) crossing the 150 m mountain-front
                   contour going downhill (canyon mouths)
  2c delineate  -- upslope catchment per outlet (INDEX mode); discard tiny; keep
                   asset-draining; dedup (larger basins claim cells first).
                   Deterministic: stable basin_id + tie-breaks by outlet (row,col).
  2d slope      -- mean_slope = tan(theta) (OWNER-CONFIRMED), raw metric DEM
  2e score+rank -- mean_burn x mean_slope x area_km2; within-fire ordinal rank
  2f truth+metrics -- creek->outlet match (<=250 m); tercile; rank-AUC; means
Outputs: validation/out/{ranking.csv, basins.geojson}, stamped SBS + screening.

All distances are metric (EPSG:32611, UTM 11N). Fail loud, never degrade (FM-10).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
import rasterio
from rasterio import features as rfeatures
from scipy.spatial import cKDTree
from shapely.geometry import shape as shapely_shape, Point
from shapely.ops import unary_union

# --- P1.1 bootstrap: make the project root importable so `from src...` resolves in EVERY
# context. This file is loaded by the standalone run (`python validation/gate.py`), by the
# pytest behavior-lock, and by the lock's standalone runner -- all three import THIS module,
# so keying the path off __file__ here is the single shared mechanism. gate.py lives at
# <root>/validation/gate.py, hence root = parents[1].
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# --- P1.1: frozen scalar tunables + grid/burn encodings now live in src/config.py and are
# imported back BY NAME (rebinds them as gate globals, so existing `gate.X` / bare-name
# references still resolve). EXTRACTED verbatim, not redefined: exactly one binding each. ---
from src.config import (
    CONTOUR_M,
    ACC_THRESHOLD_CELLS,
    MIN_BASIN_KM2,
    DRAINS_TO_ASSET_M,
    TRUTH_MATCH_M,
    BURN_WEIGHTS,
    BURN_LOW_COVERAGE,
    CANONICAL_CRS,
    CELL_M,
    MASTER_KNOWN_KM2,
    MASTER_PASS_LO,
    MASTER_PASS_HI,
    MASTER_ORDER_LO,
    MASTER_ORDER_HI,
    DIRMAP,
    D8_OFFSETS,
)
# Shared fail-loud exception + coordinate/CRS helpers (extracted verbatim to src/grids.py).
from src.grids import GateAbort, _assert_metric_crs, _rc_to_xy
# P1.2: raw input loaders (DEM/burn/assets/creeks) extracted verbatim to src/ingest.py. Paths
# stay here and are passed in; alignment validation, the burn remap/coverage, and the
# _assert_metric_crs guards remain below (conservative lift -- raw reads only).
from src.ingest import load_dem, load_burn, load_assets, load_creeks, BURN_SOURCE

# CELL_AREA_KM2 is a DERIVATION of CELL_M (m^2 per cell -> km^2), not a standalone tunable;
# per the P1.1 named-binding rule it stays computed here at its use-site from the imported
# CELL_M (= 1e-4 km^2/cell), not extracted into config.py.
CELL_AREA_KM2  = (CELL_M * CELL_M) / 1.0e6 # m^2 per cell -> km^2 (= 1e-4 km^2/cell)

SCREENING_STATEMENT = ("Within-fire relative screening ranking of watersheds warranting closer "
                       "assessment -- not a prediction of where debris will go. Not cross-fire comparable.")
# BURN_SOURCE (A4/A11 provenance) now lives in src/ingest.py and is imported above -- single
# source of truth; write_outputs reads the imported value (SCREENING_STATEMENT stays here).

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT  = ROOT / "out"
DEM_TIF, SBS_TIF = DATA / "dem.tif", DATA / "sbs.tif"
ASSETS_GJ, CREEKS_GJ = DATA / "assets.geojson", DATA / "creeks.geojson"


# ---------------------------------------------------------------------------
# 2a -- hydrology + master-outlet linchpin (FM-1)
# ---------------------------------------------------------------------------
def stage_2a_hydrology():
    """Condition the DEM and derive D8 flow direction + accumulation (pysheds)."""
    with rasterio.open(DEM_TIF) as dsrc, rasterio.open(SBS_TIF) as ssrc:
        if str(dsrc.crs).upper() != CANONICAL_CRS:
            raise GateAbort(f"DEM CRS {dsrc.crs} != {CANONICAL_CRS}.")
        if (dsrc.height, dsrc.width) != (ssrc.height, ssrc.width):
            raise GateAbort(f"DEM shape {(dsrc.height,dsrc.width)} != SBS shape "
                            f"{(ssrc.height,ssrc.width)} (alignment broken).")
        if not dsrc.transform.almost_equals(ssrc.transform):
            raise GateAbort("DEM/SBS affine transforms differ (alignment broken).")
        transform = dsrc.transform
        if abs(transform.a - CELL_M) > 1e-6 or abs(transform.e + CELL_M) > 1e-6:
            raise GateAbort(f"DEM resolution {(transform.a, transform.e)} != {CELL_M} m.")

    grid, dem, dem_raw = load_dem(DEM_TIF)   # pysheds Grid + Raster + raw float64 elev (m); src/ingest.py

    pit_filled = grid.fill_pits(dem)
    flooded    = grid.fill_depressions(pit_filled)
    inflated   = grid.resolve_flats(flooded)            # conditioned DEM for routing
    fdir = grid.flowdir(inflated, dirmap=DIRMAP, routing="d8")
    acc  = grid.accumulation(fdir, dirmap=DIRMAP, routing="d8")

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

    return {"grid": grid, "dem_raw": dem_raw, "fdir_raster": fdir,
            "fdir": np.asarray(fdir), "acc": acc_arr, "transform": transform,
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
# 2b -- canyon-mouth outlets
# ---------------------------------------------------------------------------
def stage_2b_outlets(hydro) -> list[tuple[int, int]]:
    """Channel cells (acc > threshold) that cross the 150 m contour going downhill.

    A channel cell with raw elevation >= CONTOUR_M whose D8-downstream neighbour's raw
    elevation is < CONTOUR_M is a canyon-mouth outlet (VALIDATION_REPORT s3.2). Contour
    test on RAW terrain; routing on conditioned-DEM fdir. Returns (row, col) tuples.
    """
    acc, fdir, dem_raw, shape = hydro["acc"], hydro["fdir"], hydro["dem_raw"], hydro["shape"]
    nrows, ncols = shape
    channel = acc > ACC_THRESHOLD_CELLS

    outlets: list[tuple[int, int]] = []
    cand_rows, cand_cols = np.where(channel & (dem_raw >= CONTOUR_M))
    for r, c in zip(cand_rows.tolist(), cand_cols.tolist()):
        off = D8_OFFSETS.get(int(fdir[r, c]))
        if off is None:
            continue
        nr, nc = r + off[0], c + off[1]
        if 0 <= nr < nrows and 0 <= nc < ncols and dem_raw[nr, nc] < CONTOUR_M:
            outlets.append((r, c))

    if not outlets:
        raise GateAbort("Zero canyon-mouth outlets detected -- contour/accumulation logic "
                        "or the AOI is wrong (FM-10). Refusing empty result.")
    return sorted(outlets)  # stable order


# ---------------------------------------------------------------------------
# 2c -- delineate, discard, drains-to-asset, dedup (DETERMINISTIC)
# ---------------------------------------------------------------------------
def stage_2c_delineate(hydro, outlets, asset_xy):
    """Delineate, discard tiny, keep asset-draining, dedup (larger basins claim first).

    Order (an operational choice; report lists these as prose, s3.3): delineate ->
    discard raw < MIN_BASIN_KM2 -> drains-to-asset (basin CHANNEL cells -> nearest asset
    <= 600 m, "channel reaches within 600 m of the building layer") -> dedup -> re-discard.

    Determinism: dedup ties break by (-area, row, col); basin_id assigned by sorting the
    surviving basins on outlet (row, col). Geometry is unchanged from Part 1 (no exact
    area ties exist with float areas; the keys only canonicalise label/claim order).
    """
    grid, acc, transform, shape = (hydro["grid"], hydro["acc"],
                                   hydro["transform"], hydro["shape"])
    fdir_raster = hydro["fdir_raster"]
    channel = acc > ACC_THRESHOLD_CELLS
    asset_tree = cKDTree(asset_xy)

    raw = []  # surviving (outlet, mask, raw_area, asset_dist)
    for (r, c) in outlets:
        # INDEX mode mandatory (FM-1: coordinate mode silently returns 0 km^2).
        mask = np.asarray(grid.catchment(x=int(c), y=int(r), fdir=fdir_raster,
                                         dirmap=DIRMAP, xytype="index", routing="d8"), dtype=bool)
        area = int(mask.sum()) * CELL_AREA_KM2
        if not np.isfinite(area) or area <= 0.0:
            raise GateAbort(f"Outlet (row={r}, col={c}) delineated to {area} km^2 "
                            "(0 / non-finite) -- FM-1 bug class. Aborting.")
        if area < MIN_BASIN_KM2:
            continue
        ch_rows, ch_cols = np.where(mask & channel)
        if ch_rows.size == 0:
            continue
        dmin = float(np.min(asset_tree.query(_rc_to_xy(ch_rows, ch_cols, transform), k=1)[0]))
        if dmin <= DRAINS_TO_ASSET_M:
            raw.append({"outlet": (r, c), "mask": mask, "raw_km2": area, "asset_m": dmin})

    if not raw:
        raise GateAbort("No basins survive discard + drains-to-asset -- FM-10.")

    # dedup: larger claims first; ties -> (-area, row, col) for determinism
    raw.sort(key=lambda b: (-b["raw_km2"], b["outlet"][0], b["outlet"][1]))
    claimed = np.zeros(shape, dtype=bool)
    kept = []
    for b in raw:
        own = b["mask"] & ~claimed
        own_km2 = int(own.sum()) * CELL_AREA_KM2
        if own_km2 < MIN_BASIN_KM2:
            continue
        claimed |= own
        kept.append({"outlet": b["outlet"], "mask": own,
                     "area_km2": own_km2, "asset_m": b["asset_m"]})

    # stable basin_id by outlet (row, col)
    kept.sort(key=lambda b: (b["outlet"][0], b["outlet"][1]))
    for i, b in enumerate(kept):
        b["basin_id"] = i
    return kept


# ---------------------------------------------------------------------------
# 2d -- slope (OWNER-CONFIRMED: tan theta) + 2e -- score + rank
# ---------------------------------------------------------------------------
def mean_slope_tan(dem_raw: np.ndarray) -> np.ndarray:
    """Per-cell slope as tan(theta) = rise/run gradient magnitude, DIMENSIONLESS.

    OWNER-CONFIRMED transform (reproduces the report mean_slope column to +/-0.01).
    Central differences on the RAW (metric) DEM, dx = dy = CELL_M = 10 m, so the
    gradient components are dimensionless. ("0-1 transport-energy proxy" in
    science_reference s1 is a typical-range description, not a hard bound; tan stays
    < 1 here because mean basin slopes are ~31 deg.)
    """
    gy, gx = np.gradient(dem_raw, CELL_M, CELL_M)   # d/d(row), d/d(col) in z per metre
    return np.hypot(gx, gy)                          # tan(theta), rise/run


def _burn_weight_raster(sbs: np.ndarray):
    """Per-cell burn weight (A17, canonical): classes 1-4 -> BURN_WEIGHTS; Developed(0) and
    outside-perimeter/NoData(15) -> 0.0, all INCLUDED in the denominator (coverage-weighted).
    Returns (wt, covered); covered = cells with a real burn assessment, class in {1,2,3,4}
    (excludes Developed=0 and NoData=15) -- the A18/C8 fix; used only for the burn_coverage_frac
    caveat, NOT to gate the mean."""
    wt = np.zeros(sbs.shape, dtype=np.float64)
    for cls, w in BURN_WEIGHTS.items():      # classes 1..4 (0 and 15 stay 0.0)
        wt[sbs == cls] = w
    covered = np.isin(sbs, (1, 2, 3, 4))
    return wt, covered


def stage_2e_score(hydro, basins):
    """mean_burn x mean_slope x area_km2 (science_reference s1), within-fire ordinal rank.

    score = mean_burn [0-1, dimensionless] x mean_slope [tan, dimensionless] x area_km2 [km^2].
    """
    sbs = load_burn(SBS_TIF)                 # raw SBS class raster (band 1); src/ingest.py
    wt, covered = _burn_weight_raster(sbs)   # A17: coverage-weighted (class 15 -> 0.0, included)
    slope = mean_slope_tan(hydro["dem_raw"])

    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        ncov = int((m & covered).sum())
        b["burn_coverage_frac"] = ncov / ncells if ncells else 0.0
        # A17: mean over ALL basin cells; outside-perimeter/NoData(15) included as 0.0
        b["mean_burn"] = float(np.mean(wt[m])) if ncells else 0.0
        b["mean_slope"] = float(np.mean(slope[m]))                       # tan(theta), dimensionless
        b["score"] = b["mean_burn"] * b["mean_slope"] * b["area_km2"]    # burn[0-1] x slope[tan] x km^2
        b["low_coverage"] = b["burn_coverage_frac"] < BURN_LOW_COVERAGE

    # ordinal rank: score desc, ties -> ascending basin_id (deterministic)
    order = sorted(basins, key=lambda b: (-b["score"], b["basin_id"]))
    for rank, b in enumerate(order, start=1):
        b["rank"] = rank
    scores = [b["score"] for b in basins]
    n_ties = len(scores) - len(set(round(s, 12) for s in scores))
    return order, n_ties


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
# outputs
# ---------------------------------------------------------------------------
def write_outputs(basins, creek_nearest):
    """Write validation/out/{ranking.csv, basins.geojson}, stamped SBS + screening (A4/A11)."""
    OUT.mkdir(parents=True, exist_ok=True)
    nearest_by_basin = {}
    for creek, info in creek_nearest.items():
        bid = info["basin_id"]
        if bid not in nearest_by_basin or info["dist_m"] < nearest_by_basin[bid][1]:
            nearest_by_basin[bid] = (creek, info["dist_m"])

    rows = []
    for b in sorted(basins, key=lambda x: x["rank"]):
        near = nearest_by_basin.get(b["basin_id"], (None, None))
        rows.append({
            "basin_id": b["basin_id"], "rank": b["rank"], "score": round(b["score"], 6),
            "mean_burn": round(b["mean_burn"], 4), "mean_slope": round(b["mean_slope"], 4),
            "area_km2": round(b["area_km2"], 4), "burn_coverage_frac": round(b["burn_coverage_frac"], 4),
            "drains_to_asset": True, "flowed": b["flowed"],
            "matched_creek": b["matched_creek"],
            "nearest_outlet_dist_m": round(near[1], 1) if near[1] is not None else "",
        })
    df = pd.DataFrame(rows)
    csv_path = OUT / "ranking.csv"
    with open(csv_path, "w") as fh:
        fh.write(f"# {SCREENING_STATEMENT}\n")
        fh.write(f"# burn_source={BURN_SOURCE}  validation_case=Thomas_Fire_2017/Montecito_2018\n")
        df.to_csv(fh, index=False)

    # basins.geojson: vectorise each basin mask, reproject to EPSG:4326 (GeoJSON convention)
    transform = None
    with rasterio.open(DEM_TIF) as s:
        transform = s.transform
    geoms, props = [], []
    for b in sorted(basins, key=lambda x: x["rank"]):
        mask = b["mask"].astype(np.uint8)
        polys = [shapely_shape(geom) for geom, val in
                 rfeatures.shapes(mask, mask=b["mask"], transform=transform) if val == 1]
        geoms.append(unary_union(polys))
        props.append({"basin_id": b["basin_id"], "rank": b["rank"], "score": round(b["score"], 6),
                      "mean_burn": round(b["mean_burn"], 4), "mean_slope": round(b["mean_slope"], 4),
                      "area_km2": round(b["area_km2"], 4),
                      "burn_coverage_frac": round(b["burn_coverage_frac"], 4),
                      "flowed": b["flowed"], "matched_creek": b["matched_creek"],
                      "burn_source": BURN_SOURCE, "screening": SCREENING_STATEMENT})
    gdf = gpd.GeoDataFrame(props, geometry=geoms, crs=CANONICAL_CRS).to_crs("EPSG:4326")
    gj_path = OUT / "basins.geojson"
    gdf.to_file(gj_path, driver="GeoJSON")
    # inject a top-level provenance member (A4/A11)
    with open(gj_path) as fh:
        fc = json.load(fh)
    fc["provenance"] = {"burn_source": BURN_SOURCE, "screening": SCREENING_STATEMENT,
                        "validation_case": "Thomas_Fire_2017/Montecito_2018", "crs": "EPSG:4326"}
    with open(gj_path, "w") as fh:
        json.dump(fc, fh)
    return csv_path, gj_path, df


# ---------------------------------------------------------------------------
# pipeline driver (2a -> 2f) + determinism + perturbation
# ---------------------------------------------------------------------------
def run_pipeline():
    """Run 2a -> 2f at the frozen TRUTH_MATCH_M. Returns a results dict."""
    hydro = stage_2a_hydrology()
    zone = classify_master_zone(hydro["master_km2"])
    if zone == "ABORT":
        raise GateAbort(f"Master outlet {hydro['master_km2']:.2f} km^2 in ABORT zone (FM-1).")

    outlets = stage_2b_outlets(hydro)
    assets = load_assets(ASSETS_GJ)          # GeoDataFrame; src/ingest.py
    _assert_metric_crs(assets.crs, "assets.geojson")
    asset_xy = np.column_stack([assets.geometry.x.values, assets.geometry.y.values])
    basins = stage_2c_delineate(hydro, outlets, asset_xy)

    ranked, n_ties = stage_2e_score(hydro, basins)

    creeks = load_creeks(CREEKS_GJ)          # GeoDataFrame; src/ingest.py
    _assert_metric_crs(creeks.crs, "creeks.geojson")
    if not creeks.geometry.is_valid.all():
        raise GateAbort("Invalid creek geometry -- FM-10 (geometry abort, not a match miss).")
    creek_nearest = compute_creek_nearest(basins, creeks, hydro["transform"])
    metrics = evaluate(basins, ranked, creek_nearest, TRUTH_MATCH_M)

    return {"hydro": hydro, "zone": zone, "outlets": outlets, "basins": basins,
            "ranked": ranked, "n_ties": n_ties, "creeks": creeks,
            "creek_nearest": creek_nearest, "metrics": metrics}


def perturbation_probe(basins, ranked, creek_nearest):
    """Re-run the creek->outlet match at several TRUTH_MATCH_M values (the circularity probe)."""
    rows = []
    for m in (150, 200, 300, 350):
        ev = evaluate(basins, ranked, creek_nearest, m)
        rows.append((m, ev["matched_flowed_count"],
                     f"{ev['flowed_in_top']}/{ev['n_flowed']}",
                     ev["flowed_in_top"] == ev["n_flowed"] and ev["n_flowed"] >= 6,
                     ev["rank1_is_flowed"]))
    return rows


def _ranking_signature(basins):
    """Stable signature of the ranking for determinism diffing."""
    return [(b["basin_id"], b["rank"], round(b["score"], 9), round(b["area_km2"], 9))
            for b in sorted(basins, key=lambda x: x["basin_id"])]


# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 74)
    print("P0.5 gate.py -- full reconstruction run (2a -> 2f), SBS / Thomas Fire / Montecito")
    print("=" * 74)

    R = run_pipeline()
    hydro, basins, ranked, m = R["hydro"], R["basins"], R["ranked"], R["metrics"]

    # --- 2a/2b/2c summary ---
    print(f"\n[2a] master outlet = {hydro['master_km2']:.2f} km^2 (known {MASTER_KNOWN_KM2}) "
          f"-> {R['zone']}  [row,col={hydro['master_rowcol']}, index mode, FM-1 guard]")
    print(f"[2b] canyon-mouth outlets detected = {len(R['outlets'])}")
    print(f"[2c] candidate basins = {len(basins)} (report: 32); no exact score ties: {R['n_ties']==0}")

    # --- 2e score decomposition (ranked) ---
    print("\n[2e] SCORE = mean_burn x mean_slope(tan) x area_km2   (A17: coverage-weighted, class 15 -> 0.0 incl)")
    print(f"     {'rk':>2} {'id':>3} {'score':>7} {'burn':>5} {'slope':>6} {'area':>6} "
          f"{'cov':>5} {'flowed':>6} creek")
    for b in ranked[:10]:
        print(f"     {b['rank']:2d} {b['basin_id']:3d} {b['score']:7.3f} {b['mean_burn']:5.3f} "
              f"{b['mean_slope']:6.3f} {b['area_km2']:6.2f} {b['burn_coverage_frac']:5.2f} "
              f"{str(b['flowed']):>6} {b['matched_creek']}")
    print("     report top-8 scores: 3.199, 3.158, 1.846, 0.707, 0.673, 0.672, 0.630, 0.131")
    print(f"     low-coverage basins (<{BURN_LOW_COVERAGE:.0%} SBS): {m['low_coverage_basins']}")

    # A17 coverage-weighting: documented flowed basins are well-covered, so mean_burn is reliable
    print("\n     flowed-basin coverage (A17 mean_burn reliable where coverage ~1.0):")
    for b in sorted([x for x in basins if x["flowed"]], key=lambda x: x["rank"]):
        print(f"       {b['matched_creek']:<18} mean_burn={b['mean_burn']:.3f}  coverage={b['burn_coverage_frac']:.2f}")

    # --- 2f creek match table ---
    print("\n[2f] CREEK -> OUTLET MATCH (whole-line min distance, <= 250 m -> flowed)")
    for _, creek in R["creeks"].iterrows():
        info = R["creek_nearest"][creek["name"]]
        status = "MATCHED" if info["dist_m"] <= TRUTH_MATCH_M else "UNMATCHED"
        print(f"     {creek['name']:<18} -> basin {info['basin_id']:>3}  {info['dist_m']:7.1f} m  {status}")
    for creek, dist in m["unmatched"]:
        print(f"     !! UNMATCHED creek: {creek} (nearest outlet {dist:.1f} m > {TRUTH_MATCH_M} m) -- FINDING")

    # --- gate-value block (quoted verbatim into the report) ---
    print("\n----- GATE VALUES -----")
    print(f"matched_flowed_count = {m['matched_flowed_count']} of 6")
    print(f"flowed_in_top_tercile = {m['flowed_in_top']} of {m['n_flowed']}          # top {m['tercile_k']}")
    print(f"rank1_is_flowed = {m['rank1_is_flowed']}")
    print(f"rank1_creek = {m['rank1_creek']}")
    print(f"master_area_km2 = {hydro['master_km2']:.2f}")
    print(f"auc = {m['auc']:.4f}   n_pairs = {m['n_pairs']}")
    print(f"discordant_pairs = {m['n_discordant']}   discordant_are_fm3 = {m['discordant_are_fm3']}")
    print(f"flowed_mean_score = {m['flowed_mean_score']:.3f}   nonflowed_mean_score = {m['nonflowed_mean_score']:.3f}")
    print(f"low_coverage_basins = {m['low_coverage_basins']}")
    print("-----------------------")

    # discordant pairs (the AUC-costing pairs)
    print("\n     discordant pairs (non-flowed score >= flowed score):")
    if not m["discordant"]:
        print("       (none)")
    for fid, fcreek, farea, fscore, nfid, nfarea, nfscore in m["discordant"]:
        print(f"       flowed b{fid} ({fcreek}, {farea:.2f} km^2, {fscore:.3f}) "
              f"<= non-flowed b{nfid} ({nfarea:.2f} km^2, {nfscore:.3f})")

    # --- perturbation probe (unconditional) ---
    print("\n[probe] TRUTH_MATCH_M sweep (frozen reported value = 250):")
    print(f"     {'match_m':>7} {'flowed':>6} {'in_top':>7} {'6/6_top':>8} {'#1_flowed':>9}")
    for mm, cnt, intop, sixsix, r1 in perturbation_probe(basins, ranked, R["creek_nearest"]):
        print(f"     {mm:7d} {cnt:6d} {intop:>7} {str(sixsix):>8} {str(r1):>9}")

    # --- outputs ---
    csv_path, gj_path, _ = write_outputs(basins, R["creek_nearest"])
    print(f"\n[out] wrote {csv_path.relative_to(ROOT.parent)} and {gj_path.relative_to(ROOT.parent)}")

    # --- determinism: actual second end-to-end run, diffed ---
    print("\n[determinism] second end-to-end run, diffed against the first:")
    R2 = run_pipeline()
    sig1, sig2 = _ranking_signature(basins), _ranking_signature(R2["basins"])
    if sig1 == sig2:
        print(f"     IDENTICAL: {len(sig1)} basins, ranks/scores/areas match exactly.")
    else:
        ndiff = sum(1 for a, b in zip(sig1, sig2) if a != b) + abs(len(sig1) - len(sig2))
        print(f"     DIFFER in {ndiff} rows (non-deterministic!).")

    print("\n" + "=" * 74)
    print("END OF RUN -- classify against success bands (see report). STOP for owner decision.")
    print("=" * 74)


if __name__ == "__main__":
    try:
        main()
    except GateAbort as exc:
        print(f"\nGATE ABORT (fail-loud, FM-10): {exc}", file=sys.stderr)
        sys.exit(2)
