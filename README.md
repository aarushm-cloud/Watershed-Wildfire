# Post-Fire Debris-Flow Watershed Screening

An automated screening tool that ranks which burned watersheds most warrant a closer look after a wildfire — using only free, public data — built for fires that fall outside formal USGS or state hazard-assessment coverage.

Post-fire debris-flow hazard assessments are run on a **request-and-select basis**: a fire is assessed only if an official requests it and capacity allows. Well-resourced western states (CA, WA, CO) run their own rapid-response teams; fires outside those systems — smaller, lower-profile, or in thinner-coverage regions — can fall through the gap and receive no formal screening at all. This tool closes that gap with a fast, zero-wait triage screen built entirely on public data, producing a defensible *"assess these watersheds first"* ranking for fires the request-driven system misses.

> **Status.** The ranking *method* is validated against a documented debris-flow event (see [Validation](#validation)). The pipeline is being reconstructed from the validation script into a reproducible, modular form and is **not yet runnable end-to-end**. This README documents the system as designed and validated; installation and usage will be finalized when the pipeline lands.

---

## What it is NOT

This distinction is the spine of the project, and every architectural choice below exists to hold it.

- **Not an inundation or runout predictor.** It does not claim "this house will be hit," and it never draws confident danger polygons over specific homes. Real lives are downstream; a model painting confident danger zones over individual buildings is dangerous, not merely wrong.
- **Not a competitor to or replacement for USGS.** USGS runs validated likelihood and volume models and is actively building downstream-routing tools. This tool does not out-model them and does not pretend to.
- **Not a flow-physics model.** The runout problem is genuinely hard and is where domain experts spend funded effort. This tool deliberately stops short of it.
- **Not cross-fire comparable.** Scores are ordinal and apply *within a single fire only*. A score from Fire A cannot be ranked against a score from Fire B.

Every output is framed as *"watersheds warranting detailed assessment"* — a within-fire relative ranking, never an absolute prediction.

---

## What it does

- Ingests a USGS 3DEP 10 m DEM, one burn-severity raster (Sentinel-2 dNBR or BAER Soil Burn Severity), and an OSM/Census downstream-asset layer for a target fire
- Runs a `pysheds` hydrology stack on the DEM: fill pits → fill depressions → resolve flats → D8 flow direction → flow accumulation
- Detects canyon-mouth outlets — channel cells crossing the mountain-front contour (default 150 m) going downhill, where creeks leave the mountains onto developed fans
- Delineates each outlet's upslope catchment **in index space**, discards catchments below 0.1 km², and keeps only those draining within 600 m of a downstream asset; larger catchments claim cells first to prevent double-counting
- Scores each basin by `mean_burn × mean_slope × contributing_area_km²` and produces a within-fire ordinal ranking
- Emits `ranking.csv`, `basins.geojson`, and a static screening map — each stamped with burn-source provenance and a "what this is / is not" note so framing travels with the artifact

---

## Validation

Back-tested against the **2017 Thomas Fire / 2018 Montecito** event — one of the best-documented post-fire debris-flow disasters on record.

- **Every documented-flow basin landed in the top tercile** of the ranking.
- **The #1-ranked basin (Cold Spring) flowed**, confirmed physically by ~19,000 m³ of debris excavated from its catch basin.
- **Within-fire rank-AUC ≈ 0.97** on the ordering (coverage-weighted burn treatment).

The validation case runs on a fixed canonical grid (EPSG:32611, 1413 × 1295 cells @ 10 m) with SBS as the burn input, and is locked as a reproducible behavior oracle. A master-outlet area check (sane band ±15% of the known 39.19 km² catchment) guards against the silent zero-area delineation failure described under [Design decisions](#design-decisions).

**Caveats carried forward, not buried:**

- **n = 1 event.** The method is validated as a *ranker* on *one fire*. Transferability to other ranges, rain regimes, and fire types is unestablished. A second validation event in a different region/regime is planned before any transferability claim.
- **dNBR is the production default but is validated only on SBS.** All validation evidence above is on BAER SBS. A dNBR input-swap test on the same Montecito case is the gate that validates the default path (see [Roadmap](#roadmap)).
- **Scores are within-fire and ordinal only** — see [What it is NOT](#what-it-is-not).

---

## Method

Three public data ingredients, one deliberately simple scoring heuristic, no physics simulation.

**Ingest.** Loads the DEM, the one burn raster, and the asset layer. The burn source is selected here, once, by precedence — SBS if it covers the whole analysis area, else dNBR — **never blended**, and stamped onto a single provenance object every downstream stage reads. Missing or partial burn coverage fails loud here rather than producing a ranking on incomplete inputs. `mean_burn` is computed coverage-weighted: cells outside the fire perimeter / no-data are treated as zero severity, so a partially-burned basin is not flattered by averaging only its burned cells.

**Hydrology.** `pysheds` flow modelling on the conditioned DEM: fill pits → fill depressions → resolve flats → D8 flow direction → flow accumulation. Pure terrain processing; it has no concept of outlets or scores.

**Delineation.** Channel cells are those above the flow-accumulation threshold (500 cells). Outlets are channel cells crossing the mountain-front contour going downhill. Each outlet's upslope catchment is delineated **in index mode (`row, col`)**, catchments below 0.1 km² are discarded, and only those draining within 600 m of an asset are kept. Larger catchments claim contested cells first so no cell is counted twice.

**Scoring.** Each retained basin is scored by the frozen, pre-registered heuristic:

```
score(basin) = mean_burn_severity × mean_slope × contributing_area_km²
```

Slope is the dimensionless gradient magnitude `tan θ` (rise/run, central differences on the raw DEM). The formula is not a tunable — changing it re-opens validation. Basins are then ranked ordinally within the fire.

**Output.** Writes `ranking.csv`, `basins.geojson`, and a static map to `out/<fire>/`, plus a `run_manifest.json` recording the config used, the provenance stamp, and a timestamp. Every artifact carries the burn-source provenance and the embedded screening framing.

### Parameters

All per-fire tunables live in one auditable place (`config.py`), keyed per fire so editing one fire's values can never silently break another's validated result.

| Parameter | Value | Meaning |
|---|---|---|
| `CONTOUR_M` | 150 m | Mountain-front contour for canyon-mouth outlet detection |
| `ACC_THRESHOLD_CELLS` | 500 | Min flow accumulation for a cell to count as a channel |
| `MIN_BASIN_KM2` | 0.1 | Discard catchments smaller than this |
| `DRAINS_TO_ASSET_M` | 600 m | Keep only catchments draining within this distance of an asset |
| `TRUTH_MATCH_M` | 250 m | Tolerance for matching a basin to a documented flow (validation) |
| burn weights (SBS) | `{1: 0.0, 2: 0.33, 3: 0.67, 4: 1.0}` | Soil-burn-severity class → normalized severity |

SBS class encoding: `1` unburned/very-low · `2` low · `3` moderate · `4` high · `0` masked/developed · `15` no-data.

---

## Design decisions

**Why a deliberately simple `burn × slope × area` heuristic.** The target is a *screening* triage, not a runout simulation. Each term is a first-order driver of debris-flow concern: fuel for the flow (burn), the energy to move it (slope), and the catchment that feeds it (area). The formula is pre-registered and frozen — it was validated as written, and re-tuning it after seeing results would forfeit that validation. A finished, honest ranker beats a half-built physics model every time.

**Why screening, never prediction.** A tool whose outputs may reach a county emergency manager with no surrounding context must never be mistakable for a forecast. The known failure mode for this class of tool is a confident-looking wrong answer over someone's home. The system refuses to produce one — relative ranking only, framing embedded in every artifact.

**Why dNBR is the production default even though SBS is the higher-quality input.** BAER SBS is field-validated and closer to the hydrologic cause, but it *only exists for fires a BAER team already assessed* — i.e. exactly the fires this tool is not for. The target population is un-assessed fires, which by definition lack SBS. dNBR (continuous, available anywhere Sentinel-2 sees) is what makes the tool usable on its actual targets. SBS is preferred when a fire happens to have it.

**Why burn sources are never blended.** dNBR and SBS sit on different scales measuring subtly different quantities (reflectance change vs. field-corrected soil response). Averaging them produces a number that means neither, and spatially stitching them reintroduces the same problem across one grid. Exactly one source per run, by precedence.

**Why the burn source is decided once and read everywhere.** `burn_source` must appear in the ranking CSV, the GeoJSON, the map legend, the viewer, and the limitations doc. Five places asserting one fact will eventually drift. It is determined a single time at ingest, stamped onto one frozen provenance object, and only ever read downstream — one source of truth cannot disagree with itself.

**Why there is no orchestration layer.** Stages connect through a shared data contract (`grids.py`) enforced by assertions, not through an adapter/coordinator object. More files do not equal more correctness; the original catchment bug (below) was killed by an unambiguous contract, not by indirection. The only place stage order is hardcoded is a thin top-level `run.py`.

**Why fail loud over silent degradation.** Real inputs from messy, un-assessed fires will violate the clean Montecito template — no clear mountain-front, zero detected outlets, missing burn coverage, odd DEM tiles. On such inputs the tool errors explicitly and refuses to rank, rather than degrading into a plausible-but-unfounded output. A confident-looking wrong ranking is the worst outcome this project can produce — worse than an error.

**Why outlets are `(row, col)` index tuples, not `(lon, lat)`.** In the validation build, `pysheds` `catchment()` in coordinate mode *silently returned 0 km²* for valid outlets — deleting the two largest flowed basins before it was caught. Index mode is mandatory, the rule is pinned in the data contract, and delineated areas are checked against a known-area master outlet. This is the single bug the architecture is most shaped to prevent.

---

## Data sources

| Source | Purpose | Notes |
|---|---|---|
| USGS 3DEP DEM (10 m) | Terrain; all hydrology and the runtime affine derive from here | `exportImage` from `elevation.nationalmap.gov` |
| Sentinel-2 dNBR | Burn severity — production default | Copernicus Data Space; available for any fire Sentinel-2 sees |
| BAER Soil Burn Severity | Burn severity — preferred input when available | Field-validated 4-class; only where a BAER team assessed |
| OpenStreetMap / Census | Downstream assets — defines "downstream of what" | Used to discard catchments draining away from anything to protect |

All free. No licensing cost.

---

## Architecture

Deliberately spare: seven pipeline modules, no orchestration layer, no service tier, no live data. The data is a once-per-fire ranked list, not a refreshing field, and the architecture refuses to carry weight the problem does not require. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full layer contract and the inter-stage data-contract types.

```
wildfire-watershed-screen/
├── README.md                  # this file — what & why, method, validation
├── DECISIONS.md               # the ADR log — what was decided and why (D0 governs)
├── ARCHITECTURE.md            # how the modules connect; the data contract
├── environment.yml            # pinned, lockfile-captured conda environment
├── run.py                     # the one entrypoint: python run.py --fire <name>
│
├── data/                      # inputs on disk (gitignored if large)
│   ├── dem/                   # USGS 3DEP DEM tiles (10 m)
│   ├── burn/                  # ONE burn raster per run: dNBR or BAER SBS
│   └── assets/                # OSM buildings / Census downstream-asset layer
│
├── src/                       # the seven-module pipeline (pure Python, no network)
│   ├── config.py              # static per-fire scalar tunables (contour, thresholds)
│   ├── grids.py               # inter-stage data contract: CRS, affine, (row,col)
│   │                          #   rule, nodata, boundary-validation assertions
│   ├── ingest.py              # load inputs; SELECT burn source; stamp Provenance
│   ├── hydrology.py           # pysheds: fill → flats → D8 → accumulation
│   ├── delineate.py           # outlet detection + index-mode catchment delineation
│   ├── score.py               # frozen burn×slope×area heuristic + within-fire rank
│   └── outputs.py             # ranking.csv, basins.geojson, static map + framing
│
├── out/                       # generated, namespaced PER FIRE (never flat)
│   └── <fire>/                #   ranking.csv · basins.geojson · map.png
│       └── run_manifest.json  #   config used + Provenance + timestamp
│
├── validation/                # the behavior oracle + future second-event validation
│   ├── gate.py                # the canonical validation script (source of truth)
│   └── data/                  # Thomas/Montecito inputs + documented-flow ground truth
│
├── tests/                     # behavior locks (esp. Week-0 ranking order + AUC)
│
└── docs/                      # methodology + user-facing docs
    ├── methodology.md
    ├── limitations.md
    └── science_reference.md   # scoring math + screening guardrail
```

A fire runs through a single entrypoint — `python run.py --fire <name>` — which wires the seven stages in order and writes to `out/<fire>/`. It is the only place stage order is hardcoded, kept as a thin script rather than a module so the no-orchestrator rule holds.

---

## Roadmap

The method is validated; the work is turning the validation script into a clean, reproducible, non-developer-runnable pipeline.

- [x] **Method validated** on Thomas / Montecito (top-tercile recovery, Cold Spring #1)
- [ ] **Reconstruct the validation gate** against the frozen behavior oracle *(in progress)*
- [ ] **Refactor into the seven-module pipeline** with a behavior-lock test reproducing the validated ranking order
- [ ] **dNBR input-path validation** — re-run the Montecito case with dNBR instead of SBS; confirm the ranking survives the input swap, or learn cheaply that it doesn't
- [ ] **Generalization** — run on a fresh un-assessed fire; fail loud on non-Montecito-shaped inputs rather than degrading silently
- [ ] **Second validation event** — a different region/regime, to probe transferability
- [ ] **Bare-bones viewer + handoff docs** — static map + per-basin score breakdown, runnable by a non-developer
- [ ] **Inundation / runout layer** — *quarantined research extension, explicitly not on the critical path.* Never confident polygons over homes. Default answer is no until there is a validated reason to build it.

---

## Tech stack

Pure-Python pipeline, installed via conda (the reliable path for the GDAL/GEOS/PROJ-backed geospatial stack):

`pysheds` (flow modelling) · `rasterio` (rasters) · `geopandas` + `pyogrio` (vectors) · `shapely` (geometry) · `numpy` · `folium` (static map) · `pytest` (behavior locks)

A few deliberate choices:

- **conda / conda-forge over pip** — the C-extension geospatial stack installs cleanly from conda-forge and painfully via pip wheels. The whole stack is version-pinned and captured to `environment.yml` so the *validated* result stays reproducible.
- **`pyogrio` over `fiona`** — vectorized I/O for the GeoPandas read/write path.
- **No web framework, no backend** — the output is a once-per-fire ranked list. The eventual viewer is a static map over finished artifacts, never a live service.

Python 3.11. Exact pins live in `environment.yml`.

---

## Using the outputs

Outputs are a **within-fire relative ranking of watersheds to assess first** — not a prediction of debris-flow occurrence, location, or extent. If you reference a ranking from this tool, please carry that framing with it.

## License

_To be added._
