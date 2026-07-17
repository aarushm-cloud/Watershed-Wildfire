# Post-Fire Debris-Flow Watershed Screening

An automated screening tool that ranks which burned watersheds most warrant a closer look after a wildfire — using only free, public data — built for fires that fall outside formal USGS or state hazard-assessment coverage.

Post-fire debris-flow hazard assessments are run on a **request-and-select basis**: a fire is assessed only if an official requests it and capacity allows. Well-resourced western states (CA, WA, CO) run their own rapid-response teams; fires outside those systems — smaller, lower-profile, or in thinner-coverage regions — can fall through the gap and receive no formal screening at all. This tool closes that gap with a fast, zero-wait triage screen built entirely on public data, producing a defensible *"assess these watersheds first"* ranking for fires the request-driven system misses.

It runs today on any fire in the contiguous US, either from the command line or through a local point-and-click app, and has been run end-to-end on a real, non-validation fire (see [Validation](#validation)). It is **validated as a ranker on one event so far** — read [Limitations](#limitations) before relying on it.

---

## What it is — and is NOT

This distinction is the spine of the project, and every design choice below exists to hold it.

- **Not an inundation or runout predictor.** It does not claim "this house will be hit," and it never draws confident danger polygons over specific homes. Real lives are downstream; a model painting confident danger zones over individual buildings is dangerous, not merely wrong.
- **Not a competitor to or replacement for USGS.** USGS runs validated likelihood and volume models and is actively building downstream-routing tools. This tool does not out-model them and does not pretend to.
- **Not a flow-physics model.** The runout problem is genuinely hard and is where domain experts spend funded effort. This tool deliberately stops short of it.
- **Not cross-fire comparable.** Scores are ordinal and apply *within a single fire only*. A score from Fire A cannot be ranked against a score from Fire B.

Every output is framed as *"watersheds warranting detailed assessment"* — a within-fire relative ranking, never an absolute prediction.

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

- **`ranking.csv`** — the within-fire ordinal ranking of detected basins, highest screening concern first
- **`basins.geojson`** — the delineated basin polygons
- **a static screening map** (`map.png`) — the ranked basins over the terrain
- **`run_manifest.json`** — the exact config, the burn-source provenance stamp, and a timestamp

Every artifact carries the burn-source provenance and an embedded *"what this is / is not"* note, so the framing travels with the file even when it's forwarded out of context.

**How to read a ranking:**

- Rank **1** is the highest *screening concern within this fire* — a "look here first," not "this will flow."
- It is **not** a probability, a volume, an inundation footprint, or a comparison against any other fire.
- Use it to **prioritize which catchments get a closer look** — a field recon, or a request for a USGS/state assessment — never as a hazard call over specific homes.
- If you cite or forward a ranking, **carry the framing with it.**

**A refusal is a valid result.** When a fire's terrain doesn't fit the method, the burn data is missing, or a catchment collapses (see [Design decisions](#design-decisions)), the tool **produces no ranking and says why** — emitting the raw input layers rather than a fabricated score. For this class of tool, an honest "cannot assess" is a correct outcome; a confident-looking wrong ranking is the failure mode the whole system is built to avoid.

---

## Running it

Two ways in, over the same validated pipeline:

**1 — Command line, for a registered fire:**

```
python run.py --fire <name>      # → writes out/<fire>/
```

`run.py` resolves the fire's inputs, runs the pipeline, and writes a ranking or a legible refusal.

**2 — Local app, for an arbitrary location (non-developers):**

```
streamlit run app.py
```

Draw or enter a bounding box, upload a raw dNBR GeoTIFF, and click run. The app auto-fetches the DEM and downstream assets for that box, scores the fire, and returns a ranked map + CSV — or an honest refusal, rendered as a plain message rather than a stack trace. It is a **local, single-user tool that wraps the CLI** — not a hosted or multi-user service.

**Coverage.** Any bounding box in the contiguous US (UTM zones 10N–19N) is accepted; an out-of-CONUS box refuses at the door before any data is fetched. Coverage is not the same as validation — only the Montecito case is validated so far (see [Limitations](#limitations)).

**Environment.** Python 3.11 in a conda env built from the pinned `environment.yml` (`environment.lock.yml` captures the exact solve). See [Tech stack](#architecture--tech-stack).

---

## How it works

Three public data ingredients, one deliberately simple scoring heuristic, no physics simulation. The pipeline is five pure stages (`ingest → hydrology → delineate → score → outputs`) wired in `src/pipeline.py`, with `config.py` holding per-fire scalars and `grids.py` holding the inter-stage data contract.

**Ingest.** Loads the DEM, one burn raster, and the asset layer. The burn source is selected here, once, by precedence — SBS if it covers the whole analysis area, else dNBR — **never blended**, and stamped onto a single provenance object every downstream stage reads. Missing or partial burn coverage fails loud here rather than producing a ranking on incomplete inputs. `mean_burn` is computed coverage-weighted: cells outside the fire perimeter / no-data are treated as zero severity, so a partially-burned basin is not flattered by averaging only its burned cells.

**Hydrology.** `pysheds` flow modelling on the conditioned DEM: fill pits → fill depressions → resolve flats → D8 flow direction → flow accumulation. Pure terrain processing; it has no concept of outlets or scores.

**Delineation.** Channel cells are those above the flow-accumulation threshold (500 cells). Outlets are channel cells crossing the mountain-front contour (default 150 m) going downhill — where creeks leave the mountains onto developed fans. Each outlet's upslope catchment is delineated **in index mode (`row, col`)**, catchments below 0.1 km² are discarded, and only those draining within 600 m of an asset are kept. Larger catchments claim contested cells first so no cell is counted twice.

**Scoring.** Each retained basin is scored by the frozen, pre-registered heuristic:

```
score(basin) = mean_burn_severity × mean_slope × contributing_area_km²
```

Slope is the dimensionless gradient magnitude `tan θ` (rise/run, central differences on the raw metric DEM). The formula is not a tunable — changing it re-opens validation. Basins are then ranked ordinally within the fire.

**Output.** Writes `ranking.csv`, `basins.geojson`, and a static map, plus a `run_manifest.json` recording the config, the provenance stamp, and a timestamp. Every artifact carries the burn-source provenance and the embedded screening framing.

**Burn inputs — both arms.** The pipeline scores from either burn source. **BAER SBS** is the validated input; **Sentinel-2 dNBR** is the production default for un-assessed fires (they lack SBS by definition — see [Design decisions](#design-decisions)). The dNBR path runs both arms — a headline ranking and a companion cross-check — but is **not yet rank-validated** (all validation evidence to date is on SBS; see [Limitations](#limitations)).

### Parameters

All per-fire tunables live in one auditable place (`config.py`), keyed per fire so editing one fire's values can never silently break another's validated result.

| Parameter | Value | Meaning |
|---|---|---|
| `CONTOUR_M` | 150 m | Mountain-front contour for canyon-mouth outlet detection |
| `ACC_THRESHOLD_CELLS` | 500 | Min flow accumulation for a cell to count as a channel (~0.05 km²) |
| `MIN_BASIN_KM2` | 0.1 | Discard catchments smaller than this |
| `DRAINS_TO_ASSET_M` | 600 m | Keep only catchments draining within this distance of an asset |
| `TRUTH_MATCH_M` | 250 m | Tolerance for matching a basin to a documented flow (validation) |
| burn weights (SBS) | `{1: 0.0, 2: 0.33, 3: 0.67, 4: 1.0}` | Soil-burn-severity class → normalized severity |

SBS class encoding: `1` unburned/very-low · `2` low · `3` moderate · `4` high · `0` masked/developed · `15` no-data.

---

## Validation

Back-tested against the **2017 Thomas Fire / 2018 Montecito** event — one of the best-documented post-fire debris-flow disasters on record.

- **Every documented-flow basin landed in the top tercile** of the ranking (**6 of 6**).
- **The #1-ranked basin (Cold Spring) flowed**, confirmed physically by ~19,000 m³ of debris excavated from its catch basin.
- **Within-fire rank-AUC = 0.9722** on the ordering, across **36 candidate basins** (coverage-weighted burn treatment).

The validation case runs on a fixed canonical grid (EPSG:32611) with SBS as the burn input, and is locked as a reproducible behavior oracle — the test suite asserts the ranking order and AUC (0.9722), the basin count (36), and the master-outlet area (44.73 km²) verbatim, so any drift trips a test.

> **Which numbers are canonical.** These are the **reconstructed-gate** values (AUC 0.9722 / master outlet 44.73 km² / 36 basins). Older docs cite AUC 0.987 / 39.19 km² / 32 basins — those are the *original* Week-0 run on an AOI that could not be recovered; the reconstruction reproduces the same ranked order and headline conclusions but on a slightly larger recovered AOI. Where the two disagree, the reconstructed values above govern.

**First generalization run.** The tool has since been run end-to-end on a real, non-validation fire — the **2026 Putah Fire** (Yolo County, CA), a small contained fire with no existing hazard assessment. From a computed Sentinel-2 dNBR it passed the terrain-applicability gate and produced a ranking of 6 canyon-mouth basins, with the two dNBR arms in agreement. This is a working-pipeline demonstration on new terrain — **not** a second validation event (there was no documented-flow ground truth to score against).

---

## Limitations

Carry these into every conversation and every output. Fuller treatment lives in [`docs/limitations.md`](docs/limitations.md).

- **n = 1 validation event.** The method is validated as a *ranker* on one fire (Thomas → Montecito). Transferability to other ranges, rain regimes, and fire types is **unestablished**. A second documented event in a different region/regime is needed before any transferability claim.
- **dNBR — the production default — is unvalidated for ranking.** All validation evidence is on BAER SBS. Any dNBR ranking (including the Putah run) is provisional until a dNBR input-swap test on the Montecito case closes that gap.
- **No rainfall term, and no regional susceptibility prior.** This is the single most concrete gap surfaced in practitioner outreach (CGS). Regional geology sets a susceptibility prior — the San Gabriels reliably produce debris flows, the Klamaths fewer — and whether a burned basin flowed often depends on whether high-intensity rain actually fell. This tool has neither signal; it is a *within-fire relative triage*, not a regional or rainfall-conditioned hazard estimate. The ranking implicitly assumes ~uniform rainfall across the basins compared — defensible for a spatially compact burn under one storm cell, weaker for a large scar under a moving or banded storm.
- **Scores are within-fire and ordinal only** — never cross-fire, never a probability or volume. (See [What it is NOT](#what-it-is--and-is-not).)
- **The area term is linear and uncapped.** A large, moderately-burned catchment can outrank a small severely-burned one. For reference, the USGS M1 likelihood model was calibrated on basins **0.2–8 km²** (Staley et al., 2016, USGS OFR 2016-1106); this tool's small-basin floor (0.1 km²) sits just under that lower bound, but it weights area with no upper cap, so it over-ranks at the large-area end relative to USGS practice. A dampened area term is a documented, deferred future experiment — not a live knob.
- **The top-1 result rests on one ground-truth call** (Cold Spring). Lead with the robust claims (6/6 in the top tercile, rank-AUC); treat top-1 as a caveated supporting detail.
- **Terrain-applicability is a precondition, not a guarantee.** The method ranks canyons spilling from a steep range onto a flatter plain. On incised-upland terrain with no range-to-plain break, there is no mountain-front to anchor to and the tool **refuses** rather than forcing a ranking. Two sub-cases (coalesced-fan / bajada settings, and coastal near-sea-level cells) are known open boundaries.

---

## Design decisions

**Why a deliberately simple `burn × slope × area` heuristic.** The target is a *screening* triage, not a runout simulation. Each term is a first-order driver of debris-flow concern: fuel for the flow (burn), the energy to move it (slope), and the catchment that feeds it (area). The formula is pre-registered and frozen — it was validated as written, and re-tuning it after seeing results would forfeit that validation. A finished, honest ranker beats a half-built physics model every time.

**Why screening, never prediction.** A tool whose outputs may reach a county emergency manager with no surrounding context must never be mistakable for a forecast. The known failure mode for this class of tool is a confident-looking wrong answer over someone's home. The system refuses to produce one — relative ranking only, framing embedded in every artifact.

**Why dNBR is the production default even though SBS is the higher-quality input.** BAER SBS is field-validated and closer to the hydrologic cause, but it *only exists for fires a BAER team already assessed* — i.e. exactly the fires this tool is not for. The target population is un-assessed fires, which by definition lack SBS. dNBR (continuous, available anywhere Sentinel-2 sees) is what makes the tool usable on its actual targets. SBS is preferred when a fire happens to have it.

**Why burn sources are never blended.** dNBR and SBS sit on different scales measuring subtly different quantities (reflectance change vs. field-corrected soil response). Averaging them produces a number that means neither, and spatially stitching them reintroduces the same problem across one grid. Exactly one source per run, by precedence.

**Why the burn source is decided once and read everywhere.** `burn_source` must appear in the ranking CSV, the GeoJSON, the map legend, the app, and the limitations doc. Five places asserting one fact will eventually drift. It is determined a single time at ingest, stamped onto one frozen provenance object, and only ever read downstream — one source of truth cannot disagree with itself.

**Why fail loud, and why a refusal is a feature.** Real inputs from messy, un-assessed fires will violate the clean Montecito template — no clear mountain-front, zero detected outlets, missing burn coverage, odd DEM tiles, or imagery too cloudy to compute a dNBR at all. On such inputs the tool errors explicitly and refuses to rank, rather than degrading into a plausible-but-unfounded output. Concrete refusals already exercised: an incised-upland fire with no mountain-front (terrain doesn't fit), and an actively-burning fire whose only imagery was smoke-covered (no complete post-fire scar to measure). A confident-looking wrong ranking is the worst outcome this project can produce — worse than an error.

**Why there is no orchestration layer.** Stages connect through a shared data contract (`grids.py`) enforced by assertions, not through an adapter/coordinator object. More files do not equal more correctness; the original catchment bug (below) was killed by an unambiguous contract, not by indirection. Stage order is wired only in the thin `run.py` / `src/pipeline.py` seam.

**Why a local app despite "no backend."** The eventual users — a local emergency manager, a small agency without a GIS team — are not developers, and a coordinate-draw-plus-upload UI is what makes the validated pipeline reachable by them. The Streamlit app is a deliberate, scoped exception: a **local, single-user tool over finished artifacts**, not a hosted or live service. The no-live-service rule still holds; the app only wraps the CLI.

**Why outlets are `(row, col)` index tuples, not `(lon, lat)`.** In the validation build, `pysheds` `catchment()` in coordinate mode *silently returned 0 km²* for valid outlets — deleting the two largest flowed basins before it was caught. Index mode is mandatory, the rule is pinned in the data contract, and delineated areas are checked against a known-area master outlet (the tool aborts if a master catchment collapses below a floor fraction of the analysis area). This is the single bug the architecture is most shaped to prevent.

---

## Architecture & tech stack

Deliberately spare: five pipeline stages, a per-fire config and a data contract, a thin production driver, a coordinate-acquisition layer, and a local app. No orchestration layer, no service tier, no live data. The data is a once-per-fire ranked list, not a refreshing field. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full layer contract and the inter-stage data-contract types.

```
Wildfire-Watershed/
├── README.md                  # this file — what & why, how to read outputs, method
├── CLAUDE.md                  # working manual for the repo (the vault is canonical)
├── DECISIONS.md               # the ADR log — what was decided and why
├── ARCHITECTURE.md            # how the modules connect; the data contract
├── environment.yml            # pinned conda environment
├── environment.lock.yml       # captured conda lockfile (exact solve)
│
├── run.py                     # production driver: python run.py --fire <name>
├── acquire.py                 # coordinate acquisition (the network boundary): bbox → staged DEM + assets
├── app.py                     # local Streamlit frontend: bbox + dNBR → ranked map + CSV, or refusal
│
├── src/                       # the pipeline (pure Python, no network)
│   ├── config.py              # per-fire scalar tunables (contour, thresholds, burn weights)
│   ├── grids.py               # inter-stage data contract: CRS, affine, (row,col) rule, assertions
│   ├── ingest.py              # load inputs; SELECT the one burn source; stamp Provenance
│   ├── hydrology.py           # pysheds: fill → flats → D8 → accumulation
│   ├── delineate.py           # outlet detection + index-mode catchment delineation
│   ├── score.py               # frozen burn×slope×area heuristic + within-fire rank
│   ├── outputs.py             # ranking.csv, basins.geojson, static map + framing
│   └── pipeline.py            # run_pipeline: wires the stages + the terrain-applicability refusal
│
├── data/                      # inputs on disk (gitignored if large)
├── out/                       # generated, namespaced PER FIRE (never flat)
│   └── <fire>/                #   ranking.csv · basins.geojson · map.png · run_manifest.json
│
├── validation/                # the behavior oracle (Thomas/Montecito) + gate.py
├── tests/                     # behavior locks (ranking order + AUC 0.9722)
└── docs/                      # methodology, limitations, algorithms, science reference
    ├── methodology.md
    ├── limitations.md
    ├── science_reference.md   # scoring math + screening guardrail (transcribe-verbatim)
    ├── ALGORITHMS.md
    └── …
```

Pure-Python pipeline, installed via conda (the reliable path for the GDAL/GEOS/PROJ-backed geospatial stack):

`pysheds` (flow modelling) · `rasterio` (rasters) · `geopandas` + `pyogrio` (vectors) · `shapely` (geometry) · `numpy` · `folium` (static map) · `streamlit` (local app) · `requests` (acquisition) · `pytest` (behavior locks)

A few deliberate choices:

- **conda / conda-forge over pip** — the C-extension geospatial stack installs cleanly from conda-forge and painfully via pip wheels. The whole stack is version-pinned and captured to `environment.yml` (with `environment.lock.yml` as the exact solve) so the *validated* result stays reproducible.
- **`pyogrio` over `fiona`** — vectorized I/O for the GeoPandas read/write path.
- **`numpy < 2`** — pysheds 0.5 calls the removed `np.in1d`.
- **A clean network seam** — `src/` never touches the network; all fetching lives in `acquire.py`, which stages files to disk for the pure pipeline to consume. The app is a thin UI over that seam.

Python 3.11. Exact pins live in `environment.yml`.

---

## Data sources

| Source | Purpose | Notes |
|---|---|---|
| USGS 3DEP DEM (10 m) | Terrain; all hydrology and the runtime affine derive from here | `elevation.nationalmap.gov` (validation) / COG + `py3dep` (new fires) |
| Sentinel-2 dNBR | Burn severity — production default | Copernicus Data Space; available for any fire Sentinel-2 sees |
| BAER Soil Burn Severity | Burn severity — preferred input when available | Field-validated 4-class; only where a BAER team assessed |
| OpenStreetMap / Census | Downstream assets — defines "downstream of what" | Used to discard catchments draining away from anything to protect |

All free. No licensing cost.

---

## License

Released under the [MIT License](LICENSE).
