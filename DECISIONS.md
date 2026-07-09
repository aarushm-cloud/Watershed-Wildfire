# Decisions -- ADR log

<!-- Context -> Decision -> Reasoning -> Status. D0 governs: default answer to "should we add X?" is NO until a validated need exists. Canonical source: vault note "DECISIONS". To be synced. -->

---

### A27 — Terrain-applicability refusal trigger (frozen hypsometry rule; refuse-with-reason, Option A)

> **Corrected 2026-06-29 (same-day, pre-implementation):** Option-A behavior amended — an incised
> fire produces **NO ranking to caveat**; the refusal emits *reason + a no-ranking explanation*.
> Frozen trigger `(p10 − p1) > 50 m` unchanged. See the Correction note at the end of this entry.

- **Context.** The pipeline's outlet-anchoring step assumes **range-front-over-plain** geometry —
  steep burned slopes discharging onto a flatter plain, the contour-mouth structure the validated
  Montecito case has. On **incised-upland** terrain (a dissected highland with no plain→range
  break, e.g. South Fork `sfk2024`, Ruidoso NM 2024) that structure does not exist, so the
  `CONTOUR_M` anchor is ill-posed. A25's `CONTOUR_M`-in-range guard catches the *gross* numeric
  mis-set (a wrong fire's value outside the DEM's min/max) but **not** the geomorphic case where a
  contour sits in-range yet means nothing because the terrain is unimodal. The applicability story
  the outreach docs now tell ("knows incised terrain and says why") has, until now, **no frozen
  rule behind it** — a pre-registration gap (the rule lived only in planning chats / council
  verdicts, external to this record).
- **Decision.** Freeze a single-scalar terrain-applicability trigger and adopt **refuse-with-
  reason (Option A)** behavior. A fire's terrain is classified **ill-posed for outlet-anchoring**
  iff its DEM hypsometry span exceeds the frozen threshold:

  > **REFUSE iff `(p10 − p1) > 50 m`**, where `p1`, `p10` are the 1st and 10th percentiles of
  > valid (nodata-masked) DEM elevation. (A wide low tail = an incised valley floor with no
  > compact depositional plain; a true plain compresses `p1→p10` to ~20–30 m.)

  On REFUSE, the tool **does not crash and does not stop cold** — it emits a structured,
  human-readable refusal carrying (i) the geomorphic reason, (ii) the measured span diagnostic
  `(p10 − p1)`, and (iii) an explicit statement that **no within-fire ranking is produced, because
  the scored basins are defined by the `CONTOUR_M` mountain-front anchor that this terrain does not
  support** (the `delineate.py` outlets → basins → scores chain; no anchor → no basins → no
  scores). This replaces the bare downstream `GateAbort` with an honest, legible refusal (Option A
  as corrected; A11 framing-travels). A caveated *ranking* is not available for incised terrain, and
  building a fallback basin-definition to produce one would be over-build (D0).
- **Frozen elements.**
  - **Trigger:** `(p10 − p1) > 50 m` on valid-cell DEM elevation. Single scalar, no other clause
    active.
  - **Dropped, on evidence — clause (a) "no secondary low-elevation mode."** The Montecito KAT
    found Montecito's coastal plain is the *primary* hypsometric mode (not a secondary peak below
    the mountain), so a "secondary mode" test would have mis-refused the validated case. Removed
    unanimously (Council Round 2). The earlier dip-term and distribution-shape tests were dropped
    in Round 1.
  - **Bajada terrain — out of scope here, deferred as `C12`.** A27 is **strictly the single-
    scalar span rule**; the bajada (coalesced-alluvial-fan) extension — a future dimensionless
    slope-structure ratio — is **not part of this freeze**. It is recorded as a separate
    pre-registered, deferred decision `C12` (no code, no value, no trigger yet; D0), so A27 carries
    no second clause and `C12` can be adopted later under its own ADR without re-opening A27.
  - **Firewall line (the thing that keeps this off the category-two fence):** the implementing
    detector reads only **whether** a contour is well-posed — a boolean over the hypsometry curve.
    Its only numeric outputs are **diagnostic** (mode count, span `p10 − p1`, bin sizes used).
    **No function in that path may return, select, or consume a `CONTOUR_M` candidate value.** A
    meters value that feeds anything downstream is a tuning knob and a firewall breach. The
    adversarial review of the implementing build greps specifically for this leak.
  - **Bins.** Hypsometry diagnostics reported at both 50 m and 25 m bins (descriptive only; the
    trigger itself is the percentile span, not a binned-mode count).
