"""CF-11 -- independent hydrology cross-check: pysheds vs pyflwdir (Deltares) on Montecito.

CONFIDENCE CHECK, NOT A RUNTIME PATH. The frozen pipeline uses pysheds; this asks whether an
independent flow engine reproduces its contributing areas, as a sanity anchor to carry into
practitioner conversations. It computes NOTHING the pipeline scores off; it only compares.

FINDING (2026-07-08, Montecito DEM validation/data/dem.tif, EPSG:32611):
  * The SUBSTANTIVE basins agree. Per canyon-mouth-outlet FULL catchment area, pysheds vs pyflwdir
    give **Pearson 0.9994**; the large basins (>= 1 km^2) match to within ~1-3% (median ratio ~1.00).
    Two independent engines reproducing the scored basins' areas is strong confirmation of the
    contributing-area term.
  * Divergence is confined to two places that do NOT drive the ranking, both documented, neither fixed:
    - TINY catchments (< ~1 km^2): pour-point / flat routing is sub-cell sensitive, so a 1-cell
      difference swings a small area (ratios seen 0.003..1.6). The pipeline discards basins <
      MIN_BASIN_KM2 = 0.1 km^2, and the ranking is dominated by the large basins.
    - The WHOLE-GRID max accumulation (pysheds 44.99 km^2 ~ the 44.73 behavior-lock master; pyflwdir
      ~113 km^2): a coastal ocean/edge artifact -- with the DEM's nodata undeclared (None) and ocean
      ~0 m, pyflwdir aggregates the watershed to one edge outlet. The pipeline never scores this
      whole-grid master; it is only the anti-0km^2 sanity guard.

Run:  python validation/cf11_pyflwdir_crosscheck.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

LARGE_BASIN_KM2 = 1.0   # "substantive" basins the cross-check gates on (tiny basins are noise, above)


def crosscheck(dem_path) -> dict:
    """Compare pysheds vs pyflwdir per canyon-mouth-outlet full catchment area on one DEM.
    Returns metrics (no side effects, no pipeline scoring). Independent engines, apples-to-apples."""
    import rasterio
    import pyflwdir
    from scipy.stats import pearsonr, spearmanr
    from src.ingest import load_dem
    from src.hydrology import run_hydrology
    from src.delineate import stage_2b_outlets, CELL_AREA_KM2
    from src.config import DIRMAP

    grid, dem, dem_raw = load_dem(dem_path)
    fdir, acc = run_hydrology(grid, dem)
    acc_arr, fdir_arr = np.asarray(acc), np.asarray(fdir)
    nrows, ncols = dem_raw.shape
    outlets = stage_2b_outlets(acc_arr, fdir_arr, dem_raw, dem_raw.shape)

    with rasterio.open(dem_path) as s:
        elevtn = s.read(1).astype("float32")
        nodata = s.nodata
        transform = s.transform
    flw = pyflwdir.from_dem(data=elevtn, nodata=float(nodata) if nodata is not None else -9999.0,
                            transform=transform, latlon=False)

    ps, pf = [], []
    for (r, c) in outlets:
        ps_mask = np.asarray(grid.catchment(x=int(c), y=int(r), fdir=fdir, dirmap=DIRMAP,
                                            xytype="index", routing="d8"), dtype=bool)
        ps_km2 = ps_mask.sum() * CELL_AREA_KM2
        pf_km2 = int((flw.basins(idxs=np.array([r * ncols + c], dtype="int64")) == 1).sum()) * CELL_AREA_KM2
        if ps_km2 > 0:
            ps.append(ps_km2)
            pf.append(pf_km2)
    ps, pf = np.array(ps), np.array(pf)
    ratios = pf / ps
    big = ps >= LARGE_BASIN_KM2
    return {
        "n_outlets": len(ps),
        "pearson_area": float(pearsonr(ps, pf)[0]),
        "spearman_area": float(spearmanr(ps, pf).correlation),
        "median_ratio_all": float(np.median(ratios)),
        "n_large": int(big.sum()),
        "median_ratio_large": float(np.median(ratios[big])) if big.any() else float("nan"),
        "max_abs_dev_large": float(np.max(np.abs(ratios[big] - 1.0))) if big.any() else float("nan"),
        "ps_whole_grid_master_km2": float(acc_arr.max() * CELL_AREA_KM2),
        "pf_whole_grid_master_km2": float(np.nanmax(flw.upstream_area(unit="cell")) * CELL_AREA_KM2),
    }


def main():
    m = crosscheck(_REPO_ROOT / "validation" / "data" / "dem.tif")
    print("CF-11 pysheds vs pyflwdir cross-check (Montecito):")
    print(f"  outlets compared      : {m['n_outlets']}  ({m['n_large']} >= {LARGE_BASIN_KM2} km^2)")
    print(f"  per-outlet area Pearson: {m['pearson_area']:.4f}  (Spearman {m['spearman_area']:.4f})")
    print(f"  ratio pf/ps  all median: {m['median_ratio_all']:.3f}")
    print(f"  ratio pf/ps large median: {m['median_ratio_large']:.3f}  "
          f"(max |dev| {m['max_abs_dev_large']*100:.1f}%)")
    print(f"  whole-grid master      : pysheds {m['ps_whole_grid_master_km2']:.1f} vs "
          f"pyflwdir {m['pf_whole_grid_master_km2']:.1f} km^2  (coastal edge artifact -- see module docstring)")
    ok = m["pearson_area"] >= 0.99 and abs(m["median_ratio_large"] - 1.0) <= 0.05
    print(f"  VERDICT: {'CONFIRMED -- large basins agree' if ok else 'DIVERGENCE -- investigate/document'}")


if __name__ == "__main__":
    main()
