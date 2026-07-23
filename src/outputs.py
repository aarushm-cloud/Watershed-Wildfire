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

# A39 dual-rank map filename -- single source of truth so the writer and app.py's attach sites
# (which re-derive the path independently) cannot drift apart (map-export review Fix 3).
DUAL_RANK_MAP_NAME = "map_dual_rank.png"


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
    (out_dir / "refusal.json").unlink(missing_ok=True)  # purge superseded-run debris (owner ruling)
    (out_dir / DUAL_RANK_MAP_NAME).unlink(missing_ok=True)  # ditto: a stale incised-run map must
    # not survive an accepted (SBS) re-run into the same out_dir -- intensity must NEVER appear on
    # accepted-fire output (map-export review Fix 1).
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


# A39 incised-terrain framing, carried on every incised (WhiteboxTools sub-basin) dNBR artifact --
# exploratory, unvalidated on this terrain class. Do not soften.
INCISED_FRAMING = (
    "EXPLORATORY -- INCISED TERRAIN (A39). This fire lacks the range-front-over-plain "
    "geometry the validated method assumes. Basins are whole-network sub-basins split at "
    "channel confluences by WhiteboxTools -- NOT canyon-mouth catchments, NOT anchored to a "
    "mountain front -- so individual boundaries may be approximate. Read this as relative "
    "SOURCE susceptibility for triage only: it does NOT indicate runout, deposition, or "
    "which fan is threatened. Within-fire ordinal only -- never compare across fires. "
    "UNVALIDATED ON THIS TERRAIN CLASS: the method's outcome evidence comes from one "
    "range-front fire (Montecito, effective n=6 flow events), not from incised terrain. "
    "Rows are ordered by `intensity` (mean_burn x mean_slope), which is independent of "
    "basin size; the `score` column retains the frozen burn x slope x area formula but its "
    "area term depends on the segmentation threshold here. KNOWN OPEN LIMITATION: where "
    "dissected terrain is uniformly steep, mean_slope may not discriminate between basins, "
    "in which case this ordering approaches a burn-severity ranking. For an authoritative "
    "assessment consult USGS or your state geological survey."
)