- **Reasoning.** The *ranking* (`burn × slope × area`) never required a mountain front; only the
  *outlet-anchoring* does (First-Principles decoupling). So the honest behavior on incised terrain
  is not "no result" but "ranking stands, anchoring caveated" — Option A. Collapsing the rule to a
  single percentile span is the maximally-conservative, minimally-fittable formulation: one scalar,
  pre-registered, with no free parameter the build instance could invent. The owner's bar is "won't
  crash unless handed a near-perfect fire"; a structured refuse-with-reason meets it. Refusal as a
  feature, not a bug — surfacing a caveated result is more credible to a practitioner than crashing
  or silently guessing, and the inland "we have nothing" users are better served by a fast caveated
  estimate than by a tool that declines entirely. The 50 m threshold is a result-independent
  geomorphic constant (a depositional plain's `p1→p10` compression), frozen before any South Fork
  score exists, consistent with A21's correction-test discipline.
- **Relations.** Analog of A20 (P2.1) / A24 (P3.1) as a pre-registration freeze; governed by the
  A21 correction-test (a frozen value changes only on result-independent infeasibility, before any
  score). Builds on A25's `CONTOUR_M` guard (numeric-range → adds geomorphic-shape refusal) and
  FM-16. Sits inside A24 §8 boundary-assertion work. **Bajada-terrain extension deferred as `C12`**
  (pre-registered approach, not built; D0) — A27 itself is strictly the single-scalar span rule,
  no second active clause. The South Fork REAL-BOUNDARY corroboration is tracked as an open data
  dependency (see Status). Implementing code is a **separate two-instance build (P3.4-build-1), not
  adopted here** — A27 ratifies only the rule.
- **Status.** ADOPTED / Frozen, 2026-06-29 (documentation-only; no acquisition, no score, no
  formula touch — clears A21 correction-test as an addition that reverses nothing). Council
  2026-06 (5-lens, two rounds) + Montecito KAT informed the rule; recorded here as the
  firewall-of-record freeze that those runs lacked. **Open dependency (honest):** the widened-
  footprint South Fork DEM that establishes the ~1747 m valley floor (the base-level evidence that
  South Fork is a real incised boundary, not a clipped tile) is **not yet in committed data** — the
  committed raster still carries the ~1976 m old-clip floor. A27's *rule* is frozen now; its South
  Fork *corroboration* completes when that DEM and its provenance land (tracked as a repo build
  item, not a blocker on the rule's adoption). Implementing module + runnable South-Fork-refuses
  test = P3.4-build-1 (separate build, adversarial-greps the no-CONTOUR_M-candidate firewall line).
- **Correction note (2026-06-29, same-day pre-implementation).** A27 as first ratified described
  Option A as "return the still-valid ranking as a caveated result." A read-only repo recon resolved
  the load-bearing question and **falsified that description for incised fires**: the scored basins
  are the upslope catchments of `CONTOUR_M`-defined outlets (`src/delineate.py:85-173`), so refusing
  the anchor yields zero basins and no `burn × slope × area` scores — there is no ranking to caveat.
  The council's decoupling premise (ranking independent of the mountain front) is contradicted by the
  code. **Corrected behavior:** on REFUSE the tool emits a structured refusal — a `refusal.json`
  plus a human-readable, span-based message — and writes no `ranking.csv` / `basins.geojson`; it
  does not crash and does not emit a ranking. The refusal reason is **span-based, never
  modality-based**: the committed South Fork DEM is weakly bimodal, so a "single mode" claim would
  be factually wrong — the message cites the measured span and the absence of a compact depositional
  plain. The **frozen trigger
  `(p10 − p1) > 50 m` is untouched.** Governed by A21 (result-independent infeasibility, discovered
  before any score). Original framing retained here for audit. Consistent with the P3.1
  pre-registration §7, which already anticipated "no clean mountain-front → degenerate delineation →
  loud halt → RAN-CLEAN-finding."

---

> **Freeze-order — owner attestation (not git-proven).** The 50 m span threshold is a valid
> firewall control only if its value was fixed before South Fork was evaluated against it; a
> threshold fit to a fire's own result proves nothing. **I attest that it was.** I set the
> `(p10 − p1) > 50 m` rule on result-independent geomorphic grounds before running South Fork
> through the applicability check. The value is not fit to any South Fork output: it is anchored
> on the low side by the property that a compact depositional plain compresses `p1 → p10` to
> roughly 20 to 30 m (the binding constraint, which is South-Fork-independent), and set with
> margin above that as a single non-fittable scalar. It was derived by council (five-lens, two
> rounds) informed by the Montecito KAT: round 1 dropped the dip and distribution-shape terms,
> round 2 dropped clause (a) after the KAT found Montecito's plain is the primary hypsometric
> mode. The implementing detector emits only diagnostics (mode count, span, bin sizes); no
> `CONTOUR_M` candidate value crosses the boundary. South Fork's refusal is a robust consequence
> of the value (its span sits far above 50 m at every DEM footprint tested), not a target the
> value was tuned to.
>
> **Provenance, stated honestly.** Git does not independently establish this ordering: the 50 m
> constant and the first South Fork applicability test (`test_B_southfork_corroboration`) are
> co-located in one commit (`83e4ee0`, 2026-06-29), South Fork's DEM had been in the repo since
> 2026-06-22, and the vault is untracked. The claim that the value was frozen ahead of evaluation
> therefore rests on this attestation together with the constant's result-independent nature, not
> on the vault or the commit graph proving it. This is the same ordering class A24 records as not
> git-provable; per A24's process note, future pre-registrations are committed standalone, before
> any acquisition, so the order is git-evident.

---

### A28 — Refuse behavior corrected: a refusal produces no ranking (supersedes A27's caveated-ranking clause)

- **Context.** A27 ratified refuse-with-reason (Option A) for incised terrain and stated its output
  form as *"returns the still-valid within-fire ranking as a caveated result"* — the First-Principles
  framing that the ranking never required a mountain front, only the outlet-anchoring does, so "the
  ranking stands, anchoring caveated." The implementing build (P3.4 build-1/2) shipped the opposite:
  a refusal writes only `refusal.json` (no `ranking.csv` / `basins.geojson`) and the refusal message
  states *"no ranking is produced."* The recorded decision and the shipped, adversarially-reviewed
  behavior diverged; commit F transcribed A27's caveated-ranking wording verbatim, so the
  contradiction now lives in the repo.
- **Decision.** Ratify the shipped behavior and supersede A27's caveated-ranking clause. **On REFUSE
  the tool produces no ranking** — it emits only the refusal artifact (`refusal.json`) carrying the
  geomorphic reason (mode count, measured span, bins) and a plain-language message. No `ranking.csv`,
  no `basins.geojson`, no ordered output of any kind on a refused fire.
- **Reasoning.** A27's caveated-ranking rested on a First-Principles premise — *the ranking is
  independent of the mountain front; only the outlet-anchoring needs it* — that subsequent
  incised-terrain analysis (South Fork, Trout Fire) **refuted**. On incised upland the ranking is not
  merely un-anchored, it is **meaningless**, because each of the formula's three terms loses its
  referent: `contributing_area_km²` has no meaning without a discrete basin outlet (there is no
  depositional plain for an outlet to sit on); `mean_slope` stops discriminating because all
  dissected-highland terrain is uniformly steep, so the term no longer separates basins; and there is
  no plain-referenced discharge point to define the ranking unit at all. "Ranking stands, anchoring
  caveated" was therefore wrong — the ranking does not stand. The honest output past the boundary is
  a refusal with a reason, not a hedged ordering that invites a meaningless ranking to be acted on.
- **Status.** ADOPTED 2026-06-30. Supersedes A27's refuse-behavior clause (the Option A
  caveated-ranking form); A27's text is retained for audit (append-only, not rewritten). **No code
  change** — the implementation already conforms; A28 aligns the record to the shipped, reviewed
  behavior. A27's caveated-ranking was council-resolved; this correction rests on the refuted
  First-Principles premise (terrain analysis that emerged after the council), recorded as a
  superseding decision rather than a re-litigation. Distinct from the A21 frozen-value correction-test
  (which governs category-2 constants before any score exists); refuse behavior is neither a frozen
  value nor pre-score, so A21's test does not apply — this is an ordinary superseding ADR.

---

### A29 — dNBR-select guard: fail loud until the dNBR arm is wired (guard, not a fence change)

- **Context.** The dNBR end-to-end arm (`reproject_dnbr` + `normalize_dnbr_arm_a/b` +
  `ingest_dnbr_both_arms`) is built and unit-tested but **NOT wired into the scoring path**. Without
  a stop, a partial-SBS fire would stamp `burn_source="dNBR"` while `_burn_weight_raster` scores the
  SBS raster (out-of-codeset cells silently weighted 0) — a silent mislabel.
- **Decision.** `ingest_burn` fails loud (`GateAbort`) when `select_burn_source` returns anything
  other than `"SBS"`. This converts the silent mislabel to a loud refusal.
- **Reasoning.** Consistent with A8 fail-loud and A3 precedence (whose dNBR arm is decided but
  unwired). No category-two fence value changes. No behavior change for Montecito (selects SBS) or
  South Fork (refuses at A27 before `ingest_burn`).
- **Status.** Guard in place; no fence value changes. Full dNBR dispatch is **P2.2c**, gated on the
  A/B arm-selection decision (deferred).

---

### A30 — Per-fire I/O parameterization: `run_pipeline(fire=None)` threads a fire config; Montecito default byte-identical (architecture, behavior-preserving)

- **Context.** The pipeline was still Montecito-bound: `run_pipeline` took no arguments and read
  I/O + provenance straight from module globals, and `run.py` (the intended production driver) was
  an empty stub. Two consequences surfaced in the P3.4-close read-through: no second fire can run
  through the production entrypoint without editing globals; and a latent provenance mislabel —
  `write_outputs` stamped the Montecito `validation_case` string on *any* output, so a non-Montecito
  run would carry Montecito provenance.
- **Decision.** Parameterize per-fire I/O. `run_pipeline(fire=None)` threads a fire config carrying
  **only I/O + provenance** — `name, dem, sbs, assets, creeks, out_dir, expected_crs,
  validation_case`; the no-arg default is `MONTECITO_FIRE` (== the existing module globals), so
  Montecito output is **byte-identical** and the behavior lock is untouched. `validation_case`
  becomes **per-fire** (parameterized in `write_outputs`, defaulted to the Montecito string), fixing
  the latent mislabel. `run.py` becomes the production driver (thin `--fire` CLI, lean output);
  `gate.main` stays the Montecito validation driver (probes, report-values); both share
  `run_pipeline` / `dispatch_result` / `write_outputs`. Does **NOT** change pipeline stage order.
- **Scope boundary.** The frozen analytical scalars — `CONTOUR_M, ACC_THRESHOLD_CELLS,
  MIN_BASIN_KM2, DRAINS_TO_ASSET_M, TRUTH_MATCH_M, BURN_WEIGHTS, DNBR_*, DIRMAP, D8_OFFSETS,
  CELL_M` — remain **global/frozen**, NOT per-fire in this change. Per-fire tuning of any of them
  (e.g. a fire needing `CONTOUR_M ≠ 150`) affects outlets → ranking and is a **separate
  pre-registered decision** (A26 cat-1/cat-2 split; D0). This change threads I/O and provenance only.
- **Reasoning.** A behavior-preserving generalization in the A25 mold — additive, default-preserving,
  regression-gated. The no-arg default collapsing to the existing globals is what keeps Montecito
  byte-identical, so the behavior lock stays a true regression detector (A19). Fixing `validation_case`
  in the same pass closes a provenance-integrity hole — a mislabeled output is the silent-wrong-
  provenance failure the fail-loud spine exists to prevent (A8/A11) — without touching any scored
  value. Keeping the analytical fence global honors A26 (only I/O generalizes now) and D0 (no
  per-fire scalar without a validated trigger).
- **Status.** ADOPTED (build-2a). By construction Montecito output is byte-identical (no-arg default
  == existing globals) and the behavior lock is untouched; no category-two value moves; stage order
  unchanged. Ratify-first: the repo `DECISIONS.md` stub commits before the build-2a code commit.

  ### A31 — Terrain-applicability gate runs before hydrology and burn ingest (pipeline reorder)

**Status:** Accepted (pre-registered; ratify-first — this stub is committed BEFORE the implementing code).
**Scope:** Pipeline stage ordering + per-fire I/O wiring. No analytical change. Frozen category-two fence untouched; frozen scalars stay global.

**Context.**
`assess_hypsometric_applicability` (the A27 terrain check) is invoked in `run_pipeline` via `_terrain_applicability_gate`, and it consumes `dem_raw`/`dem_nodata` that are currently produced *inside* `stage_2a_hydrology`. `stage_2a` opens and `assert_aligned`s the SBS raster together with the DEM before `run_pipeline` reaches the gate. Current `run_pipeline` order:

1. `stage_2a_hydrology` — opens+aligns DEM & SBS, runs hydrology, computes master outlet
2. master-outlet ABORT (FM-1)
3. A27 terrain-applicability refusal
4. A25 contour-in-range guard

Consequence: an un-assessed incised fire with no SBS cannot reach an honest A27 refusal through the pipeline. South Fork (the P3 dNBR-path fire) has **no SBS raster** — its burn products are entirely dNBR-derived. Routing it through `run_pipeline` today would fail at `rasterio.open(fire["sbs"])` inside `stage_2a` (missing SBS / no `sbs` field), not at the terrain gate. This is why South Fork's refusal is presently exercised only by calling `assess_hypsometric_applicability` directly on its DEM (`test_B_southfork_corroboration`), bypassing the pipeline.

**Decision.**
Move the A27 terrain-applicability gate ahead of `stage_2a_hydrology` in `run_pipeline`, so refusal is decided on the DEM alone before any hydrology runs or any SBS/burn raster is opened.

- DEM load is lifted ahead of both the gate and `stage_2a` (standalone `load_dem(fire["dem"])` at the top of `run_pipeline`, or DEM-load split out of `stage_2a`). The gate receives `dem_raw`/`dem_nodata` from this early load.
- On refusal, `run_pipeline` returns the refusal (writes `refusal.json` to `fire["out_dir"]`) without opening SBS, running hydrology, or evaluating the master-outlet ABORT.
- `stage_2a_hydrology` runs only for fires that pass the terrain gate; it retains its open+align-SBS-with-DEM contract for those fires.

New `run_pipeline` order:

1. DEM load (`fire["dem"]`)
2. A27 terrain-applicability refusal ← **now first; refuses on DEM alone**
3. `stage_2a_hydrology` — opens+aligns SBS, hydrology, master outlet
4. master-outlet ABORT (FM-1)
5. A25 contour-in-range guard

**This subsumes the prior "master-outlet-ABORT-runs-before-A27" finding:** the terrain gate now precedes both hydrology and the ABORT, so no hydrology work is done for a fire that will refuse on terrain.

**South Fork wiring.**
- A `southfork` fire dict is added and registered in `FIRES`, with no valid SBS (`sbs` = `None`/absent). Because the terrain gate now runs before SBS is opened, `--fire southfork` runs end-to-end to a refusal and emits `refusal.json` on machines where the (gitignored) South Fork data is present. This is the real-fire *demonstration* of the refusal path; the fixture test below is the *guarantee*.
- The registered `southfork` fire is **not a CI dependency** — its data is gitignored and unavailable on a clean checkout.
- **Clean-exit sub-decision:** a registered fire whose local data is absent must exit cleanly. `resolve_fire`/`run.py` checks input existence and raises `SystemExit` with a data-absence message (e.g. `"southfork data not present (gitignored); see acquisition_manifest.json"`) rather than crashing deep in `rasterio.open`/`load_dem`. "Registered but data-absent" is thus a defined, graceful state, not a broken one. This check is generic (applies to any registered fire), not South-Fork-special-cased.

**Test plan.**
- **Hermetic end-to-end refusal (the guarantee):** a new test drives `run_pipeline` with a fire dict pointing at the tracked `tests/fixtures/incised_synthetic.tif` (EPSG:32613, 40×80, 10 m) and `sbs=None`, asserts a refusal is returned / `refusal.json` written, and — critically — is **non-vacuous**: it fails if the reorder is reverted (i.e. if the pipeline attempts SBS-open before the gate, or does hydrology before refusing). This also dynamically exercises the `assets`/`creeks`/`out_dir` threading through `run_pipeline` that was previously proven only by grep.
- **Path-threading (bad-path, not parity):** a fire dict with a nonexistent `dem` (and/or `sbs`) must cause `run_pipeline` to raise. This proves config threading — explicitly NOT the false-coverage `run_pipeline()` vs `run_pipeline(MONTECITO_FIRE)` determinism comparison.
- **Clean-exit on absent data:** a test invokes `resolve_fire`/`run_fire` for a registered fire pointing at a nonexistent input path and asserts a `SystemExit` with the data-absence message — distinguishing a graceful "data not present" exit from a raw `rasterio`/`load_dem` crash. (The bad-path threading test above covers `run_pipeline` raising on a bad path; this covers the driver layer exiting cleanly.)
- **Existing South Fork corroboration** (`test_B_southfork_corroboration`) is retained unchanged (skip-when-absent), now complemented by the hermetic pipeline test.

**Hard gates (unchanged).**
- Montecito **byte-identical** — behavior lock is the oracle. Montecito proceeds through A27 (passes terrain), so the reorder must not change its output. `test_behavior_lock.py` is never edited to make this pass.
- `git diff src/config.py` empty.
- Frozen seam (`delineate` / `score` / `hydrology` / `grids` / `ingest`) untouched.
- Frozen analytical scalars (`CONTOUR_M, ACC_THRESHOLD_CELLS, MIN_BASIN_KM2, DRAINS_TO_ASSET_M, TRUTH_MATCH_M, CELL_M`) stay global — no per-fire tuning (that is a separate pre-registered decision).

**Non-goals / defer.**
- No dNBR wiring. The A29 fail-loud on non-SBS burn selection stands; full dNBR end-to-end dispatch is P2.2c (council-gated).
- No promotion of `run_pipeline` out of `validation/gate.py` (deferred nit).

---

### A32 — `mean_slope` raises `GateAbort` on an empty mask (run-premises-void, not sibling-symmetry)

*(IDs: A32 = this; A33 = the coastal deferral below. Assigned as the next two free above the live
max A31 (pipeline reorder). An earlier draft proposed A31/A32 for these two, but A31 was already
taken by the ordering ADR — hence the renumber.)*

- **Context.** In `src/score.py`, three per-basin quantities are computed side by side.
  `burn_coverage_frac` (line 40) and `mean_burn` (line 42) each carry a `… if ncells else 0.0`
  guard; `mean_slope` (line 43) did not. An empty mask therefore yielded `np.mean(slope[[]]) → nan
  → score = mean_burn × nan × area → nan`, a silent nan poisoning the ranking. Unreachable on
  Montecito and under current `delineate` (which guarantees every retained basin ≥ `MIN_BASIN_KM2`),
  but the guard was genuinely absent from shipped code — a live asymmetry, not a hypothetical.
- **Decision.** `mean_slope` on `ncells == 0` **raises `GateAbort`**, with a message naming the basin
  and the violated `MIN_BASIN_KM2` invariant. It does **not** return `0.0`.
- **Reasoning.** The rationale is **premises-void, not symmetry with the siblings.**
  `mean_burn`/`burn_coverage_frac` return `0.0` because a zero there is a *meaningful* value (a
  genuinely unburned or fully-uncovered basin). An empty *mask* is not a meaningful zero — it means a
  basin with no cells, which can only occur if `MIN_BASIN_KM2` has been violated upstream, i.e. the
  run's own premises are broken. `GateAbort` halts all 36 basins; that blast radius is justified
  **only** on premises-void grounds (the whole run is unsound), which is exactly the case here — so
  the abort is correct, but it must be ratified on "empty mask ⇒ `MIN_BASIN_KM2` void ⇒ run premises
  broken ⇒ abort," **not** on matching the sibling guards. (Ratifying on symmetry invites a future
  reader to "fix" it back to `return 0.0` for consistency — the exact silent-failure this closes.)
  This is A8/A29 fail-loud: an impossible-by-invariant state surfaces loudly rather than being
  swallowed.
