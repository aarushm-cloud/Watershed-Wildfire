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
