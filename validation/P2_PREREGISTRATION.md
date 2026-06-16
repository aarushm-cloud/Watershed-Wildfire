# P2.1 — dNBR Input-Swap Pre-Registration (FROZEN BEFORE FIRST SCORE)

> **Repo provenance / citation anchors** *(added on transcription — metadata only, no knob altered)*
> - Transcribed verbatim from the owner's frozen P2.1 pre-registration; committed to the repo
>   on 2026-06-16. The body below is the firewall and is **not edited to make any run pass** (A16).
> - **Cited from `docs/science_reference.md` (canonical = vault `science_reference.md`):** §1 (the
>   `burn × slope × area` formula and the burn-weight map `1→0.0, 2→0.33, 3→0.67, 4→1.0`); §2 (the
>   `dNBR ÷ 1000` integer-scaling convention and the dNBR-is-continuous-not-a-BAER-class note); §4
>   (dNBR ≠ BAER SBS). **DECISIONS:** A17 (coverage-weighted `mean_burn`), A18 (coverage = class
>   ∈ {1,2,3,4}), A12 (the swap question), A16 (oracle is read-only).
> - **Control numbers below are the live P0.5 behavior lock**, verified against
>   `tests/test_behavior_lock.py` + `validation/out/ranking.csv` on transcription: 36 basins,
>   rank-AUC `0.9722222`, flowed `{4,6,9,14,21,23}`, Cold Spring = basin 6 = rank 1 (flowed),
>   top tercile = `n//3` = 12. **Not** CLAUDE.md's stale 0.987 / 32-basin figures (AOI unrecoverable).
> - **Science-anchor gap RESOLVED 2026-06-16:** the §2 break array `[−0.5, 0.1, 0.27, 0.44, 0.66, 1.3]`
>   now has a canonical home in `science_reference.md` §7. Owner checked the primary source directly
>   (Key & Benson 2006, RMRS-GTR-164-CD, LA-25…LA-32): the GTR confirms the dNBR formula, the five
>   severity-level names, and the ×1000 scaling, but publishes **no fixed numeric break table** (levels
>   are per-fire CBI-calibrated). The specific array is therefore attributed to the **secondary
>   USGS/UN-SPIDER first-approximation table**, with Key & Benson as framework lineage — exactly as §2
>   states. The literal, zero-adjustment firewall stance is unaffected; the GTR's own per-scene
>   calibration is itself primary support for carrying the generic-table limitation as a stated honesty
>   caveat (§2), not a tuned knob.
> - **ADR A20** logs this pre-registration in the canonical (vault) `DECISIONS.md`.

**Status:** Owner decisions resolved (§9) + five-seat senior review folded in. Ready to route to Claude
Code as a transcribe-and-commit prompt (land at `validation/P2_PREREGISTRATION.md`, cite from
`science_reference.md`, add DECISIONS ADR A20). **Hard stop after commit: no dNBR score, no data
acquisition** until P2.0 is separately prompted.
**Purpose:** This document *is* the anti-fitting firewall (P2 plan §2). It fixes every burn-boundary
knob, the comparison metric, the agreement criterion, and the numeric fail threshold **before any dNBR
score exists**, so the result cannot be reverse-fitted to the validated SBS answer. Once the first dNBR
score is computed it is **frozen and never reopened** (A16 / P0.5 lesson: never tune toward 0.987).

**The one rule that governs the rest:** take the published dNBR breaks literally, with **zero
adjustment**. A pass that required moving any value below is not a pass — it is the oracle-integrity
failure the whole project exists to prevent.

---

## 0. What is being tested (and what a pass does NOT establish)

Re-run the validated Montecito case with **dNBR instead of SBS**, same AOI / same DEM / same hydrology
/ same frozen formula — **only the burn input changes** — and ask: *does the within-fire ranking
survive the input swap?* (A12, resolving OPEN-1.)

A clean pass establishes **only** that, on the single validated case, dNBR and SBS rank the 6
documented-flow basins consistently. It does **not** independently validate dNBR as a ranker in
general — the only ground truth here is the same Montecito event SBS was validated on. The swap
confirms *agreement with the control*, not *correctness in the abstract*. Generalization is P3/P4.

