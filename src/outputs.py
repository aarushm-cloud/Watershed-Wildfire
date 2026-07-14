"""outputs.py -- write the deliverables (ranking.csv, basins.geojson, static
map), each stamped with burn-source provenance and the screening / 'what this
is / is not' framing. See ARCHITECTURE.md and DECISIONS A4/A11.

P1.6 SCOPE (behavior-preserving extract from validation/gate.py): write_outputs + the
SCREENING_STATEMENT (A11) framing it emits, lifted VERBATIM. EXPLICIT-ARGS signature (no dict-bag):
out_dir, dem_tif (path for the transform re-open), and burn_source (provenance, READ-ONLY) arrive as
named args -- this module is the DAG SINK: it imports only third-party (NO project modules at all,
A25: the per-fire output CRS is read off the DEM handle, not from config), and NEVER imports
ingest/score/delineate/hydrology/grids/config, never re-derives or re-asserts the burn source
(A4/A15: burn-source selection lives only in ingest). Serialization + formatting only -- it writes the
`rank` score/delineate produced, never recomputes it. No new types (C9).

PRESERVED VERBATIM (deferred items, NOT touched this phase): the vestigial hardcoded
"drains_to_asset": True; the DEM-transform re-open (georeferences the basins.geojson polygons --
live output, not vestigial). SCREENING_STATEMENT is byte-identical (the ethical spine in the artifact).

IMPORT-TIME I/O BAN: nothing executes at module load (the geopandas/GDAL init I/O on import is the
library's, not this module's). Writes happen only inside write_outputs.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio import features as rfeatures
from shapely.geometry import shape as shapely_shape
from shapely.ops import unary_union

# A11 framing stamped into every artifact -- screening, never prediction. Byte-identical; do not reword.
SCREENING_STATEMENT = ("Within-fire relative screening ranking of watersheds warranting closer "
                       "assessment -- not a prediction of where debris will go. Not cross-fire comparable.")

_log = logging.getLogger(__name__)


def build_refusal_message(reason_code, span_m, span_threshold_m):
    """Span-based, human-readable refusal text for an A27 terrain-applicability REFUSE.

    SPAN-BASED, never modality-based (A27.1): the message cites the measured percentile span and the
    absence of a compact depositional plain -- it makes NO mode-count / "single mode" claim (the
    committed South Fork DEM is weakly bimodal, so a single-mode claim would be factually wrong).
    Plain prose, no em-dashes.

    reason_code      -- the detector's reason_code (assess_hypsometric_applicability).
    span_m           -- measured valid-cell (p10 - p1) elevation span (m).
    span_threshold_m -- the frozen A27 threshold (m), for context.
    """
    if reason_code == "REFUSED_INCISED_TERRAIN":
        return (
            f"Refused: this fire's terrain is an incised valley, not a steep range above a flat "
            f"plain. The elevation spread near the valley floor is {span_m:.0f} m between the 1st "
            f"and 10th percentiles, far wider than the ~20 to 30 m a compact depositional plain "
            f"shows. The tool ranks canyons by where they spill onto flatter ground; this terrain "
            f"has no mountain-front break, so there are no canyon mouths to anchor to and no "
            f"ranking is produced. This is a known boundary of the method, not a failure."
        )
    # Not a refusal -- this builder is only reached on REFUSE in normal use; return a neutral line.
    return (f"Terrain applicable: valley-floor elevation span is {span_m:.0f} m "
            f"(threshold {span_threshold_m:.0f} m); range-front-over-plain anchoring is well-posed.")


def write_refusal(verdict, out_dir):
    """Write {out_dir}/refusal.json for an A27 terrain-applicability REFUSE (A27 / A27.1).

    Emits an honest, legible refusal instead of a ranking: on incised terrain the scored basins are
    the upslope catchments of the CONTOUR_M mountain-front anchor that this terrain does not define,
    so there are no basins and no scores to caveat. ranking.csv / basins.geojson are NOT written on
    REFUSE (the caller does not call write_outputs; gate wiring is build-2). Does not crash.

    verdict  -- the detector dict from delineate.assess_hypsometric_applicability:
                {refuse, reason_code, span_m, span_threshold_m, n_valid}.
    out_dir  -- output directory path (Path-like, gate-owned), same convention as write_outputs.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    message = build_refusal_message(verdict["reason_code"], verdict["span_m"],
                                    verdict["span_threshold_m"])
    payload = {
        "status": "REFUSED",
        "reason_code": verdict["reason_code"],
        "trigger": "hypsometric_span",
        "span_m": verdict["span_m"],
        "span_threshold_m": verdict["span_threshold_m"],
        "n_valid": verdict["n_valid"],
        "message": message,
        "screening": SCREENING_STATEMENT,
        "ranking_produced": False,
        "explanation": ("No within-fire ranking is produced: the scored basins are the upslope "
                        "catchments of CONTOUR_M-anchored canyon-mouth outlets, an anchor incised "
                        "terrain does not define. No anchor, no basins, no scores to caveat "
                        "(DECISIONS A27 / A27.1)."),
    }
    refusal_path = out_dir / "refusal.json"
    with open(refusal_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    _log.info("A27 refusal written: %s (reason_code=%s, span_m=%.4f m, threshold=%.1f m)",
              refusal_path, verdict["reason_code"], verdict["span_m"], verdict["span_threshold_m"])
    return refusal_path


def write_outputs(basins, creek_nearest, out_dir, dem_tif, burn_source,
                  validation_case="Thomas_Fire_2017/Montecito_2018"):
    """Write {out_dir}/{ranking.csv, basins.geojson}, stamped burn_source + screening (A4/A11).

    Args (explicit, from gate's call site): basins -- scored basins (rank/score/decomposition/mask/
    flowed/matched_creek); creek_nearest -- per-creek nearest-outlet info; out_dir -- output dir path
    (gate-owned); dem_tif -- DEM path for the transform re-open; burn_source -- provenance string
    (read-only, from ingest via gate); validation_case -- per-fire provenance stamp (A30), defaulting
    to the Montecito case so the no-kwarg call is byte-identical. SCREENING_STATEMENT is this module's
    constant."""
    if not basins:                                     # F9: never emit an empty artifact (A8 fail-loud)
        raise ValueError("write_outputs: refusing to write outputs for 0 basins -- the delineation "
                         "produced none; an empty ranking is indistinguishable from a broken run "
                         "(A8 fail-loud).")
    out_dir.mkdir(parents=True, exist_ok=True)
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
            # drains_to_asset tautologically True: delineate only emits basins past the 600 m drains-to-asset filter (A19/C9)
            "drains_to_asset": True, "flowed": b["flowed"],
            "matched_creek": b["matched_creek"],
            "nearest_outlet_dist_m": round(near[1], 1) if near[1] is not None else "",
        })
    df = pd.DataFrame(rows)
    csv_path = out_dir / "ranking.csv"
    with open(csv_path, "w") as fh:
        fh.write(f"# {SCREENING_STATEMENT}\n")
        fh.write(f"# burn_source={burn_source}  validation_case={validation_case}\n")
        df.to_csv(fh, index=False)

    # basins.geojson: vectorise each basin mask, reproject to EPSG:4326 (GeoJSON convention)
    transform = None
    with rasterio.open(dem_tif) as s:
        transform = s.transform
        dem_crs = s.crs              # A25: per-fire decided CRS, read from the DEM (== dem_profile["crs"],
        #                              the same CRS gate.py validates the DEM against). NOT a 2nd decision.
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
                      "burn_source": burn_source, "screening": SCREENING_STATEMENT})
    gdf = gpd.GeoDataFrame(props, geometry=geoms, crs=dem_crs).to_crs("EPSG:4326")
    gj_path = out_dir / "basins.geojson"
    gdf.to_file(gj_path, driver="GeoJSON")
    # inject a top-level provenance member (A4/A11)
    with open(gj_path) as fh:
        fc = json.load(fh)
    fc["provenance"] = {"burn_source": burn_source, "screening": SCREENING_STATEMENT,
                        "validation_case": validation_case, "crs": "EPSG:4326"}
    with open(gj_path, "w") as fh:
        json.dump(fc, fh)
    return csv_path, gj_path, df


