# dNBR Input-Swap Validation Finding — Wildfire Watershed Downstream-Impact Screening

**Related:** [[Post-Fire Watershed Tool MOC]] · [[VALIDATION_REPORT]] · [[P2_PREREGISTRATION]] · [[DECISIONS]] (A12, A20, A21, A23) · [[science_reference]] · [[FAILURE_MODES]] · [[limitations]]

> **📌 What this file is (read first).** This is the P2.4 write-up of the **dNBR input-swap test** —
> the parallel artifact to the SBS [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md). It documents the
> result the pre-registered P2.3 swap test (`validation/p2_3_swap_test.py`) already produced; it
> **decides nothing new, re-runs nothing, and tunes nothing** (A16). Every number traces to the
> committed, regenerable side-by-side (`validation/out/montecito_dnbr/p2_3_side_by_side.{md,csv}`)
> and the swap-test script — **not** to any narrative summary. If a run ever disagrees with these
> numbers, the *run* is the finding, not a reason to edit this file.
>
> **Within-fire relative screening ranking of watersheds warranting closer assessment — NOT a
> prediction of where debris will go. Within-fire ordinal only; not cross-fire comparable (A5/A11).**

**Validation case:** 2017 Thomas Fire → 9 January 2018 Montecito debris-flow disaster (same case as the SBS report).
**What changed:** *only the burn input* — BAER SBS → dNBR. Same AOI, same DEM, same hydrology, same delineation, same frozen `burn × slope × area` formula. Slope and contributing area are therefore **identical across all three arms**; only `mean_burn` moves.
**Swap test run:** 2026-06-17 (`validation/p2_3_swap_test.py`, committed `d68c17d`). **Finding written:** 2026-06-18.

---

## 1. Headline, verdict, and why both are true (read this section whole)

Three statements, in one breath, none of which softens the others:

**① The result is strong on the metric that maps to the tool's purpose.**
**rank-AUC = 0.9722 under dNBR-A, dNBR-B, *and* SBS — identical.** All **6/6** documented-flow basins
land in the top tercile under dNBR-A, and the overall rank correlation with the SBS control is
**ρ = 0.944** (p = 5.8e-18). On the one fire where we can check, the free, available-anywhere input
(dNBR) preserved the flowed/non-flowed discrimination — the tool's actual triage job — **as well as the
field-validated soil-burn-severity input (SBS).** This result does **not** depend on which dNBR
normalization is treated as primary.

**② The pre-registered gate FAILED.** The pre-registered primary arm (**dNBR-A**) **failed criterion 2**:
Cold Spring ranked **2, not #1**, beaten by San Ysidro Creek by a **1.03%** score gap (3.280 vs 3.314).
Criterion 2 is binary, with no escape hatch (P2.1 §7). **The gate verdict is FAIL.** It is not "narrowly
passed," not "effectively passed," not "passed the substantive criteria" — the pre-registered binary
top-rank criterion was missed, so the gate failed.

**③ The hinge — why an *identical* AUC and a *failed* gate are both correct, not a contradiction:**
rank-AUC measures whether the tool **finds the flow basins** — the separation of the 6 flowed basins from
the 30 that did not flow (fully preserved: 0.9722 under all three inputs, 6/6 recovered). Criterion 2
measures something narrower — whether the **single most fragile basin** (Cold Spring) lands at *exactly*
rank #1 (missed, by a 1.03% score gap). These are **different questions**, and one can hold while the
other fails. The honest answer to "so does the tool work?" is therefore precise: **yes for triage —
finding which watersheds warrant a closer look (AUC, the job it exists to do); not for placing the single
most fragile basin at exact rank #1 (criterion 2)** — and triage is what it is for. A documented fail is a
legitimate, valuable A12 outcome, not a project failure.