- **Ship condition (test discipline).** Ships **with** a synthetic fixture that forces `ncells == 0`
  and asserts the raise fires — constructed directly (the pipeline cannot produce this state, so the
  fixture must build it by hand). A guard no fixture can trip is dead code by this project's own
  standard; A32 does not ship without a passing trip-test.
- **Frozen-value impact.** None. Formula, `BURN_WEIGHTS`, dNBR bins, `DIRMAP`/`D8_OFFSETS` untouched;
  category-two fence intact. Behavior lock (AUC 0.9722 / 44.7273 km² / 36 basins) byte-identical — the
  new path is unreachable on Montecito.
- **Status.** RATIFIED + IMPLEMENTED locally 2026-07-06 (uncommitted — owner commits). Guard added in
  `src/score.py` `stage_2e_score` (`raise GateAbort` on `ncells == 0`; imports `GateAbort` from
  `grids`). Trip-fixture `tests/test_empty_mask_abort.py` (empty-mask → GateAbort; non-empty →
  scores) passes; full suite 67/67; behavior lock 7/7 byte-identical (AUC 0.9722 / 44.7273 km² / 36
  basins). Ratify-first commit sequencing (stub-before-code) and adversarial review left to the owner.
  → FAILURE_MODES FM-13 (the `score = 0.0` / coverage family), DECISIONS **A18** (denominator basis),
  **A29** (fail-loud-on-impossible-state precedent).