# A34 dNBR framing (n=1), carried on every dNBR artifact -- triage-validated, NOT exact-rank-validated.
# Do not soften: the pre-registered exact-#1 criterion FAILED on the one validated fire (by 1.03%).
DNBR_FRAMING = (
    "dNBR ranking: triage-validated (finds the flow basins as well as field-validated SBS on the one "
    "validated fire, rank-AUC 0.9722), NOT exact-rank-validated (n=1). Arm A (binned) is the primary "
    "headline ranking; Arm B (continuous) is a companion. rank_delta = |rankA - rankB| flags basins "
    "where the two burn methods disagree -- treat those ranks as uncertain.")


def write_dnbr_outputs(arm_a, arm_b, creek_nearest, out_dir, dem_tif,
                       validation_case):
    """Write {out_dir}/{ranking.csv, basins.geojson} for the dNBR BOTH-ARMS path (A34/P2.2c).

    Arm A (binned) is the headline ranking (rank/score); Arm B (continuous) rides alongside
    (rank_b/score_b) with rank_delta = |rankA - rankB| as the honest uncertainty flag. Every artifact
    carries SCREENING_STATEMENT + the n=1 DNBR_FRAMING + burn_source=dNBR. This is the dNBR sibling of
    write_outputs (which stays the untouched SBS single-arm writer); it recomputes no score/rank.

    arm_a / arm_b -- the per-arm dicts from run_pipeline (each carries 'basins' scored + 'ranked').
    validation_case -- REQUIRED provenance stamp (no default: a careless direct caller must not silently
    stamp a real fire "Montecito"). creek_nearest -- per-creek nearest-outlet info, or None (a real fire
    with no truth-creek layer).
    out_dir -- output dir; dem_tif -- DEM path for the GeoJSON transform/CRS re-open (A25)."""
    if not arm_a["basins"]:                            # F9: never emit an empty artifact (A8 fail-loud)
        raise ValueError("write_dnbr_outputs: refusing to write outputs for 0 basins -- the "
                         "delineation produced none; an empty ranking is indistinguishable from a "
                         "broken run (A8 fail-loud).")
    out_dir.mkdir(parents=True, exist_ok=True)
    b_by = {b["basin_id"]: b for b in arm_b["basins"]}

    nearest_by_basin = {}
    if creek_nearest is not None:
        for creek, info in creek_nearest.items():
            bid = info["basin_id"]
            if bid not in nearest_by_basin or info["dist_m"] < nearest_by_basin[bid][1]:
                nearest_by_basin[bid] = (creek, info["dist_m"])

    rows = []
    for a in sorted(arm_a["basins"], key=lambda x: x["rank"]):   # order by the Arm A headline rank
        bid = a["basin_id"]
        b = b_by[bid]
        near = nearest_by_basin.get(bid, (None, None))
        rows.append({
            "basin_id": bid,
            "rank": a["rank"], "score": round(a["score"], 6),            # Arm A -- headline
            "rank_b": b["rank"], "score_b": round(b["score"], 6),        # Arm B -- companion
            "rank_delta": abs(a["rank"] - b["rank"]),                    # honest uncertainty flag
            "mean_burn_a": round(a["mean_burn"], 4), "mean_burn_b": round(b["mean_burn"], 4),
            "mean_slope": round(a["mean_slope"], 4),                     # identical across arms (terrain)
            "slope_coverage_frac": round(a["slope_coverage_frac"], 4),   # F4: clean (non-nodata-ring) fraction
            "low_slope_coverage": a["low_slope_coverage"],               # F4: flagged if scored on a small remnant
            "area_km2": round(a["area_km2"], 4),                         # identical across arms (delineation)
            "burn_coverage_frac": round(a["burn_coverage_frac"], 4),    # Arm A operational (A23)
            "low_coverage": a["low_coverage"],
            "flowed": a.get("flowed", False), "matched_creek": a.get("matched_creek", ""),
            "nearest_outlet_dist_m": round(near[1], 1) if near[1] is not None else "",
        })
    df = pd.DataFrame(rows)
    csv_path = out_dir / "ranking.csv"
    with open(csv_path, "w") as fh:
        # consumer contract: the leading '#' lines are provenance/framing -- read the table with
        # pd.read_csv(path, comment='#'); the default reader would treat them as data rows.
        fh.write(f"# {SCREENING_STATEMENT}\n")
        fh.write(f"# {DNBR_FRAMING}\n")
        fh.write(f"# burn_source=dNBR  validation_case={validation_case}\n")
        df.to_csv(fh, index=False)

    # basins.geojson: vectorise each Arm A basin mask, reproject to EPSG:4326, both-arm properties.
    with rasterio.open(dem_tif) as s:
        transform = s.transform
        dem_crs = s.crs              # A25: per-fire CRS read off the DEM handle (not a constant)
    geoms, props = [], []
    for a in sorted(arm_a["basins"], key=lambda x: x["rank"]):
        bid = a["basin_id"]
        b = b_by[bid]
        mask = a["mask"].astype(np.uint8)
        polys = [shapely_shape(geom) for geom, val in
                 rfeatures.shapes(mask, mask=a["mask"], transform=transform) if val == 1]
        geoms.append(unary_union(polys))
        props.append({"basin_id": bid, "rank": a["rank"], "score": round(a["score"], 6),
                      "rank_b": b["rank"], "score_b": round(b["score"], 6),
                      "rank_delta": abs(a["rank"] - b["rank"]),
                      "mean_burn_a": round(a["mean_burn"], 4), "mean_burn_b": round(b["mean_burn"], 4),
                      "mean_slope": round(a["mean_slope"], 4), "area_km2": round(a["area_km2"], 4),
                      "slope_coverage_frac": round(a["slope_coverage_frac"], 4),   # F4
                      "low_slope_coverage": a["low_slope_coverage"],               # F4
                      "burn_coverage_frac": round(a["burn_coverage_frac"], 4),
                      "low_coverage": a["low_coverage"],                          # minor: parity with the CSV
                      "flowed": a.get("flowed", False), "matched_creek": a.get("matched_creek", ""),
                      "burn_source": "dNBR", "screening": SCREENING_STATEMENT})
    gdf = gpd.GeoDataFrame(props, geometry=geoms, crs=dem_crs).to_crs("EPSG:4326")
    gj_path = out_dir / "basins.geojson"
    gdf.to_file(gj_path, driver="GeoJSON")
    with open(gj_path) as fh:
        fc = json.load(fh)
    fc["provenance"] = {"burn_source": "dNBR", "screening": SCREENING_STATEMENT,
                        "dnbr_framing": DNBR_FRAMING, "headline_arm": "arm_a (binned)",
                        "companion_arm": "arm_b (continuous)",
                        "validation_case": validation_case, "crs": "EPSG:4326"}
    with open(gj_path, "w") as fh:
        json.dump(fc, fh)
    return csv_path, gj_path
