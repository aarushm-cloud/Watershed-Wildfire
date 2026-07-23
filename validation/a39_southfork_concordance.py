"""A39 -- pre-registered incised-terrain concordance check (South Fork, USGS sfk2024).

READ-ONLY confidence check, NOT a runtime path. It compares the A39 sub-basin `intensity`
ordering against the independent USGS sfk2024 post-fire debris-flow hazard assessment. It scores
NOTHING the pipeline ranks off; it only correlates two already-produced layers.

PRE-REGISTERED before this ran -- vault `DECISIONS.md`, A39 entry, "Concordance check
(pre-registered)". A CONCORDANCE check, never an equivalence claim: the project score is not the
USGS M1 model and must never be described as approximating it (CLAUDE.md). USGS uses rainfall and
soil; this does not.

Method (as pre-registered, with the owner rulings 2026-07-19 baked in):
  * Hazard metric = H_24mmh -- USGS "Combined hazard class for a peak 15-minute rainfall intensity
    of 24 mm/hour"; an ORDINAL class (Spearman on ties uses scipy's average-rank convention).
  * Aggregation = LENGTH-weighted mean (the plan said "area-weighted", but the 1021 USGS segments
    are LineStrings -- length, not area). Each segment is clipped to each A39 sub-basin; a segment
    crossing several basins contributes its within-basin PORTION (length in metres) to each.
  * rho = spearmanr(basin intensity VALUE, aggregated H_24mmh), BOTH oriented higher = more
    concerning; positive rho = concordance. This equals -spearman(intensity_rank, H_24mmh) exactly
    (intensity_rank is a strictly decreasing transform of intensity); the identity is asserted below.
  * Bands (pre-registered): rho >= 0.5 concordant; 0.2 <= rho < 0.5 weak; rho < 0.2 HALT.

The basins.geojson artifact is stored in EPSG:4326 (geographic); the sub-basins were natively
computed in the DEM's projected CRS (EPSG:32613). Both layers are reprojected to that metric CRS
so lengths are in metres -- lengths are meaningless in degrees.

Run:  python validation/a39_southfork_concordance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

HAZARD_FIELD = "H_24mmh"   # owner ruling 2026-07-19; USGS ordinal combined hazard class (24 mm/hour)

BASINS_GEOJSON = _REPO_ROOT / "validation" / "out" / "southfork" / "basins.geojson"
# segment-based method uses sfk2024-segments.shp ONLY (NOT sfk2024-basins.shp -- a different, un-pre-registered object)
SEGMENTS_SHP = _REPO_ROOT / "data" / "southfork" / "reference" / "shp" / "sfk2024-segments.shp"
DEM_TIF = _REPO_ROOT / "data" / "southfork" / "dem" / "dem.tif"   # native metric CRS the basins were built in


def concordance(basins_path, segments_path, dem_path) -> dict:
    """Length-weighted USGS-hazard vs A39-intensity concordance. Returns metrics; no side effects."""
    import geopandas as gpd
    import rasterio
    from scipy.stats import spearmanr

    with rasterio.open(dem_path) as s:
        metric_crs = s.crs                       # EPSG:32613 (projected, metres) for South Fork
    assert metric_crs is not None and metric_crs.is_projected, \
        f"metric CRS must be projected (metres); got {metric_crs!r} -- lengths meaningless in degrees"

    basins = gpd.read_file(basins_path).to_crs(metric_crs)       # A39 sub-basins; carry intensity/intensity_rank
    segments = gpd.read_file(segments_path).to_crs(metric_crs)   # USGS sfk2024 network EPSG:4269 -> metric
    n_total = len(basins)
    n_segments_total = len(segments)

    haz = segments[HAZARD_FIELD].to_numpy(dtype="float64")
    dist_all = segments[HAZARD_FIELD].value_counts(dropna=False).sort_index()   # ordinal-class distribution
    finite = np.isfinite(haz)
    seg = segments.loc[finite, [HAZARD_FIELD]].astype({HAZARD_FIELD: "float64"}).copy()
    seg["geometry"] = segments.loc[finite, "geometry"]
    seg = gpd.GeoDataFrame(seg, geometry="geometry", crs=metric_crs).reset_index(drop=True)
    seg["seg_id"] = seg.index
    n_finite = len(seg)

    # candidate (segment, basin) pairs via spatial index; then the exact within-basin portion length.
    joined = gpd.sjoin(seg[["seg_id", HAZARD_FIELD, "geometry"]],
                       basins[["basin_id", "geometry"]], predicate="intersects", how="inner")
    bgeom = dict(zip(basins["basin_id"], basins.geometry))
    joined["len_m"] = [g.intersection(bgeom[bid]).length            # metres (metric CRS)
                       for g, bid in zip(joined.geometry, joined["basin_id"])]
    joined = joined[joined["len_m"] > 0.0]                          # drop boundary point-touches (len 0)
    n_segments_used = int(joined["seg_id"].nunique())

    # per-basin length-weighted mean of the ordinal hazard class (vectorised, no groupby.apply)
    num = (joined[HAZARD_FIELD] * joined["len_m"]).groupby(joined["basin_id"]).sum()
    den = joined["len_m"].groupby(joined["basin_id"]).sum()
    haz_by_basin = (num / den)                                     # Series: basin_id -> weighted hazard class

    b = basins.set_index("basin_id")
    b = b.assign(haz=haz_by_basin)                                 # NaN where a basin has no intersecting segment
    scored = b.dropna(subset=["haz", "intensity"])
    n_scored = len(scored)

    intensity = scored["intensity"].to_numpy(dtype="float64")      # A39 headline metric (mean_burn x mean_slope)
    hazard = scored["haz"].to_numpy(dtype="float64")               # length-weighted USGS H_24mmh class
    rank = scored["intensity_rank"].to_numpy(dtype="float64")      # 1 = most intense

    rho = float(spearmanr(intensity, hazard).correlation) if n_scored >= 3 else float("nan")
    rho_rank = float(spearmanr(rank, hazard).correlation) if n_scored >= 3 else float("nan")

    return {
        "rho": rho,                                # pre-registered orientation: + = concordance
        "rho_from_rank": rho_rank,                 # = -rho by construction (identity check below)
        "n_scored": n_scored,
        "n_total_basins": n_total,
        "n_excluded": n_total - n_scored,          # basins with zero intersecting USGS segments
        "coverage_frac": n_scored / n_total if n_total else float("nan"),
        "n_segments_total": n_segments_total,
        "n_segments_finite_hazard": n_finite,
        "n_segments_used": n_segments_used,
        "hazard_dist_all": dist_all,
        "intensity_min_max": (float(intensity.min()), float(intensity.max())) if n_scored else (float("nan"),) * 2,
        "hazard_min_max": (float(hazard.min()), float(hazard.max())) if n_scored else (float("nan"),) * 2,
    }


def _verdict(rho: float) -> str:
    if not np.isfinite(rho):
        return "UNDEFINED (n_scored too small for Spearman)"
    if rho >= 0.5:
        return "CONCORDANT (rho >= 0.5) -- ordering broadly agrees with the independent USGS assessment"
    if rho >= 0.2:
        return "WEAK CONCORDANCE (0.2 <= rho < 0.5) -- report as such in the ADR and the disclaimer"
    return "HALT (rho < 0.2) -- no useful concordance on this terrain; report to owner, tune NOTHING"


def main():
    m = concordance(BASINS_GEOJSON, SEGMENTS_SHP, DEM_TIF)
    print("A39 South Fork concordance check (USGS sfk2024, pre-registered):")
    print(f"  hazard metric          : {HAZARD_FIELD} (USGS ordinal combined hazard class, 24 mm/hour)")
    print(f"  H_24mmh class distribution (all {m['n_segments_total']} segments):")
    for cls, cnt in m["hazard_dist_all"].items():
        print(f"      class {cls!r:>6}: {cnt}")
    print(f"  segments finite / used : {m['n_segments_finite_hazard']} finite; {m['n_segments_used']} intersect a basin")
    print(f"  basins scored / total  : {m['n_scored']} / {m['n_total_basins']}  "
          f"(excluded {m['n_excluded']}; coverage {m['coverage_frac']*100:.1f}%)")
    print(f"  intensity range scored : {m['intensity_min_max'][0]:.4f} .. {m['intensity_min_max'][1]:.4f}")
    print(f"  weighted-hazard range  : {m['hazard_min_max'][0]:.3f} .. {m['hazard_min_max'][1]:.3f}  (ordinal class, length-weighted)")
    print(f"  Spearman rho (intensity VALUE vs hazard): {m['rho']:.4f}   [+ = concordance]")
    print(f"    identity check  -rho(intensity_rank, hazard) = {-m['rho_from_rank']:.4f}  "
          f"(should equal rho; |diff|={abs(m['rho'] - (-m['rho_from_rank'])):.2e})")
    if m["n_scored"] < 10:
        print(f"  ** FRAGILE: n_scored = {m['n_scored']} (< 10) -- rho on a handful of basins is weak evidence either way **")
    print(f"  VERDICT: {_verdict(m['rho'])}")


if __name__ == "__main__":
    main()