---

### A33 — Coastal-slope nodata contamination (R1): deferred 2026-07-06 → OVERRIDDEN + BUILT + COMMITTED `ebc1e06` (2026-07-07)

> **⚠️ SUPERSEDED 2026-07-07 — the original "do not build" decision below was OVERRIDDEN by the owner and the real fix shipped.** `mean_slope_tan(dem_raw, dem_nodata)` now NaN-drops the nodata-adjacent ring via the shared `_valid_dem_mask` (answering the open question below — slope no longer bypasses the valid-mask); `stage_2e_score` means over the clean (non-NaN) cells, and an all-NaN basin fails loud (A8, like A32). Made testable via the synthetic coastal fixture `tests/test_coastal_slope.py`, which was the D0/untestable objection the deferral rested on. Montecito behavior lock byte-identical (inland basins → no NaN). Committed `ebc1e06`. **The deferred reasoning below is retained as the audit trail; the Status at the bottom is updated to reflect the build.** → FAILURE_MODES **FM-17**.

- **Context.** `mean_slope_tan` (`src/pipeline.py:204`) runs `np.gradient` over the **raw** DEM and
  applies no valid-cell mask, while every other DEM consumer — delineation and the A25/A27 guards —
  intersects `_valid_dem_mask` (`src/delineate.py:49`). pysheds clamps undeclared nodata to 0 (FM-12),
  so a valid land cell adjacent to a 0-clamped ocean/nodata cell reads a spurious cliff (Δ~100 m / 10 m
  → `tan` of several), inflating that basin's `mean_slope` → `score` with no flag. This is the same
  unenforced contract as A32's root cause: **slope is the one DEM derivative bypassing the shared
  valid-mask.** R1 (this) and R3 (A32) are two symptoms of that one gap.