**Frame this as the product story, not a disclaimer (Outsider).** The reason the target user — an
under-resourced-state EM whose fires *never* get the field-validated BAER input — should care is
precisely this: the free, available-anywhere input (dNBR) ranks the basins the same way the
gold-standard field input (SBS) did, on the one fire where we can actually check. That is the pitch.
**State it in P2.4 and the O4 conversation this way:** "validated on SBS, and the production dNBR input
ranks consistently with it on that case" — n=1, two inputs, and the honest answer to "how many fires?"
stays *one*. The precision is the credibility, not a hedge to manage.

---

## 1. The control and the two arms

| Arm | Burn input | Normalization | Role |
|---|---|---|---|
| **Control** | Thomas Fire BAER SBS (objectid 3248) | A17 coverage-weighted `mean_burn`, untouched | The frozen behavior-lock baseline (AUC 0.9722, 36 basins). Never modified. |
| **dNBR-A** (PRIMARY) | Thomas Fire dNBR | Bin → 4 classes, reuse `BURN_WEIGHTS` + `score._burn_weight_raster` | Faithful-to-the-SBS-encoding path. The pre-registered primary. |
| **dNBR-B** (COMPANION) | Thomas Fire dNBR | Continuous dNBR → [0,1] monotonic map | Robustness companion. Reported, not gating. |

**Why both (council verdict):** A↔B agreement proves the ranking is **robust to the
normalization-function choice** — real and worth having. It is **blind by construction** to whether the
dNBR *signal* is a worse proxy than field-validated SBS; both arms inherit that error identically. Do
not oversell A↔B stability as signal validation (P2 plan §5). Hold this line in P2.4.

**Why A is primary on honest grounds:** A reuses the *exact* validated burn-weight computation. But A
does **not** inherit SBS's perimeter/NoData handling for free — `score._burn_weight_raster` computes
`covered = np.isin(sbs, (1,2,3,4))`, and continuous dNBR has a value at every in-scene pixel, so binning
maps essentially everything to classes 1–4 unless an explicit outside-burn rule sends some pixels to a
non-covered sentinel. **A therefore owes the same outside-burn decision as B (§4)** — it just hides it
inside the binning step. This is exactly the assumption A17's Status line flags for revisit at P2.

---

## 2. KNOB 1 — Arm A binning function (literal published breaks + explicit collapse)

**Source (named, not invented):** USGS / UN-SPIDER burn-severity classification, lineage **Key, C.H. &
Benson, N.C. (2006), *Landscape Assessment (LA)*, in FIREMON: Fire Effects Monitoring and Inventory
System, USDA Forest Service RMRS-GTR-164-CD.** Published bound array (raw dNBR, taken verbatim):

```
[-0.500, 0.100, 0.270, 0.440, 0.660, 1.300]
  → ['Unburned', 'Low', 'Moderate-low', 'Moderate-high', 'High']
```

The full published table additionally defines two **enhanced-regrowth** bins below −0.100 (high regrowth
−0.500…−0.251, low regrowth −0.250…−0.100). So the native scheme is **5 burn classes + 2 regrowth
classes**, and the collapse to SBS's 4 classes is a **choice the published table does not make for you**
— it must be pinned here.

**⚠️ Scale — FROZEN HERE (not deferred to P2.0):** all breaks in this document are **raw dNBR**
(≈ −0.5 … +1.3). The pipeline carries **raw dNBR**, period. If the acquired product is delivered
×1000-scaled (common for classified products), ingest **divides by 1000 as a deterministic normalization
step** before any binning or clamping — this is a fixed conversion, **not a decision and not a tunable.**
A scaled product silently binning every pixel to "High" is the exact runs-but-quietly-wrong failure this
freeze prevents (DATA_SOURCES §2 gotcha). P2.0 acquires; it does not get to pick the scale.

**The frozen 5→4 collapse rule** (maps published severity → SBS 4-class encoding → `BURN_WEIGHTS`):