| Pre-registered criterion (gated on **dNBR-A**) | Result | Pass? |
|---|---|:--:|
| **C1** — all 6/6 documented-flow basins in the top tercile (top 12 of 36) | 6/6 in top 12 | ✅ |
| **C2** — Cold Spring is the #1-ranked basin **and** flowed (binary) | Cold Spring rank **2** (score 3.280 vs San Ysidro Creek 3.314) | ❌ |
| **C3** — ρ(SBS order, dNBR-A order) ≥ 0.80 over all 36 basins | ρ = **0.944** (p = 5.8e-18) | ✅ |
| **Overall** | one of three criteria failed | ❌ **FAIL** |

---

## 2. What was tested (pre-registered, A12) — and what a result does NOT establish

The question, pre-registered and frozen *before any dNBR score existed* (P2.1 / A20): **re-run the
validated Montecito case with dNBR instead of SBS — only the burn input changes — and ask whether the
within-fire ranking survives the swap.**

A clean pass would have established **only** that, on the single validated case, dNBR and SBS rank the
6 documented-flow basins consistently. By the same logic, this fail (and the strong discrimination
alongside it) establishes facts **about agreement with the control on one fire** — not about dNBR's
correctness as a ranker in the abstract. The only ground truth here is the same Montecito event SBS was
validated on. **Generalization is P3/P4. n = 1.** (P2.1 §0.)

The firewall held: every binning break, transfer function, coverage rule, metric, and pass threshold was
frozen in `P2_PREREGISTRATION.md` before the first dNBR score (A20). Nothing below was tuned to make a
number land.

---

## 3. The control and the two arms — and exactly what the comparison is

| Arm | Burn input | Normalization | Role |
|---|---|---|---|
| **Control** | Thomas Fire BAER SBS (objectid 3248), 4-class | A17 coverage-weighted `mean_burn`, untouched | The frozen P0.5 behavior-lock baseline |
| **dNBR-A** (PRIMARY) | Thomas Fire dNBR | Bin → 4 classes (Key & Benson / USGS-UN-SPIDER breaks), reuse `BURN_WEIGHTS` untouched | The pre-registered **gating** primary |
| **dNBR-B** (COMPANION) | Thomas Fire dNBR | Continuous dNBR clamp `[0.1, 1.3]` → linear `[0,1]` | Reported, **not** gating |

**The comparand is the P0.5-reconstructed control, not the Week-0 figures.** The SBS control here is the
reconstructed `gate.py` re-run fresh on HEAD: **36 basins, top tercile = 12, rank-AUC 0.9722, Cold Spring
(basin 6) = #1, flowed set {4, 6, 9, 14, 21, 23}.** These are **not** the Week-0 report's 32-basin /
0.987 / top-10 figures — that run's exact AOI is unrecoverable (see [[VALIDATION_REPORT]] banner and the
P0.5 reconstruction findings). The swap test re-runs this control fresh and asserts it reproduces its own
lock (Cold Spring #1, AUC 0.9722, 6/6 flowed) **before** trusting any dNBR comparison; it does. So the
0.9722 here is the like-for-like comparand for the dNBR arms — **not** to be read against the Week-0 0.987.

**The cleanest possible isolation.** Because the DEM, hydrology, and delineation are identical across all
three arms (asserted basin-by-basin: same 36 IDs, same `(row,col)` outlets), **slope and area are bit-for-
bit identical** in every column. Only `mean_burn` differs. Any rank change between SBS and dNBR is therefore
attributable **entirely to the burn term** — there is no terrain or delineation confound to disentangle.

### Provenance of the dNBR input (claim precision is mandatory — A4/A21)

- **Sensor:** Landsat-8 (OLI), **30 m** native — **not** the Sentinel-2 20 m product P2.1 §6 named as
  primary. This is the documented MTBS fallback, recorded as a **sensor caveat**, never a silent swap.
- **Assessment type:** **extended** — pre-scene `LC08…20170616` (2017-06-16), post-scene
  `LC08…20180619` (2018-06-19). The post-scene is **~6 months after the fire** (A21 imagery stamp).
- **Exactly what this is (the O4-facing line):** **dNBR computed from MTBS's validated Extended-assessment
  scene selection** — the two scenes MTBS's analysts chose for the Thomas Fire (event
  `CA3442911910020171205`), differenced with the standard NBR = (B5−B7)/(B5+B7), raw scale. It is **NOT**
  "MTBS's dNBR product" (that raster was not token-free downloadable). Do not let the looser phrasing
  ("we used MTBS dNBR") into the write-up or the O4 conversation.

