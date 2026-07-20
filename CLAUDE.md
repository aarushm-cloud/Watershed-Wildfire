# Post-Fire Debris-Flow Watershed Screening Tool — Operating Manual for Claude

This file is the **operating manual** for working in this repo. It is deep on purpose, but it
is **not** the source of truth. The **Obsidian vault is canonical** for the science, the
decisions, and the validated method. This file tells you how to build; the vault tells you what
is true and why. When they conflict on anything scientific or architectural, **the vault wins —
stop and reconcile** (see Epistemic Guardrails).

> **Read order at the start of every session:** this file → the vault MOC (current phase) →
> then ask the user what we're working on. See **Session Protocol** at the bottom.

## How to reach the vault (read from disk — do NOT use the obsidian MCP)

Read vault notes with the normal file tools (`Read` / `Glob` / `Grep`) at their absolute paths.
The vault base for this project is:

```
/Users/aarushmadhireddy/aarush-vault/10-projects/Post-Fire Debris-Flow Watershed Screening Tool/
```

Exact note paths and *when to read each* are in the **Vault Map** section near the end. Call the
vault often — it is cheap insurance against silent error, and this project is built to be
checked against it.

---

# ⛔ THE SPINE — Screening, never prediction

This is the ethical core. It is not a feature and it is never relaxed.

- **Outputs are a *within-fire relative ranking* of "which watersheds warrant a closer look
  first."** They are **never** an absolute prediction. Never produce, imply, or visualize "this
  house / this area will be hit." Real lives are downstream of being wrong here.
- The score is an **uncalibrated ordinal ranker**. It is **not** a probability and **not** a
  volume. Never claim it approximates the USGS M1 likelihood model or the Gartner volume model —
  it deliberately omits rainfall and soil inputs those require. (See vault `science_reference` §4.)
- **Not a USGS competitor.** USGS runs validated likelihood/volume models. This tool does not
  out-model them; it triages fires that fall *outside* formal USGS/state assessment.
- **No inundation / runout modeling.** That is a deliberately quarantined research extension
  (decision **C5**), not part of v1 and not on the critical path. Default answer to "should we
  add runout?" is **NO** until v1 ships, is validated, is handed off, and there's a stated reason.