- **Decision.** **Do not build a fix or a flag now.** Record the finding as a P3 hazard note and defer.
  No per-basin adjacency scan, no boundary flag, no source change to `mean_slope_tan` in this cycle.
- **Reasoning.**
  1. **D0 / untestable.** There is no coastal fire, no nodata-contact AOI, and no fixture that can trip
     a contamination flag today. Building a per-basin adjacency scan that no fixture can exercise is the
     over-build D0 exists to prevent — the same "untestable machinery" objection that (correctly) sinks
     it also means we cannot validate any fix against a real boundary.
  2. **The naive fix is incomplete.** Intersecting `mean_slope` with `_valid_dem_mask` does **not**
     remove the contamination: the spurious cliff lives in the *valid* land cell whose 0-clamped
     neighbor `np.gradient` already consumed, not in the nodata cell itself. Removing it requires either
     dropping the nodata-adjacent ring of cells or computing a correct boundary gradient — both are real
     work that must be decided against real coastal data, not pre-committed blind.
  3. **Flag-vs-fix is premature, not wrong.** The earlier "flag, don't silently drop" instinct (for
     A18 consistency) isn't incorrect in spirit, but it can't be chosen over drop-ring or
     boundary-gradient until a testable coastal case exists to distinguish them.
- **The open question this note owns (answer at P3, before scoring any coastal fire).** *Why does
  `mean_slope_tan` skip `_valid_dem_mask` when delineation and the A25/A27 guards apply it?* That single
  decision — the slope term's nodata contract at the source — determines whether the eventual fix is a
  flag, a drop-ring, or boundary-gradient correctness. Everything downstream (connectivity choice, flag
  naming, flag-vs-drop) collapses into detail once it's settled.