---

## 4. Results

### 4.1 The basis of "AUC 0.9722, identical across arms"

rank-AUC here rests on **6 flowed vs 30 non-flowed** basins → 6 × 30 = **180 flowed/non-flowed pairs**.
AUC 0.9722 means **175 of those 180 pairs** are ordered correctly (the flowed basin scores higher). The
five discordant pairs are the **known `× area` weakness** — a larger non-flowed basin outranking a
smaller flowed one (the Oak Creek / Toro-Canyon-type signature, FM-3, governed by C1, DO-NOT-FIX). The
P2.3 FM-3 check confirmed the dNBR-A discordances are the **same** `× area` signature as the control's,
not a new failure mode introduced by dNBR.

**Read "identical across arms" correctly (statistician's note):** the AUC is identical *to the reported
precision* (0.9722 at four decimals) and rests on only **6 positives**. It means the three inputs are
**indistinguishable as flowed/non-flowed discriminators at this resolution** — not that they are
infinitely precise, exactly-equal continuous discriminators. With 6 positives, AUC moves in steps of
1/180 ≈ 0.0056; finer equivalence than that is not something n = 6 can resolve.

### 4.2 The three pre-registered criteria, individually

- **C1 — tercile recovery: PASS (6/6).** All six documented-flow basins (Cold Spring, San Ysidro, Romero,
  Hot Springs, Buena Vista, Oak) are in the top 12 of 36 under dNBR-A. Same six basins the SBS control
  recovers.
- **C2 — Cold Spring #1-and-flowed (binary): FAIL.** Under dNBR-A, **San Ysidro Creek is #1
  (score 3.314)** and **Cold Spring is #2 (score 3.280)** — a gap of 0.034, = **1.03% of the #1 score.**
  Because slope and area are identical across arms, this #1/#2 order is set **entirely by `mean_burn`:**
  dNBR read San Ysidro's burn as 0.641 vs Cold Spring's 0.564 (SBS read 0.611 vs 0.550). dNBR raised
  San Ysidro's burn by +0.031 and Cold Spring's by only +0.014 — **~2× more** — enough to transpose the
  top two. The 1.03% gap is reported as a **fact that makes the fail informative** (the failure is a
  hair's-breadth transposition, not a scrambled top of the list); it is **not** a reason the fail
  "doesn't count."
- **C3 — overall rank correlation: PASS.** ρ(SBS order, dNBR-A order) = **0.944** (p = 5.8e-18) over all
  36 basins, well above the frozen 0.80 floor. The whole ranking — not just the top — survives the swap.

### 4.3 Secondary metrics (reported, NOT gating)

| Metric | Value | Note |
|---|---|---|
| rank-AUC, per arm | SBS 0.9722 / dNBR-A 0.9722 / dNBR-B 0.9722 | identical to four d.p. (see §4.1 on resolution) |
| ρ(SBS, dNBR-A) | **0.944** | criterion 3 (gating) |
| ρ(SBS, dNBR-B) | **0.954** | **not meaningfully different from A's 0.944 at n = 36** (see below) |
| ρ(dNBR-A, dNBR-B) | **0.992** | A↔B agreement — robustness to the *normalization choice*, **not** signal validation |
| flowed-basin coverage (dNBR-A) | all ≥ 0.866 (min: Cold Spring 0.866) | confirms the P2.1 §4 / A21 assumption — flowed basins stay well-covered under dNBR |
| A23 `low_coverage` count | SBS 26/36 → dNBR 29/36 | dNBR flags 3 more (weaker instrument) — **expected (A23), not a discrepancy** |

**On ρ(SBS, dNBR-B) = 0.954 vs ρ(SBS, dNBR-A) = 0.944 — do not over-read this.** A 0.010 difference in
rank correlation over **36 basins is within sampling noise**; the two arms are **statistically
indistinguishable** as correlates of the SBS order at n = 36. "B correlates with SBS slightly better than
A" is **not** a supported reading of this number, and nothing in this finding should be taken to mean it.

**A↔B agreement (ρ = 0.992) is blind to signal quality by construction.** Both arms ingest the *same*
dNBR raster; high A↔B agreement shows the ranking is robust to *which normalization function* you pick,
not that the dNBR *signal* is a good proxy. Both arms inherit any dNBR-vs-SBS signal error identically.
Do not oversell A↔B stability as signal validation.

### 4.4 The mechanism of the miss (facts, not interpretation)

The #1/#2 transposition is a pure `mean_burn` effect, isolated by the identical-slope/area design:

| Basin | SBS burn → dNBR-A burn (Δ) | SBS rank → dNBR-A rank |
|---|---|---|
| San Ysidro Creek (b9) | 0.611 → 0.641 (+0.031) | 2 → **1** |
| Cold Spring Creek (b6) | 0.550 → 0.564 (+0.014) | 1 → **2** |

dNBR raised both basins' burn slightly (it reads marginally hotter than SBS here), but raised San Ysidro
**~2× more** than Cold Spring, and that 0.017 differential in the burn term — multiplied through the
identical slope and area — is the entire 1.03% score gap that flips the top two.

---

## 5. The open question: which arm is primary — DEFERRED to P3 (not resolved here)

dNBR-B (the pre-registered companion) **reproduces Cold Spring at #1** and would have passed criterion 2;
A↔B agreement is ρ = 0.992. This is a genuinely interesting observation: it suggests the **binning step**
(A's 5→4 collapse), **not dNBR-as-an-input**, is what loses the top rank — the continuous arm, fed the
same raster, keeps it.

**But which arm should be primary is exactly the question one fire cannot answer.** A-beats-B or
B-beats-A on a single 1.03% transposition of a single basin is within noise (and §4.3: the two arms'
correlation with SBS is statistically indistinguishable at n = 36). Crowning either arm off n = 1 — and
especially promoting B *after seeing it pass* — is precisely the goalpost-move the pre-registration
firewall exists to prevent. dNBR-A was the pre-registered primary; it stays the primary on the record.

**Therefore the primary-arm question is deferred to P3** (a fresh fire), where multi-fire evidence can
adjudicate it pre-registered and clean. If B is genuinely the better normalization, P3 shows it on
evidence, and the promotion is then unimpeachable. **This finding reports both arms; it does not pick,
and it does not recommend shipping B.**

---

## 6. Honest positioning — the O4 line (n = 1, stated, not buried)

> The tool is **validated on SBS** (the 2017 Thomas → 2018 Montecito case). On that same validated fire,
> the production dNBR input — **computed from MTBS's validated Extended-assessment scene selection** —
> **preserved the flowed/non-flowed discrimination as well as field-validated SBS (rank-AUC 0.9722) and
> recovered all six flow basins**, while **failing the pre-registered binary top-rank criterion by a
> ~1% transposition of the top two basins**; the continuous normalization reproduced the SBS top rank,
> which is an open question for a second fire. This is **one fire** — the swap confirms *agreement with
> the validated control on that case*, not signal-correctness in general.

The honest answer to "how many fires?" stays **one**. The precision is the credibility, not a hedge:
the target user is an under-resourced-state emergency manager whose fires *never* get the field-validated
BAER input — the fact that the free input ranks the basins much as the gold-standard input did, on the
one fire we can check, **is the pitch**, stated with its ceiling intact.

---

## 7. Limitations and caveats carried by this finding

1. **n = 1 ceiling.** This is agreement with the SBS control on a single fire — not validation of dNBR
   as a ranker in the abstract. Transferability to other ranges, rain regimes, and fire types is
   unestablished. Generalization is P3/P4.
2. **30 m burn signal on a 10 m grid (coarse-burn limitation, new with the Landsat fallback).** The dNBR
   came from Landsat (30 m), not the Sentinel-2 20 m P2.1 assumed. After reprojection to the 10 m
   hydrology grid, each 30 m burn value is *replicated* (not interpolated) into nine 10 m cells under the
   nearest-neighbor resample. Values are therefore **not biased**, but the burn term's **effective
   resolution is 30 m regardless of the 10 m terrain grid** — so the **smallest basins have fewer
   independent burn samples**, i.e. the burn term is coarser than the slope and area terms for the
   smallest catchments. Practitioner consequence: trust the burn component least on the smallest basins.
3. **Cross-domain proxy (A20 caveat 1).** dNBR is a vegetation/canopy-change index; it is being mapped
   onto an encoding built for *soil* burn severity (SBS — hydrophobicity, the hydrologic cause). These
   are different physical axes. dNBR is a defensible screening **proxy**; it is **not** reconstructing
   SBS. This is *why* a swap can degrade even when nothing is mis-coded.
4. **Green-up under-read (A21 caveat).** Extended-assessment dNBR (next growing season) can under-read
   low-severity burned margins as chaparral regreens, pulling some margin pixels below the 0.1 covered
   floor; the mid-June post-scene makes this somewhat more live. **Confirmed immaterial to the flowed
   basins here** — all six stayed ≥ 0.866 covered under dNBR-A (§4.3) — but it is the direction of bias
   to carry for low-severity, low-coverage basins on other fires.
5. **Inter-arm temporal asymmetry (A21).** The SBS control derives from a BAER product generated *around
   containment*; the dNBR arms use a *next-season* signal. The two measure different temporal slices of
   the same burn. This does not invalidate the swap (the test asks whether the *typically-available*
   extended input ranks consistently with the control), but it is a structural feature of the n = 1
   result, not a hidden detail.
6. **The `× area` weakness is unchanged, not introduced.** The five discordant pairs under dNBR are the
   same Oak/Toro `× area` signature as the SBS control (FM-3 / C1). dNBR did not create a new failure mode.

> **A post-hoc observation for P3 to formalize (explicitly labeled post-hoc — NOT a reinterpretation of
> this verdict).** That rank-AUC was fully preserved while a binary exact-#1 check failed by 1% is an
> argument that, for a *screening* tool, a separation metric (AUC) may be a better fitness measure than a
> single binary top-rank check. That is **open reasoning for P3 to pre-register and decide** — it does
> **not** convert this gate's fail into a pass. The binary criterion was binary; the gate failed.

---

## 8. Bottom line

A deliberately simple `burn × slope × area` screen, re-run on the validated Montecito case with the free,
available-anywhere **dNBR** input in place of field-validated **SBS** — *only the burn input changed* —
**preserved the flowed/non-flowed discrimination as well as SBS (rank-AUC 0.9722 under all three inputs,
6/6 flow basins recovered, ρ = 0.944)**, and **failed the pre-registered binary top-rank criterion** when
the primary binning arm transposed the top two basins by a **1.03%** burn-driven margin. **Gate verdict:
FAIL** — a legitimate, documented A12 result.

What this supports: **dNBR's fitness for the tool's actual job — triage / relative ranking — on the
validated fire.** What stays open: the specific normalization (the continuous arm reproduced the SBS top
rank; which arm is primary is **deferred to P3**, undecided here) and transferability to other fires
(P3/P4). **No shipping decision beyond that** is earned by this finding — in particular, **not** "switch
to dNBR-B."

### Numbers in this finding (all traceable, stated to consistent precision)

All figures regenerate from `validation/p2_3_swap_test.py` and are read verbatim from the committed
side-by-side (`validation/out/montecito_dnbr/p2_3_side_by_side.{md,csv}`) and `provenance.json`:
AUC = 0.9722 (all three arms); ρ(SBS,A) = 0.944; ρ(SBS,B) = 0.954; ρ(A,B) = 0.992; Cold Spring 3.280 vs
San Ysidro 3.314, gap = 1.03%; A23 low_coverage 26→29; flowed-basin dNBR-A coverage ≥ 0.866. (The
side-by-side `.md` is the human-readable companion to this finding; it is gitignored and regenerates from
the script per OUT-CLEANUP.)