| Published dNBR range (raw) | Published class | → SBS class | `BURN_WEIGHTS` |
|---|---|---|---|
| dNBR < 0.100 (incl. all regrowth + unburned) | Unburned / regrowth | **non-covered** (see §4) | — (enters denominator as 0.0) |
| 0.100 ≤ dNBR < 0.270 | Low | **2** | 0.33 |
| 0.270 ≤ dNBR < 0.440 | Moderate-low | **3** | 0.67 |
| 0.440 ≤ dNBR < 0.660 | Moderate-high | **3** | 0.67 |
| 0.660 ≤ dNBR | High | **4** | 1.0 |

**Collapse decisions, stated explicitly (each is a frozen choice, not a default):**
- **Moderate-low + Moderate-high → SBS class 3** (the single SBS "Moderate"). This is the one genuine
  5→4 merge; the two published moderate bins collapse into SBS's one moderate class.
- **SBS class 1 (unburned/very-low, weight 0.0) is NOT populated by binning.** Under A17, SBS class 1
  and class 15 both contribute 0.0 to `mean_burn`; the only behavioral question is whether a pixel is
  *covered* (in the denominator as a burned-footprint cell) or *non-covered*. The dNBR<0.1 floor is
  routed to **non-covered (§4)**, matching how SBS treats outside-perimeter cells. No dNBR pixel is
  assigned SBS class 1.
- **Reuse is exact:** once the class raster exists, `score._burn_weight_raster` + `BURN_WEIGHTS` run
  **untouched**. Binning produces the class raster in/before ingest's handoff (P2.2b).

**Firewall note — do NOT use MTBS thematic to dodge the collapse.** MTBS thematic severity is already
~4-class but its thresholds are **set per-scene by an analyst** = scene-fitted = the exact thing
zero-adjustment forbids. The collapse above is literature-anchored and fixed; that is the point even if
it costs accuracy.

