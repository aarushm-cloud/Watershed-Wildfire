# Post-Fire Debris-Flow Watershed Screening

An automated screening tool that ranks which burned watersheds most warrant a closer look after a wildfire — using only free, public satellite and terrain data — built for fires that fall outside formal USGS or state hazard-assessment coverage.

Post-fire debris-flow hazard assessments are run on a **request-and-select basis**: a fire is assessed only if an official requests it and capacity allows. Well-resourced western states (CA, WA, CO) run their own rapid-response teams; fires outside those systems — smaller, lower-profile, or in thinner-coverage regions — can fall through the gap and receive no formal screening at all. This tool closes that gap with a fast, zero-wait triage screen built entirely on public data, producing a defensible *"assess these watersheds first"* ranking for fires the request-driven system misses.

> **For a reviewer coming from remote sensing or GIS:** the fastest way to judge this tool is by what it refuses to claim. It is an **uncalibrated, within-fire ordinal ranker** — not a likelihood model, not a volume model, not an inundation footprint. It deliberately omits rainfall and soils (the inputs a real hazard model needs) and stops short of runout physics. Everything below is organized around holding that line. The deep algorithm reference is [`docs/ALGORITHMS.md`](docs/ALGORITHMS.md).

---

## What it is — and is NOT

This distinction is the spine of the project, and every design choice below exists to hold it.

- **Not an inundation or runout predictor.** It does not claim "this house will be hit," and it never draws confident danger polygons over specific buildings. Real lives are downstream; a model painting confident danger zones over individual homes is dangerous, not merely wrong.
- **Not a competitor to or replacement for USGS.** USGS runs field-validated likelihood (M1) and volume (Gartner) models and is actively building downstream-routing tools. This tool does not out-model them and does not pretend to. It is not a probability and not a volume, and it must never be described as approximating M1 or Gartner — it omits the rainfall and soil inputs those models require.
- **Not a flow-physics model.** The runout problem is genuinely hard and is where domain experts spend funded effort. This tool deliberately stops short of it.
- **Not cross-fire comparable.** Scores are ordinal and apply *within a single fire only*. A score from Fire A cannot be ranked against a score from Fire B.

Every output is framed as *"watersheds warranting detailed assessment"* — a within-fire relative ranking, never an absolute prediction. That exact sentence is stamped into every artifact the tool writes.

---

## Where it fits

This tool is a **triage screen for the fires the existing system doesn't reach** — it complements the programs below and replaces none of them.

| Program | What it provides | Coverage |
|---|---|---|
| **USGS post-fire assessments** | Field-validated likelihood (M1) and volume (Gartner) models; downstream-routing tools in development | Request-and-select; capacity-limited |
| **State rapid-response teams** (CA, WA, CO) | Fast local hazard assessment | Well-resourced western states only |
| **BAER teams** | Field-validated Soil Burn Severity (SBS) | Only fires a BAER team is dispatched to |
| **State geological surveys** (e.g. CGS) | Prioritization stacks: burn, slope, area **plus** a regional susceptibility prior and rainfall history | Program-dependent |
| **This tool** | A zero-wait within-fire ranking from public data — "assess these watersheds first" | Any contiguous-US fire; no request required |

A jurisdiction whose fire was never assessed can still get a defensible starting point for *where to look first* — not a hazard determination, but a prioritized list to route scarce assessment capacity. It is a first pass that says "these catchments, before those," meant to precede (never substitute for) a real assessment.

---

## What it produces, and how to read it

One run over a single fire writes to `out/<fire>/`:

- **`ranking.csv`** — the within-fire ordinal ranking of detected basins, highest screening concern first, with the per-basin terms (`mean_burn`, `mean_slope`, `area_km2`), both dNBR arms, and coverage/uncertainty flags. Two leading comment lines carry the screening framing and the burn-source provenance stamp.
- **`basins.geojson`** — the delineated basin polygons, reprojected to EPSG:4326, with a top-level `provenance` member so the framing travels with the geometry.

The interactive **ranked basin map** is rendered live in the local app (fill = headline rank, with basins outlined where the two dNBR arms disagree); it is a view over the artifacts above, not a separate persisted file. There is no confident-hazard raster and no per-building output by design.

Every artifact carries the burn-source provenance and an embedded *"what this is / is not"* note, so the framing survives being forwarded out of context.

**How to read a ranking:**