- Every user-facing artifact must carry the screening framing ("watersheds warranting detailed
  assessment," relative, never absolute).

---

# 🧭 EPISTEMIC GUARDRAILS — No assumptions, no silent errors

**Why this section exists:** LLM-written geoscience code tends to *run, look plausible, and be
quietly wrong* — wrong coefficient, wrong exponent, wrong units, dNBR/SBS conflation. The
project owner is a strong engineer but **less able to catch domain-science mistakes**, so the
burden is on you to not make them and to surface uncertainty loudly. Silent error is the single
worst outcome in this repo — worse than being slow, worse than asking an "obvious" question.

## The two-tier uncertainty rule (the core behavior)

**Tier 1 — HALT and verify.** If what you're about to do touches **any** of: the scientific
method, the scoring formula, a frozen parameter, a coefficient, units, a data source, burn-source
selection, the validation gate, or any scientific claim — then:
1. **Stop.** Do not write the code or make the change yet.
2. **State plainly** what you're uncertain about, in terms the owner can sanity-check.
3. **Read the relevant vault note** (Vault Map below) and transcribe values *verbatim* — do not
   reconstruct a formula, coefficient, or parameter from memory.
4. **Ask the owner** before proceeding if any ambiguity remains.
5. **Never** tune, swap, round, or "improve" a frozen value to make a run pass or a test go green.

**Tier 2 — Proceed, but flag.** For pure engineering/plumbing (refactors, file I/O, CLI glue,
test scaffolding, packaging) — keep moving, but **surface every assumption inline** with an
explicit confidence level: `stated | high | medium | speculation`. Never present a guess as fact.

When unsure which tier you're in, **treat it as Tier 1.** The science is the part that bites.

## The science prompting guardrail (from vault `science_reference` §0 — obey verbatim)

> For any geoscience formula, **transcribe the equation and every coefficient verbatim from the
> vault `science_reference.md`** — do not reconstruct from memory.
> State the **units of every variable inline** in the code (comment or docstring).
> Confirm **degrees vs radians**, **percent vs proportion**, and **m vs km** explicitly.
> Before using any computed result, implement a **known-answer test** against a published value;
> if no published test value exists, **say so** rather than asserting correctness.
> Do **not** claim the project score approximates M1 likelihood or Gartner volume — it does not.

Note: in `science_reference`, the M1 likelihood coefficients (§2) are **✓ verified**, but the
Gartner volume equation (§3) is **⚠ UNVERIFIED** — do not let any code rely on it until it's
checked digit-by-digit against the primary source.

## Fail loud, never silent (decision A8 + `FAILURE_MODES`)

Missing or unreachable inputs, empty results, a 0 km² basin, a CRS/affine mismatch, an
unmatched truth creek, a burn raster that doesn't cover the AOI — these **raise and abort with a
clear message**. Never emit a confident ranking from broken or partial inputs. A loud crash is a
correct outcome; a plausible-looking wrong CSV is the failure mode we are designing against.

**Known-answer tests are the real defense.** A reference fixes recall errors (wrong
coefficient); only a check against a known output catches *application* errors (right equation,
wrong-unit input). Precedent: the pysheds `catchment()` bug silently returned 0 km² and deleted
the two largest flowed basins — caught by **one assertion**: a known ~39 km² master outlet must
return ~39, not 0. Keep that assertion; add more like it.

## No fabrication

Never invent data, parameters, coefficients, citations, basin counts, or results. If a value
isn't in the vault or the data, the answer is "unknown — let me check / let me ask," not a
plausible number. Every scientific claim cites its vault note (and that note's source) inline.

## Known bugs to carry as already-fixed (`FAILURE_MODES` FM-1 / FM-2)

- **pysheds `catchment()` runs in INDEX mode** — `xytype='index'`, with `x=col, y=row`.
  Coordinate mode silently returns 0 km² for valid outlets. Verify against the 39.19 km² master
  outlet. This is NOT-A-BUG to "fix" back; it's a deliberate fix already learned.
- If the pinned NumPy needs it, shim pysheds' removed `np.in1d` → `np.isin`.

---

# Project Overview

An automated screening tool that ranks which burned watersheds most warrant a closer look after
a wildfire, **using only free public data**, aimed at fires that don't receive a formal USGS or
state hazard assessment. The honest niche: USGS assesses on a request-and-select basis; well-
resourced states (CA, WA, CO) run rapid-response teams, but fires outside that coverage fall
through the gap. This is a **fast, zero-wait triage screen** so a jurisdiction that wasn't
assessed can still get a defensible "look at these watersheds first" ranking.

**What it is NOT:** a precise inundation predictor, a USGS competitor, or a flow-physics
breakthrough. (Full framing: vault `Initial Context/Initial README.md`.)

This is the owner's **second climate-tech proof point** (after the DFW Air Quality dashboard) —
the restraint (no over-building) is itself the portfolio signal.

---

# The Method (FROZEN)

```
score(basin) = mean_burn_severity(basin) × mean_slope(basin) × contributing_area_km²
```
then a **within-fire ordinal ranking** of basins by score (NOT cross-fire comparable).

What each term proxies (vault `science_reference` §1): `mean_burn_severity` → runoff generation /
infiltration collapse (0–1 after the burn-weight mapping); `mean_slope` → driving stress /
transport energy; `contributing_area_km²` → water + sediment volume available.

**Burn-weight mapping** (BAER Soil Burn Severity class → 0–1): `1→0.0, 2→0.33, 3→0.67, 4→1.0`
(unburned / outside perimeter → 0). This even spacing is a *modeling choice*, not a measurement.

> 🔒 **Canonical-locked, repeated on purpose.** This map (and the AUC `0.987`, the `39.19 km²`
> outlet, and the config scalars) is intentionally restated verbatim in several sections — frozen
> values belong at each point of use because mid-document context is recalled less reliably
> ("lost in the middle"; Liu et al. TACL 2024 + Anthropic "context rot"). Drift risk is ~zero
> *because they are frozen*. The one cost: **if a value is ever unfrozen (e.g. C1 area-dampening),
> every copy must change together** — grep the literal and update all occurrences in lockstep.

**FROZEN means frozen.** The formula was pre-registered and validated (rank-AUC 0.987). Changing
it re-opens validation — that's a Tier-1 HALT. The known `× area` mis-ranking (a large
moderately-burned basin can outrank a small severely-burned flowed one — the Oak Creek vs Toro
Canyon case) is a **documented, deferred** v2 experiment (decision **C1**: `area^0.5` / `ln(area)`
/ normalization). **Never tune it before the validated baseline ships** — changing the scoring
after seeing results is how you fool yourself.

---

# Architecture (seven modules, no orchestrator)

```
ingest → hydrology → delineate → score → outputs
            (+ config.py = per-fire scalars,  grids.py = inter-stage data contract)
```

Two deliberate consequences (decision **A7**): **no orchestrator** (stages connect through the
`grids.py` data contract enforced by assertions, not a coordinator object) and **no backend**
(the Phase-5 viewer is a static read over output artifacts). More files ≠ more correctness.

| Module | Responsibility | Must NOT do |
|---|---|---|
| `config.py` | Per-fire scalar tunables (contour elevation, accumulation threshold, min basin area, drains-to-asset distance, truth-match tolerance, burn-weight map). Never edited in place. | Hold the affine transform or runtime-derived grid meaning (that's `grids.py`). |
| `grids.py` | The inter-stage data contract: CRS, affine convention, `(row, col)` outlet rule, dtype/nodata, boundary-validation helpers (anti-0km² guard, sane-area, alignment). | Become an orchestrator; route data; hold business logic. |
| `ingest.py` | Front door: load DEM/burn/assets; **select the one burn source by precedence**; emit a single `Provenance` stamp; fail loud if burn missing. | Let any later stage re-decide the burn source; blend sources; silently proceed on missing inputs. |
| `hydrology.py` | pysheds: fill pits → fill depressions → resolve flats → D8 flow direction → flow accumulation. | Detect outlets or score; re-decide burn source; emit anything coordinate-space. |
| `delineate.py` | Canyon-mouth outlet detection + upslope catchments in **INDEX mode**; discard tiny basins; keep asset-draining ones; larger basins claim cells first. | Re-run hydrology; accept outlets that aren't `(row, col)` index tuples. |
| `score.py` | Apply the frozen `burn × slope × area` heuristic; produce within-fire ordinal ranking. | Change the formula; imply cross-fire comparability. |
| `outputs.py` | Write `ranking.csv`, `basins.geojson`, static map — each stamped with burn-source provenance + screening framing. | Recompute the burn source; emit a confident ranking from unprocessable inputs. |

`run.py` is the only place the stage order is wired: `python run.py --fire <name>` → writes
`out/<fire>/`. This does **not** violate "no orchestrator" (A7): `run.py` wires the *call order*
only — it holds no inter-stage state and makes no decisions; stages still communicate solely
through the `grids.py` data contract, never through a coordinator object. Authoritative contract shapes (`Provenance` / `Grids` / `Outlet` / `Basin`) live
in the vault `ARCHITECTURE.md` (the repo `DATA_CONTRACTS` note is a deferred stub until P1 — do
not treat it as a second source of truth, per A4).

---

# Frozen Invariants (always-true rules — violating any is a Tier-1 HALT)

> These are **standing invariants, not a to-do list.** Each `⛔` is permanently in force; there is
> nothing here to "complete." (Written as rules, not `- [ ]` checkboxes, so they are never misread
> as unmet tasks.)

- ⛔ Screening, never prediction (the Spine).
- ⛔ Scoring formula `burn × slope × area` is frozen.
- ⛔ **One burn source per run, never blended** — SBS if it covers the whole AOI, else dNBR for
      the whole AOI (decisions A2/A3). Two sources measure subtly different quantities at
      different scales; averaging produces a number meaning neither.
- ⛔ Burn source is decided **once**, in `ingest.py`, and flows from a single `Provenance`
      object read everywhere (A4/A15).
- ⛔ **D0 — default NO.** Don't add features, services, abstractions, or "nice to haves" without
      a concrete, validated need. The dominant failure mode here is over-building, not under-building.

---

# Build Phases (P0 → P6)

The vault `DECISIONS.md` is canonical for phase status; this table is the working summary.

| Phase | Lands | Gate |
|---|---|---|
| **P0 — Setup & grounding** | conda env + pinned `environment.yml`, git repo, scaffold, OFR 2023-1025 read | imports succeed; recovered report committed to repo; Montecito input sources reachable |
| **P0.5 — Reconstruct `validation/gate.py`** | single-script gate rebuilt from the report | reproduces the report's documented results (see Validation Oracle) |
| **P1 — Re-implement as modules** | the seven modules | modules reproduce the gate's ranking order exactly + AUC within 1e-3 of 0.987 |
| **P2 — dNBR input path + swap test** | dNBR ingestion/normalization | dNBR-input ranking holds vs SBS — or you learn cheaply it doesn't (either is a finding) |
| **P3 — Generalization** | fail-loud + boundary assertions on a fresh fire | runs on a non-Montecito un-assessed fire → sane ranking or loud failure, never silent bad output |
| **P4 — Second validation event** | different region/regime | documented where the method holds and breaks |
| **P5 — UI / viewer (A9 floor)** | bare-bones viewer over `outputs.py` artifacts | a non-developer reads the ranking correctly, including the "what this is not" framing |
| **P6 — Handoff & demo** | `methodology.md`, `limitations.md`, demo | someone outside the project can run it |

> **Phase status — single source of truth is vault `DECISIONS.md`. Read it; do not trust this
> line.** This is a rot-prone pointer kept here only as a starting prior, not an authority.
> Last-known: P0 / P0.5 — repo is a scaffold of stubs; next real work is reconstructing the
> validation gate *(as of 2026-06-06)*. If this disagrees with `DECISIONS.md`, `DECISIONS.md` wins.

**The real definition of done is outreach milestone O4** — a named practitioner says "this would
be useful." P6 enables it; O4 *is* success. (Outreach track: vault `DECISIONS.md` §P.)

---

# The Validation Oracle (P0.5)

`validation/gate.py` (the Week-0 gate, to be reconstructed) and `validation/VALIDATION_REPORT.md`
(the recovered Week-0 report) together are the **behavior oracle** — the thing P0.5's rebuild and
P1's module tests assert against. Validation case: 2017 Thomas Fire → 9 Jan 2018 Montecito disaster.

**🚫 Neither is ever edited to make a run pass (decision A16).** They are read-only behavior
anchors. If a run doesn't match, the *run* is wrong, not the oracle.

**Headline numbers the rebuild must reproduce:**
- 32 candidate basins; 6 documented-flow basins.
- **6/6 flowed basins in the top tercile (100%)**; **#1-ranked basin flowed** (Cold Spring).
- **rank-AUC = 0.987** (±1e-3).
- master-outlet sanity check: **0.00 → 39.19 km²** (the index-mode `catchment()` fix).

**Exact reconstruction parameters (A16 — must match, or the result isn't comparable to AUC 0.987):**
- **DEM:** USGS 3DEP 10 m via the `elevation.nationalmap.gov` ImageServer `exportImage`
  endpoint, output **EPSG:32611**, ~1413 × 1295 px @ 10.0 m. *(Use this exact path for
  reconstruction fidelity — not the COG / `py3dep` path used for new fires.)*
- **Burn:** Thomas Fire **BAER SBS**, raster-locked to **objectid 3248** (so no later fire
  contaminates it). 4-class.
- **Burn-weight map:** `1→0.0, 2→0.33, 3→0.67, 4→1.0`.
- **Assets:** OSM buildings via Overpass (~12,066 points on the Montecito fans).
- **Truth target:** the watershed-level substitute the report used (Kean et al. 2019's flowed
  creeks + OSM channels + debris-basin excavation volumes) — **NOT** the canonical USGS
  inundation polygons (those never downloaded; swapping them is deferred, C7).
- **Config scalars:** 150 m mountain-front contour; accumulation threshold **500 cells (~0.05 km²)**;
  minimum basin area **0.1 km²**; drains-to-asset distance **600 m**; truth-match tolerance **250 m**.

**⚠️ Precondition gaps to resolve before P0.5 (verified 2026-06-06):**
1. `validation/VALIDATION_REPORT.md` is **not actually in the repo** — A16 says it was committed
   read-only, but on disk it survives only in the vault. **First task: materialize it into the
   repo** (copy from vault `VALIDATION_REPORT.md`) so the oracle can't vanish (A4: "an oracle
   that can vanish is not an oracle").
2. The Montecito inputs (DEM, SBS, assets, creeks) were deleted and must be **re-acquired** via
   the exact sources above. The report flags this as fragile (2 GB ScienceBase stalls, IP rate-
   limiting). If a source is unreachable or returns materially different data, that's a **finding
   to record**, not something to paper over.

Full method, the per-basin ranking table, and the Cold Spring classification call: vault
`VALIDATION_REPORT.md` (and `Initial Context/Wildfire_Watershed_Screening_Validation_Report.md`).

---

# Data Sources (canonical = vault)

Inputs: **USGS 3DEP DEM** (10 m terrain), **burn severity** (BAER SBS, or Sentinel-2 dNBR as the
*production default for new un-assessed fires* — but dNBR is **UNVALIDATED** for ranking until
Phase 2), and **OpenStreetMap** (buildings/assets + channels via Overpass).

For live endpoints, formats, bands, auth, and ingestion gotchas, **read the vault
`DATA_SOURCES.md`** — that doc rots fastest (endpoints move, portals get redesigned), so it has a
single home and carries last-verified dates. Do not duplicate its endpoints here. Reconstruction
exception: P0.5 uses the ImageServer DEM path for fidelity; new fires use COG / `py3dep`.

**dNBR ≠ BAER SBS** — dNBR is a continuous spectral-change index; SBS is a field-validated 4-class
*soil* product. They are not interchangeable (vault `science_reference` §4).

---

# Key Decisions (summary — full log in the vault)

The full lightweight-ADR log (Context → Decision → Reasoning → Status), **A1–A16** plus deferred
**C1–C7** and the phase/outreach tracks, lives in vault `DECISIONS.md`. **Read it before any
architectural or method change.** The load-bearing ones:

- **D0 — default NO** to new scope until a concrete validated need (anti-over-building).
- **A2/A3 — one burn source, never blended.** dNBR is the production default; SBS is what's validated.
- **A4/A15 — single ingestion owner.** Burn-source selection lives only in `ingest.py`, stamped
  to one `Provenance`. Reason `ingest.py` is the 7th module.
- **A7 — no orchestrator.** Stages connect through the `grids.py` contract.
- **A8 — fail loud.**
- **A16 — reconstruct the deleted gate** against the recovered report as behavior oracle (P0.5).
- **C1 — area-term dampening**, **C5 — inundation/runout**, **C7 — USGS-polygon truth**: all
  deferred; default NO until their explicit triggers.

---

# Dev Environment, Setup & Run

- **Python 3.11** in a conda env named `wildfire-watershed`.
- **`numpy < 2`** (pysheds 0.5 calls the removed `np.in1d`).
- Stack: `pysheds`, `rasterio`, `geopandas`, `shapely`, `fiona`, `pandas`, `requests`.
- **`environment.yml` is pinned; `environment.lock.yml` is the captured conda lockfile** (A13/C3),
  landed 2026-06-15 once P0.5 reproduced the validated result. When you add a dependency, update both.
- **Run contract:** `python run.py --fire <name>` → writes deliverables to `out/<fire>/`.

**P0 preconditions met (verified 2026-07-06):** the repo is **git-initialized** (first commit
2026-06-06, branch `main`); `environment.yml` **and** the captured `environment.lock.yml` are tracked
(lockfile landed 2026-06-15, A13/C3); and the recovered `VALIDATION_REPORT.md` **is committed** at
`validation/VALIDATION_REPORT.md` (2026-06-12) as the read-only oracle (see Validation Oracle).

---

# Vault Map (read from disk; base path above)

| Read this... | ...when | Path (under the vault base) |
|---|---|---|
| `Post-Fire Watershed Tool MOC.md` | session start — orientation + current status | `Post-Fire Watershed Tool MOC.md` |
| `Initial Context/Initial README.md` | for the full "what it is / isn't", niche, roadmap, outreach | `Initial Context/Initial README.md` |
| `ARCHITECTURE.md` | **before any architecture/contract change** — authoritative module + data-contract spec | `ARCHITECTURE.md` |
| `DECISIONS.md` | **before any method/architecture change** — full A1–A16, C1–C7, phases | `DECISIONS.md` |
| `science_reference.md` | **before writing any geoscience formula** — transcribe verbatim | `Science Reference/science_reference.md` |
| `USGS Landslide Hazards - Scientific Background.md` | for the underlying USGS models (M1, Gartner) | `Science Reference/USGS Landslide Hazards - Scientific Background.md` |
| `VALIDATION_REPORT.md` | for the oracle — per-basin ranking, AUC, the Cold Spring call | `VALIDATION_REPORT.md` |
| `DATA_SOURCES.md` | **before fetching any input** — endpoints, auth, gotchas (rots fastest) | `DATA_SOURCES.md` |
| `FAILURE_MODES.md` | when something looks like a bug — check it isn't a deliberate design choice | `FAILURE_MODES.md` |

When in doubt, read the vault before acting. That habit is the point of this file.

---

# Session Protocol (start of every session)

1. Read this file.
2. Read the vault MOC and confirm the **current phase** (don't trust a possibly-stale "Current
   focus" line above — verify against the vault).
3. State which **guardrail tier** today's work falls under (science = HALT; plumbing = proceed-
   and-flag), so the owner knows what to expect.
4. Ask the owner what we're working on. **Don't refactor prior phases unprompted.**

# Conventions

- Small, readable, well-commented functions; explicit over clever. The owner is a strong engineer
  but **newer to debris-flow / geospatial science** — explain the science reasoning in plain terms
  and cite the vault, so they can sanity-check you.
- State units inline for every geoscience variable (degrees vs radians, % vs proportion, m vs km).
- Keep the screening framing in all user-facing output.
- Update `environment.yml` when adding a dependency.
- Match the existing module docstring style already in `src/`.