- **P3 hazard note (verbatim, to carry into the P3 / generalization section).**
  > *Coastal DEMs: `mean_slope_tan` (`pipeline.py:204`) reads the raw DEM with no valid mask, so a land
  > cell adjacent to nodata (clamped to 0, FM-12) reads a spurious cliff that inflates that basin's
  > slope and score. Masking at the mean does not remove it — the bad value is in the adjacent valid
  > cell. Before scoring any nodata-adjacent (coastal / patchy) fire, decide the slope term's nodata
  > contract at source: drop the nodata-adjacent ring, or compute a correct boundary gradient. Until
  > then the tool has no coastal-slope guarantee and must not present a nodata-adjacent basin's rank as
  > clean.*
- **Also parked (not a build reason).** The "honesty-ledger" framing (a boundary flag as a
  practitioner-facing transparency asset) is retained as a **P3 design note**, not a justification to
  build unreachable code now.
- **Frozen-value impact.** None — nothing is changed. Documentation only.
- **Status.** ~~DEFERRED to P3 (hazard note recorded 2026-07-06)~~ → **BUILT + COMMITTED `ebc1e06`
  (2026-07-07).** The owner overrode the deferral and shipped the **drop-ring** fix (one of the two
  "real fixes" named above) plus a synthetic coastal fixture (`tests/test_coastal_slope.py`) that
  trips the old inflation and proves the fix drops it — directly answering the D0/untestable
  objection. The open question is resolved: `mean_slope_tan` no longer bypasses `_valid_dem_mask`.
  Full suite 74 passed at commit; Montecito behavior lock 7/7 byte-identical (inland → no NaN);
  frozen formula untouched. → FAILURE_MODES **FM-17** (this fix), FM-12 (nodata-to-0 clamp),
  FM-15/FM-16 (the A25 CRS/CONTOUR guard family that *does* mask), DECISIONS **A18** (burn-side
  coverage precedent), **A28** (refuse-on-terrain precedent), **A32** (the sibling score-path fail-loud).

