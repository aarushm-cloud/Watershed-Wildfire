# Post-Fire Debris-Flow Watershed Screening Tool: Algorithms Reference

**Author:** Aarush Madhireddy
**Date:** July 6, 2026 · reconciled to the live tree 2026-07-11 · **reconciled again 2026-07-20 (A39, §9)**
**Scope:** A walkthrough of every algorithm in the scoring pipeline, from burn/DEM ingest through the frozen `mean_burn × mean_slope × contributing_area_km²` heuristic to the within-fire ranking and the validation metrics. Each algorithm gets one plain-language explanation ("For the Environmental Scientist") and one implementation-level explanation ("For the Programmer").

> **The spine, stated once, up top.** Every score in this pipeline is a **within-fire ordinal ranking** of "which burned watersheds warrant a closer look first." It is **never** a prediction of where debris will go, never a probability, never a volume, and never cross-fire comparable. The tool triages fires that fall outside formal USGS/state assessment; it does not out-model USGS. This framing is stamped into every artifact ([outputs.py:35](../src/outputs.py#L35)) and it governs how you read everything below.

This document reflects the **live code tree**. **Reconciled 2026-07-11:** since the 2026-07-06 draft, three sections advanced in the tree and are updated below — the dNBR arm is now **wired** into production (A34, §6–§7), the coastal-slope nodata-ring drop **shipped** (A33 + the F4 slope-coverage flag, §4), and the pysheds flow model gained an independent **pyflwdir cross-check** (CF-11, §3). Where the code and an existing `.md` disagree on a number, the code wins and the discrepancy is flagged inline. The project's canonical science notes live in the Obsidian vault; the repo's `ARCHITECTURE.md` and `docs/science_reference.md` are one-line stubs, so this doc cites source lines, not those stubs.

**Reconciled again 2026-07-20 (A39):** §9 advanced from a terrain **refusal** to a terrain **router** — incised-upland fires no longer stop at `refusal.json`; they route to a disclaimed, exploratory WhiteboxTools sub-basin ranking instead (decision **A39**, supersedes A27/A28's refuse-behavior clause; the A27 detector itself is unchanged). §11's parameter table gained the four frozen `SUBBASIN_*` constants. Range-front fires (Montecito) are byte-identical; the behavior lock is untouched. Full test suite: 261 passed / 0 failed at reconciliation time.

---

## Contents

1. [Pipeline overview](#1-pipeline-overview)
2. [Foundations (brief)](#2-foundations-brief)
3. [Flow modeling: hydrology (deep)](#3-flow-modeling-hydrology-deep)
4. [Slope (deep)](#4-slope-deep)
5. [Outlet detection and catchment delineation (deep)](#5-outlet-detection-and-catchment-delineation-deep)
6. [Burn severity to weight, and the coverage-weighted mean (deep)](#6-burn-severity-to-weight-and-the-coverage-weighted-mean-deep)
7. [The dNBR burn-source arm: built, tested, wired (deep)](#7-the-dnbr-burn-source-arm-built-tested-wired-deep)
8. [The frozen score and within-fire ranking (deep)](#8-the-frozen-score-and-within-fire-ranking-deep)
9. [Terrain routing: range-front vs incised (deep)](#9-terrain-routing-range-front-vs-incised-deep)
10. [Validation algorithms (brief-to-medium)](#10-validation-algorithms-brief-to-medium)
11. [Parameter summary table](#11-parameter-summary-table)

---

## 1. Pipeline overview

Seven modules, no orchestrator. Stages connect through the `grids.py` data contract, not a coordinator object. `run.py` (production) and `validation/gate.py` (the Montecito validation harness) both call the one shared `run_pipeline` in [pipeline.py](../src/pipeline.py); `gate.py` is now a backward-compat re-export shim ([gate.py:57-63](../validation/gate.py#L57-L63)).

The conceptual module order is `ingest → hydrology → delineate → score → outputs`. At runtime the burn raster is loaded and remapped by the `ingest.ingest_burn` seam late (just before scoring), and the per-cell slope raster is computed in the pipeline body; both flow into `score` alongside the delineated basins.

```
DEM.tif ─┐
         ├─> ingest.load_dem ─> A39 terrain ROUTER (A27 detector, unmodified: span > 50 m
         │                       = incised; runs FIRST on the raw DEM; ROUTES, no longer refuses)
         │                            │
         │              ┌─────────────┴──────────────┐
         │        (range_front)                  (incised)
         │              │                    incised + SBS present ─> GateAbort
         │              │                    [STOP: v1 scope -- no both-arms shape
         │              │                     for the mandatory disclaimer]
         │              ▼                              ▼
         │   hydrology.run_hydrology (pysheds 5-step, BOTH modes; unconditional)
         │   fill pits -> fill depressions -> resolve flats -> D8 -> accumulation
         │   + assert_master_outlet_scale (FM-1 scale-free guard, both modes)
         │              │  fdir, acc                   │  dem_raw
         │              ▼                              ▼
         │  delineate.stage_2b_outlets          subbasins.segment_subbasins (WhiteboxTools:
         │  (canyon mouths, CONTOUR_M)          breach-carve -> D8 -> accumulation ->
         │  delineate.stage_2c_delineate        extract_streams -> whole-network Subbasins,
         │  (index-mode catchments,             split at channel confluences)
         │   asset filter applied)              subbasins.build_geometry_records (phase 1,
         │              │  basins[]              DEM-only; drops footprint-truncated basins;
         │              │                         empty -> GateAbort; NO asset filter)
         │              └──────────────┬──────────────┘
         │                             │  basins[]
SBS.tif ─┴─> ingest.ingest_burn        │            (range-front only; incised+SBS aborted above)
   or                                   │            (A3: ONE source, never blended; A4 Provenance)
dNBR ──────> ingest.ingest_dnbr_both_arms (both arms; incised: subbasins.filter_burned_steep
                                          phase 2 -- burn+slope geometry filter; empty -> GateAbort)
                                                                          │  wt, covered
   pipeline.mean_slope_tan(dem_raw) ──────────────────────── slope ──────┤
                                                                          ▼
                    score.stage_2e_score (SBS)  /  _score_one_arm x2 (dNBR, both arms)
                    mean_burn x mean_slope x area_km2 -> within-fire ordinal rank (score desc)
                    incised only: + score.add_intensity_rank (mean_burn x mean_slope,
                    area-independent) -> HEADLINE ordering on incised output
                                                                          │
                              creeks.geojson ─> pipeline.evaluate (unchanged, both modes)
                              (truth match <= 250 m; tercile n//3; strict-pairwise rank-AUC)
                                                                          ▼
                    outputs.write_outputs (SBS)  /  write_dnbr_outputs (dNBR, both-arms writer)
                                     ranking.csv + basins.geojson
                    (stamped burn_source + SCREENING_STATEMENT; incised adds INCISED_FRAMING +
                     WhiteboxTools engine provenance -- never a refusal.json on incised terrain)
```

**Key design principle.** Two things are load-bearing and neither is negotiable.

1. **The formula is frozen.** `mean_burn × mean_slope × contributing_area_km²` was pre-registered and validated. Changing the term order, the evaluation order, `BURN_WEIGHTS`, the dNBR bin/clamp/floor constants, or `DIRMAP`/`D8_OFFSETS` re-opens validation. These are the "category-two frozen fence." The known `× area` mis-ranking (a large moderately-burned basin can outrank a small severely-burned flowed one) is tracked as decision **C1** and deliberately left un-tuned.
2. **Incised-upland terrain gets a disclaimed exploratory ranking, not a refusal (A39, supersedes A27/A28).** On terrain that is dissected highland all the way (no mountain-front break onto a plain), the `CONTOUR_M` outlet anchor is still ill-posed, but WhiteboxTools whole-network sub-basin segmentation removes the need for one: basins split at channel confluences instead of canyon mouths. The tool ranks these basins by `intensity` (`mean_burn × mean_slope`, area-independent), keeps the frozen `score` as a companion column, drops the drains-to-asset filter, and stamps every artifact with `INCISED_FRAMING` — **EXPLORATORY, UNVALIDATED ON THIS TERRAIN CLASS**. It is never confused with the validated range-front ranking. An incised fire that supplies SBS instead of dNBR still fails loud (§9); `refusal.json` is retained for other triggers but is not currently written by any live path (§9).

---

## 2. Foundations (brief)

Three shared primitives underpin the deep sections. All are in `config.py` (the dependency leaf that imports nothing from the project) and `grids.py` (the inter-stage contract).

### Index-space `(row, col)` contract and the metric-CRS guard

[grids.py:40-44](../src/grids.py#L40-L44), [grids.py:23-37](../src/grids.py#L23-L37)

Outlets and catchments are addressed in integer **index space** `(row, col)`, not projected coordinates. This is not a stylistic choice: pysheds' `catchment()` silently returns 0 km² in coordinate mode for valid outlets, so the whole delineation runs in `xytype='index'` (failure mode FM-1, carried as already-fixed). `_rc_to_xy` converts a cell `(row, col)` to projected cell-centre `(x, y)` in metres only when a real distance is needed (asset proximity, creek matching).

All distance math is metric. `_assert_metric_crs` fails loud unless a layer's CRS is in the allowlist `ALLOWED_UTM_ZONES = {32611, 32613}` ([config.py:54](../src/config.py#L54)): 32611 is the Montecito validation zone, 32613 is South Fork. Any geographic CRS (e.g. EPSG:4326) or an un-onboarded zone aborts rather than silently computing distances in degrees.

### Grid alignment

[grids.py:47-78](../src/grids.py#L47-L78)

`assert_aligned` refuses to proceed unless two rasters share one grid: equal CRS (string-normalised on both sides), equal shape, and an affine transform equal to within `almost_equals`. This is what pins the SBS raster (and, later, a reprojected dNBR raster) to the DEM grid cell-for-cell before any burn value is read. A half-pixel offset would shift every downstream value by up to a cell (the "north-shift ghost" that cost the original reconstruction its bit-reproducibility).

### Burn-weight mapping and the coverage-weighted `mean_burn` denominator (A17)

[config.py:21](../src/config.py#L21), [ingest.py:118-131](../src/ingest.py#L118-L131), [score.py:46](../src/score.py#L46)

BAER Soil Burn Severity classes map to a 0-1 weight:

```
BURN_WEIGHTS = {1: 0.0, 2: 0.33, 3: 0.67, 4: 1.0}     # class 0 (Developed) and 15 (NoData/outside) -> 0.0
```

The even 0.33 spacing is a modeling choice, not a measurement. `_burn_weight_raster` builds a per-cell weight raster `wt` from these, and a boolean `covered` mask defined as `sbs in {1,2,3,4}` (a real burn assessment; excludes Developed=0 and NoData=15).

**A17 is the denominator rule and it is easy to get wrong.** `mean_burn` for a basin is `np.mean(wt[mask])` over **all** cells in the basin mask ([score.py:46](../src/score.py#L46)). Because outside-perimeter/NoData (class 15) and Developed (class 0) cells carry weight 0.0 but are still counted in the mask, they sit in the denominator as zeros. So `mean_burn = sum(weights) / n_all_cells`, coverage-weighted, not `sum(weights) / n_covered_cells`. The `covered` mask does **not** gate this mean; it only feeds the `burn_coverage_frac` diagnostic and the `low_coverage` flag ([score.py:44](../src/score.py#L44), [score.py:57](../src/score.py#L57)). A basin that is half outside the perimeter reads a genuinely lower `mean_burn`, by design.

---

## 3. Flow modeling: hydrology (deep)

**File:** [hydrology.py:21-37](../src/hydrology.py#L21-L37)
**Constant:** `DIRMAP` at [config.py:66](../src/config.py#L66)

### The chain

```
pit_filled = grid.fill_pits(dem)
flooded    = grid.fill_depressions(pit_filled)
inflated   = grid.resolve_flats(flooded)                       # conditioned DEM, chain-internal
fdir       = grid.flowdir(inflated, dirmap=DIRMAP, routing="d8")
acc        = grid.accumulation(fdir, dirmap=DIRMAP, routing="d8")
```

Returns `(fdir, acc)` as pysheds Rasters. The conditioned DEM (`resolve_flats` output) feeds `flowdir` and is not consumed downstream, so it is not returned (fixed arity 2). Nothing runs at import; the chain is `grid` methods on the caller's own `grid` instance, mutated in place.

### For the Environmental Scientist

Water flows downhill and collects in channels. Before we can trace which slopes drain to which canyon mouth, we have to turn a raw elevation grid into a clean "which way does water leave each cell" map. Raw DEMs have two defects that break flow routing: single-cell **pits** (a cell lower than all its neighbours, usually a sensor artifact) and larger **depressions** (closed basins with no outlet). Both trap water that should keep moving. So the pipeline fills pits, fills depressions, and then resolves **flats** (dead-level areas where "downhill" is undefined) by nudging them into a consistent gentle gradient.

Once the surface drains cleanly, each cell gets a **flow direction**: which one of its eight neighbours it sends water to. Then **flow accumulation** counts, for every cell, how many upstream cells ultimately drain through it. A cell with an accumulation of 500 has 500 cells worth of hillslope feeding it. High-accumulation cells are the channels; that count is how the next stage finds the creek network.

### For the Programmer

Straight pysheds D8 (`routing="d8"`). `fill_pits → fill_depressions → resolve_flats` is the standard conditioning sequence; each returns a new array, and the order is preserved verbatim from the reconstructed gate.

`DIRMAP` is pysheds' default D8 direction encoding, listed clockwise from North:

```
DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)     # (N, NE, E, SE, S, SW, W, NW)
```

The same `DIRMAP` is passed to both `flowdir` and `accumulation` so the encoding is consistent across the two calls. `accumulation` returns cell counts (each cell = 100 m² at 10 m resolution), which is what `stage_2b_outlets` thresholds against `ACC_THRESHOLD_CELLS = 500` to define channels (~0.05 km² of contributing area). The companion decode table `D8_OFFSETS` ([config.py:67-68](../src/config.py#L67-L68)) maps each direction code to its `(drow, dcol)` step and is used at outlet-detection time to follow a channel cell to its downstream neighbour; it is the frozen partner of `DIRMAP`.

`stage_2a_hydrology` wraps this call and adds the **master-outlet linchpin** ([pipeline.py:177-180](../src/pipeline.py#L177-L180)): the domain pour-point is the max-accumulation cell (`np.argmax`), and its catchment is delineated in index mode. That area (44.73 km² on Montecito) is the anti-0 km² sanity check described in Section 5.

**Independent cross-check (CF-11).** The whole pysheds routing is corroborated by an independent flow engine: [validation/cf11_pyflwdir_crosscheck.py](../validation/cf11_pyflwdir_crosscheck.py) reruns the per-outlet contributing areas through **pyflwdir** (Deltares) and gets **Pearson 0.9994** against pysheds, with the large (≥1 km²) basins that drive the ranking agreeing to a median ratio ~1.00 (max deviation ~3%). Locked as a confidence test ([tests/test_pyflwdir_crosscheck.py](../tests/test_pyflwdir_crosscheck.py)); it scores nothing the pipeline consumes. Two independent engines reproducing the same areas is the empirical backing for the contributing-area term. (The one documented divergence — the whole-grid master, pyflwdir ~113 km² vs pysheds ~45 — is a coastal-edge / undeclared-nodata artifact the pipeline never scores.)

---

## 4. Slope (deep)

**File:** [pipeline.py:229-259](../src/pipeline.py#L229-L259) (`mean_slope_tan`)

### The method: as actually coded

```
gy, gx = np.gradient(dem_raw, CELL_M, CELL_M)      # d/d(row), d/d(col), z per metre
slope  = np.hypot(gx, gy)                           # sqrt(gx^2 + gy^2) = tan(theta), rise/run
```

This is **not** Horn's method and **not** a pysheds slope product. It is a plain `numpy.gradient` central-difference on the **raw metric DEM**, with the two partial derivatives combined into a gradient magnitude by `np.hypot`. The result is `tan(theta)`, dimensionless rise-over-run, per cell. The per-basin `mean_slope` is then the arithmetic mean of `tan(theta)` over the basin mask ([score.py:55](../src/score.py#L55)).

### For the Environmental Scientist

Steeper burned slopes mobilize debris more easily: gravity does more of the work, so water and loose post-fire sediment move with more energy. Slope is the score's "transport energy" term.

We measure steepness as **rise over run** (`tan` of the slope angle), computed from the elevation grid itself. For each cell we look at how fast elevation changes going east-west and north-south, and combine those into a single steepness number. A value of 0 is flat; a value near 0.6 corresponds to a basin averaging about 31 degrees, which is what the steep Montecito catchments actually show. The tool reports the **mean** steepness across a basin, so a small very-steep gully and a large gentle fan get different, physically-sensible slope terms.

One honest edge the code now handles: on a **coastal** DEM, a land cell next to an ocean/nodata cell (which pysheds clamps to elevation 0) would read a spurious cliff and inflate that basin's slope. This is **fixed** (decision **A33**, committed `ebc1e06`): when a nodata sentinel is present, `mean_slope_tan` drops the nodata-adjacent **ring** to NaN at source (the contamination lives in the valid land cell whose gradient consumed a 0-clamped neighbour, so masking at the mean would not remove it), and the per-basin mean is taken over the clean (non-NaN) cells only. A companion **F4** diagnostic (`slope_coverage_frac` / `low_slope_coverage < 0.80`) flags a basin scored on a small clean remnant. Montecito is inland → no NaN → the behavior lock is byte-identical. (Earlier drafts recorded this as a deferred P3 hazard note; the owner overrode the deferral and shipped the fix with a synthetic coastal fixture.)

### For the Programmer

`np.gradient(dem_raw, CELL_M, CELL_M)` uses central differences in the interior and one-sided differences at the edges, with the spacing argument `CELL_M = 10.0` m applied to both axes, so `gx`/`gy` come out dimensionless (metres of rise per metre of run). `np.hypot(gx, gy)` is the L2 magnitude, i.e. `tan(theta)`. Owner-confirmed to reproduce the reconstruction's `mean_slope` column to within 0.01, which is why it is frozen as-is rather than swapped for Horn's 8-neighbour method.

Units discipline matters here: the term is `tan`, not degrees and not percent. The `science_reference` "0-1 transport-energy proxy" phrase is a typical-range description, not a hard clamp; `tan` stays below 1 only because mean basin slopes sit around 31 degrees. Masking is applied at the nodata edge only (the A33 ring-drop above, via the shared `_valid_dem_mask` the A25/A27 guards use); every other cell gets a slope, and the per-basin reduction happens later in `score` over the clean basin-mask cells (an all-NaN basin fails loud, A8, like the A32 empty-mask guard).

---

## 5. Outlet detection and catchment delineation (deep)

**File:** [delineate.py:176-264](../src/delineate.py#L176-L264)
**Constants:** `CONTOUR_M`, `ACC_THRESHOLD_CELLS`, `MIN_BASIN_KM2`, `DRAINS_TO_ASSET_M` at [config.py:13-16](../src/config.py#L13-L16)

### 5a. Canyon-mouth outlets

[delineate.py:176-202](../src/delineate.py#L176-L202)

```
channel = acc > ACC_THRESHOLD_CELLS                          # 500 cells (~0.05 km^2)
candidates = channel & (dem_raw >= CONTOUR_M)                # channel cells at/above the mountain front
for each candidate (r, c):
    off = D8_OFFSETS[ fdir[r, c] ]                            # its D8 downstream step
    (nr, nc) = (r + off[0], c + off[1])
    if in-bounds and dem_raw[nr, nc] < CONTOUR_M:            # downstream neighbour is below the contour
        outlet (r, c)                                         # a canyon mouth
```

Zero outlets is a fail-loud abort (contour/accumulation logic or AOI is wrong), never an empty result.

### 5b. Delineate, discard, drains-to-asset, dedup

[delineate.py:208-264](../src/delineate.py#L208-L264)

```
for each outlet (r, c) sorted:
    mask = grid.catchment(x=c, y=r, fdir=fdir_raster, dirmap=DIRMAP, xytype="index", routing="d8")
    area = mask.sum() * CELL_AREA_KM2                        # CELL_AREA_KM2 = 1e-4 km^2/cell
    guard: area finite and > 0                               # FM-1 anti-0 km^2 abort
    discard if area < MIN_BASIN_KM2                          # 0.1 km^2
    keep only if a basin CHANNEL cell is within DRAINS_TO_ASSET_M of an asset   # 600 m, cKDTree
sort survivors by (-raw_area, outlet_row, outlet_col)       # larger claims first
claimed = all-False grid
for each survivor:
    own = mask & ~claimed                                    # cells not already taken
    re-discard if own_area < MIN_BASIN_KM2
    claimed |= own                                           # mark taken; smaller neighbours can't reclaim
assign basin_id by sorting kept basins on outlet (row, col)
```

### For the Environmental Scientist

The tool ranks watersheds by where creeks leave the mountains and spill onto the flatter fans where people live. So step one is: find the **canyon mouths**. A canyon mouth is a channel cell (enough water accumulates there to be a real creek) sitting at or above the mountain-front elevation, whose water flows to a neighbour that sits **below** that elevation. In plain terms: the creek crosses the mountain-front line going downhill. The mountain-front line here is a fixed 150 m elevation contour (`CONTOUR_M`), the value that matches the Montecito range front.

Step two: for each canyon mouth, trace **uphill** everything that drains to it. That upslope area is the watershed (the "contributing area"). Then three filters:

- **Too small to matter.** Anything under 0.1 km² is dropped. A tiny sliver is noise, not a watershed worth ranking.
- **Doesn't reach anyone.** We keep a basin only if its creek passes within 600 m of a building. The tool is a triage screen for populated fans; a canyon that drains into empty backcountry is out of scope for this ranking.
- **Don't count the same ground twice.** Watersheds can overlap where the delineation is ambiguous. The rule is simple and deterministic: **larger basins claim their cells first**, and a smaller basin only keeps the cells not already taken. This prevents the same hillslope from inflating two different basins' areas.

### For the Programmer

The crossing test is a pure D8 lookup: `D8_OFFSETS[int(fdir[r,c])]` gives the downstream step, and the outlet condition is `dem_raw[downstream] < CONTOUR_M <= dem_raw[r,c]`. The contour test is on the **raw** DEM; routing uses the **conditioned-DEM** `fdir`. Both anti-0 km² guards are intact: `grid.catchment(..., xytype="index", ...)` is mandatory (coordinate mode returns 0 km², silently deleting the largest flowed basins), and `area <= 0 or non-finite` aborts.

The dedup is order-sensitive and that is the point. `raw.sort(key=lambda b: (-b["raw_km2"], row, col))` then a single shared `claimed` boolean grid with `own = mask & ~claimed; claimed |= own`. Reordering this sort silently changes which basin wins a contested cell, so it is frozen. Ties break by `(-area, row, col)` for determinism; with float areas no exact ties occur, so the keys only canonicalise label/claim order. Final `basin_id` is assigned by sorting the survivors on outlet `(row, col)`, giving a stable, reproducible labelling. Asset proximity uses a `scipy.spatial.cKDTree` over asset `(x, y)` and a `k=1` query against each basin's channel cells converted via `_rc_to_xy`.

`CONTOUR_M` gets a fail-loud range guard before this runs: `assert_contour_in_dem_range` ([delineate.py:74-106](../src/delineate.py#L74-L106)) aborts if 150 m falls outside the DEM's valid min/max, catching a wrong-fire contour that would yield zero canyon mouths. It catches the gross numeric mis-set only, not geomorphic correctness; the geomorphic case is the A27/A39 terrain router in Section 9.

---

## 6. Burn severity to weight, and the coverage-weighted mean (deep)

**Files:** [ingest.py:96-159](../src/ingest.py#L96-L159) (the A15 seam), [score.py:40-57](../src/score.py#L40-L57) (the per-basin reduction)

### Source selection, weight remap, single provenance

[ingest.py:96-115](../src/ingest.py#L96-L115), [ingest.py:134-159](../src/ingest.py#L134-L159)

```
select_burn_source(sbs):
    n_invalid = count( sbs not in SBS_CODESET {0,1,2,3,4,15} )
    return "SBS" if n_invalid == 0 else "dNBR"               # covers whole AOI -> SBS, else dNBR; NEVER blended

ingest_burn(burn_path):
    sbs = load_burn(burn_path)
    source = select_burn_source(sbs)
    if source != "SBS": raise GateAbort(...)                 # A29 fail-loud on PARTIAL-SBS (dNBR-only fires route via ingest_dnbr_both_arms, A34)
    wt, covered = _burn_weight_raster(sbs)                   # A17 weights + A18 coverage
    provenance = {"burn_source": source}                    # A4: ONE stamp, read everywhere
    return wt, covered, provenance
```

### For the Environmental Scientist

The burn input answers "how badly did each patch of ground burn," which stands in for how much the soil's ability to absorb water collapsed. Higher severity means more runoff, which means more water and sediment available to a debris flow. That is why burn severity is a term in the score.

The pipeline uses exactly **one** burn source per fire, never a blend. If a field-validated BAER Soil Burn Severity map covers the whole area, it uses that (it is the validated input). If it does not, the intended production fallback is satellite dNBR for the whole area. Two sources measure subtly different things at different scales, so averaging them would produce a number that means neither. The choice is made once, in one place, and stamped onto every output so a reader always knows which input produced the ranking.

Severity classes become a 0-to-1 weight: unburned/very-low = 0.0, low = 0.33, moderate = 0.67, high = 1.0. Developed land and ground outside the burn perimeter count as 0.0. A basin's burn term is the average of these weights over the whole basin, including the outside-perimeter zeros. So a watershed that is only partly inside the burn honestly reads a lower burn term than one that burned wall-to-wall. A separate "coverage" number tracks how much of each basin actually had a real burn assessment, and flags basins below 80% covered so a user knows when the burn term rests on thin data.

### For the Programmer

`SBS_CODESET = (0, 1, 2, 3, 4, 15)` ([ingest.py:38](../src/ingest.py#L38)) is the validity test: `sbs.tif` declares no rasterio nodata, so codeset membership (not a GDAL mask) defines "covered the AOI." Class 15 (outside-perimeter) counts as covered ("assessed: outside the burn"), an owner decision. `select_burn_source` returns `"dNBR"` for a partial-SBS AOI. The dNBR end-to-end arm is now **wired** (A34; Section 7): a dNBR-only fire (`sbs=None` + a native dNBR raster) is routed through `ingest_dnbr_both_arms` in the pipeline, not through this SBS seam. `ingest_burn` itself still fails loud (A29) when a **partial-SBS** raster reaches it — that guard is still correct, because a partial SBS is ambiguous (a genuine straddle vs a broken/clipped raster), and without it `_burn_weight_raster` would score the SBS raster while stamping a `"dNBR"` provenance, a silent mislabel.

`_burn_weight_raster` ([ingest.py:118-131](../src/ingest.py#L118-L131)) zero-inits `wt`, loops `BURN_WEIGHTS` to set classes 1-4, leaves 0/15 at 0.0, and computes `covered = np.isin(sbs, (1,2,3,4))`. It moved here verbatim from `score.py` in P2.2a so the weight raster and coverage mask are produced once at ingest; `score` consumes them.

The per-basin reduction in `stage_2e_score` ([score.py:40-57](../src/score.py#L40-L57)):

```python
ncells = int(m.sum())
ncov   = int((m & covered).sum())
b["burn_coverage_frac"] = ncov / ncells
b["mean_burn"] = float(np.mean(wt[m]))                       # A17: denominator = ALL basin cells
b["low_coverage"] = b["burn_coverage_frac"] < BURN_LOW_COVERAGE   # 0.80
```

`low_coverage` is flag-only; it never excludes a basin from the ranking. Note the current contract conflates "outside burn footprint" (class 15) and "masked developed land" (class 0) into one `low_coverage=True`; the reason-code split is deferred (C8).

---

## 7. The dNBR burn-source arm: built, tested, wired (deep)

**File:** [ingest.py:167-298](../src/ingest.py#L167-L298)
**Constants:** `DNBR_BIN_EDGES`, `DNBR_CLAMP`, `DNBR_FLOOR` at [config.py:32-38](../src/config.py#L32-L38)
**Status:** implemented, unit-tested against hand-computed known-answers ([tests/test_dnbr_arm.py](../tests/test_dnbr_arm.py)), and **now wired end-to-end** (A34, committed `a523dde`; P2.2c). A dNBR-only fire (`sbs=None` + a native dNBR raster) is dispatched through `ingest_dnbr_both_arms` and scored through **both arms** — Arm A (binned) is the headline ranking, Arm B (continuous) rides alongside with `rank_delta = |rankA − rankB|` as an honest uncertainty flag ([outputs.py:181](../src/outputs.py#L181), `write_dnbr_outputs`). `ingest_burn`'s A29 guard now fires only on a **partial-SBS** raster (§6). The bin/clamp/floor constants remain on the frozen fence. Validation: the Montecito dNBR both-arms run reproduces the P2.3 swap-test oracle (rank-AUC 0.9722, both arms), and the dNBR ranking is carried as **triage-validated, not exact-rank-validated** (n=1 — the pre-registered exact-#1 criterion missed by 1.03%); that n=1 framing is stamped on every dNBR artifact.

The bin edges, clamp, floor, and the 5→4 collapse are pre-registered in [validation/P2_PREREGISTRATION.md](../validation/P2_PREREGISTRATION.md) and frozen: a value that "looks off" is a P2.3 finding, never a P2.2b edit.

### Arm A: bin to 4 classes (primary)

[ingest.py:199-225](../src/ingest.py#L199-L225)

```
DNBR_BIN_EDGES = (0.100, 0.270, 0.440, 0.660)                # four interior edges, raw dNBR
np.digitize(safe, DNBR_BIN_EDGES, right=False)               # left-closed / right-open

  dNBR < 0.100          -> non-covered (class 15), weight 0.0     (enters denominator as 0.0)
  [0.100, 0.270)        -> SBS class 2   (Low)            weight 0.33
  [0.270, 0.440)        -> SBS class 3   (Moderate-low)   weight 0.67
  [0.440, 0.660)        -> SBS class 3   (Moderate-high, collapses into the single SBS Moderate)
  dNBR >= 0.660         -> SBS class 4   (High)           weight 1.0
```

The one genuine 5→4 merge is Moderate-low + Moderate-high both mapping to SBS class 3. No dNBR pixel is ever assigned SBS class 1: below-floor routes to class 15, not 1. Invalid cells (NaN/nodata/cloud) are replaced with a below-floor sentinel `-1.0` **before** `np.digitize` and overwritten to class 15 **after**, because `NaN < 0.100` is `False` and an unmasked NaN would otherwise dodge the floor and digitize into the top bin (the pre-registration's named "silent-wrong" defect). The resulting class raster is handed to `_burn_weight_raster` untouched, so Arm A reuses the exact SBS weight computation.

### Arm B: continuous transfer (companion)

[ingest.py:228-240](../src/ingest.py#L228-L240)

```
DNBR_CLAMP = (0.100, 1.300)
b  = clip(dNBR, 0.100, 1.300)
wt = (b - 0.100) / (1.300 - 0.100)                           # linear [0,1]; lower clamp maps to exactly 0.0
covered = valid & (dNBR >= DNBR_FLOOR)                       # below-floor + invalid -> non-covered, weight 0.0
```

Linear on purpose: a power curve would be a tunable. The `0.100` lower clamp is the same number as the first bin edge and the floor. The three knobs deliberately share one value.

### For the Environmental Scientist

BAER soil burn severity is field-validated but only exists for fires someone assessed. dNBR is a satellite "how much did the landscape change" index available for any fire, anywhere, for free. That makes it the natural production input for the un-assessed fires this tool targets. But dNBR is a continuous vegetation-change signal, not a 4-class soil product, so the pipeline has to convert it. The primary path bins dNBR into the same four severity classes SBS uses (so the rest of the math is identical), using published break points taken literally with zero adjustment. A companion path maps dNBR straight to a 0-to-1 number as a robustness check. Both share the same "below 0.1 is outside the burn" floor.

This arm is now switched on: a fire without SBS is scored on dNBR and stamped honestly as `burn_source=dNBR`, with the binned (Arm A) ranking as the headline and the continuous (Arm B) ranking alongside as a robustness companion. Because it has been validated on only one fire, every dNBR output carries a loud "triage-validated, not exact-rank-validated (n=1)" caveat — the fail-loud spine keeping the honest limitation visible rather than hidden.

### For the Programmer

`reproject_dnbr` ([ingest.py:167-196](../src/ingest.py#L167-L196)) snaps native dNBR onto the DEM grid via explicit `dst_transform`/`dst_shape` (never the dNBR scene's own grid), Arm A with `Resampling.nearest`, Arm B with `Resampling.bilinear`. Sensor note: the committed dNBR raster is **Landsat 30 m** (a 3× upsample onto the 10 m grid), **not** the 20 m Sentinel-2 the pre-registration originally assumed — a documented sensor caveat (A21/A4), carried raw-scale (never ×1000). `ingest_dnbr_both_arms` ([ingest.py:243-298](../src/ingest.py#L243-L298)) derives one shared valid footprint from Arm A's nearest reproject and applies it to both arms, so A and B are byte-identical in footprint by construction; `assert_aligned` runs on both before any thresholding, and a fail-loud guard rejects any non-finite/sentinel value inside the valid footprint. The known-answer tests pin every bin boundary (0.100 left-closed to class 2, 0.440 collapsing to class 3, and so on), the linear Arm-B transfer, the shared floor, and the NaN-routing defect.

---

## 8. The frozen score and within-fire ranking (deep)

**File:** [score.py:31-65](../src/score.py#L31-L65)

### The formula

```python
b["score"] = b["mean_burn"] * b["mean_slope"] * b["area_km2"]      # burn[0-1] x slope[tan] x km^2
```

then a within-fire ordinal rank:

```python
order = sorted(basins, key=lambda b: (-b["score"], b["basin_id"]))  # score desc, ties -> ascending id
for rank, b in enumerate(order, start=1):
    b["rank"] = rank
```

`mean_burn` is dimensionless [0-1], `mean_slope` is dimensionless `tan(theta)`, `area_km2` is km². The product has units of km² and is meaningful only as a within-fire ordinal.

### For the Environmental Scientist

Three ingredients, multiplied:

- **How badly it burned** (`mean_burn`, 0 to 1). Burned soil sheds water instead of absorbing it. This is the "how much runoff" term.
- **How steep it is** (`mean_slope`). Steeper means more energy to move debris. This is the "how much push" term.
- **How big it is** (`contributing_area_km²`). A bigger watershed gathers more water and sediment. This is the "how much material" term.

They **multiply** rather than add because a debris flow needs all three at once. A severely burned basin that is dead flat won't mobilize much; a steep basin that didn't burn generates normal runoff; a tiny basin, however burned and steep, has little material to deliver. Multiplication means a zero in any term drags the score toward zero, which is the right physical intuition: remove any one ingredient and the hazard drops sharply. The tool then sorts basins by this score, highest first, and hands back a ranked list: "look at these watersheds first."

**Read this as a ranking within one fire, and nothing more.** The score is not a probability, not a debris volume, and not comparable between two different fires. It answers only "within this fire, which basins should a responder examine first."

There is one **known, deliberately-unfixed** mis-ranking. Because area multiplies in linearly, a large moderately-burned basin can outscore a small severely-burned basin that actually flowed. On the validation fire this shows up as Oak Creek (small, flowed) sitting below Toro Canyon (large, not flowed). It is tracked as decision **C1** and left un-tuned on purpose: changing the formula after seeing which basins flowed is how you fool yourself into a model that only looks good on the data you already have.

### For the Programmer

The term order and the evaluation order are frozen. IEEE floating-point multiply is not associative, so re-associating `mean_burn * mean_slope * area_km2` could flip a hair-close pair and change the ranking; the expression is written left-to-right and left that way. The tie-break `(-score, basin_id)` is deterministic (ascending `basin_id`), which matters because ranks 26-36 in the Montecito run all share score 0.0 and must order reproducibly.

`stage_2e_score` also enforces the **A32** empty-mask guard ([score.py:49-54](../src/score.py#L49-L54)): if a basin mask has zero cells, `mean_slope` would be `np.mean(slope[[]]) = nan` and poison the score, so it raises `GateAbort` instead. The rationale is premises-void, not sibling-symmetry: `mean_burn`/`burn_coverage_frac` return `0.0` on an empty basin because zero is a meaningful value there, but an empty **mask** can only mean `delineate`'s `MIN_BASIN_KM2` guarantee broke, i.e. the run's premises are unsound, so the whole run aborts. The guard ships with a synthetic trip-fixture ([tests/test_empty_mask_abort.py](../tests/test_empty_mask_abort.py)); it is unreachable on Montecito.

The documented `× area` mis-ranking is verified, not hand-waved: `evaluate` computes `discordant_are_fm3` ([pipeline.py:276](../src/pipeline.py#L276)), asserting every AUC-costing discordant pair is a smaller flowed basin outranked by a larger one (the C1 signature), so a *new* kind of discordance would be caught rather than blamed on the known one.

---

## 9. Terrain routing: range-front vs incised (deep)

**Detector:** [delineate.py:119-170](../src/delineate.py#L119-L170) — **unmodified** by A39
**Router:** `_terrain_mode` at [pipeline.py:365-371](../src/pipeline.py#L365-L371); called first in `run_pipeline`, before SBS is opened or hydrology runs, at [pipeline.py:483-492](../src/pipeline.py#L483-L492)
**Sub-basin engine (incised only):** [src/subbasins.py](../src/subbasins.py) — WhiteboxTools
**Constant:** `HYPSOMETRIC_SPAN_THRESHOLD_M = 50.0` at [delineate.py:116](../src/delineate.py#L116) — **unmodified**; new `SUBBASIN_*` constants at [config.py:92-95](../src/config.py#L92-L95), see §11
**Decisions:** A27 (rule, unmodified), A28 (superseded), A31 (DEM-first ordering, preserved), **A39** (supersedes A27's refuse-behavior clause and A28 — incised terrain ranks instead of refusing)
**Status:** built, wired, and tested end-to-end (suite 261 green). Two tiers, two engines; the range-front path is byte-identical to pre-A39.

### The rule (detector unchanged; only the response to it changed)

```
valid   = finite DEM cells, and (if a nodata sentinel exists) != that sentinel
p1, p10 = 1st and 10th percentiles of valid-cell elevation (m), method='linear'
span_m  = p10 - p1
INCISED iff span_m > 50.0                                    # strict >; was REFUSE, now a ROUTE
```

`assess_hypsometric_applicability` — the detector itself — is retained **byte-identical**. What A39 changed is what happens with its verdict: instead of writing `refusal.json` and stopping, `span_m > 50.0` now selects the WhiteboxTools sub-basin engine (below) in place of the pysheds canyon-mouth engine. The refusal machinery (`write_refusal` / `build_refusal_message`, [outputs.py:41-102](../src/outputs.py#L41-L102)) is **not deleted** — `run_pipeline`'s polymorphic return contract and `dispatch_result`/`app.py`'s view model still support a `status="refused"` result for interface completeness and any future non-terrain refusal trigger — but as of this reconciliation **no live code path calls `write_refusal`**: terrain shape no longer refuses, and every other precondition failure (below) raises `GateAbort` directly rather than producing a `refusal.json`. Do not describe `refusal.json` as a currently-reachable output; it is a dormant, still-supported contract, not a live one.

### For the Environmental Scientist

The whole method assumes one shape of landscape: steep mountains rising over a flatter plain, with creeks that leave the range at a mountain front and spill onto fans where people live. That mountain-front break is what the **range-front** method anchors its watersheds to.

Some burned terrain isn't shaped like that. An **incised upland** is dissected highland all the way: deep valleys cut into high ground, with no plain to spill onto. On that terrain the range-front method's assumptions quietly fail: there is no mountain-front break, so there are no canyon mouths to anchor to; there is no depositional plain, so "contributing area to an outlet" has no natural unit the way it does for a canyon-mouth catchment.

Rather than force those same three ingredients onto terrain where they lose their referent, the tool switches to a **different measurement**. It still starts the same way — taking the 1st and 10th percentiles of the terrain's elevation and checking whether the gap between them is tight (a true range-front-over-plain, ~20–30 m) or wide (an incised valley floor, no compact plain) — but instead of stopping there, a wide gap now **routes** to a second engine built for exactly this terrain: WhiteboxTools delineates the whole drainage network into sub-basins wherever channels meet (a confluence), using a conditioning method (breach-carving) that preserves incised channels instead of filling them level. Those sub-basins are then ranked by burn × slope alone — the area term is dropped, because it has no anchored meaning on a basin whose boundary comes from a segmentation threshold rather than a canyon mouth — and the whole result is labeled **exploratory and unvalidated on this terrain class**, kept visibly separate from the validated range-front ranking. A practitioner who said "even rough information would be really helpful" about exactly this terrain (the stakeholder request behind this decision, vault `DECISIONS.md` A39) gets a ranked starting point instead of nothing, with the uncertainty stated up front rather than hidden.

### For the Programmer

`assess_hypsometric_applicability` is untouched: `np.percentile(valid_elevations, [1, 10], method='linear')`, `span_m = p10 - p1 > 50.0` (strict `>`; the exact `== 50.0` boundary does not route to incised, pinned by test). `_valid_dem_mask` ([delineate.py:49-68](../src/delineate.py#L49-L68)) is still the single source of truth for "which cells are terrain." The **firewall** — the detector returns exactly `{refuse, reason_code, span_m, span_threshold_m, n_valid}`, no absolute elevation, no `CONTOUR_M` candidate — is unchanged and still adversarially tested ([tests/test_a27_applicability.py](../tests/test_a27_applicability.py) group D). An all-nodata DEM still raises `GateAbort` directly (a broken input, not an "incised" verdict).

What changed is the caller. `_terrain_mode` wraps the same detector and returns `("incised" if verdict["refuse"] else "range_front", verdict)`; `run_pipeline` reads `terrain_mode` **first**, on the raw DEM, before SBS is opened or hydrology runs (A31's ordering is preserved), and branches:

- **`incised` + an SBS burn input present** → immediate `GateAbort` ([pipeline.py:485-492](../src/pipeline.py#L485-L492)): "incised terrain with an SBS burn input is not supported in v1 (A39)." The SBS single-arm path has no both-arms shape to hang the mandatory disclaimer and UI branch on, so running it would silently emit an **undisclaimed** ranking. This is a v1 scope guard, not a science gate — see [[FAILURE_MODES]] FM-18 in the vault; the fix for a real incised+SBS fire is a dNBR input, not a code change.
- **`incised`, dNBR (or no burn opened yet)** → `subbasins.segment_subbasins` ([subbasins.py:33-94](../src/subbasins.py#L33-L94)) conditions the DEM with WhiteboxTools `BreachDepressionsLeastCost` (`SUBBASIN_BREACH_DIST_CELLS`) — not pysheds' fill-based conditioning, which raises an incised canyon floor to its spill level and smears the channel, the specific failure mode this terrain triggers — then runs `D8Pointer → D8FlowAccumulation → ExtractStreams` (`SUBBASIN_ACC_THRESHOLD_CELLS`) `→ Subbasins`, a whole-network split at every channel confluence. `build_geometry_records` ([subbasins.py:117-144](../src/subbasins.py#L117-L144), phase 1 — DEM-only, because burn weights and slope do not exist yet at the delineation call site) drops any label touching the raster border **or** abutting invalid/nodata terrain via `_footprint_edge_ids` ([subbasins.py:97-114](../src/subbasins.py#L97-L114); border-only checking is not enough — an interior nodata hole truncates a basin just as surely as the outer edge, silently yielding partial area and burn stats otherwise; see [[FAILURE_MODES]] FM-19) and any basin under `MIN_BASIN_KM2` (reused, no new area floor). An outlet is the max-flow-accumulation cell inside the basin, not the minimum-elevation cell (breach-carving can leave an interior elevation artifact). Zero survivors → `GateAbort` ([pipeline.py:517-521](../src/pipeline.py#L517-L521)). The drains-to-asset filter is **not** applied (A39 clause 5 — a wilderness incised fire would otherwise silently re-derive the old refusal by filtering to zero basins; `asset_m` is hardcoded `None`). After the dNBR both-arms ingest, `filter_burned_steep` ([subbasins.py:147-176](../src/subbasins.py#L147-L176), phase 2) drops any basin whose burned-cell fraction (`burn_weight > 0`, reusing the frozen burn binning — no second severity threshold) is below `SUBBASIN_BURN_FRAC_MIN`, or whose mean slope is below `SUBBASIN_SLOPE_FLOOR_TAN`; zero survivors → `GateAbort` ([pipeline.py:577-581](../src/pipeline.py#L577-L581)). Basin membership is fixed by **Arm A**; Arm B ([pipeline.py:606-607](../src/pipeline.py#L606-L607)) scores the identical set so `rank_delta` stays a meaningful comparison.
- **`range_front`** → unchanged: `assert_contour_in_dem_range` → `stage_2b_outlets` → `stage_2c_delineate`, with the asset filter applied, byte-identical to pre-A39.

Hydrology (`stage_2a_hydrology`) and the FM-1 `assert_master_outlet_scale` guard run **unconditionally for both modes** ([pipeline.py:499-500](../src/pipeline.py#L499-L500)) — incised terrain still needs the pysheds `fdir`/`acc` chain for slope's neighbourhood math, and the master-outlet check is a DEM sanity floor, not a range-front-only concern.

On incised output, `score.add_intensity_rank` ([pipeline.py:614-616](../src/pipeline.py#L614-L616)) adds `intensity = mean_burn × mean_slope` — the frozen score with the area exponent set to zero, which A39 explicitly binds as a use of the deferred **C1** area-dampening family, scoped to incised terrain only; promoting it to range-front output is forbidden by the decision. Rows are then ordered by `intensity_rank`; `score`/`rank` (the frozen formula) ride along as companion columns, never dropped. `outputs.write_dnbr_outputs` stamps `INCISED_FRAMING` (verbatim EXPLORATORY / UNVALIDATED language, [outputs.py:187-202](../src/outputs.py#L187-L202)) plus WhiteboxTools engine provenance (`engine`, `wbt_version`, `acc_threshold_cells`, `breach_dist_cells`) in place of the plain provenance a range-front artifact carries; the `incised=False` default keeps pre-A39 callers byte-identical.

> **Surviving limitation, not solved by A39.** A28 gave three reasons an incised ranking is meaningless. A39 answers the area argument (`intensity`) and the ranking-unit argument (confluence splits). Verbatim from the vault `DECISIONS.md` A39 entry: "It does NOT rebut the second: that `mean_slope` may stop discriminating on uniformly steep dissected highland, in which case `intensity` degenerates toward a burn-severity map. This is an ACKNOWLEDGED OPEN LIMITATION carried verbatim in `INCISED_FRAMING`, not a solved problem." `INCISED_FRAMING` itself says: "KNOWN OPEN LIMITATION: where dissected terrain is uniformly steep, mean_slope may not discriminate between basins, in which case this ordering approaches a burn-severity ranking."
>
> **The supporting evidence is range-front evidence, not incised-terrain evidence.** The pre-ratification confirmation run at shipping parameters (`MIN_BASIN_KM2 = 0.1`) reproduces, on **Montecito** — verbatim from vault `DECISIONS.md` A39: "88 basins, AUC(intensity) 0.887, AUC(size) 0.790, 10/10 of the top-10 intensity-ranked basins flowed" (22/25 of top-25). That is the validated range-front case re-run under the incised engine's segmentation parameters, and it rests on "6 independent flow events — effective n = 6": "consistency-with-known-outcome, not generalizable predictive skill." The first incised-terrain evidence is a separate, later, pre-registered concordance check against a USGS assessment; its result is out of scope for this document and is not cited here.

---

## 10. Validation algorithms (brief-to-medium)

**File:** [pipeline.py:220-291](../src/pipeline.py#L220-L291). These are re-exported by [validation/gate.py](../validation/gate.py) for existing call sites; the algorithms themselves live in `pipeline.py` after the P-promotion, not in `gate.py`.

These are the "how we know it works" algorithms. The validation case is the 2017 Thomas Fire feeding the 9 January 2018 Montecito debris-flow disaster.

### Truth matching within tolerance

[pipeline.py:220-251](../src/pipeline.py#L220-L251)

For each documented-flow creek, `compute_creek_nearest` computes the minimum distance from the whole creek LineString to each basin's outlet point (`_rc_to_xy` of the outlet cell), and takes the nearest basin (argmin, ties to lowest `basin_id`). `evaluate` then labels a basin **flowed** if a creek matches within `TRUTH_MATCH_M = 250` m ([config.py:17](../src/config.py#L17)). Creeks beyond 250 m are recorded as unmatched findings. This is the ground-truth labelling the AUC is scored against.

### Tercile split

[pipeline.py:258-259](../src/pipeline.py#L258-L259)

```python
tercile_k = n // 3          # floor division; 36 basins -> top 12
top = [b for b in ranked if b["rank"] <= tercile_k]
```

The pre-registered pass criterion is that all 6 documented-flow basins land in the top tercile (top 12 of 36), which they do (6/6), and that the #1-ranked basin flowed (it does: basin 6, Cold Spring Creek).

### Rank-AUC: strict pairwise concordance

[pipeline.py:265-274](../src/pipeline.py#L265-L274)

```python
for f in flowed:
    for nf in nonflowed:
        if f["score"] > nf["score"]: concordant += 1
        else:                        discordant.append((f, nf))       # ties count AGAINST
auc = concordant / (len(flowed) * len(nonflowed))
```

Every flowed/non-flowed pair is checked; a flowed basin must **strictly** outscore a non-flowed one to be concordant (ties count against). On Montecito: 6 flowed × 30 non-flowed = 180 pairs, 175 concordant, so **AUC = 175/180 = 0.9722**. The five discordant pairs are all the known C1 `× area` signature (a smaller flowed basin outranked by a larger non-flowed one), confirmed by `discordant_are_fm3`.

> **Numbers discipline: read before quoting AUC.** The live, reconstructed oracle is **AUC 0.9722 / 36 basins / 44.7273 km² master outlet**, frozen in [tests/test_behavior_lock.py:83,94,102](../tests/test_behavior_lock.py#L83). The Week-0 report's **0.987 / 39.19 km² / 32 basins** are the **original-AOI** figures and are **not bit-reproducible** (the original AOI is unrecoverable). Do not cite them as current. `CLAUDE.md` still repeats `0.987 / 39.19 / 32` as "canonical-locked"; that is the stale copy. The behavior lock and `P2_PREREGISTRATION.md` both explicitly anchor on the reconstructed 0.9722 / 36 / 44.7273 instead. This is the same "a superseded number lives in a doc" situation the reader should treat like any stale constant: the code and the lock win.

---

## 11. Parameter summary table

| Parameter | Value | File:Line | Rationale |
|---|---|---|---|
| `CONTOUR_M` | 150 m | [config.py:13](../src/config.py#L13) | Mountain-front contour; outlets cross it going downhill |
| `ACC_THRESHOLD_CELLS` | 500 cells (~0.05 km²) | [config.py:14](../src/config.py#L14) | Min flow accumulation for a cell to count as a channel |
| `MIN_BASIN_KM2` | 0.1 km² | [config.py:15](../src/config.py#L15) | Discard catchments below this (noise, not watersheds) |
| `DRAINS_TO_ASSET_M` | 600 m | [config.py:16](../src/config.py#L16) | Keep basins whose channel reaches within this of a building |
| `TRUTH_MATCH_M` | 250 m | [config.py:17](../src/config.py#L17) | Max creek-to-outlet distance to label a basin "flowed" |
| `BURN_WEIGHTS` | `{1:0.0, 2:0.33, 3:0.67, 4:1.0}` | [config.py:21](../src/config.py#L21) | **Frozen fence.** SBS class to 0-1 weight; 0/15 -> 0.0. Even spacing is a modeling choice |
| `DNBR_BIN_EDGES` | `(0.100, 0.270, 0.440, 0.660)` | [config.py:32](../src/config.py#L32) | **Frozen fence.** Arm A interior edges (Key & Benson / USGS-UN-SPIDER lineage) |
| `DNBR_CLAMP` | `(0.100, 1.300)` | [config.py:34](../src/config.py#L34) | **Frozen fence.** Arm B continuous clamp; linear map to [0,1] |
| `DNBR_FLOOR` | 0.100 | [config.py:38](../src/config.py#L38) | **Frozen fence.** Below-floor -> non-covered; shared by both arms |
| `DNBR_NODATA_FAILLOUD_FRAC` | 0.20 | [config.py:42](../src/config.py#L42) | dNBR NoData/cloud over >20% of a flowed basin -> fail loud |
| `BURN_LOW_COVERAGE` | 0.80 | [config.py:46](../src/config.py#L46) | Flag basins with < 80% SBS-covered cells (flag only, never excludes) |
| `CANONICAL_CRS` | EPSG:32611 | [config.py:49](../src/config.py#L49) | Montecito/UTM-11N validation zone; per-fire default |
| `ALLOWED_UTM_ZONES` | `{32611, 32613}` | [config.py:54](../src/config.py#L54) | Metric-CRS allowlist; a missing zone fails loud |
| `CELL_M` | 10.0 m | [config.py:55](../src/config.py#L55) | DEM resolution; `dx = dy`; slope spacing; `CELL_AREA_KM2 = 1e-4` |
| `DIRMAP` | `(64,128,1,2,4,8,16,32)` | [config.py:66](../src/config.py#L66) | **Frozen fence.** pysheds D8 encoding, `(N,NE,E,SE,S,SW,W,NW)` |
| `D8_OFFSETS` | code -> `(drow,dcol)` map | [config.py:67-68](../src/config.py#L67-L68) | **Frozen fence.** Decodes `fdir` to the downstream step at outlet detection |
| `HYPSOMETRIC_SPAN_THRESHOLD_M` | 50.0 m | [delineate.py:116](../src/delineate.py#L116) | A27/A39 terrain-router trigger (span > 50 m ⟹ incised engine); frozen, no per-fire override, no config entry |
| `SUBBASIN_ACC_THRESHOLD_CELLS` | 3000 cells (~0.30 km²) | [config.py:92](../src/config.py#L92) | **Frozen fence (A39).** WhiteboxTools trunk-network channel threshold for confluence splitting on incised terrain (~6× the range-front `ACC_THRESHOLD_CELLS`) |
| `SUBBASIN_BURN_FRAC_MIN` | 0.25 | [config.py:93](../src/config.py#L93) | **Frozen fence (A39).** Minimum burned-cell fraction to keep an incised sub-basin (phase 2) |
| `SUBBASIN_SLOPE_FLOOR_TAN` | 0.05 (~2.9°) | [config.py:94](../src/config.py#L94) | **Frozen fence (A39).** Drops degenerate flat incised sub-basins only; NOT result-blind (set after seeing output — see vault `DECISIONS.md` A39) |
| `SUBBASIN_BREACH_DIST_CELLS` | 100 cells (1 km at 10 m) | [config.py:95](../src/config.py#L95) | **Frozen fence (A39).** WhiteboxTools least-cost breach search radius, incised conditioning only |
| Score formula | `mean_burn × mean_slope × area_km2` | [score.py:56](../src/score.py#L56) | **Frozen fence.** Term + evaluation order frozen (IEEE non-associativity) |
| `MASTER_KNOWN_KM2` | 39.19 km² | [config.py:73](../src/config.py#L73) | **Print-only reference** (see note); no live logic keys off it. Reconstructed master = 44.7273 |
| `MASTER_MIN_AOI_FRACTION` | 0.05 | [config.py:81](../src/config.py#L81) | **FM-1 scale-free floor** (A38). `master_km2 ÷ valid-AOI` must be ≥ this, else GateAbort. Derived: Montecito 0.2648 ÷ ~5 |
| **Oracle: rank-AUC** | **0.9722** (175/180) | [test_behavior_lock.py:83](../tests/test_behavior_lock.py#L83) | Behavior-lock value; reconstructed, not the report's 0.987 |
| **Oracle: master outlet** | **44.7273 km²** (±0.5) | [test_behavior_lock.py:94](../tests/test_behavior_lock.py#L94) | FM-1 anti-0 km² linchpin; reconstructed, not 39.19 |
| **Oracle: basin count** | **36 basins** | [test_behavior_lock.py:102](../tests/test_behavior_lock.py#L102) | AOI-shift delta vs the report's 32 (a finding, not a bug) |
| Oracle: truth set | `{4,6,9,14,21,23}` (6 flowed) | [test_behavior_lock.py:88](../tests/test_behavior_lock.py#L88) | Ground-truth flowed basins; 6/6 in top tercile; #1 = basin 6 |

> **Master-outlet guard is SCALE-FREE (A38, supersedes T5).** The FM-1 anti-collapse check no longer bins the domain pour-point into PASS / FINDING / ABORT km² bands centered on `MASTER_KNOWN_KM2` — those flagged on absolute size (calibrated to Montecito) and did not generalize. It now aborts iff the master catchment is a below-floor **fraction of the AOI's valid DEM area**: `assert_master_outlet_scale` ([pipeline.py:214](../src/pipeline.py#L214)) GateAborts when `master_km2` is non-finite/≤0, `valid_area_km2` ≤ 0, or `master_km2 / valid_area_km2 < MASTER_MIN_AOI_FRACTION` (0.05). Lower-only (a master ≈ AOI is a clean single-drainage crop). The reconstructed Montecito master is **44.7273 km²** = **26.5%** of its 168.93 km² valid AOI ([test_behavior_lock.py:94](../tests/test_behavior_lock.py#L94)) → well above the 5% floor, so it does not abort. `MASTER_KNOWN_KM2 = 39.19` survives only as a print-only reference in `validation/gate.py`; treat 44.7273 as truth. There is **no per-fire delineation-confidence surface** — the guard is a binary collapse detector (none until the P4 reference check).

**Frozen fence, restated:** the score formula, `BURN_WEIGHTS`, the `DNBR_*` bin/clamp/floor constants, and `DIRMAP`/`D8_OFFSETS` are category-two frozen. Changing any of them re-opens validation. They are documented here as frozen for that reason.

The four `SUBBASIN_*` constants (A39) are frozen the same way, scoped to the **incised tier only** — never promotable to range-front scoring. Unlike the others, their provenance is explicitly **not result-blind**: the author had already seen South Fork / Trout / Montecito / Cooks Peak / Putah output at these settings before they were frozen, and the slope floor specifically was added after degenerate flat basins appeared in that output (vault `DECISIONS.md` A39). Carried here as an honest caveat on the evidence, not a license to re-tune.