**Pre/post scene dates — FROZEN HERE (a hidden burn-boundary knob).** dNBR magnitude depends on *which*
post-fire scene is differenced: an **initial assessment** (immediate post-fire, captures peak signal
before any regrowth/rain wash) ranks basins differently from an **extended assessment** (next growing
season). This is a frozen choice, not a P2.0 acquisition detail. **Frozen: initial-assessment dNBR** —
pre-fire scene = least-cloudy Sentinel-2 L2A within ~weeks before Thomas Fire ignition (2017-12-04);
post-fire scene = least-cloudy L2A as soon as smoke/active-fire clears post-containment, *before* the
first major post-fire storm (the 2018-01-09 Montecito storm). Rationale: matches what an operational
triage user would have at decision time (the tool's actual use case) and isolates the burn signal from
storm/regrowth confounds. The exact two scene IDs + acquisition dates are recorded in `Provenance` at
P2.0 and reported in P2.4; the *assessment-window policy* (initial, pre-first-storm) is frozen here.
>
> **⚠ SUPERSEDED by Amendment A21 (2026-06-16, appended below):** the initial-assessment / pre-first-
> storm policy was found *physically unsatisfiable* for the Montecito basins (containment 2018-01-12
> postdates the 2018-01-09 storm). Corrected to **extended assessment** per A21 — read A21 before acting
> on this paragraph. Original text retained for audit.

**Caveat to carry (P2.1 + P2.4) — two parts:**
1. **Cross-domain mapping.** Arm A maps a *vegetation/canopy-change* index (dNBR, Key & Benson lineage)
   onto an encoding built for *soil burn severity* (BAER SBS — hydrophobicity, the hydrologic cause).
   These are different physical axes. A is a defensible screening *proxy*; it is **not** reconstructing
   SBS, and P2.4 must say so. This is *why* a swap can degrade even when nothing is mis-coded.
2. **Regime-generic floor.** The 0.1 unburned/low break is a continental default; in chaparral
   (Montecito's regime) the real unburned-to-low boundary often sits higher, so a literal 0.1 may pull
   genuine low-severity margin into "covered." We take 0.1 literally anyway — zero adjustment is the
   firewall — and **state** the caveat rather than tune it. The published breaks and the collapse were
   derived for other sensors / ecosystems / atmospheric corrections; applying them here is itself an
   assumption, taken literally on purpose even if it costs accuracy.

---

## 3. KNOB 2 — Arm B transfer function (continuous → [0,1])

**Frozen:** clamp raw dNBR to `[0.100, 1.300]`, then linear monotonic map to `[0, 1]`:

```
b = clip(dNBR, 0.100, 1.300)
mean_burn_pixel_B = (b - 0.100) / (1.300 - 0.100)
```

- Lower clamp `0.100` = the published unburned/low boundary, so B's "burned" floor matches A's covered
  floor (same outside-burn rule, §4). Below 0.100 → non-covered, identical to A.
- Upper clamp `1.300` = the published High upper bound.
- Linear (not sigmoid/power) — a power curve is a tunable; linear is the assumption-free monotonic map.
- The A17 coverage-weighted denominator treatment applies **identically** to B (§5), or the arms are
  not comparable.

---

## 4. KNOB 3 — Outside-burn / NoData rule (BOTH arms — the coverage-semantics gap)

dNBR has no native perimeter. The rule that decides which pixels are "outside the burn" sets both arms'
`covered` mask and therefore their A18 `low_coverage` semantics. This is a **production-architecture
choice**, not a normalization detail.

**Decided:** **dNBR threshold, not an external perimeter feed.** But "outside the burn" and "no usable
data" are **two distinct non-covered paths** and must not be conflated — a clouded or scene-edge pixel
over a genuinely-burned basin is NOT `dNBR < 0.1`, it is *garbage*, and treating it as low-burn silently
under-reads a hot basin (an A8 fail-loud violation at the burn boundary).

```
# evaluated AFTER reprojection, on the canonical 10 m grid (see ordering note)
nodata_dNBR   = cloud_mask | scene_edge | dNBR == sensor_nodata   # path 1: no usable data
class15_dNBR  = nodata_dNBR | (dNBR < 0.100)                      # both → class-15 sentinel, weight 0.0, NOT covered
covered_dNBR  = (~nodata_dNBR) & (dNBR >= 0.100)                  # the burned footprint
```

- **Path 1 — NoData/cloud:** Sentinel-2 L2A scene-classification (SCL) cloud/cirrus/shadow mask + scene
  edge + sensor nodata → class-15, non-covered. **Fail-loud guard (A8):** if NoData covers more than a
  pinned fraction of any flowed basin (**frozen: > 20% of basin cells**), the run **errors loudly** for
  that basin rather than ranking it — a clouded scene is a bad scene, not a low-burn finding. This is
  exactly the degraded-input case the project's spine refuses to paper over.
- **Path 2 — outside-burn:** `dNBR < 0.100` → class-15, non-covered (the published unburned/low break,
  shared with §2/§3 so the knobs use one number, not four).

**Ordering — FROZEN:** reproject/resample to the canonical grid **first**, then threshold on the
canonical grid. Thresholding native-resolution pixels and *then* resampling the mask would let arm B's
bilinear smear the class-15 boundary into fractional coverage — wrong. Mask and class assignment happen
**after** the §5 reprojection, on the 10 m grid.

**Why dNBR-cutoff over external perimeter:**
- Keeps P2 to "only the burn input changes" (A12's isolation). An external Thomas Fire perimeter feed
  introduces a *new data dependency* and a new acquisition/provenance surface mid-phase — D0 says no
  without a trigger, and the swap-isolation argument is that trigger working against it.
- The 0.100 floor is the same published break already fixed in §2/§3, so the three knobs share one
  number rather than introducing a fourth.

**Stated assumption / known risk:** this makes A's coverage semantics depend on the dNBR floor rather
than a field-mapped perimeter, so a dNBR scene that under-reads a genuinely-burned low-severity margin
will mark it non-covered. This is the **A17 / FM-11 known failure direction** re-expressed for dNBR
(coverage-weighting can suppress a partially-covered hot basin). Immaterial on Montecito if the flowed
basins remain ≥0.9 covered under dNBR — **confirm this in P2.3, do not assume it.**

---

## 5. KNOB 4 — Resample method (per arm)

dNBR (20 m native, Sentinel-2 B8A/B12) reprojected to the canonical grid (EPSG:32611, 1413×1295 @ 10 m,
bounds `(253179.22, 3809589.31, 267309.22, 3822539.31)`).

| Arm | Resample | Rationale |
|---|---|---|
| **A** | **nearest** | A bins to discrete classes; bilinear/cubic blur classes across boundaries (wrong for a thematic path). Nearest preserves class integrity. |
| **B** | **bilinear** | B is continuous; bilinear is the standard antialiased continuous resample. Cubic introduces overshoot beyond the clamp range — rejected. |

Resample happens **after** dNBR is on the canonical grid and **before** normalization. The A17
coverage-weighted `mean_burn` treatment is applied identically across all three arms.

**Reprojection contract — FROZEN (prevents the P0.5 north-shift ghost).** Going from 20 m native dNBR
to the 10 m canonical grid is *upsampling*, and a half-pixel grid offset shifts class boundaries by up
to 10 m — the same AOI-drift class of error that cost P0.5 its bit-reproducibility. Therefore:
- Reproject with `rasterio.warp.reproject` using an **explicit `dst_transform` and `dst_shape` equal to
  the DEM's** (the canonical 1413×1295 @ 10 m, bounds above) — snap dNBR to the **DEM grid**, never to
  the dNBR scene's own grid.
- `grids.py`'s **`assert_aligned`** (shape + CRS + transform) MUST pass on the reprojected dNBR against
  the DEM **before** any thresholding or scoring. A misaligned dNBR fails loud here, not silently
  downstream (A7 / A8).
- Resampling method per the table above is the `Resampling` enum passed to that single `reproject` call.

---

## 6. KNOB 5 — dNBR source (decided here, acquired in P2.0)

**Primary: Sentinel-2 dNBR** (B8A NIR / B12 SWIR, both 20 m, L2A surface reflectance, pre/post Thomas
Fire). The production-representative path — "validate what you ship." Caveat: co-varies sensor +
resolution on top of the input-type change, slightly muddying isolation; recorded, not hidden.

**Documented fallback: MTBS dNBR** (Landsat, no token). Cleaner isolation but a different sensor.
Used **only** if Copernicus acquisition stalls (see precedent: the 2 GB ScienceBase truth-polygon IP
rate-limit, VALIDATION_REPORT §6). A fallback is **recorded as a sensor caveat in Provenance + P2.4,
never a silent swap** (A4 / A8 / A16).

**Both:** stamped once onto the single `Provenance` field (`burn_source="dNBR"`, `sensor=...`), read
everywhere (A4). Exactly one source per run, never blended (A3).

---

## 7. The metric, the agreement criterion, and the fail threshold (frozen)

The control is the **P0.5-reconstructed gate**: 36 basins, top tercile = top 12 (`n//3`), AUC 0.9722,
6 documented-flow basins `{4, 6, 9, 14, 21, 23}`, Cold Spring #1. **Not** the original-AOI
0.987 / 32-basin / top-10 figures (AOI unrecoverable; A17 / P0.5).

**Control provenance — FROZEN (don't compare against stale artifacts).** In P2.3 the SBS control is
**re-run fresh on current HEAD**, not read from the committed `validation/out/` artifacts — those are
flagged as possibly reflecting a pre-P1.1-fix state (open item from P1.1). Precondition: the **7/7
behavior lock must be green on HEAD** (`pytest tests/test_behavior_lock.py`) before the control run is
trusted as the baseline. A plan can assert "7/7"; P2.3 must prove it.

**Basin-set alignment — FROZEN ASSERTION.** The truth set `{4,6,9,14,21,23}` is only meaningful against
a specific delineation. The DEM is identical across all three arms, so the delineation *should* be
bit-identical — but "should" is not "is." Before any cross-arm comparison, **assert the dNBR run
produces exactly 36 basins with the same basin IDs and same `(row,col)` outlets as the SBS control.**
Mismatch → fail loud (the truth set would otherwise silently misalign and the comparison would be
meaningless). This is a `grids.py`-style boundary assertion (A7), not a judgment call.

**Spearman implementation — FROZEN.** Use `scipy.stats.spearmanr` over all 36 basin scores
(default average-rank tie handling). This is a *different* convention from the gate's strict-pairwise
rank-AUC (ties → discordant); both are reported and they are **not** expected to be numerically equal.
Name the function so the number is reproducible and not silently swapped for a hand-rolled rank corr.

### Primary pass criterion — AGREEMENT, gated on dNBR-A (ALL THREE must hold)
1. **Tercile recovery:** all **6/6** documented-flow basins stay in the top tercile (top 12 of 36).
   *(Restating the plan's "≥70%" as its true integer bar: 6/6 required; 5/6 = 83.3% is the documented
   fail edge; 4/6 = 66.7% fails. See note below on why this is 6/6 not 5/6.)*
2. **Cold Spring #1 — BINARY.** Cold Spring is the #1-ranked basin AND classified flowed. If it is no
   longer #1-and-flowed, that is a **fail, full stop.** No "documented explanation" escape hatch
   (removed per review — it contradicts no-goalpost-moving, and Cold Spring is the single most fragile
   basin: the control's own verdict already turns on its OSM-naming call, FM-4 / VALIDATION_REPORT §6).
3. **Spearman ρ floor:** Spearman rank correlation between the SBS-control ordering and the dNBR-A
   ordering (all 36 basins) **≥ 0.80** (frozen).

**Why a Spearman floor at all:** tercile membership is coarse — a basin can fall rank 2→11 and still be
"top tercile," so 6/6 + Cold-Spring can pass on a ranking that scrambled the middle. The ρ floor is what
makes "survives" mean *survives*, not *coincidentally tercile-intact*.

**ρ ≥ 0.80 (decided, not 0.60):** the SBS control already separates flowed/non-flowed at AUC 0.9722
(near-complete). A swap that drops the *whole-ranking* correlation below 0.80 means the middle of the
list reshuffled substantially even if the top survived — a real degradation worth surfacing as a
finding, not waving through. 0.60 would pass rankings that should not be called "the same screen."

### Why 6/6 (not 5/6) on criterion 1
The control captures 6/6. The swap question is "does the ranking *survive*," and the control's own bar
is 6/6. Accepting 5/6 would mean the swap is allowed to *lose* a flowed basin from the top tercile and
still "pass" — that is a degradation, hence a finding, not a pass. (5/6 is therefore part of the fail
condition, §below, not the pass bar.)

### Secondary reported metrics (NOT gating)
- Rank-AUC for dNBR-A and dNBR-B (against the same 6-flowed truth set).
- **A↔B agreement:** Spearman ρ between dNBR-A and dNBR-B orderings (the robustness headline).
- Per-basin coverage fraction under dNBR for the 6 flowed basins (confirms the §4 assumption).

### Required P2.3 deliverable — the human-readable side-by-side (FROZEN, not a nicety)
A non-developer (the O4 target — an under-resourced-state EM who doesn't run Python) must be able to
**see what moved between SBS and dNBR and why**. This is a frozen P2.3 output, not a drop-under-deadline
extra: a single table/figure showing, for all 36 basins, the SBS rank vs dNBR-A rank vs dNBR-B rank,
the 6 flowed basins marked, and the score components (`burn`, `slope`, `area`) for each arm. "Asserted ≠
validated" — if a reasonable outsider can't read what changed, the comparison isn't done. (Outputs carry
the A11 screening framing; scores are within-fire ordinal only, A5.)

### The numeric fail threshold (frozen)
A **documented dNBR-ranking failure** (shipped as a finding, A12) is **any** of:
- **< 6/6** flowed basins in the top tercile under dNBR-A (i.e. 5/6 or fewer), **OR**
- Cold Spring **not** (#1 AND flowed) under dNBR-A, **OR**
- Spearman ρ(SBS, dNBR-A) **below the §7 floor**.

A fail is a real result, not a project failure. Legitimate P2.4 outcomes: "dNBR needs a different
normalization," "dNBR is a coarser ranker than SBS — ship with that caveat explicit," or "the continuous
B path ranks as well or better — the production input is the strong input." None reopens the firewall.

### The FM-3 discordant pair — confirm it's the KNOWN weakness, not a new one
The control's only genuine discordance is **Oak Creek (small, flowed) outranked by Toro Canyon (large,
non-flowed)** — the `× area` term, governed by C1, DO-NOT-FIX. Under dNBR-A/B, confirm the discordant
pairs are the **same** Oak/Toro signature (`discordant_are_fm3` true: every discordant pair is a
smaller flowed basin outranked by a larger one), **not** a new discordance. A different burn
normalization can move Oak/Toro for reasons unrelated to dNBR quality — check it's the *same*
discordance, not coincidental number-matching.

---

## 8. What this document freezes (never reopened after the first dNBR score)

1. Arm A binning function: published breaks `[−0.5, 0.1, 0.27, 0.44, 0.66, 1.3]` (Key & Benson 2006 /
   USGS-UN-SPIDER) **+ the 5→4 collapse** (§2).
2. Arm B transfer function: clamp `[0.1, 1.3]` → linear `[0,1]` (§3).
3. dNBR scale: **raw, not ×1000**; ×1000→raw is a fixed ingest conversion, not a P2.0 decision (§2).
4. Pre/post scene-date policy: **initial assessment, pre-first-storm** (§2).
5. Outside-burn / NoData rule, both arms: **two distinct non-covered paths** — NoData/cloud (SCL mask,
   >20% flowed-basin coverage → fail loud) AND `dNBR < 0.1` → class-15 sentinel (§4).
6. Reprojection contract: snap to **DEM grid** via explicit `dst_transform`/`dst_shape`; `assert_aligned`
   must pass; **threshold after reprojection** on the 10 m grid (§5).
7. Resample: nearest (A) / bilinear (B) (§5).
8. dNBR source: Sentinel-2 primary, MTBS documented fallback (§6).
9. Control provenance: SBS **re-run on HEAD with 7/7 lock green**, not read from stale `out/`; basin-ID
   + outlet alignment asserted before comparison (§7).
10. Primary metric + agreement criterion: 6/6 tercile + Cold Spring #1 binary + Spearman ρ ≥ 0.80
    (`scipy.stats.spearmanr`) (§7).
11. Secondary reported metrics: rank-AUC, A↔B agreement, flowed-basin coverage (§7).
12. Required human-readable side-by-side as a P2.3 deliverable (§7).
13. Numeric fail threshold (§7).

The two stated caveats carried to P2.4 (cross-domain vegetation-index→soil-severity mapping; regime-
generic 0.1 floor) are **honesty obligations, not knobs** — they are stated, never tuned (§2).

---

## 9. Owner decisions — RESOLVED

1. **§4 outside-burn rule:** **dNBR-cutoff at 0.1** — decided (keeps P2 to a pure input swap; no
   external perimeter feed). Frozen.
2. **§7 Spearman floor:** **ρ ≥ 0.80** — decided. Frozen.

All knobs pinned. The gate for P2.1 is met when this is committed + ADR (A20) landed + all five firewall
knobs, the metric, the agreement criterion, and the fail threshold on record — **no dNBR score computed
yet.**

---

## A21 — Scene-date policy: initial-assessment → extended-assessment (frozen value corrected, not tuned)

**Date:** 2026-06-16. **Scope:** the scene-date policy *only*. All other frozen knobs (§2 binning, §3
transfer, §4 outside-burn, §5 reproject/resample, §6 source, §7 metric/threshold) remain closed and
unreopened by this amendment.

### What was frozen, and why it was physically impossible
The original §2 froze: **initial-assessment dNBR, post-containment, before the 2018-01-09 storm.**
Timeline scouting (2026-06-16, before any dNBR score existed) falsified it:
- Thomas Fire ignition **2017-12-04**; the Montecito basins (the validation targets, southern Santa
  Ynez front) were **still actively burning through late December**;
- **full containment 2018-01-12** — *three days after* the 2018-01-09 debris-flow storm being
  validated against.

There is therefore **no post-containment, pre-storm window** for the basins that matter. Any scene in
the frozen window is over a still-burning / smoke-contaminated fire in the late-December low-sun-angle
period the primary GTR (RMRS-GTR-164-CD, LA-27/28) explicitly warns degrades dNBR. The frozen policy
was not merely suboptimal — it was **unsatisfiable**.

### The correction
**Corrected frozen policy: extended-assessment dNBR (next growing season).**
- **Post-fire scene:** least-cloudy Sentinel-2 L2A from the **next growing season, ~spring 2018** —
  late enough to capture delayed mortality, early enough to **predate significant chaparral
  regrowth/green-up** (target late spring, NOT late summer 2018).
- **Pre-fire scene:** least-cloudy L2A from the **same season one year prior (~spring 2017)**, to hold
  phenology constant. (Council/raster: extended assessment is a *paired* shift — moving only the post
  date and differencing against a fall-2017 pre reintroduces the seasonal-phenology confound. Both
  dates move.)
- This is the literature-standard choice (Key & Benson: extended assessment is "more representative of
  the actual severity," GTR LA-29) and is also what the **MTBS no-token fallback** yields, so the
  fallback no longer co-varies the assessment type.

### Why this is a correction, not a firewall breach — the reusable test
> **Correction-test (the reusable rule this amendment establishes):** *A frozen pre-registration value
> may be corrected if and only if (1) it is shown infeasible or incoherent on grounds **independent of
> any result**, and (2) the demonstration occurs **before any dNBR score exists.** Both conditions must
> hold; either alone is insufficient. A value that is merely producing an unwelcome number may **never**
> be changed.*

This change clears the test cleanly: the contradiction is a fixed historical fact (containment 3 days
after the storm), found by timeline scouting, with **zero knowledge of how initial-vs-extended would
move the AUC, the Spearman ρ, or any ranking** — because no dNBR has been acquired or scored.

**Auditable not-yet-known record (so "not result-driven" is verifiable, not asserted):** as of
2026-06-16, the following did not exist and were unknown to anyone when A21 was decided — any dNBR
raster for Thomas Fire, any dNBR-A or dNBR-B basin score, any dNBR↔SBS Spearman ρ, any dNBR rank-AUC,
any tercile-recovery count, the Cold Spring rank under dNBR, the FM-3 discordant-pair behavior under
dNBR. P2.0 (acquisition) has not started. This list is the timestamp: the policy was re-frozen blind
to its consequences.

### Caveat correction (supersedes the §2 part-2 "rain-wash" intuition)
The original framing's worry — that an extended scene captures "post-event hillslope rework" — is the
**wrong** caveat. The Jan-9 rain eroded already-burned hillslopes; it did not change the *burn*, and the
tool does not measure hillslope sediment (no inundation layer, by design). The **real** signal caveat
for extended assessment is the opposite mechanism: **next-season chaparral green-up can under-read
low-severity burned margins**, pulling some margin pixels below the 0.1 covered-floor. Direction of
bias: *under*-ranking low-severity, low-coverage basins. Likely immaterial to the top tercile (the
flowed basins are high-burn, 0.92–1.00 covered under SBS), but it is the caveat to carry in P2.4 — and
it is **why the per-arm flowed-basin coverage-fraction check (§7 secondary metric) is load-bearing
here**, not decorative. Confirm in P2.3, do not assume.

### Inter-arm temporal asymmetry (foreground in the side-by-side and the P2.4 claim)
The SBS control oracle derives from BAER soil burn severity, an **emergency product generated around
containment**; the dNBR arms now use an **extended/next-season** signal. The two arms therefore measure
**different temporal slices** of the same burn. This does **not** invalidate the swap test — the test
asks whether the production input (whatever is cleanly available for an un-assessed fire, which is
*typically* extended) ranks consistently with the validated control — but it is a **structural feature
of the n=1 result**, not a hidden detail. It sharpens, and is consistent with, the §0 ceiling
(*agreement with the control, not signal-correctness in the abstract*). State it plainly in the P2.3
side-by-side and the P2.4 write-up.

### Output stamping (carry to P2.4 / P5)
Every artifact that shows a dNBR-derived ranking must **plainly state the imagery date** in human
terms, e.g. "burn severity from satellite imagery acquired ~[N] months after the fire" — not buried in
metadata. (A11 framing-travels-with-artifact; Outsider: bad timing handled in the open is *evidence the
tool survives messy real fires*, the target user's actual condition — not a liability to minimize.)

### What this amendment freezes (additions to §8)
14. Scene-date policy: **extended assessment** — post ~spring-2018, pre ~spring-2017, phenology-matched
    (supersedes the initial-assessment §2 freeze via A21).
15. The correction-test as the reusable rule for any future frozen-value change.
16. Caveat correction: green-up under-read (not rain-wash) is the stated extended-assessment caveat.
17. Inter-arm temporal asymmetry stated as a structural n=1 feature; imagery date stamped on outputs.

The exact two scene IDs + acquisition dates are still recorded in `Provenance` at P2.0 and reported in
P2.4; the *assessment-window policy* is frozen here as **extended**.