---

### A34 — dNBR scoring wired into production (P2.2c): both arms surfaced, Arm A the pre-registered headline, Arm B a non-gating companion

- **Context.** The dNBR path (`reproject_dnbr` / `normalize_dnbr_arm_a` / `normalize_dnbr_arm_b` /
  `ingest_dnbr_both_arms`) was built + unit-tested but **UNWIRED**: `ingest_burn` hard-refused any
  non-SBS selection (A29), so production could not score dNBR at all. The coordinate frontend's target
  population is un-assessed fires, which by definition lack SBS — so the frontend was blocked until the
  dNBR path ran end-to-end. The P2.3 input-swap test is a documented pre-registered **FAIL**: identical
  triage rank-AUC 0.9722 across SBS / Arm A / Arm B and 6/6 flowed basins recovered, but the binary
  "Cold Spring is exactly #1" criterion missed — Arm A ranks San Ysidro #1 (3.314) / Cold Spring #2
  (3.280), a 1.03% burn-driven margin; Arm B reproduces Cold Spring #1. **n = 1.**
- **Decision.** Route a dNBR fire (`sbs=None`) through `ingest_dnbr_both_arms` and score **both arms**.
  **Arm A (binned, the pre-registered primary) is the sole headline ranking; Arm B (continuous
  companion) rides alongside** as a non-gating column, with per-basin `rank_delta = |rank_A − rank_B|`
  surfaced as an honest uncertainty signal. Every dNBR artifact carries the framing: triage-validated,
  **NOT** exact-rank-validated, **n = 1**. The screening spine stays on every artifact (A11).
- **Explicitly NOT decided (firewall).** Does **not** promote Arm B and does **not** resolve which arm is
  primary — deferred to a fresh fire (P3/P4). Surfacing both and picking neither respects the
  pre-registration firewall; crowning B off n = 1 is the goalpost-move the firewall forbids.
- **Reasoning.** The target fires only have dNBR, so an honest dNBR path is what makes the tool usable at
  all; the finding establishes dNBR is fit for the tool's actual job (triage / relative ranking) on the
  validated fire. The SBS branch is left literally untouched, so the Montecito SBS behavior lock stays a
  true regression detector; the both-arms path is gated against the committed P2.3 side-by-side oracle.
- **Frozen-value impact.** None — formula, `BURN_WEIGHTS`, `DNBR_BIN_EDGES` / `DNBR_CLAMP` / `DNBR_FLOOR`
  all untouched. The SBS-vs-dNBR *input routing* sits in `run_pipeline` (per-fire I/O, A30); the
  coverage-based *selection* (A4/A15) stays inside `ingest_burn`.
- **Status.** IMPLEMENTED + verified + **COMMITTED `a523dde`** (2026-07-07; labeled "A32" at commit,
  renamed A32→A34 in code comments in the follow-up `ebc1e06` after discovering the repo already used
  A32 (empty-mask) + A33 (coastal)). Montecito SBS behavior lock byte-identical (7/7); reproduces the
  P2.3 oracle (Arm A → San Ysidro #1 3.314339 / Cold Spring #2 3.280250; Arm B → Cold Spring #1;
  rank-AUC 0.9722 both arms) via `tests/test_dnbr_pipeline.py` **and** the independent
  `validation/p2_3_swap_test.py`. Resolves the P2.2c / A29 deferred; enables A35 / A36.

---

### A35 — Coordinate-driven acquisition layer (`acquire.py`), outside `src/`

- **Context.** "Coordinates in" requires auto-fetching the DEM + buildings from a bbox. Both capabilities
  are proven (`validation/p3_acquire_dem.py` = USGS 3DEP 1/3″ COG via `/vsicurl/`;
  `validation/p3_acquire_assets.py` = OSM via `osmnx`) but each was hardcoded to South Fork's single
  frozen grid / tile / UTM zone. `src/` is a pure, no-network seam (`ingest.py` bans import-time I/O),
  so network fetching must not live there.
