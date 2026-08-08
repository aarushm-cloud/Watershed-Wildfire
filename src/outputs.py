"""outputs.py -- write the deliverables (ranking.csv, basins.geojson, static maps), stamped
with burn-source provenance + the screening framing (A11).

The DAG sink: imports only third-party; serialization only -- never recomputes a score/rank
or re-decides the burn source.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio import features as rfeatures
from shapely.geometry import shape as shapely_shape
from shapely.ops import unary_union

# The ethical spine, stamped into every artifact (A11). Byte-frozen; do not reword.
SCREENING_STATEMENT = ("Within-fire relative screening ranking of watersheds warranting closer "
                       "assessment -- not a prediction of where debris will go. Not cross-fire comparable.")

DUAL_RANK_MAP_NAME = "map_dual_rank.png"   # single source of truth; app.py re-derives this path


def build_refusal_message(reason_code, span_m, span_threshold_m):
    """Human-readable refusal text for a terrain-applicability REFUSE. Span-based, never
    modality-based (A27.1). span_m / span_threshold_m in metres."""
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
    """Write {out_dir}/{ranking.csv, basins.geojson}, stamped burn_source + screening framing."""
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
            "slope_coverage_frac": round(b["slope_coverage_frac"], 4),   # F4: clean (non-nodata-ring) fraction
            "low_slope_coverage": b["low_slope_coverage"],               # F4: flagged if scored on a small remnant
            "area_km2": round(b["area_km2"], 4), "burn_coverage_frac": round(b["burn_coverage_frac"], 4),
            "low_coverage": b["low_coverage"],
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
                      "slope_coverage_frac": round(b["slope_coverage_frac"], 4),   # F4
                      "low_slope_coverage": b["low_slope_coverage"],               # F4
                      "area_km2": round(b["area_km2"], 4),
                      "burn_coverage_frac": round(b["burn_coverage_frac"], 4),
                      "low_coverage": b["low_coverage"],
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
    "Rows are ranked by the frozen `score` (burn x slope x area), as on range-front fires. An "
    "`intensity` companion column (mean_burn x mean_slope, area-independent) is shown because the "
    "score's area term depends on the segmentation threshold here -- and intensity scored HIGHER than "
    "score on the one (range-front) validation case, so treat BOTH as exploratory. KNOWN OPEN "
    "LIMITATION: where dissected terrain is uniformly steep, mean_slope may not discriminate between "
    "basins, in which case the `intensity` companion approaches a burn-severity ranking. For an "
    "authoritative assessment consult USGS or your state geological survey."
)


def _refusal_reason(nodata_frac):
    """The fixed refusal-reason string (A41), shared by the CSV and GeoJSON sidecars."""
    return f"dNBR NoData {nodata_frac:.0%} > 20% (cloud/scene-edge)"


def _mask_features(records, transform, crs, props):
    """Vectorise each record's boolean mask -> polygon (the same rasterio.features.shapes call,
    transform and CRS basins.geojson uses), pair with caller-built props, reproject to
    EPSG:4326. Shared by the clean and refused GeoJSON writers so geometry can never diverge
    (A41)."""
    geoms = []
    for r in records:
        mask = r["mask"].astype(np.uint8)
        polys = [shapely_shape(geom) for geom, val in
                 rfeatures.shapes(mask, mask=r["mask"], transform=transform) if val == 1]
        geoms.append(unary_union(polys))
    return gpd.GeoDataFrame(props, geometry=geoms, crs=crs).to_crs("EPSG:4326")


def write_dnbr_outputs(arm_a, arm_b, creek_nearest, out_dir, dem_tif,
                       validation_case, incised=False, subbasin_meta=None,
                       refused=None, imagery=None):
    """Write {out_dir}/{ranking.csv, basins.geojson} for the dNBR both-arms path (A34): Arm A
    headline (rank/score), Arm B companion (rank_b/score_b), rank_delta uncertainty flag.
    validation_case is REQUIRED (no default -- a direct caller must not silently stamp
    "Montecito"). incised=True appends intensity companion columns + INCISED_FRAMING (A39/A40).

    refused (A41 Task 3): result["refused_basins"] records, or None/[] on a clean run. ALL
    refusal rendering (sidecars, banner, provenance counts) is gated strictly on `if refused:`,
    so a zero-refused run's pre-existing output is untouched (projection-identity) apart from
    the always-on nodata_frac column. imagery: optional {sensor, pre_id, pre_date, post_id,
    post_date} -> one header line (A21); omitted (None) on paths with no pair provenance."""
    if not arm_a["basins"]:                            # F9: never emit an empty artifact (A8 fail-loud)
        raise ValueError("write_dnbr_outputs: refusing to write outputs for 0 basins -- the "
                         "delineation produced none; an empty ranking is indistinguishable from a "
                         "broken run (A8 fail-loud).")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Purge superseded-run debris: a stale incised map or refusal sidecar must never sit beside
    # accepted/clean output.
    (out_dir / "refusal.json").unlink(missing_ok=True)
    (out_dir / DUAL_RANK_MAP_NAME).unlink(missing_ok=True)
    (out_dir / "refused_basins.csv").unlink(missing_ok=True)
    (out_dir / "refused_basins.geojson").unlink(missing_ok=True)
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
        nd = a.get("nodata_frac")   # A41: every pipeline basin carries it; "" fallback for hand-built test basins
        row["nodata_frac"] = round(nd, 4) if nd is not None else ""
        if incised:   # A39: appended LAST -- pandas headers follow dict insertion order
            row["intensity"] = round(a.get("intensity"), 6)   # score-family precision (score/score_b)
            row["intensity_rank"] = int(a.get("intensity_rank"))
        rows.append(row)
    # A40: incised rows stay in the frozen `rank` order from the loop above (headline = score, same as
    # range-front); intensity/intensity_rank ride along as companion columns, no re-sort.
    banner = None
    if refused:
        # A41: M is the visible universe on THIS map (ranked + refused); on incised fires the
        # refused ids are phase-1-denominated (see the refused_basins sidecar note below).
        n_refused, m_total = len(refused), len(rows) + len(refused)
        banner = (f"{n_refused} of {m_total} basins could not be assessed (insufficient "
                 "cloud-free imagery). Their hazard is UNKNOWN -- not low. Any refused basin "
                 "could rank high if data existed; see refused_basins.csv.")

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
        if imagery:   # A21: winning-pair provenance; absent on paths with no scene pair (upload)
            fh.write(f"# imagery: {imagery['sensor']} pre {imagery['pre_id']} "
                     f"({imagery['pre_date']}) -> post {imagery['post_id']} "
                     f"({imagery['post_date']})\n")
        if banner:
            fh.write(f"# {banner}\n")
        df.to_csv(fh, index=False)

    # basins.geojson: vectorise each Arm A basin mask, reproject to EPSG:4326, both-arm properties.
    with rasterio.open(dem_tif) as s:
        transform = s.transform
        dem_crs = s.crs              # A25: per-fire CRS read off the DEM handle (not a constant)
    ordered_basins = sorted(arm_a["basins"], key=lambda x: x["rank"])
    props = []
    for a in ordered_basins:
        bid = a["basin_id"]
        b = b_by[bid]
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
    gdf = _mask_features(ordered_basins, transform, dem_crs, props)   # A41: shared geometry path
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

    # A41: basins.geojson stays CLEAN-ONLY; refused geometry gets its own sidecar. On incised
    # fires phase1_basin_id is NOT basin_id's id space (renumbered clean ids can collide by
    # value) -- never join the two on id; geometry is the authoritative join.
    refused_gj_path = out_dir / "refused_basins.geojson"
    if refused:
        provenance["refused_count"] = len(refused)
        provenance["n_basins_total"] = len(rows) + len(refused)
        refused_sorted = sorted(refused, key=lambda r: r["basin_id"])
        refused_props = [{"phase1_basin_id": r["basin_id"],
                          "nodata_frac": round(r["nodata_frac"], 4),
                          "reason": _refusal_reason(r["nodata_frac"])} for r in refused_sorted]
        _mask_features(refused_sorted, transform, dem_crs, refused_props).to_file(
            refused_gj_path, driver="GeoJSON")

        refused_rows = []
        for r in refused_sorted:
            # area_km2 is attached at delineation time (stage_2c_delineate / build_geometry_
            # records), BEFORE the partition -- every refused record has it. Read directly;
            # a missing key is a broken invariant upstream, not a gap to paper over here.
            ms = r.get("mean_slope")
            ms_val = "" if ms is None or np.isnan(ms) else round(ms, 4)
            refused_rows.append({"phase1_basin_id": r["basin_id"],
                                 "nodata_frac": round(r["nodata_frac"], 4),
                                 "reason": _refusal_reason(r["nodata_frac"]),
                                 "area_km2": round(r["area_km2"], 4), "mean_slope": ms_val})
        pd.DataFrame(refused_rows).to_csv(out_dir / "refused_basins.csv", index=False)

    fc["provenance"] = provenance
    with open(gj_path, "w") as fh:
        json.dump(fc, fh)
    if incised:   # A39 product artifact: the dual-rank map travels ONLY on incised output
        write_dual_rank_map(gj_path, dem_tif, out_dir / DUAL_RANK_MAP_NAME, validation_case,
                            refused_gj_path=refused_gj_path)
    return csv_path, gj_path


def write_dual_rank_map(gj_path, dem_path, out_png, fire_label, top_n=8, refused_gj_path=None):
    """Static dual-rank PNG for the incised path (A39/A40): score-rank panel (headline) beside
    intensity-rank panel, over a DEM hillshade, exploratory framing in the footer. Deterministic.

    refused_gj_path (A41 Task 3): refused_basins.geojson sidecar -- drawn as a gray cross-hatch
    layer with a legend entry ONLY when the file exists (a clean run's path was never written)."""
    import matplotlib
    matplotlib.use("Agg")   # headless render; never a GUI backend
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    from matplotlib.patches import Patch

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
    refused_gdf = None
    if refused_gj_path is not None and Path(refused_gj_path).exists():
        refused_gdf = gpd.read_file(refused_gj_path).to_crs(dem_crs)
        if refused_gdf.empty:
            refused_gdf = None
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
            if refused_gdf is not None:   # A41: hazard-unknown, never absence-reads-as-safe
                refused_gdf.plot(ax=ax, facecolor="none", edgecolor="dimgray", hatch="////",
                                 linewidth=0.6, zorder=4)
            ax.set_title(title)
            ax.set_xlabel("Easting (m)")
        axes[0].set_ylabel("Northing (m)")
        if refused_gdf is not None:
            legend_patch = Patch(facecolor="none", edgecolor="dimgray", hatch="////",
                                 label="refused -- insufficient data (hazard unknown)")
            axes[1].legend(handles=[legend_patch], loc="lower right", fontsize=7, framealpha=0.85)
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