def write_dnbr_outputs(arm_a, arm_b, creek_nearest, out_dir, dem_tif,
                       validation_case, incised=False, subbasin_meta=None):
    """Write {out_dir}/{ranking.csv, basins.geojson} for the dNBR BOTH-ARMS path (A34/P2.2c).

    Arm A (binned) is the headline ranking (rank/score); Arm B (continuous) rides alongside
    (rank_b/score_b) with rank_delta = |rankA - rankB| as the honest uncertainty flag. Every artifact
    carries SCREENING_STATEMENT + the n=1 DNBR_FRAMING + burn_source=dNBR. This is the dNBR sibling of
    write_outputs (which stays the untouched SBS single-arm writer); it recomputes no score/rank.

    arm_a / arm_b -- the per-arm dicts from run_pipeline (each carries 'basins' scored + 'ranked').
    validation_case -- REQUIRED provenance stamp (no default: a careless direct caller must not silently
    stamp a real fire "Montecito"). creek_nearest -- per-creek nearest-outlet info, or None (a real fire
    with no truth-creek layer).
    out_dir -- output dir; dem_tif -- DEM path for the GeoJSON transform/CRS re-open (A25).
    incised -- A39 sub-basin path: appends intensity/intensity_rank, orders rows by intensity_rank,
    stamps INCISED_FRAMING, adds engine provenance. Default False is byte-identical to pre-A39 output.
    subbasin_meta -- WhiteboxTools engine metadata (engine/wbt_version/acc_threshold_cells/
    breach_dist_cells), stamped into GeoJSON provenance when incised."""
    if not arm_a["basins"]:                            # F9: never emit an empty artifact (A8 fail-loud)
        raise ValueError("write_dnbr_outputs: refusing to write outputs for 0 basins -- the "
                         "delineation produced none; an empty ranking is indistinguishable from a "
                         "broken run (A8 fail-loud).")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "refusal.json").unlink(missing_ok=True)  # purge superseded-run debris (owner ruling)
    (out_dir / DUAL_RANK_MAP_NAME).unlink(missing_ok=True)  # ditto: a stale incised-run map must
    # not survive an accepted (incised=False) re-run into the same out_dir -- intensity must NEVER
    # appear on accepted-fire output (map-export review Fix 1); regenerated fresh below if incised.
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
        row = {
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
        }
        if incised:   # A39: appended LAST -- pandas headers follow dict insertion order
            row["intensity"] = round(a.get("intensity"), 6)   # score-family precision (score/score_b)
            row["intensity_rank"] = int(a.get("intensity_rank"))
        rows.append(row)
    if incised:   # A39: intensity is the headline ordering on incised terrain, not the frozen rank
        rows.sort(key=lambda r: r["intensity_rank"])
    df = pd.DataFrame(rows)
    csv_path = out_dir / "ranking.csv"
    with open(csv_path, "w") as fh:
        # consumer contract: the leading '#' lines are provenance/framing -- read the table with
        # pd.read_csv(path, comment='#'); the default reader would treat them as data rows.
        fh.write(f"# {SCREENING_STATEMENT}\n")
        fh.write(f"# {DNBR_FRAMING}\n")
        if incised:
            fh.write(f"# {INCISED_FRAMING}\n")
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
        feat_props = {"basin_id": bid, "rank": a["rank"], "score": round(a["score"], 6),
                      "rank_b": b["rank"], "score_b": round(b["score"], 6),
                      "rank_delta": abs(a["rank"] - b["rank"]),
                      "mean_burn_a": round(a["mean_burn"], 4), "mean_burn_b": round(b["mean_burn"], 4),
                      "mean_slope": round(a["mean_slope"], 4), "area_km2": round(a["area_km2"], 4),
                      "slope_coverage_frac": round(a["slope_coverage_frac"], 4),   # F4
                      "low_slope_coverage": a["low_slope_coverage"],               # F4
                      "burn_coverage_frac": round(a["burn_coverage_frac"], 4),
                      "low_coverage": a["low_coverage"],                          # minor: parity with the CSV
                      "flowed": a.get("flowed", False), "matched_creek": a.get("matched_creek", ""),
                      "burn_source": "dNBR", "screening": SCREENING_STATEMENT}
        if incised:
            feat_props["intensity"] = round(a.get("intensity"), 6)   # score-family precision (score/score_b)
            feat_props["intensity_rank"] = int(a.get("intensity_rank"))
        props.append(feat_props)
    gdf = gpd.GeoDataFrame(props, geometry=geoms, crs=dem_crs).to_crs("EPSG:4326")
    gj_path = out_dir / "basins.geojson"
    gdf.to_file(gj_path, driver="GeoJSON")
    with open(gj_path) as fh:
        fc = json.load(fh)
    provenance = {"burn_source": "dNBR", "screening": SCREENING_STATEMENT,
                 "dnbr_framing": DNBR_FRAMING, "headline_arm": "arm_a (binned)",
                 "companion_arm": "arm_b (continuous)",
                 "validation_case": validation_case, "crs": "EPSG:4326"}
    if incised:
        provenance["incised_framing"] = INCISED_FRAMING
        if subbasin_meta:
            provenance["basin_engine"] = subbasin_meta.get("engine")
            provenance["wbt_version"] = subbasin_meta.get("wbt_version")
            provenance["acc_threshold_cells"] = subbasin_meta.get("acc_threshold_cells")
            provenance["breach_dist_cells"] = subbasin_meta.get("breach_dist_cells")
    fc["provenance"] = provenance
    with open(gj_path, "w") as fh:
        json.dump(fc, fh)
    if incised:   # A39 product artifact: the dual-rank map travels ONLY on incised output
        write_dual_rank_map(gj_path, dem_tif, out_dir / DUAL_RANK_MAP_NAME, validation_case)
    return csv_path, gj_path