- Rank **1** is the highest *screening concern within this fire* — a "look here first," not "this will flow."
- It is **not** a probability, a volume, an inundation footprint, or a comparison against any other fire.
- Use it to **prioritize which catchments get a closer look** — a field recon, or a request for a USGS/state assessment — never as a hazard call over specific homes.
- If you cite or forward a ranking, **carry the framing with it.**

**Two terrain tiers, always inside the screening frame.** Range-front fires (a steep range spilling onto a flatter plain) get the validated canyon-mouth ranking described in [How it works](#how-it-works). Fires without that shape — incised, dissected highland — get a separate **exploratory, disclaimed** ranking instead of no ranking at all: unvalidated on this terrain class, and every artifact it produces says so plainly. **A loud failure is still a valid result** for what neither tier can handle — missing or incompatible burn data, a DEM that doesn't cover the fire's drainage network, imagery too cloudy to compute a dNBR, or a catchment that collapses (see [Design decisions](#design-decisions)) — the tool **produces no ranking and says why** rather than emitting a fabricated score. An honest "cannot assess" is a correct outcome; a confident-looking wrong ranking is the failure mode the whole system is built to avoid.

---

## Running it

Two ways in, over the same validated pipeline.

**1 — Command line, for a registered fire:**

```
python run.py --fire <name>      # → writes out/<fire>/
```

`run.py` resolves a registered fire's staged inputs, runs the pipeline, and writes a ranking (or a legible refusal). Registered cases: `montecito` (SBS validation), `montecito_dnbr` (dNBR swap), `southfork` (incised).

**2 — Local app, for an arbitrary location (non-developers):**

```
streamlit run app.py
```

Draw or enter a bounding box, then choose how to supply burn severity:

- **Upload a dNBR** — provide a raw-scale dNBR GeoTIFF (≈ −1…1) you already have. The app auto-fetches the DEM and downstream assets for the box, scores the fire, and returns the ranked map + CSV.
- **Generate from dates** — supply the ignition and containment dates and let the tool acquire the imagery itself: it searches public Sentinel-2 / Landsat catalogs for a clean pre/post pair, scores each candidate against a cloud rubric, and presents a **human-approval scorecard** (per-scene cloud over your fire, true-color previews, a good/OK/marginal verdict, and a swap-in-other-candidates option) before it builds anything. On approval it computes a real dNBR and runs the same pipeline. See [Auto-acquiring a dNBR](#auto-acquiring-a-dnbr).

Either way the result is a within-fire ranked map + CSV, or an honest refusal rendered as a plain message rather than a stack trace. The app is a **local, single-user tool that wraps the CLI** — not a hosted or multi-user service.

**Coverage.** Any bounding box in the contiguous US (UTM zones 10N–19N, EPSG 32610–32619) is accepted; an out-of-CONUS box refuses at the door before any data is fetched. A single-fire area cap (1.0 deg²) also gates at the door.

**Environment.** Python 3.11 in a conda env built from the pinned `environment.yml` (`environment.lock.yml` captures the exact solve). See [Architecture & tech stack](#architecture--tech-stack).

---

## How it works

Three public data ingredients, one deliberately simple scoring heuristic, no physics simulation. The pipeline is five pure stages (`ingest → hydrology → delineate → score → outputs`) wired in `src/pipeline.py`, with `config.py` holding per-fire scalars and `grids.py` holding the inter-stage data contract.

### Data inputs

| Input | Source | Notes |
|---|---|---|
| **Terrain (DEM)** | USGS 3DEP 1/3 arc-second (~10 m) COG | The National Map staged products on AWS S3 (`prd-tnm.s3.amazonaws.com`), read via `/vsicurl/`; windowed to the bbox and bilinearly reprojected onto the canonical 10 m UTM grid. The Montecito validation case runs on a fixed EPSG:32611 grid for reconstruction fidelity. |
| **Burn severity — dNBR** (production default) | Sentinel-2 L2A (primary), Landsat 8/9 Collection-2 L2 (fallback) | Continuous spectral-change index computed by the tool; available for any fire the satellites see. Sentinel-2 via Earth Search on AWS (Element84 STAC); Landsat via Microsoft Planetary Computer STAC. |
| **Burn severity — BAER SBS** (preferred when it exists) | BAER Soil Burn Severity | Field-validated 4-class soil product; exists only for fires a BAER team assessed. |
| **Downstream assets** | OpenStreetMap building footprints (Overpass) | Defines "downstream of what." Used to discard catchments draining away from anything to protect. Reduced to representative centroid points in the metric CRS. |

All free, no licensing cost. The network boundary is deliberate: `src/` never touches the network — all fetching lives in `acquire.py` (and the `autoacquire/` package), which stages files to disk for the pure pipeline to consume.

### Burn severity from dNBR

For fires without BAER SBS — the tool's actual target population — burn severity comes from the Normalized Burn Ratio difference, computed from public surface-reflectance imagery:

```
NBR   = (NIR − SWIR) / (NIR + SWIR)
dNBR  = NBR_pre − NBR_post          # raw scale (~ −0.5 … +1.3), positive = burned; never ×1000
```

- **Sentinel-2 L2A** (primary): NIR = **B8A**, SWIR = **B12** (both 20 m). Surface reflectance from digital number as `(DN − 1000) / 10000` (BOA offset −1000, processing baseline ≥ 04.00). Per-pixel cloud/shadow masking from the Scene Classification Layer (SCL).
- **Landsat 8/9 Collection-2 Level-2** (fallback): NIR = **B5**, SWIR2 = **B7** (both 30 m). Surface reflectance as `DN × 0.0000275 − 0.2`. Masking from the QA_PIXEL fill + cloud bits.

The continuous dNBR is binned to a 4-class severity using the interior break edges `(0.100, 0.270, 0.440, 0.660)` (raw scale). **Honest provenance for a remote-sensing reviewer:** the NBR/dNBR index math and the sensor scaling are settled, primary-source-verified science (Key & Benson 2006; ESA/Copernicus and USGS documentation). The severity break table is a real and conventional first-approximation table (widely attributed to USGS via UN-SPIDER's recommended-practice guidance, Key & Benson framework lineage), but its citation chain does not terminate as cleanly as a footnote implies, and fixed universal thresholds carry a known, published bias in sparsely-vegetated terrain (Miller & Thode 2007; the case for relativized indices such as RdNBR/RBR). The tool adopts the generic table literally and un-tuned as a deliberate anti-fitting firewall, and treats the classes as a *first approximation* — which is exactly what an ordinal triage ranker can tolerate and a prediction could not.

The dNBR path runs **two arms**: Arm A (nearest-neighbor resampling, binned to the 4 classes) is the pre-registered headline; Arm B (bilinear, continuous transfer) rides alongside as a cross-check. `rank_delta = |rankA − rankB|` flags basins where the two disagree — treat those ranks as uncertain.

### The pipeline

**Ingest.** Loads the DEM, one burn raster, and the asset layer. The burn source is selected here, once, by precedence — SBS if it covers the whole analysis area, else dNBR — **never blended**, and stamped onto a single provenance object every downstream stage reads. Missing or partial burn coverage fails loud here rather than producing a ranking on incomplete inputs. `mean_burn` is computed coverage-weighted: cells outside the fire perimeter / no-data are treated as zero severity, so a partially-burned basin is not flattered by averaging only its burned cells.

**Hydrology.** `pysheds` flow modeling on the conditioned DEM: fill pits → fill depressions → resolve flats → D8 flow direction → flow accumulation. Pure terrain processing; it has no concept of outlets or scores. (An independent `pyflwdir` engine reproduces this flow field to within a Pearson correlation of 0.9994 on the scored basins — a confidence check, not a runtime dependency.)

**Delineation.** Channel cells are those above the flow-accumulation threshold (500 cells, ≈ 0.05 km²). Outlets are channel cells crossing the mountain-front contour (default 150 m) going downhill — where creeks leave the mountains onto developed fans. Each outlet's upslope catchment is delineated **in index mode (`row, col`)**, catchments below 0.1 km² are discarded, and only those draining within 600 m of an asset are kept. Larger catchments claim contested cells first so no cell is counted twice.

**Scoring.** Each retained basin is scored by the frozen, pre-registered heuristic:

```
score(basin) = mean_burn_severity × mean_slope × contributing_area_km²
```

Slope is the dimensionless gradient magnitude `tan θ` (rise/run, central differences on the raw metric DEM); `mean_burn` is dimensionless in `[0, 1]`; area is in km². Each term proxies a first-order driver: burn → runoff generation / infiltration collapse; slope → transport energy; area → water and sediment volume available. The formula is not a tunable — changing it re-opens validation. Basins are then ranked ordinally within the fire.

**Output.** Writes `ranking.csv` and `basins.geojson`, each stamped with the burn-source provenance and the embedded screening framing.

### Two terrain tiers

Everything above is the **range-front** path: canyon-mouth outlets, index-mode catchments, the frozen formula — the validated method. A router (`assess_hypsometric_applicability`, run first on the raw DEM) measures the low-elevation hypsometric span `p10 − p1`; a span `> 50 m` means there is no plain→range break to anchor a canyon-mouth outlet to.

Such fires — incised, dissected highland — no longer refuse outright. They route instead to a separate, **exploratory and disclaimed** tier:

- **WhiteboxTools** delineates the whole drainage network into sub-basins split at channel confluences (no canyon-mouth outlet needed), using breach-carve conditioning (`BreachDepressionsLeastCost`) that preserves incised channels where the production fill-only chain would smear them, then `D8Pointer → D8FlowAccumulation → ExtractStreams → Subbasins`.
- The drains-to-asset filter is **dropped** (there is no depositional plain for a building to sit near; a wilderness fire would otherwise return zero basins and silently recreate a refusal).
- Basins are ordered by **intensity** (`mean_burn × mean_slope`) rather than the area-weighted score, because contributing area has no anchored meaning on a segmentation-threshold basin the way it does on a canyon-mouth catchment. The frozen `score` still rides along as a companion column, never dropped.

Every incised artifact carries an explicit disclaimer — **unvalidated on this terrain class**, read as relative *source* susceptibility for triage only (not runout, not deposition, not which fan is threatened) — so it is never mistaken for the validated ranking. The A27 detector that used to gate the ranking now only selects the engine; it no longer refuses. An incised fire that supplies SBS instead of dNBR still fails loud (v1 scope: the SBS path has no both-arms shape to hang the mandatory disclaimer on), as does one supplying documented-flow "truth" creeks.

This is **two tiers, two engines**: `pysheds` canyon-mouth catchments for accepted range-front fires (physically anchored where confluence cuts are arbitrary — the range-front path stays frozen and byte-identical), WhiteboxTools whole-network sub-basins for the incised exploratory tier.

### Auto-acquiring a dNBR

The **Generate from dates** app mode (and the `autoacquire/` CLI) turns coordinates + fire dates into a real dNBR without the user touching a satellite catalog. The design principle is **AI proposes, deterministic code disposes** — there is no LLM anywhere in the science path.

- **Scene search.** Sentinel-2 L2A is primary (Earth Search on AWS STAC); Landsat 8/9 Collection-2 L2 is a pair-level fallback (Microsoft Planetary Computer STAC). Sensors are never mixed within a pair. Same-sensor, same-day adjacent tiles are merged into one candidate; cross-UTM-zone groups fail loud.
- **Timing windows.** Pre-fire scene within 90 days before ignition; post-fire scene at or after containment, bounded by a green-up ceiling (default +90 days, operator-extendable to +180) so a regrown scene doesn't wash out the burn signal. If no clean post-fire scene exists yet, the tool reports a **waiting** state rather than fabricating one.
- **Cloud gating.** A coarse metadata pre-filter drops tiles over 80% cloud (never the decisive gate). The decisive gate is a per-pixel **box gate**: the combined pre-∩-post valid fraction over the drawn box must be ≥ 0.50 (derived from the pipeline's 20% per-basin NoData fail-loud guard). A scorecard rubric bins each pair Good / OK / Marginal on cloud-over-fire.
- **Human approval is a separate, mandatory gate.** `select()` proposes and scores; it builds nothing. Nothing becomes a dNBR until a person approves the pair on the scorecard.

The failure mode of the whole tool moves here, to scene selection — which is why acquisition is deterministic, auditable, and gated by a human rather than automated end-to-end.

### Parameters

All per-fire tunables live in one auditable place (`src/config.py`), keyed per fire so editing one fire's values can never silently break another's validated result.

| Parameter | Value | Meaning |
|---|---|---|
| `CONTOUR_M` | 150 m | Mountain-front contour for canyon-mouth outlet detection (per-fire; operator-set in the app) |
| `ACC_THRESHOLD_CELLS` | 500 | Min flow accumulation for a cell to count as a channel (~0.05 km²) |
| `MIN_BASIN_KM2` | 0.1 | Discard catchments smaller than this |
| `DRAINS_TO_ASSET_M` | 600 m | Keep only catchments draining within this distance of an asset (range-front only) |
| `TRUTH_MATCH_M` | 250 m | Tolerance for matching a basin to a documented flow (validation) |
| `HYPSOMETRIC_SPAN_THRESHOLD_M` | 50 m | Low-elevation span above which a fire routes to the incised tier |
| burn weights (SBS) | `{1: 0.0, 2: 0.33, 3: 0.67, 4: 1.0}` | Soil-burn-severity class → normalized severity |
| `DNBR_BIN_EDGES` | `(0.100, 0.270, 0.440, 0.660)` | Interior break edges, raw dNBR → 4 severity classes (Arm A) |
| `DNBR_NODATA_FAILLOUD_FRAC` | 0.20 | Per-basin NoData fraction that fails the run loud |
| `MASTER_MIN_AOI_FRACTION` | 0.05 | Master-outlet catchment must exceed this fraction of the valid AOI (anti-0 km² guard) |
| `ALLOWED_UTM_ZONES` | EPSG 32610–32619 | Accepted ingest zones (CONUS, UTM 10N–19N) |

SBS class encoding: `1` unburned/very-low · `2` low · `3` moderate · `4` high · `0` masked/developed · `15` no-data.

**Incised-tier constants** (frozen, `SUBBASIN_*`): accumulation threshold 3000 cells (~0.30 km², ~6× the production channel threshold, to split at trunk confluences); burn-fraction floor 0.25; slope floor `tan θ ≥ 0.05` (~2.9°, drops degenerate flats); breach search radius 100 cells (1 km at 10 m). These were carried over from the sandbox that developed the tier and were **not** set result-blind (the slope floor in particular was added after seeing output); they are frozen and documented as such rather than described as pre-registered.

---

## Validation

The ranking method is back-tested against the **2017 Thomas Fire / 2018 Montecito** event — one of the best-documented post-fire debris-flow disasters on record — with BAER Soil Burn Severity as the burn input on a fixed EPSG:32611 grid:

- **All six documented-flow basins landed in the top tercile** of the ranking.
- **The top-ranked basin, Cold Spring, flowed** — confirmed physically by roughly 19,000 m³ of debris excavated from its catch basin.
- **Within-fire rank-AUC = 0.9722** across 36 candidate basins; a ~25× separation between flowed and non-flowed basin scores.

The case is locked by the test suite — ranking order, AUC, basin count, and a 44.7273 km² master-outlet sanity area are all asserted, so any regression trips a test. (Older documents cite `0.987 / 39.19 km² / 32 basins`; those are the **original-AOI** Week-0 figures and are not bit-reproducible. The reconstructed oracle that the code actually locks — with the original AOI unrecoverable — is `0.9722 / 44.7273 km² / 36 basins`. The code and the lock are authoritative.)

**dNBR input-swap test (the honest headline).** Swapping the burn input from field-validated SBS to a self-computed dNBR on the same Montecito AOI — same DEM, hydrology, delineation, and frozen formula, only `mean_burn` changes — the dNBR ranking reproduces the SBS result on the metric that maps to the tool's job: **rank-AUC = 0.9722 under SBS, dNBR Arm A, and dNBR Arm B, identical**, all 6/6 flowed basins in the top tercile, Spearman ρ(SBS, dNBR-A) = 0.944. **But the pre-registered binary "Cold Spring is exactly #1" criterion failed** — Arm A ranks San Ysidro Creek #1 and Cold Spring #2, a 1.03% score margin (Arm B recovers Cold Spring #1). That is why the dNBR path is framed as **triage-validated, not exact-rank-validated (n = 1)**: it finds the flow basins as well as SBS, but the exact top-of-list order is not established on one fire. (This validation dNBR was Landsat-8 30 m; the 30 m burn signal on a 10 m grid is itself a stated caveat.)

**Generalization.** The tool has been run end-to-end on the **2026 Putah Fire** (Yolo County, CA), a small contained fire with no existing hazard assessment. From a Sentinel-2 dNBR it passed the terrain-applicability check and ranked six canyon-mouth basins, with the two dNBR arms in agreement — a demonstration that the pipeline generalizes to a new fire on new terrain (no ground-truth AUC; it is an un-assessed fire).

**Incised tier.** The exploratory incised ranking has two pieces of evidence, of different kinds and both honestly bounded:
1. Re-running the *range-front* Montecito case under the incised engine's segmentation settings, the `intensity` ordering keeps its skill — AUC(intensity) = 0.887 over 88 sub-basins, 10/10 of the top-10 intensity-ranked basins flowed. This shows `intensity` discriminates where truth exists, but it is a range-front fire (effective n = 6 flow events), **not** the incised terrain class the tier serves.
2. On an actual incised fire — the 2024 South Fork / Salt fires near Ruidoso, NM — a **pre-registered** concordance check found the `intensity` ordering broadly agrees with the independent USGS `sfk2024` assessment: **Spearman ρ = +0.740** (length-weighted combined-hazard class at 24 mm/h, 93 of 99 sub-basins, band ≥ 0.5 = concordant). This is the first real incised-terrain signal, and it is **concordance, not equivalence** — the two share burn and slope as drivers, USGS additionally uses rainfall and soil, and it is one fire with a coarse near-binary hazard ordinal. It is consistency with an independent assessment, not predictive skill.

---

## Limitations

The tool is a screening aid, and its rankings are meant to be read with these boundaries in mind. (This section is self-contained; `docs/limitations.md` is a placeholder pending the Phase-6 handoff writeup.)

- **Rankings are relative and within-fire.** A ranking orders basins within a single fire; it is not a probability, a volume, or a comparison between fires.
- **Fixed dNBR breaks are region-dependent.** Published per-fire calibrated thresholds vary widely, and absolute-dNBR thresholding carries a documented bias in sparsely-vegetated terrain (chaparral, shrubland, arid sites — precisely Montecito's terrain, and precisely where the bias is worst). The tool adopts a generic table literally and un-tuned; the classes are a first approximation. The best CBI-validated dNBR datasets are confined to western-US conifer forests, so even the accuracy ceiling (~50% of variance explained, ~60% overall accuracy) is an out-of-domain extrapolation here — well within what an ordinal triage ranker tolerates, well outside what a prediction could.
- **The `× area` term is linear and uncapped.** A large, moderately-burned catchment can outrank a small, severely-burned one. For context, the USGS M1 likelihood model is calibrated on basins of 0.2–8 km² (Staley et al., 2016, USGS OFR 2016-1106); this tool applies no such upper bound and so leans toward larger basins at the high end. A documented future experiment (area-dampening), not a live knob.
- **No rainfall or regional susceptibility.** The screen weighs burn severity, slope, and contributing area — but not storm intensity, nor the regional susceptibility that geology, soils, and sediment supply confer (the San Gabriels reliably produce debris flows; other ranges far less so). Both strongly influence whether a basin actually flows, so a ranking complements — never replaces — an assessment that accounts for them. This is the single most concrete gap surfaced in practitioner outreach.
- **Coverage-weighted `mean_burn` can under-rank a genuinely-hot but low-coverage basin** (the deliberate direction of that treatment), and a `score = 0.0` is disambiguated from "not assessed" by a `low_coverage` flag rather than being silently trusted.

---

## Design decisions

**Why a deliberately simple `burn × slope × area` heuristic.** The target is a *screening* triage, not a runout simulation. Each term is a first-order driver of debris-flow concern: fuel for the flow (burn), the energy to move it (slope), and the catchment that feeds it (area). The formula is pre-registered and frozen — it was validated as written, and re-tuning it after seeing results would forfeit that validation. A finished, honest ranker beats a half-built physics model every time.

**Why screening, never prediction.** A tool whose outputs may reach a county emergency manager with no surrounding context must never be mistakable for a forecast. The known failure mode for this class of tool is a confident-looking wrong answer over someone's home. The system refuses to produce one — relative ranking only, framing embedded in every artifact.

**Why dNBR is the production default even though SBS is the higher-quality input.** BAER SBS is field-validated and closer to the hydrologic cause, but it *only exists for fires a BAER team already assessed* — i.e. exactly the fires this tool is not for. The target population is un-assessed fires, which by definition lack SBS. dNBR (continuous, available anywhere Sentinel-2/Landsat see) is what makes the tool usable on its actual targets. SBS is preferred when a fire happens to have it.

**Why burn sources are never blended.** dNBR and SBS sit on different scales measuring subtly different quantities (reflectance change vs. field-corrected soil response). Averaging them produces a number that means neither, and spatially stitching them reintroduces the same problem across one grid. Exactly one source per run, by precedence.

**Why the burn source is decided once and read everywhere.** `burn_source` must appear in the ranking CSV, the GeoJSON, the map, the app, and the docs. Several places asserting one fact will eventually drift. It is determined a single time at ingest, stamped onto one frozen provenance object, and only ever read downstream — one source of truth cannot disagree with itself.

**Why fail loud, and why a refusal is a feature.** Real inputs from messy, un-assessed fires will violate the clean Montecito template — zero detected outlets, missing burn coverage, odd DEM tiles, or imagery too cloudy to compute a dNBR at all. On such inputs the tool errors explicitly rather than degrading into a plausible-but-unfounded output. Terrain shape alone no longer triggers a refusal — an incised fire routes to the exploratory tier instead — but that tier still fails loud where it genuinely can't proceed (incised + SBS, incised + truth creeks, or a DEM that doesn't cover the fire's drainage network). A confident-looking wrong ranking is the worst outcome this project can produce — worse than a loud error.

**Why there is no orchestration layer.** Stages connect through a shared data contract (`grids.py`) enforced by assertions, not through an adapter/coordinator object. More files do not equal more correctness; the original catchment bug (below) was killed by an unambiguous contract, not by indirection. Stage order is wired only in the thin `run.py` / `src/pipeline.py` seam.

**Why a local app despite "no backend."** The eventual users — a local emergency manager, a small agency without a GIS team — are not developers, and a coordinate-draw-plus-upload UI is what makes the validated pipeline reachable by them. The Streamlit app is a deliberate, scoped exception: a **local, single-user tool over finished artifacts**, not a hosted or live service.

**Why outlets are `(row, col)` index tuples, not `(lon, lat)`.** In the validation build, `pysheds` `catchment()` in coordinate mode *silently returned 0 km²* for valid outlets — deleting the two largest flowed basins before it was caught. Index mode is mandatory, the rule is pinned in the data contract, and delineated areas are checked against a known-area master outlet (the tool aborts if a master catchment collapses below a floor fraction of the analysis area). This is the single bug the architecture is most shaped to prevent.

---

## Architecture & tech stack

Deliberately spare: five pipeline stages, a per-fire config and a data contract, a thin production driver, a coordinate-acquisition layer, an auto-acquire package, and a local app. No orchestration layer, no service tier, no live data. The data is a once-per-fire ranked list, not a refreshing field.

```
Wildfire-Watershed/
├── README.md                  # this file — what & why, how to read outputs, method
├── ARCHITECTURE.md            # module + data-contract spec (stub)
├── environment.yml            # pinned conda environment
├── environment.lock.yml       # captured conda lockfile (exact solve)
│
├── run.py                     # production driver: python run.py --fire <name>
├── acquire.py                 # coordinate acquisition (the network boundary): bbox → staged DEM + assets
├── app.py                     # local Streamlit frontend: bbox + dNBR (upload or generate) → ranked map + CSV, or refusal
│
├── src/                       # the pipeline (pure Python, no network)
│   ├── config.py              # per-fire scalar tunables (contour, thresholds, burn weights, dNBR + subbasin constants)
│   ├── grids.py               # inter-stage data contract: CRS, affine, (row,col) rule, assertions
│   ├── ingest.py              # load inputs; SELECT the one burn source; stamp Provenance
│   ├── hydrology.py           # pysheds: fill → flats → D8 → accumulation
│   ├── delineate.py           # canyon-mouth outlet detection + index-mode catchments; the terrain-span router
│   ├── score.py               # frozen burn×slope×area heuristic + within-fire rank; incised intensity rank
│   ├── subbasins.py           # incised terrain (A39): WhiteboxTools breach-carve + whole-network sub-basins
│   ├── outputs.py             # ranking.csv, basins.geojson + embedded screening / dNBR / incised framing
│   └── pipeline.py            # run_pipeline: wires the stages + the two-tier terrain router (range-front / incised)
│
├── autoacquire/               # coords + fire dates → a real dNBR (deterministic; no LLM in the science path)
│   ├── scene_select.py        # STAC search (Sentinel-2 primary, Landsat 8/9 fallback) + cloud gate + rubric
│   ├── dnbr_create.py         # band math → raw dNBR GeoTIFF + quicklook + provenance
│   └── autoacquire_run.py     # select → human approval → create → reuse the frozen ingest → rank
│
├── data/                      # inputs on disk (gitignored if large)
├── out/                       # generated, namespaced PER FIRE (never flat)
│   └── <fire>/                #   ranking.csv · basins.geojson
│
├── validation/                # the Montecito oracle (gate.py, locked by tests), the dNBR swap finding,
│   │                          #   the incised South Fork concordance check, the pyflwdir cross-check
│   ├── gate.py                # reconstructed Week-0 oracle (AUC 0.9722 / 36 basins / 44.7273 km²)
│   ├── VALIDATION_REPORT.md   # the SBS validation writeup
│   ├── DNBR_VALIDATION_FINDING.md   # the dNBR input-swap finding
│   └── a39_southfork_concordance.py # incised concordance vs USGS sfk2024
├── tests/                     # behavior locks (ranking order + AUC 0.9722, frozen constants, terrain routing)
└── docs/
    ├── ALGORITHMS.md          # the deep, maintained algorithm reference (start here for method detail)
    ├── ALGORITHMS_REVIEW.md   # design-review notes (2026-07-06; predates the incised + auto-acquire builds)
    ├── methodology.md         # stub (Phase-6 handoff writeup pending)
    ├── limitations.md         # stub (see Limitations above)
    └── science_reference.md   # stub (scoring math + guardrail)
```

Pure-Python pipeline, installed via conda (the reliable path for the GDAL/GEOS/PROJ-backed geospatial stack):

`pysheds` (range-front flow modeling) · `whitebox` / WhiteboxTools (incised sub-basins) · `rasterio` (rasters) · `geopandas` + `pyogrio` (vectors) · `shapely` + `pyproj` (geometry/CRS) · `numpy` · `scipy` · `osmnx` (OSM assets via Overpass) · `folium` + `streamlit` + `streamlit-folium` (local app + maps) · `pyflwdir` (flow cross-check, validation only) · `pytest` + `hypothesis` (behavior + property locks)

A few deliberate choices:

- **conda / conda-forge over pip** — the C-extension geospatial stack installs cleanly from conda-forge and painfully via pip wheels. The whole stack is version-pinned and captured to `environment.yml` (with `environment.lock.yml` as the exact solve) so the *validated* result stays reproducible. (WhiteboxTools is the one pip dependency; its engine binary is provisioned at install time so `src/` stays network-free at runtime.)
- **`pyogrio` over `fiona`** — vectorized I/O for the GeoPandas read/write path.
- **`numpy < 2`** — pysheds 0.5 calls the removed `np.in1d`.
- **A clean network seam** — `src/` never touches the network; all fetching lives in `acquire.py` and `autoacquire/`, which stage files to disk for the pure pipeline to consume. The app is a thin UI over that seam.

Python 3.11. Exact pins live in `environment.yml`.

---

## Data sources

| Source | Purpose | Access |
|---|---|---|
| USGS 3DEP DEM (1/3 arc-second, ~10 m) | Terrain; all hydrology and the runtime affine derive from here | The National Map staged COGs on AWS S3 (`prd-tnm.s3.amazonaws.com`), via `/vsicurl/`; fixed EPSG:32611 grid for the Montecito validation |
| Sentinel-2 L2A (B8A, B12) | Burn severity — production default (primary) | Earth Search on AWS (Element84 STAC); available for any fire Sentinel-2 sees |
| Landsat 8/9 Collection-2 L2 (B5, B7) | Burn severity — dNBR fallback when Sentinel-2 is unusable | Microsoft Planetary Computer STAC (SAS-signed) |
| BAER Soil Burn Severity | Burn severity — preferred input when available | Field-validated 4-class; only where a BAER team assessed |
| OpenStreetMap buildings (Overpass) | Downstream assets — defines "downstream of what" | Used to discard catchments draining away from anything to protect |

All free. No licensing cost.

---

## Further reading

- **[`docs/ALGORITHMS.md`](docs/ALGORITHMS.md)** — the maintained, code-reconciled algorithm reference: hydrology, slope, delineation, the dNBR arms, the frozen score, terrain routing, and the parameter table. Start here for method detail.
- **[`validation/`](validation/)** — the SBS validation report, the dNBR input-swap finding, the reconstructed oracle (`gate.py`), and the incised concordance script.

---

## License

Released under the [MIT License](LICENSE).
