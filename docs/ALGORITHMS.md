# Post-Fire Debris-Flow Watershed Screening Tool: Algorithms Reference

**Author:** Aarush Madhireddy
**Date:** July 6, 2026
**Scope:** A walkthrough of every algorithm in the scoring pipeline, from burn/DEM ingest through the frozen `mean_burn × mean_slope × contributing_area_km²` heuristic to the within-fire ranking and the validation metrics. Each algorithm gets one plain-language explanation ("For the Environmental Scientist") and one implementation-level explanation ("For the Programmer").

> **The spine, stated once, up top.** Every score in this pipeline is a **within-fire ordinal ranking** of "which burned watersheds warrant a closer look first." It is **never** a prediction of where debris will go, never a probability, never a volume, and never cross-fire comparable. The tool triages fires that fall outside formal USGS/state assessment; it does not out-model USGS. This framing is stamped into every artifact ([outputs.py:35](../src/outputs.py#L35)) and it governs how you read everything below.

This document reflects the **live code tree**, read this session. Where the code and an existing `.md` disagree on a number, the code wins and the discrepancy is flagged inline (see the closing note for the reconciliation list). The project's canonical science notes live in the Obsidian vault; the repo's `ARCHITECTURE.md` and `docs/science_reference.md` are one-line stubs, so this doc cites source lines, not those stubs.

---

## Contents

1. [Pipeline overview](#1-pipeline-overview)
2. [Foundations (brief)](#2-foundations-brief)
3. [Flow modeling: hydrology (deep)](#3-flow-modeling-hydrology-deep)
4. [Slope (deep)](#4-slope-deep)
5. [Outlet detection and catchment delineation (deep)](#5-outlet-detection-and-catchment-delineation-deep)
6. [Burn severity to weight, and the coverage-weighted mean (deep)](#6-burn-severity-to-weight-and-the-coverage-weighted-mean-deep)
7. [The dNBR burn-source arm: built, tested, not yet wired (deep)](#7-the-dnbr-burn-source-arm-built-tested-not-yet-wired-deep)
8. [The frozen score and within-fire ranking (deep)](#8-the-frozen-score-and-within-fire-ranking-deep)
9. [Terrain-applicability refusal: the hypsometric gate (deep)](#9-terrain-applicability-refusal-the-hypsometric-gate-deep)
10. [Validation algorithms (brief-to-medium)](#10-validation-algorithms-brief-to-medium)
11. [Parameter summary table](#11-parameter-summary-table)

---

## 1. Pipeline overview

Seven modules, no orchestrator. Stages connect through the `grids.py` data contract, not a coordinator object. `run.py` (production) and `validation/gate.py` (the Montecito validation harness) both call the one shared `run_pipeline` in [pipeline.py](../src/pipeline.py); `gate.py` is now a backward-compat re-export shim ([gate.py:57-63](../validation/gate.py#L57-L63)).

The conceptual module order is `ingest → hydrology → delineate → score → outputs`. At runtime the burn raster is loaded and remapped by the `ingest.ingest_burn` seam late (just before scoring), and the per-cell slope raster is computed in the pipeline body; both flow into `score` alongside the delineated basins.

```
DEM.tif ─┐
         ├─> ingest.load_dem ─> A27 terrain gate ──(incised: span > 50 m)──> refusal.json   [STOP: no ranking]
         │                            │
         │                            └──(range-front: proceed)─┐
         │                                                      ▼
         │                             hydrology.run_hydrology  (pysheds 5-step:
         │                             fill pits -> fill depressions -> resolve flats
         │                             -> D8 flow direction -> flow accumulation)
         │                                                      │  fdir, acc
         │                                                      ▼
         │                             delineate.stage_2b_outlets   (canyon mouths)
         │                             delineate.stage_2c_delineate (index-mode upslope
         │                             catchments -> basins[])
         │                                                      │
SBS.tif ─┴─> ingest.ingest_burn                                │  basins[]
             (A3: ONE source, never blended;                   │
              A17 weight raster wt[], A18 coverage[];  wt, cov ─┤
              A4 single Provenance stamp)                       │
                                                                │
   pipeline.mean_slope_tan(dem_raw) ──────────────── slope ─────┤
                                                                ▼
                                             score.stage_2e_score
                                   mean_burn x mean_slope x area_km2
                                   -> within-fire ordinal rank (score desc)
                                                                │
                              creeks.geojson ─> pipeline.evaluate
                              (truth match <= 250 m; tercile n//3; strict-pairwise rank-AUC)
                                                                │
                                                                ▼
                                             outputs.write_outputs
                                     ranking.csv + basins.geojson
                             (each stamped burn_source + SCREENING_STATEMENT)
```

**Key design principle.** Two things are load-bearing and neither is negotiable.

1. **The formula is frozen.** `mean_burn × mean_slope × contributing_area_km²` was pre-registered and validated. Changing the term order, the evaluation order, `BURN_WEIGHTS`, the dNBR bin/clamp/floor constants, or `DIRMAP`/`D8_OFFSETS` re-opens validation. These are the "category-two frozen fence." The known `× area` mis-ranking (a large moderately-burned basin can outrank a small severely-burned flowed one) is tracked as decision **C1** and deliberately left un-tuned.
2. **The tool refuses on incised-upland terrain rather than emitting a low-confidence rank.** On terrain that is dissected highland all the way (no mountain-front break onto a plain), the `CONTOUR_M` outlet anchor is ill-posed and each of the score's three terms loses its referent. The tool writes an honest `refusal.json` with a reason and produces **no ranking**. The refusal is a feature, not a failure (decisions **A27**/**A28**).

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

---

## 4. Slope (deep)

**File:** [pipeline.py:204-214](../src/pipeline.py#L204-L214)

### The method: as actually coded

```
gy, gx = np.gradient(dem_raw, CELL_M, CELL_M)      # d/d(row), d/d(col), z per metre
slope  = np.hypot(gx, gy)                           # sqrt(gx^2 + gy^2) = tan(theta), rise/run
```

This is **not** Horn's method and **not** a pysheds slope product. It is a plain `numpy.gradient` central-difference on the **raw metric DEM**, with the two partial derivatives combined into a gradient magnitude by `np.hypot`. The result is `tan(theta)`, dimensionless rise-over-run, per cell. The per-basin `mean_slope` is then the arithmetic mean of `tan(theta)` over the basin mask ([score.py:55](../src/score.py#L55)).

### For the Environmental Scientist

Steeper burned slopes mobilize debris more easily: gravity does more of the work, so water and loose post-fire sediment move with more energy. Slope is the score's "transport energy" term.

We measure steepness as **rise over run** (`tan` of the slope angle), computed from the elevation grid itself. For each cell we look at how fast elevation changes going east-west and north-south, and combine those into a single steepness number. A value of 0 is flat; a value near 0.6 corresponds to a basin averaging about 31 degrees, which is what the steep Montecito catchments actually show. The tool reports the **mean** steepness across a basin, so a small very-steep gully and a large gentle fan get different, physically-sensible slope terms.

One honest caveat the code carries: this slope pass reads the raw DEM with no valid-cell mask. On a normal inland fire that is fine. On a **coastal** DEM, a land cell next to an ocean/nodata cell (which pysheds clamps to elevation 0) reads a spurious cliff and inflates that basin's slope. There is no coastal fire in scope and no fixture that can exercise it, so this is recorded as a P3 hazard note (decision **A33**) and deliberately not "fixed" blind. Until a real coastal case exists, the tool has no coastal-slope guarantee.

### For the Programmer

`np.gradient(dem_raw, CELL_M, CELL_M)` uses central differences in the interior and one-sided differences at the edges, with the spacing argument `CELL_M = 10.0` m applied to both axes, so `gx`/`gy` come out dimensionless (metres of rise per metre of run). `np.hypot(gx, gy)` is the L2 magnitude, i.e. `tan(theta)`. Owner-confirmed to reproduce the reconstruction's `mean_slope` column to within 0.01, which is why it is frozen as-is rather than swapped for Horn's 8-neighbour method.

Units discipline matters here: the term is `tan`, not degrees and not percent. The `science_reference` "0-1 transport-energy proxy" phrase is a typical-range description, not a hard clamp; `tan` stays below 1 only because mean basin slopes sit around 31 degrees. No masking is applied (see the A33 caveat above), so every cell in the DEM gets a slope, and the per-basin reduction happens later in `score` over the basin mask only.

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

`CONTOUR_M` gets a fail-loud range guard before this runs: `assert_contour_in_dem_range` ([delineate.py:74-106](../src/delineate.py#L74-L106)) aborts if 150 m falls outside the DEM's valid min/max, catching a wrong-fire contour that would yield zero canyon mouths. It catches the gross numeric mis-set only, not geomorphic correctness; the geomorphic case is the A27 refusal in Section 9.

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
    if source != "SBS": raise GateAbort(...)                 # A29 fail-loud (dNBR arm not yet wired)
    wt, covered = _burn_weight_raster(sbs)                   # A17 weights + A18 coverage
    provenance = {"burn_source": source}                    # A4: ONE stamp, read everywhere
    return wt, covered, provenance
```

### For the Environmental Scientist

The burn input answers "how badly did each patch of ground burn," which stands in for how much the soil's ability to absorb water collapsed. Higher severity means more runoff, which means more water and sediment available to a debris flow. That is why burn severity is a term in the score.

The pipeline uses exactly **one** burn source per fire, never a blend. If a field-validated BAER Soil Burn Severity map covers the whole area, it uses that (it is the validated input). If it does not, the intended production fallback is satellite dNBR for the whole area. Two sources measure subtly different things at different scales, so averaging them would produce a number that means neither. The choice is made once, in one place, and stamped onto every output so a reader always knows which input produced the ranking.

Severity classes become a 0-to-1 weight: unburned/very-low = 0.0, low = 0.33, moderate = 0.67, high = 1.0. Developed land and ground outside the burn perimeter count as 0.0. A basin's burn term is the average of these weights over the whole basin, including the outside-perimeter zeros. So a watershed that is only partly inside the burn honestly reads a lower burn term than one that burned wall-to-wall. A separate "coverage" number tracks how much of each basin actually had a real burn assessment, and flags basins below 80% covered so a user knows when the burn term rests on thin data.

### For the Programmer

`SBS_CODESET = (0, 1, 2, 3, 4, 15)` ([ingest.py:38](../src/ingest.py#L38)) is the validity test: `sbs.tif` declares no rasterio nodata, so codeset membership (not a GDAL mask) defines "covered the AOI." Class 15 (outside-perimeter) counts as covered ("assessed: outside the burn"), an owner decision. `select_burn_source` returns `"dNBR"` for a partial-SBS AOI, but `ingest_burn` guards that and fails loud, because the dNBR end-to-end arm is built but not wired (A29; Section 7). Without the guard, `_burn_weight_raster` would score the SBS raster while stamping a `"dNBR"` provenance, a silent mislabel.

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

## 7. The dNBR burn-source arm: built, tested, not yet wired (deep)

**File:** [ingest.py:167-298](../src/ingest.py#L167-L298)
**Constants:** `DNBR_BIN_EDGES`, `DNBR_CLAMP`, `DNBR_FLOOR` at [config.py:32-38](../src/config.py#L32-L38)
**Status:** implemented and unit-tested against hand-computed known-answers ([tests/test_dnbr_arm.py](../tests/test_dnbr_arm.py)), but **not wired into `ingest_burn`**. `ingest_burn` fails loud on any non-SBS selection (A29). Full end-to-end dispatch is deferred to P2.2c. Documented here because the code is live in the tree and its constants are on the frozen fence; it does not run on Montecito.

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

This arm is written and unit-tested but not yet switched on: today a fire without full SBS coverage is refused loudly rather than scored under a dNBR label the pipeline can't yet honestly stamp. That is the fail-loud spine choosing a clear stop over a plausible-looking guess.

### For the Programmer

`reproject_dnbr` ([ingest.py:167-196](../src/ingest.py#L167-L196)) snaps native dNBR onto the DEM grid via explicit `dst_transform`/`dst_shape` (never the dNBR scene's own grid), Arm A with `Resampling.nearest`, Arm B with `Resampling.bilinear`. `ingest_dnbr_both_arms` ([ingest.py:243-298](../src/ingest.py#L243-L298)) derives one shared valid footprint from Arm A's nearest reproject and applies it to both arms, so A and B are byte-identical in footprint by construction; `assert_aligned` runs on both before any thresholding, and a fail-loud guard rejects any non-finite/sentinel value inside the valid footprint. The known-answer tests pin every bin boundary (0.100 left-closed to class 2, 0.440 collapsing to class 3, and so on), the linear Arm-B transfer, the shared floor, and the NaN-routing defect.

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

## 9. Terrain-applicability refusal: the hypsometric gate (deep)

**Detector:** [delineate.py:119-170](../src/delineate.py#L119-L170)
**Wiring:** [pipeline.py:297-329](../src/pipeline.py#L297-L329), called first in `run_pipeline` at [pipeline.py:382-385](../src/pipeline.py#L382-L385)
**Constant:** `HYPSOMETRIC_SPAN_THRESHOLD_M = 50.0` at [delineate.py:116](../src/delineate.py#L116)
**Decisions:** A27 (rule), **A28** (supersedes A27's caveated-ranking behavior), A31 (runs before hydrology)
**Status:** built, wired, and tested end-to-end. Not a design on paper.

### The rule

```
valid   = finite DEM cells, and (if a nodata sentinel exists) != that sentinel
p1, p10 = 1st and 10th percentiles of valid-cell elevation (m), method='linear'
span_m  = p10 - p1
REFUSE iff span_m > 50.0                                     # strict >
```

On REFUSE the pipeline writes `refusal.json` ([outputs.py:67-102](../src/outputs.py#L67-L102)) and returns a refusal-result. It writes **no** `ranking.csv` and **no** `basins.geojson`. The refusal message is span-based ([outputs.py:41-64](../src/outputs.py#L41-L64)).

### For the Environmental Scientist

The whole method assumes one shape of landscape: steep mountains rising over a flatter plain, with creeks that leave the range at a mountain front and spill onto fans where people live. That mountain-front break is what the tool anchors its watersheds to.

Some burned terrain isn't shaped like that. An **incised upland** is dissected highland all the way: deep valleys cut into high ground, with no plain to spill onto. On that terrain the method's assumptions quietly fail. There is no mountain-front break, so there are no canyon mouths to anchor to. There is no depositional plain, so "contributing area to an outlet" has no natural unit. And because everything is uniformly steep, the slope term stops telling basins apart. Every one of the score's three ingredients loses its meaning.

So instead of emitting a low-confidence ranking that looks authoritative and isn't, the tool **declines, with a reason**. It measures one thing: how spread out the low elevations are. It takes the 1st and 10th percentiles of the terrain's elevation and looks at the gap between them. A true range-front-over-plain compresses that gap to roughly 20 to 30 m (the plain is a tight cluster of low elevations). An incised valley floor spreads it wide. If the gap exceeds 50 m, the terrain is incised, and the tool writes an honest refusal explaining that this is a known boundary of the method, not a failure. A refusal a practitioner can read beats a confident guess a practitioner might act on.

### For the Programmer

`assess_hypsometric_applicability` computes `np.percentile(valid_elevations, [1, 10], method='linear')` and refuses on `span_m = p10 - p1 > 50.0` (strict `>`; the exact `== 50.0` boundary does not refuse, pinned by test). `_valid_dem_mask` ([delineate.py:49-68](../src/delineate.py#L49-L68)) is the single source of truth for "which cells are terrain" (finite and, critically, `!= nodata`; pysheds clamps an undeclared nodata to 0, so failing to exclude it collapses the valid min to 0). An all-nodata DEM raises `GateAbort` (a broken input is fail-loud, not an "incised" verdict).

The **firewall** is load-bearing and adversarially tested ([tests/test_a27_applicability.py](../tests/test_a27_applicability.py) group D): the detector returns exactly `{refuse, reason_code, span_m, span_threshold_m, n_valid}`. `span_m` is a difference (a vertical extent), the only elevation-derived number that leaves the function. `p1` and `p10` are logged and discarded, never returned. No absolute elevation and no `CONTOUR_M` candidate crosses the boundary, because a meters value feeding anything downstream would be a tuning knob and would put this rule on the category-two scoring fence. The signature is `(dem_raw, dem_nodata)` only, with no threshold parameter and no `config.py` override path.

**A28 supersedes A27's original "caveated ranking" clause.** A27 first ratified "return the still-valid ranking, anchoring caveated," on the premise that the ranking is independent of the mountain front. Later incised-terrain analysis refuted that premise, and A28 ratified the shipped behavior: on incised upland the ranking is not merely un-anchored, it is meaningless, because `contributing_area_km²` has no discrete outlet, `mean_slope` stops discriminating on uniformly-steep terrain, and there is no plain-referenced discharge point to define the ranking unit. So **no ranking is produced**. Per A31 ([pipeline.py:374-385](../src/pipeline.py#L374-L385)), the gate runs on the raw DEM **before** SBS is opened or hydrology runs, so an un-assessed dNBR-only incised fire (South Fork) reaches an honest refusal end-to-end instead of crashing on a missing SBS. Montecito's span is ~15 m, so it passes the gate and the behavior lock is untouched. The end-to-end refusal path is verified hermetically ([tests/test_a31_reorder.py](../tests/test_a31_reorder.py)) and is non-vacuous: reverting the reorder makes the test fail.

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
| `HYPSOMETRIC_SPAN_THRESHOLD_M` | 50.0 m | [delineate.py:116](../src/delineate.py#L116) | A27 refusal trigger; frozen, no per-fire override, no config entry |
| Score formula | `mean_burn × mean_slope × area_km2` | [score.py:56](../src/score.py#L56) | **Frozen fence.** Term + evaluation order frozen (IEEE non-associativity) |
| `MASTER_KNOWN_KM2` | 39.19 km² | [config.py:73](../src/config.py#L73) | **Print-only reference** (see note); no live logic keys off it. Reconstructed master = 44.7273 |
| `MASTER_MIN_AOI_FRACTION` | 0.05 | [config.py:81](../src/config.py#L81) | **FM-1 scale-free floor** (A38). `master_km2 ÷ valid-AOI` must be ≥ this, else GateAbort. Derived: Montecito 0.2648 ÷ ~5 |
| **Oracle: rank-AUC** | **0.9722** (175/180) | [test_behavior_lock.py:83](../tests/test_behavior_lock.py#L83) | Behavior-lock value; reconstructed, not the report's 0.987 |
| **Oracle: master outlet** | **44.7273 km²** (±0.5) | [test_behavior_lock.py:94](../tests/test_behavior_lock.py#L94) | FM-1 anti-0 km² linchpin; reconstructed, not 39.19 |
| **Oracle: basin count** | **36 basins** | [test_behavior_lock.py:102](../tests/test_behavior_lock.py#L102) | AOI-shift delta vs the report's 32 (a finding, not a bug) |
| Oracle: truth set | `{4,6,9,14,21,23}` (6 flowed) | [test_behavior_lock.py:88](../tests/test_behavior_lock.py#L88) | Ground-truth flowed basins; 6/6 in top tercile; #1 = basin 6 |

> **Master-outlet guard is SCALE-FREE (A38, supersedes T5).** The FM-1 anti-collapse check no longer bins the domain pour-point into PASS / FINDING / ABORT km² bands centered on `MASTER_KNOWN_KM2` — those flagged on absolute size (calibrated to Montecito) and did not generalize. It now aborts iff the master catchment is a below-floor **fraction of the AOI's valid DEM area**: `assert_master_outlet_scale` ([pipeline.py:214](../src/pipeline.py#L214)) GateAborts when `master_km2` is non-finite/≤0, `valid_area_km2` ≤ 0, or `master_km2 / valid_area_km2 < MASTER_MIN_AOI_FRACTION` (0.05). Lower-only (a master ≈ AOI is a clean single-drainage crop). The reconstructed Montecito master is **44.7273 km²** = **26.5%** of its 168.93 km² valid AOI ([test_behavior_lock.py:94](../tests/test_behavior_lock.py#L94)) → well above the 5% floor, so it does not abort. `MASTER_KNOWN_KM2 = 39.19` survives only as a print-only reference in `validation/gate.py`; treat 44.7273 as truth. There is **no per-fire delineation-confidence surface** — the guard is a binary collapse detector (none until the P4 reference check).

**Frozen fence, restated:** the score formula, `BURN_WEIGHTS`, the `DNBR_*` bin/clamp/floor constants, and `DIRMAP`/`D8_OFFSETS` are category-two frozen. Changing any of them re-opens validation. They are documented here as frozen for that reason.