def write_dual_rank_map(gj_path, dem_path, out_png, fire_label, top_n=8):
    """Static dual-rank PNG for the incised (A39) path: two panels over a grey DEM hillshade --
    LEFT the frozen score rank (SIZE, burn x slope x area), RIGHT the intensity rank
    (burn x slope, the incised headline). Rank 1 renders brightest; the top-{top_n} basins of
    each panel get numbered circular badges at their representative points.

    Reads the just-written basins.geojson back (rank / intensity_rank feature properties) and
    reprojects it to the DEM CRS so both layers draw in metric coordinates (A25: the per-fire
    CRS comes off the DEM handle). Carries the EXPLORATORY framing: suptitle + a footer with the
    first sentence of INCISED_FRAMING verbatim -- never wording that implies validation or
    prediction. Deterministic (no timestamps). Agg backend, function-local matplotlib import so
    module load stays light; the figure is closed before return. Returns out_png."""
    import matplotlib
    matplotlib.use("Agg")   # headless render; never a GUI backend
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource

    with rasterio.open(dem_path) as s:
        dem = s.read(1).astype("float64")
        if s.nodata is not None:
            dem[dem == s.nodata] = np.nan
        extent = (s.bounds.left, s.bounds.right, s.bounds.bottom, s.bounds.top)
        dem_crs = s.crs
        dx, dy = abs(s.transform.a), abs(s.transform.e)   # cell size (m) for the vert_exag math
    valid = np.isfinite(dem)
    # flat-fill nodata so the gradient (and matplotlib's contrast stretch) stays NaN-free,
    # then blank those cells back out -- nodata renders empty, never as fake terrain
    hs = LightSource(azdeg=315, altdeg=45).hillshade(
        np.where(valid, dem, np.nanmin(dem) if valid.any() else 0.0), vert_exag=1.0, dx=dx, dy=dy)
    hs = np.where(valid, hs, np.nan)

    gdf = gpd.read_file(gj_path).to_crs(dem_crs)   # writer stored EPSG:4326; draw metric
    n = len(gdf)
    # size the figure from the DEM aspect (panels draw with equal metric aspect) so tall or wide
    # extents don't leave dead whitespace; clamped so a degenerate extent can't blow the canvas
    panel_h = min(max(8.0 * (extent[3] - extent[2]) / (extent[1] - extent[0]), 3.0), 10.0)
    fig, axes = plt.subplots(1, 2, figsize=(16, panel_h + 1.6), sharex=True, sharey=True)
    try:   # figure-leak guard: any exception below must still close fig (pyplot's global manager
        # holds it open for the life of this long-running Streamlit process otherwise)
        panels = (("rank", "SIZE rank (burn·slope·area)", "magma"),
                  ("intensity_rank", "INTENSITY rank (burn·slope)", "viridis"))
        for ax, (col, title, cmap_name) in zip(axes, panels):
            ax.imshow(hs, cmap="gray", extent=extent)
            cmap = plt.get_cmap(cmap_name)
            # rank 1 = brightest end of the colormap; last rank = darkest
            colors = [cmap(1.0 - (r - 1) / max(n - 1, 1)) for r in gdf[col]]
            gdf.plot(ax=ax, color=colors, alpha=0.55, edgecolor="black", linewidth=0.4)
            for _, row in gdf[gdf[col] <= top_n].iterrows():
                pt = row.geometry.representative_point()
                ax.text(pt.x, pt.y, str(int(row[col])), ha="center", va="center", fontsize=9,
                        fontweight="bold", color="black", zorder=5,
                        bbox=dict(boxstyle="circle,pad=0.25", fc="white", ec="black", alpha=0.9))
            ax.set_title(title)
            ax.set_xlabel("Easting (m)")
        axes[0].set_ylabel("Northing (m)")
        fig.suptitle(f"{fire_label} — EXPLORATORY (incised terrain, A39) | {n} sub-basins",
                     fontsize=14)
        # degradation contract: split on the sentence boundary ". " (not the first raw "."), so a
        # future rewording with a mid-sentence decimal (e.g. "0.25") can't truncate the footer
        # mid-clause; a rewording with no ". " at all falls back to the FULL string rather than
        # raising (a ValueError here would otherwise propagate as a bare "substring not found").
        parts = INCISED_FRAMING.split(". ", 1)
        first_sentence = parts[0] + "." if len(parts) > 1 else INCISED_FRAMING
        fig.text(0.5, 0.01, f"{first_sentence} Full framing: ranking.csv header.",
                 ha="center", fontsize=8, style="italic")
        fig.savefig(out_png, dpi=150, bbox_inches="tight")
    finally:
        plt.close(fig)
    return out_png
