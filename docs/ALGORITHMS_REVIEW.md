# ALGORITHMS.md — Review Notes & Action Items

Working notes from a section-by-section review of [docs/ALGORITHMS.md](./ALGORITHMS.md)
(dated **2026-07-06**) against the live tree, conducted **2026-07-11**. Captures decisions,
tasks, and doc-vs-code drift for **final review later**. This is a staging doc, **not
canonical** — the vault remains the source of truth; edits to `ALGORITHMS.md` and the code
flow *out* of here once reviewed.

Item IDs: **B#** = bigger / higher-priority, **T#** = task / smaller refactor,
**Q#** = open question, **S#** = staleness fix owed to `ALGORITHMS.md`.

---

## Staleness vs the live tree (doc dated 2026-07-06)

Commits after the doc that change what it claims:

- **S1 — dNBR arm is WIRED** (`a523dde`, A34). Section 7 ("built, tested, **not yet wired**")
  and Section 6's claim that `ingest_burn` fails loud on any dNBR selection (A29) are **stale**.
  `run.py` now has a full dNBR both-arms output path ([run.py:84-90](../run.py#L84-L90)).
- **S2 — slope conditionality patch** (`ebc1e06`, A33). Section 4's coastal caveat ("deliberately
  not fixed... no coastal-slope guarantee") is **stale**: `outputs.py` confirms a slope-coverage
  layer now exists (**F4** — `slope_coverage_frac` + `low_slope_coverage`,
  [outputs.py:215-216](../src/outputs.py#L215-L216)). Note asymmetry: F4 fields ride the **dNBR**
  writer only, not the SBS `write_outputs`. Detail the slope-code change + the asymmetry in Section 4.
- **S3 — pyflwdir cross-check added** (`bf998ca`, **CF-11**). Directly answers the Flow-Modeling
  "Heuristic vs Backed" comment: pysheds' contributing areas are now independently reproduced by
  pyflwdir (Deltares) — **Pearson 0.9994** per-outlet, large (≥1 km²) basins within ~3%
  ([validation/cf11_pyflwdir_crosscheck.py](../validation/cf11_pyflwdir_crosscheck.py), locked as a
  confidence test). `ALGORITHMS.md` §3 predates this and doesn't mention it — owed an addition.
- **S4 — dNBR is Landsat 30 m, not Sentinel-2 20 m.** The committed dNBR raster is **Landsat 30 m**
  ([ingest.py:173-174](../src/ingest.py#L173-L174), caveat A21/A4), a **3× upsample** to the 10 m grid.
  `CLAUDE.md`/`DATA_SOURCES` (and the doc's §7 satellite framing) still say Sentinel-2 20 m — doc fix owed.
- Context only: `acquire.py` + initial UI landed (`cfa1825`, `faa11f2`).

Everything else in the doc still appears to match the tree; verify per section.

---

## Bigger / higher-priority items

### B1 — Exploratory output on terrain refusal (rough basins / burn highlighting for viewer-led analysis)
Merges **Pipeline comment #3** ("add the ranking into the refusal json") and the **Section 9**
comments ("build our own rough basins and roughly score those; focus on highlighting burn areas;
let viewers conduct their own determination"). Confirmed: same idea.

- **Tension to respect:** decision **A28** (on incised terrain the score-ranking is *meaningless* —
  no discrete outlet → `area` undefined; uniformly-steep → `slope` stops discriminating) and the
  **Spine** (screening, never prediction). This must **not** ship as an authoritative ranking.
- **Design (Section 9 pass):** keep `refusal.json`'s honest STOP; *beside* it, emit exploratory material
  the viewer interprets themselves — **the defensible, stronger version of the owner's idea**:
  - ✅ **Burn severity map** over the AOI (where / how badly it burned) — pure input display.
  - ✅ **Generic descriptive drainage/catchments** (accumulation-threshold channels or pour-point
    catchments), labeled **"drainage structure, not hazard basins."**
  - ✅ **Raw per-unit terms side by side** (`mean_burn`, `mean_slope`, `area` — unmultiplied) + **sortable
    by burn severity** (a true ordering on any terrain). The human combines them with their own judgment.
  - ❌ **NO combined `burn × slope × area` score, NO rank, ever.** "Roughly score those" re-creates the
    exact meaningless ranking A28 refuses (area has no outlet unit; slope stops discriminating). The line:
    *"here's the burn + terrain, you decide"* (OK) vs *"here are ranked basins"* (forbidden).
- **Unifies with Q3a:** the same "refusal + exploratory layer, never a ranking" pattern serves BOTH the
  A28 terrain refusal AND the no-dNBR-yet refusal. One design, two uses.
- **UI dimension:** surfaced in the viewer (A36), not just `refusal.json`.
- **Why bigger / Tier-1:** reopens the refusal contract (A27/A28); needs a pre-registered decision + vault
  sign-off. Hard invariant: **no score, no rank, on any refusal.**
- **Priority:** high (owner-flagged), gated on the A27/A28 pre-registration.
- **Decision (owner, 2026-07-11):** chose this (raw terms + sort-by-burn) over "composite score + big
  disclaimers." Surfaced: A28 (superseded A27's caveated ranking) — on incised terrain there are no
  anchored basins to score and the composite is *meaningless*, not uncertain; a disclaimer can't repair a
  meaningless number, and a ranked table is what a stressed EM acts on. **Hard invariant: no composite
  score / no rank on any refusal.**

### B2 — Per-fire mountain-front contour as an operator input (not auto-derived)
Answers Section 5 "why is `CONTOUR_M` fixed to Montecito / can we make it fire-dependent?"
- **Why it's fixed now (deliberate, not oversight):** `CONTOUR_M = 150` is **global/frozen by
  decision** ([config.py:13](../src/config.py#L13)). The decisions log holds the frozen scalars global
  and defers per-fire tuning to a **separate pre-registered decision** (DECISIONS.md:201-203, 264).
  `CONTOUR_M` is load-bearing: the scored basins *are* its anchored catchments, so changing it moves
  the validated ranking.
- **Sanctioned path:** carry `contour_m` per-fire in the fire dict (like `expected_crs`), **operator-set**
  from that fire's range front; thread it into `stage_2b_outlets` (currently reads the global,
  [delineate.py:190](../src/delineate.py#L190)/196) and the already-parameterized
  `assert_contour_in_dem_range` ([delineate.py:74-75](../src/delineate.py#L74-L75)). **Montecito stays
  150 → validated ranking untouched (prove byte-identical).**
- **Decision (owner, 2026-07-11):** `contour_m` is **optional, defaults to 150**. The existing
  `assert_contour_in_dem_range` out-of-range abort is the **forcing function** — when 150 falls outside a
  new fire's DEM range, the run fails loud ("Set CONTOUR_M for this fire") and the operator must supply a
  per-fire value. Residual (accepted): a 150 that is *in range but geomorphically wrong* still passes
  silently — same gap A25 documents; not closed by this design.
- **Discipline:** Tier-1; pre-registered decision + vault sign-off before code. Operator input only —
  NOT terrain-auto-derived (that's Q2 / firewall).
- **Priority:** high (this is what blocks honest non-Montecito runs).

### B3 — Dual ranking: asset-gated (primary) + asset-agnostic (safety net)
Answers Section 5 "two rankings, one excluding the building factor, if not confident in building detection."
- **Motivation:** OSM building false-negatives could wrongly **discard** a real hazard basin
  (drains-to-asset is a keep/discard **filter**, not a score term). An asset-agnostic view surfaces them.
- **Validation-safety subtlety:** can't be done by relaxing the 600 m filter — the filter runs *before*
  the dedup ([delineate.py:241](../src/delineate.py#L241) then 248), so removing it lets more basins into
  the "larger-claims-first" dedup and **perturbs the geometry of basins that would have passed anyway**.
  Must be a **separate delineation pass**, not a filter toggle; primary stays byte-identical.
- **Framing:** asset-agnostic = **safety-net / audit view** ("not filtered for populated exposure — check
  if the building layer missed a community"), never the headline. Shares B1's let-the-viewer-decide philosophy.
- **Priority:** medium (robustness hedge).

### B4 — Auto-acquire dNBR subsystem (Q3 option 3)
- New network capability: search + fetch pre/post-fire Sentinel-2/Landsat scenes, cloud-mask, compute raw
  dNBR, stage onto the canonical grid (feeding the wired both-arms path). Extends `acquire.py`, which today
  only ingests an **operator-uploaded** dNBR + the CF-9 raw-scale guard ([acquire.py:294](../acquire.py#L294)) —
  it does **not** fetch/compute dNBR.
- **Blocked on Q3a** (no-usable-scene-yet behavior). Do **not** ship a burn-less ranking (see Q3a landmine).
- **Priority:** high (the core product motion for un-assessed fires), but **big** + Tier-1 (burn source +
  data acquisition): pre-registered decision + vault sign-off + its own validation before code.

---

## Tasks / smaller refactors

### T1 — Retire the `gate.py` re-export shim
- **Now:** `gate.py` does two jobs — a backward-compat **re-export shim**
  ([gate.py:57-63](../validation/gate.py#L57-L63)) *and* the **validation harness** (`main()` +
  perturbation/determinism probes). `run.py` already imports from `src.pipeline` directly.
- **Fix:** migrate the remaining shim consumers (`p2_*` scripts; tests `test_behavior_lock`,
  `test_entrypoint`, `test_a27_wired`, `test_a31_reorder`) onto `src.pipeline`; delete the
  re-export block; leave `gate.py` as a *pure* validation harness (one job).
- **Risk:** low (behavior-neutral import churn). Gate on: behavior-lock still passes.
- **Priority:** normal / small.

### T2 — Verified source-hunt for flow-modeling method provenance *(declined — CF-11 is sufficient backing)*
- The "find source to help design justification" ask (Flow Modeling). CF-11 is already strong
  *empirical* backing; this task adds the *literature* lineage.
- Steps: (1) check the vault `science_reference.md` for existing hydrology citations; (2) confirm
  which algorithms pysheds actually implements (fill_depressions, resolve_flats, D8) from its docs;
  (3) transcribe primary sources verbatim (candidates: D8 = O'Callaghan & Mark 1984; depression fill
  = Planchon & Darboux 2002 / priority-flood; flats = Garbrecht & Martz 1997) — **do not cite until
  verified** (repo science guardrail).
- **Priority:** low; only if we want citations in `ALGORITHMS.md` beyond the CF-11 anchor.

### T3 — Back-port F4 slope-coverage (+ `low_coverage` boolean) to the SBS writer
- `write_outputs` (SBS) omits `slope_coverage_frac`, `low_slope_coverage`, and the `low_coverage`
  boolean that `write_dnbr_outputs` emits ([outputs.py:125-133](../src/outputs.py#L125-L133) vs
  [outputs.py:208-222](../src/outputs.py#L208-L222)). The data is computed for **every** basin in
  `score.py` regardless of burn source; only the serialization differs. Harmless on inland Montecito
  (all `1.0`/`False`), but a future coastal **SBS** fire would silently drop the F4 safety flags.
- **Priority:** **bumped to high (owner, 2026-07-11)** — "publish all basin data" wants these flags on
  every run; surface `low_coverage` + F4 `slope_coverage_frac`/`low_slope_coverage` on the SBS
  `write_outputs`. Safe: additive columns, no score/rank change; update the CSV-schema test.

### T4 — ALGORITHMS.md reconciliation pass (apply S1–S4 + review outcomes)
- Bring `docs/ALGORITHMS.md` up to the live tree: §7 dNBR **wired** (S1), Landsat-30m sensor caveat (S4),
  §4 slope A33/F4 coastal patch (S2), §3 CF-11 cross-check (S3), plus §8–11 fixes as they land. Bump the
  header date + reconciliation note.
- **Owner (2026-07-11): low priority but do early.** Do as **one coherent pass after the review completes**
  (captures §8–11 + header/date together), not piecemeal mid-review.
- **Priority:** low-but-early. Pure documentation; no code/science change.

### T5 — Config hygiene: re-baseline `MASTER_KNOWN_KM2` + document convention-threshold rationale
- **SUPERSEDED (re-baseline half) by A38, 2026-07-13.** Instead of re-baselining the km² bands to 44.7273,
  the PASS/FINDING classification was **removed** and the FM-1 guard made **scale-free** (`master_km2 ÷
  valid-AOI ≥ MASTER_MIN_AOI_FRACTION = 0.05`, derived from Montecito's 0.2648 ÷ ~5). `MASTER_PASS_*` /
  `MASTER_ORDER_*` are gone; `MASTER_KNOWN_KM2 = 39.19` stays only as a print-only reference. See
  [[DECISIONS]] A38. The convention-threshold documentation bullet below is still open (folds into T4).
- In the param table, add the WHY-this-value for `ACC_THRESHOLD_CELLS` / `MIN_BASIN_KM2` /
  `DRAINS_TO_ASSET_M` / `TRUTH_MATCH_M`, or honestly mark them "reasonable convention, not derived / not
  cross-fire validated."
- **Priority:** low; folds into the T4 reconciliation pass.

---

## Per-section discussion log

### §1 Pipeline
- **Orchestrator?** — default stays **no** (A7 + D0). Result-type dispatch is already centralized
  in `dispatch_result`. Pending: user's specific motivation before reconsidering.
- **gate/run interconnection** → **T1** (the heavy `run.py`-loads-`gate.py` coupling is already gone;
  residual is `gate.py`'s dual role).
- **ranking in refusal.json** → **B1** (same idea as Section 9).

### §2 Foundations
- User self-correction ("average burn is addressed by the data-thinness gate") is **right**, with
  two refinements: (a) the real mechanism is the **A17 coverage-weighted denominator** —
  `mean_burn = mean(wt over ALL basin cells)`, so outside-perimeter (15) / developed (0) cells sit
  in the denominator as 0.0 and a partly-burned basin honestly reads lower ([score.py:46](../src/score.py#L46));
  (b) `low_coverage` (<0.80) is a **flag, not a gate** — it never excludes a basin ([score.py:57](../src/score.py#L57)).
- `burn_coverage_frac` **is** in the CSV ([outputs.py:128](../src/outputs.py#L128)); the boolean
  `low_coverage` is **not** in the SBS CSV (only the dNBR CSV carries it). → Section-6 thread.
- **Q1 (resolved — keep A17 as-is)** — A17 is *coverage-weighted* (a small intense core is diluted by
  surrounding unburned area); alternative was *covered-only mean + coverage flag*. Owner: keep as-is;
  the Foundations comment was informational, not a change request. No doc/code action; A17 unchanged.

### §3 Flow Modeling
- **Fill chain** = pysheds' textbook conditioning, verbatim ([hydrology.py:32-36](../src/hydrology.py#L32-L36)):
  `fill_pits` (1-cell sinks) → `fill_depressions` (closed basins) → `resolve_flats` (impose gradient)
  → D8 `flowdir` → `accumulation`. Conditioned DEM routes only, never scored (arity-2). Not hand-rolled.
- **Heuristic vs Backed** = reframe: flow modeling is the **least** heuristic part of the pipeline
  (established library + CF-11 independent-engine agreement, Pearson 0.9994). The *heuristic* is the
  **score** (burn × slope × area), not the routing. → S3, T2.
- CF-11's whole-grid divergence is the **coastal-edge / undeclared-nodata** artifact — same theme as
  A33/F4 (Section 4). Pipeline never scores that whole-grid master.

### §4 Slope
- **Method** = `np.gradient` central-difference on the raw metric DEM → `hypot` = `tan(theta)`
  ([pipeline.py:229](../src/pipeline.py#L229)). Not Horn's. Justification = empirical (reproduces the
  report `mean_slope` ±0.01) + it's the plainest first-order gradient; now routed through the shared
  `_valid_dem_mask` (A33).
- **A33/F4 coastal patch (`ebc1e06`, dated 2026-07-07 — the day AFTER the doc)** overrode the doc's
  deferral. *Source-side:* `mean_slope_tan` drops the nodata-adjacent RING to NaN — the spurious cliff
  lives in the valid cell that consumed a 0-clamped neighbor, so masking at the mean wouldn't fix it
  ([pipeline.py:246-259](../src/pipeline.py#L246-L259)). *Reduction-side:* mean over CLEAN cells only;
  whole-NaN basin → fail loud; **F4** `slope_coverage_frac`/`low_slope_coverage` (<0.80) diagnostic,
  never gates ([score.py:55-71](../src/score.py#L55-L71)). Montecito inland → byte-identical.
  **→ Section 4's caveat is stale; S2 doc fix owed.**
- **"Switch to Horn's / more complex"** = **Tier-1 / re-opens validation.** Not a named frozen
  constant, but the slope estimator feeds the frozen score, so changing it moves every `mean_slope` →
  every score → the validated ranking. Recommendation: **no pre-baseline**; if ever, a post-v1
  swap-test that re-validates (like C1). Horn (1981) is the "widely used" GIS-default reference.
- SBS/dNBR writer asymmetry (F4 fields on the dNBR writer only) → **T3** (carry to §6).

### §5 Outlet Detection & Delineation
- **Why `CONTOUR_M` is fixed to Montecito** = deliberate: frozen-global by decision, per-fire tuning
  deferred to a *separate pre-registered decision*, auto-derivation firewalled (A27). → **B2**, **Q2**.
- **Building-proximity logic** (Section 5 "explain"): a basin is **kept iff at least one of its CHANNEL
  cells** (`mask & (acc > ACC_THRESHOLD_CELLS)`) is within `DRAINS_TO_ASSET_M = 600 m` of a building —
  a cKDTree `k=1` min-distance query ([delineate.py:237-241](../src/delineate.py#L237-L241)). Keep/discard
  **filter on the channel network** (where debris travels), not a score weight, not outlet-based.
- **Two rankings** → **B3**.
- **Q2 (deferred — A27 firewall collision)** — "10 terrain-picked contour options / semi-adjustable."
  Auto-deriving a `CONTOUR_M` candidate from terrain is an **explicit firewall breach**: *"No function in
  that path may return, select, or consume a `CONTOUR_M` candidate value… a meters value that feeds
  anything downstream is a tuning knob and a firewall breach"* (DECISIONS.md:52-59). Only viable as
  **human-in-the-loop operator-assist** (terrain *suggests*, human *picks*), and even then post-v1 + its
  own validation (the suggester must output ~150 on Montecito or the oracle breaks). Not a v1 change.
- All §5 changes are **Tier-1**: pre-registered decision + vault + owner sign-off before any code.
  Nothing implemented in this pass.

### §6 Burn Severity → Weight & Coverage-Weighted Mean
- **"Outside-perimeter zeros" (user misread, elaborated):** there is no per-cell "perimeter of zeros." A
  basin is a set of cells; *some of those cells* are zeros (outside-burn class 15 or developed class 0) and
  stay in the basin-average denominator (A17). Different basins have **different** zero-counts (fully-inside
  → 0 zeros; half-outside → ~50%), not "the same amount." "Removing the zeros" = the *covered-only mean*
  alternative from **Q1**, which owner kept coverage-weighted. No spurious padding exists.
- **80% rule "how open":** **very open** — `BURN_LOW_COVERAGE = 0.80` is **flag-only, never excludes a
  basin**, and is **NOT** on the frozen-scalar list (DECISIONS.md:202), so changing it does **not** touch
  scores/ranks/AUC. Modeling-choice convention (not empirically derived); coarse by **C8** (conflates
  class 0 developed + class 15 outside into one flag).
- **In the CSV?** `burn_coverage_frac` **yes** (both writers); `low_coverage` boolean **only on the dNBR
  writer** → **T3**.
- **"Publish all basin data alongside"** = **T3** (surface flags consistently on the SBS path) + **B3**
  (asset-filtered-out basins). Scored-basin columns are already fairly complete; publishing
  discarded-as-tiny basins (< MIN_BASIN_KM2) is possible but low value (noise by design).

### §7 dNBR Burn-Source Arm — BIG staleness (doc says "not wired"; it is)
- **Wired (A34 / P2.2c done).** An explicit dNBR-only fire (`sbs=None` + `dnbr` key, e.g.
  `MONTECITO_DNBR_FIRE`) routes to `ingest_dnbr_both_arms` and scores end-to-end via `write_dnbr_outputs`
  (Arm A headline + Arm B companion + `rank_delta`). Doc §7 ("built, tested, NOT wired") and §6's
  "ingest_burn fails loud on dNBR (A29)" are **stale** → S1. **Nuance:** `ingest_burn` *does* still
  A29-fail on a **partial-SBS** fire ([ingest.py:146-156](../src/ingest.py#L146-L156)) — no silent
  SBS→dNBR fallback; the operator must declare a fire dNBR-only. Arguably correct (no-silent-decisions),
  not a gap.
- **Logic (current), for the §7 rewrite:** reproject native dNBR → DEM grid (explicit `dst_transform`,
  no north-shift ghost) → ONE shared valid footprint → Arm A (nearest; bin raw dNBR to 4 SBS classes via
  frozen edges + the 5→4 collapse; reuse `_burn_weight_raster` UNTOUCHED; NaN→sentinel→class 15) + Arm B
  (bilinear; linear clip-map). Both emit the same `(wt, covered)` → `stage_2e_score` UNCHANGED.
- **Literature pressure-test: ALREADY DONE (2026-06-16).** Owner checked the primary source (Key & Benson
  2006, RMRS-GTR-164-CD): the GTR has **no fixed numeric break table** (per-fire CBI-calibrated). So the
  edges `[0.100, 0.270, 0.440, 0.660]` are the **USGS/UN-SPIDER first-approximation table** (verbatim);
  Key & Benson = framework lineage; the generic-table limitation is carried as an honest caveat
  (P2_PREREGISTRATION §2; science_reference §7). Grounded **and** honestly bounded — a strength.
- **Existing-method pressure-test: the P2.3 swap test.** dNBR vs field-validated SBS on Montecito →
  **triage-validated** (rank-AUC 0.9722 both arms, all 6 flowed basins found), **NOT exact-rank-validated**
  (Arm A #1 = San Ysidro vs SBS #1 = Cold Spring, off ~1.03%; n=1). Honest framing baked into `DNBR_FRAMING`
  on every dNBR artifact ([outputs.py:172-178](../src/outputs.py#L172-L178)). Do not soften.
- **Satellite integration → S4:** committed dNBR is **Landsat 30 m** (3× upsample; nearest A / bilinear B),
  not Sentinel-2 20 m as the high-level docs say. Scale discipline solid (raw dNBR, NEVER ×1000 — the
  "silently bin everything to High" failure the freeze prevents). Cloud/NoData > 20% of a flowed basin →
  fail loud (`DNBR_NODATA_FAILLOUD_FRAC`).
- **Net:** §7's real work is a **doc rewrite** (S1 + S4), not new code — the arm, the literature check, and
  the swap test are all live.
- **Q3 (in discussion — auto SBS→dNBR fallback)** — owner: most target fires have no SBS, so prefer
  auto-dNBR when SBS is inadequate. Findings: (1) the **no-SBS** case ALREADY auto-routes to dNBR
  (`sbs=None` + `dnbr`), so the common un-assessed-fire path is already frictionless; fail-loud fires only
  on **partial SBS**. (2) The old A29 "silent mislabel" rationale is **moot** now the arm is wired (routing
  partial-SBS to `ingest_dnbr_both_arms` scores+stamps dNBR correctly). (3) Real remaining risk: partial
  SBS is **ambiguous** — genuine straddle vs a DATA ERROR (wrong objectid / clipped / corrupt); silent
  fallback would mask the latter (the exact silent-degradation the spine guards against). (4) Fallback
  presupposes a dNBR input (provided, or auto-acquired — ties to acquire.py/A35). **Recommendation:**
  explicit opt-in policy (`burn_policy: "sbs_then_dnbr"`), loudly logged with SBS-coverage % + stamped dNBR
  provenance; **default stays fail-loud**. Validation-safe (Montecito has full SBS → never triggers).
  Tier-1: pre-registered decision + vault sign-off before code.
- **Q3 RESOLVED (owner, 2026-07-11): option 3 — auto-acquire dNBR.** Rationale: target fires are analyzed
  fast, before a human would fetch dNBR (and long before BAER SBS). Spawns **B4** (new subsystem — today's
  `acquire.py` only ingests an *uploaded* dNBR).
- **Q3a (open — the "before dNBR lands" window):** dNBR = NBR_pre − NBR_post needs a post-fire,
  cloud-free scene (Sentinel-2 ~5-day revisit / Landsat ~8–16-day, + cloud luck) → often **no usable scene
  yet** in the urgent window. Defined behavior needed: (a) fail-loud + earliest-usable-pass ETA, (b)
  poll/retry until it lands then auto-run, (c) terrain-only provisional. **Scientific landmine (Tier-1):** a
  burn-less slope×area ranking is a DIFFERENT, UNVALIDATED model (an unburned steep canyon is not a
  debris-flow hazard) — do NOT fake the burn term; refuse rather than mislead (spine). Rec: (a)/(b), never
  (c) as a "ranking." Sets the tool's real speed bound = "as fast as dNBR allows" (days), not minutes.
- **Q3a RESOLVED (owner, 2026-07-11): option 2 — poll/retry + auto-run.** The tool watches for the first
  usable post-fire scene and runs itself when one lands; show option-1's honest ETA while waiting. Never
  ship a burn-less ranking. Feeds B4's design.

### §8 Frozen Score & Within-Fire Ranking — HOLD THE LINE (Tier-1)
- Owner note: "add hard-coded tuning for the area linear-multiplication problem." This is the **C1** known
  mis-ranking (large moderately-burned basin outranks a small severely-burned flowed one; Oak Creek < Toro
  Canyon). **The instinct is valid — the linear × area term is a real, acknowledged weakness.**
- **But this is the frozen fence.** `burn × slope × area` is pre-registered + validated (rank-AUC 0.9722).
  Changing the area term re-opens validation = **Tier-1 HALT**.
- **Why not now / not this way (epistemics):** C1 was found by looking at which basins flowed on Montecito.
  Hard-coding a tune to "fix" that case = **fitting to already-seen validation data** → the AUC stops being
  an out-of-sample number. CLAUDE.md verbatim: *"changing the scoring after seeing results is how you fool
  yourself."* "Hard-coded tuning" is the exact anti-pattern; even C1's candidates are **principled
  functional forms** (area^0.5 / ln(area) / normalization — sub-linear volume-vs-area scaling), not a
  per-case constant.
- **Sanctioned path (C1 deferred, not dead):** (1) ship + hand off the validated baseline; (2) pre-register
  the dampening form + physical rationale BEFORE seeing its ranking effect; (3) test **out-of-sample on a
  second fire (P4)** as a swap-test; (4) adopt only if it holds. Even an *optional* experimental mode needs
  a 2nd fire to be evaluated honestly (else it just refits Montecito).
- Already instrumented: `evaluate().discordant_are_fm3` asserts every AUC-costing discordance IS the C1
  signature, so a NEW error type would be caught. C1 is handled with care, not neglect.
- **Decision: C1 stays deferred. No area tuning in this pass.** *(owner aligned 2026-07-11: leave frozen.)*

### §9 Terrain-Applicability Refusal (Hypsometric Gate)
- Current: clean STOP — `refusal.json` (status REFUSED, `span_m`, message, `ranking_produced: False`); NO
  `ranking.csv` / `basins.geojson` ([outputs.py:67-102](../src/outputs.py#L67-L102)). Honest by design.
- Owner idea (B1): on refusal, provide rough basins + burn highlighting for viewer-led determination.
  **Resolved via the B1 design above:** burn map + generic drainage + raw burn/slope layers = ✅; combined
  score/rank = ❌ (A28: meaningless on incised terrain). The defensible core ("you decide") is the stronger
  version of the idea; "roughly score" is the part that re-creates the forbidden ranking.
- Reuses for the Q3a no-dNBR-yet refusal too. → **B1**.

### §11 Parameter Table — "probe the config constants deeper"
Taxonomy by justification (the useful lens):
- **Physical / not tunable:** `CELL_M`=10 (USGS 3DEP resolution), `CELL_AREA_KM2` (derived),
  `DIRMAP`/`D8_OFFSETS` (pysheds encoding). No probing needed — facts.
- **Literature-frozen (provenance documented):** `BURN_WEIGHTS` (even spacing = stated modeling choice),
  `DNBR_*` (USGS/UN-SPIDER table, verified vs primary source — §7). Justified; don't touch.
- **Convention/heuristic thresholds (the ones that actually warrant probing — reasonable but NOT derived):**
  `ACC_THRESHOLD_CELLS`=500 (~0.05 km² channel-initiation; landscape-dependent, Montgomery & Foufoula 1993),
  `MIN_BASIN_KM2`=0.1 (noise floor), `DRAINS_TO_ASSET_M`=600 (why 600? a buffer, not derived),
  `TRUTH_MATCH_M`=250 (validation tolerance). The table's "Rationale" column is *descriptive* (what it does),
  not *justificatory* (why this value). → document the WHY or mark "reasonable convention."
- **Montecito-reference / STALE:** `MASTER_KNOWN_KM2`=39.19 is the Week-0 figure; live master is **44.7273**.
  **RESOLVED by A38 (2026-07-13):** the km² bands were removed and the guard made scale-free (fraction of
  valid AOI); `MASTER_KNOWN_KM2` is now print-only. (See T5 above.)
- **Key generalization insight:** the convention thresholds are the **same Montecito-calibrated global family
  as `CONTOUR_M`** — DECISIONS.md holds them all global with per-fire tuning deferred to a pre-registered
  decision (DECISIONS.md:201-203, 264). So **B2's per-fire question applies to the whole class**, not just
  the contour. Decide per-constant: some become per-fire operator inputs; some stay global.