- **Decision.** Add **`acquire.py`** at repo root (peer of `run.py`) generalizing the proven scripts to an
  arbitrary bbox: derive the UTM zone (EPSG:326xx) from the bbox centroid, build a 10 m canonical grid,
  fetch + mosaic 3DEP + OSM buildings onto it, stage files, and assemble the A30 `fire` dict
  (`sbs=None`). Network → staged files → pure pipeline. Fail loud (via `GateAbort`, A8) on all-NoData,
  native-CRS drift, or 0 buildings over a populated AOI. Validate the uploaded dNBR is raw scale
  (~−2..2), refusing an apparent ×1000 upload (protects the frozen `DNBR_BIN_EDGES`).
- **Build decisions.** (1) DEM fetch = manual AWS COG `/vsicurl/` + `rasterio.merge`, **not** `py3dep`
  (not in env; COG is the proven path — zero new dependency). (2) **Building footprints reduced to
  representative POINTS** (`_buildings_to_points`, centroid in the metric CRS): the pipeline reads
  `assets.geometry.x/.y` and the validated Montecito assets are Points — a bug the CF-10 end-to-end gate
  caught (fetch emitted Polygons; South Fork's terrain refusal had hidden it because it refuses before
  assets are used). ⚠️ The proven `p3_acquire_assets.py` (+ the committed South Fork gpkg) still emit
  Polygons — dormant (South Fork refuses), flagged not fixed (prior-phase, D0).
- **Reasoning.** Keeps the pure-`src/` invariant intact while reusing validated code; the A30 `fire` dict
  is already the exact seam. Porting, not research.
- **Frozen-value impact.** None — `src/` + all frozen constants untouched (`acquire.py` *reads*
  `DNBR_CLAMP`, never re-derives it).
- **Status.** IMPLEMENTED + verified + **COMMITTED** (2026-07-08). Built test-first (`utm_epsg` /
  `canonical_grid` [CF-6], `tiles_for_bbox` / `fetch_dem` [CF-7], `fetch_buildings` /
  `_buildings_to_points` [CF-8], `assert_raw_dnbr` / `build_fire_config` [CF-9]). Primary gate:
  `canonical_grid` reproduces South Fork's frozen grid EXACTLY (EPSG:32613, 966×1439, UL
  426400.8/3697312.6) from its bbox — corner-point reprojection + `round()` (**not** `transform_bounds`,
  which bows the box outward and inflates it ~2%). Live-verified: reproduces the committed South Fork DEM
  grid + elevation range + 633 buildings; end-to-end `build_fire_config` → `run_pipeline` → A27 refusal.
  `osmnx` pinned (A10/A13). Feeds A30's `run_pipeline(fire)`; consumed by A36.

---

### A36 — Local Streamlit frontend (`app.py`); C2 / D0 / A7 consciously overridden

- **Context.** The tool was CLI-only (`run.py --fire <name>` over pre-staged data); a non-developer could
  not use it. The decision log schedules only a bare-bones output *viewer* at P5 (A9); anything richer is
  deferred to outreach O3 (C2); D0 defaults NO to "an elaborate frontend"; A7 says "no backend / no live
  service."
- **Decision.** Build a local **Streamlit** app (`app.py`) — a thin UI over `run_pipeline`: draw/enter a
  bbox, upload a raw dNBR GeoTIFF → `acquire.build_fire_config` (A35) auto-fetches DEM + buildings →
  dNBR both-arms (A34) → a ranked folium map + `ranking.csv`, or a legible refusal. The owner
  **explicitly overrides C2 and D0** for this build and reconciles A7: it is a **local, single-user tool
  that wraps the CLI**, not a hosted service, so A7's "no live service" spirit holds; A1 already
  sanctions "a small Streamlit app."
- **Trigger (per D0).** Reduce friction + widen the user base toward outreach O3/O4. A usable demo is a
  legitimate way to *earn* the O3/O4 ask rather than wait for it. Recorded as a deliberate scope
  decision, not drift.
- **Reasoning + cost carried honestly.** The override weakens the "restraint as portfolio signal"
  argument A9/C2 leaned on; mitigation is to keep `app.py` a deliberately minimal outreach demo, never a
  product. Every user-facing artifact keeps the screening spine + the A34 n = 1 dNBR framing. Logic lives
  in pure importable helpers; the UI is in `main()` behind an `if __name__ == "__main__"` guard (testable
  via `import app` + Streamlit `AppTest`). Results persist in `st.session_state` (an `st_folium` rerun
  would otherwise wipe them).
- **Frozen-value impact.** None — Tier-2 UI plumbing; `src/` + all frozen constants untouched.
- **Status.** IMPLEMENTED + verified + **COMMITTED** (2026-07-08). Verified three ways: unit
  (`tests/test_app.py` — pure helpers + an `AppTest` smoke test + a rerun-persistence regression test);
  the **CF-10 end-to-end gate, both paths** (Montecito network e2e → 44 basins ranked + map + CSV; South
  Fork → legible refusal); full suite green with the behavior lock byte-identical. Owner chose the
  draw-on-map scope → `streamlit` + `streamlit-folium` pinned. Confidence layer (CF-E): pyflwdir
  cross-check (per-outlet catchment area Pearson 0.9994 on the scored basins; whole-grid divergence a
  documented coastal-ocean/edge artifact) + hypothesis property tests locking the frozen scoring
  invariants. Depends on A34 (dNBR) + A35 (acquire); overrides C2/D0, reconciles A7, invokes A1.