# A41 — Per-Basin Cloud Refusal + Bounded Scene Sweep (DRAFT)

**This file is a DRAFT.** It is not a DECISIONS.md entry. The vault DECISIONS.md is the
canonical governance record (per the A39 session rule); the owner transcribes this entry there
personally, edits wording as they see fit, and only then is a repo DECISIONS.md mirror owed.
Nothing in this file has been applied anywhere — in particular the pre-registration appendix
note at the bottom is drafted text only, staged nowhere, pending explicit owner approval before
it is appended to `validation/reports/P2_PREREGISTRATION.md`.

Drafted 2026-08-05.

## Context

Auto-acquire runs on Post / Laguna / Eaton refused with `dNBR NoData covers N% of basin K (>
20%)`. Investigation (2026-08-03) established that the per-basin guard (`src/pipeline.py`) was
fatal on **all** basins whenever `creeks=None` (every real, non-validation fire) — one
over-bar basin killed the entire run. Stress-divergence evidence (2026-07-28) showed these
refusals were scene-recoverable: Post ranked on the next S2 post (+3 d); Eaton on the next
Landsat post; Laguna on Landsat outright after S2 failed 6/6 October posts. The app ran the
recommended pair exactly once; alternatives were reachable only behind a manual swap expander.

Design work (2026-08-03) went through three independent specialty review passes —
correctness/silent-error, domain-epistemics/governance, integration/wiring — all three
returning sound-with-fixes; every accepted finding (the basin-erasure channel, the
staging/classification premise) is folded into what shipped. The build (Tasks 1-8 on
`feature/scene-sweep-refusal`) is complete and suite-green (454 passed, 1 pre-existing skip)
at the time this entry is drafted.

## Decision

1. **Partition semantics (creeks=None only; flowed arm verbatim-unchanged).** The
   previously-fatal all-basins NoData guard becomes a **partition** at the same frozen 0.20 bar
   (strictly `>`), run immediately after `ingest_dnbr_both_arms` on the scene-independent basin
   geometry and **before** `filter_burned_steep` — partitioning after the burn filter would let
   a clouded burned basin exit the set silently as "unburned" (the filter's own docstring:
   clouded/NoData counts as unburned), and the sweep would then optimize toward cloudy scenes
   by deletion. Clean basins are scored/ranked exactly as before; over-bar basins are refused
   individually — never scored, never ranked, never renumbered, excluded from top-K, rendered
   hatched. Per-attempt zero-clean-basins raises `GateAbort(scope="attempt")` inside
   `run_pipeline` (`pipeline.py:449,461`); the sweep catches that at its call site and records
   it as a failed attempt, then continues. When **no** attempt ever ranks, `run_sweep` returns
   `{"status": "aborted"}` — a returned value, not a raised exception. Either way, an empty
   ranking is never emitted (B1). **The flowed-basin fatal guard (creeks present) is unchanged,
   verbatim** — the pre-registered ">20% of any
   flowed basin -> errors loudly" sentence in `P2_PREREGISTRATION.md` §4 is untouched by this
   entry.

2. **Approval-gate scope (amends B4).** One human approval now covers a bounded, fully vetted
   family rather than a single pair: the recommended pair -> its pre-vetted
   `alternatives["post"]` (cap `MAX_POST_SWAPS`, clause 4) -> the other sensor's package (same
   sweep shape). Nothing outside that pre-scored, vetted family is ever tried, nothing is built
   without the one approval, and every attempt made under it is disclosed (clause 4's trail) —
   the gate is not weakened, only its scope is widened from one pair to a bounded family.

3. **Selection key is frozen and score-blind.** Among attempts, the winner is chosen by fewest
   refused basins -> lowest total nodata fraction -> earliest post date. The PRIMARY key
   (refused count) is a pure coverage property and is attempt-comparable on both terrain tiers.
   The SECONDARY key (total nodata fraction, `sweep.py`'s `total_nodata`) sums `nodata_frac` over
   `refused_basins` (the phase-1 partition) plus `arm_a["basins"]` (the clean set as scored). On
   range-front terrain that clean set is the phase-1 partition's clean half verbatim, so the two
   terms together reconstruct the full phase-1 basin count and the summation set is
   attempt-invariant. **On incised terrain, `arm_a["basins"]` is instead the set AFTER
   `filter_burned_steep` (phase 2) — a basin that is clean but not burned/steep is dropped from
   both terms, so on the exploratory tier the tiebreak's summation set is attempt-variant and
   burn-dependent, not the fixed phase-1 denominator this clause previously claimed.** This key
   may never read scores, ranks, or burn statistics beyond that nodata sum — refusal count and
   nodata fraction are coverage properties, not ranking content. Locked by a test asserting a
   monkeypatched score change cannot move the chosen attempt.
   **[OWNER-DECISION]** the owner may instead direct changing the incised-tier secondary-key sum
   to the fixed phase-1 clean set (before `filter_burned_steep`) rather than `arm_a["basins"]` —
   that would amend this clause; decide at transcription.

4. **`MAX_POST_SWAPS = 6` is an owned value**, not inherited from the stress harness (whose own
   constant, 2, is a harness budget, not a product bound). Laguna's evidence required exhausting
   6 in-window posts before Landsat succeeded; 6 is the bound the sweep tries before falling
   back to the other sensor.

5. **`GateAbort` gains a `scope` attribute** (`"fire"` default; `"attempt"` set only where the
   raise site itself knows the failure is pair-scoped). Two distinct patterns implement this:
   (a) **direct `scope=` at raise sites the build owns** — `pipeline.py:449,461` (zero clean
   basins after partition, both range-front and incised) and `ingest.py:152,156` (a pair-
   dependent footprint hole on one dNBR arm) pass `scope="attempt"` at the raise itself; (b)
   **call-site re-tagging for shared raisers** — `autoacquire/dnbr_create.py`'s seven
   `GateAbort` raises never pass `scope=` (they default to `"fire"`) because `dnbr_create` is a
   shared raiser with no way to know it is running inside a sweep, so the sweep re-tags them at
   its own call site instead (`autoacquire/sweep.py:61-68`: catch `GateAbort`, and if
   `scope == "fire"`, re-raise `GateAbort(str(e), scope="attempt") from e`, because those
   create-time aborts — 403/zone/baseline/grid — are per-**pair** by construction even though
   the raiser can't say so). The governing rule is the same for both patterns: unclassified
   aborts stay fire-scoped and stop the sweep loudly; a call site re-tags to attempt-scoped only
   when it positively knows the failure cannot recur on a different pair.

6. **Every `ranking.csv` row gains a non-gating `nodata_frac` column.** Clean basins may still
   carry up to 20% nodata scored as 0.0 burn; this column makes deflation-suspect ranks
   inspectable rather than folded silently into the score.

7. **Explicit "NOT result-blind" provenance block.** This amendment is motivated by observed
   refusals on real fires plus the stress-divergence dataset — it is owner-directed and
   informed by outcomes, on the same footing as the A39 precedent, and it is never to be
   described as an A21-style correction of a prior defect. `sweep_attempts.json` carries a
   standing provenance line stating the winning attempt was chosen by coverage only and that
   ranking content was never consulted, so clause 3's score-blindness is independently
   auditable from the written artifact, not just from this entry's text.

## Reasoning and honest cost

Refusing a whole run over one clouded basin discarded usable signal on every other basin in the
fire, and the stress evidence showed the missing scenes existed and were retrievable inside the
same pre-registered acquisition window. The partition recovers that signal without touching the
frozen formula, the frozen 0.20 bar, or the flowed-basin guard.

**Honest cost: degraded maps are new surface area this tool has not shipped before.** A ranking
that ships with some basins hatched "insufficient data" invites a careless misread — a hatched
basin looking like "assessed and low," when the truth is the opposite: a refused basin's hazard
is UNKNOWN, not low, and it could rank high if data existed. Mitigations, all load-bearing, none
optional: the banner sentence ("N of M basins could not be assessed (insufficient cloud-free
imagery). Their hazard is UNKNOWN — not low. Any refused basin could rank high if data existed;
see refused_basins.csv.") is emitted on every artifact carrying any refused basins; the map
hatches refused geometry with a legend entry visually distinct from the low end of the score
ramp; `refused_basins.csv`/`.geojson` sidecars carry only burn-independent facts (nodata_frac,
area, slope — no score/rank/burn keys, so nothing in them implies a score exists); the
ranking.csv `#` header carries the same framing so it survives being forwarded out of context.
A second, narrower cost: on incised terrain, refused records keep phase-1 basin ids while the
clean set is renumbered after the burn filter, so the two id spaces can collide numerically —
the sidecar column is named `phase1_basin_id` (never `basin_id`) specifically so that collision
is never read as a match; geometry, not id, is the authoritative join.

The sweep itself trades a small amount of runtime (extra attempts, extra network calls, one
static spinner) for coverage that would otherwise require a human to notice the swap expander,
guess which alternate might work, and re-run manually per basin — the automation discloses the
same information a careful operator already had access to, it does not add opacity.

## Status

Accepted 2026-08-0X (owner-directed). Tests: **no prior lock existed on either fatal guard arm**
— verified against the pre-refactor suite, which exercised only the non-fatal
`_dnbr_nodata_flags` helper. New locks were added during this build for both arms:
`tests/core/test_nodata_guard_locks.py` pins the flowed-basin fatal arm verbatim through the
refactor; `tests/core/test_partition_refused.py` covers the new creeks=None partition, including
the basin-erasure regression (a clouded basin over a burned incised basin must be refused, never
silently dropped or counted as a clean win). This is **not** a RED->GREEN retarget of an
existing lock — there was no existing lock to retarget, so there is nothing to reconcile against
a prior green baseline.

**Relations.**

- **Amends** the `creeks=None` extension of A20/A21 (imagery-provenance stamping): A41 adds
  per-basin refusal semantics and the sweep's imagery-header obligation (winning sensor, pre
  id/date, post id/date — now attempt-dependent) on top of the A20/A21 stamping rule, without
  changing what A20/A21 themselves require to be stamped.
- **Extends** B4 (human approval is a separate, mandatory gate) from single-pair scope to a
  bounded, disclosed, pre-vetted family scope (clause 2). The gate's mandatoriness is unchanged.
- **A39's** terrain-tier renumbering (incised phase-1 ids vs. post-burn-filter ids) is preserved
  unmodified; A41's `phase1_basin_id` naming is additive documentation of an id-space boundary
  A39 already created, not a change to A39's renumbering behavior.

## Decision-log row

| ID | Date | One-line summary |
|---|---|---|
| A41 | 2026-08-05 | `creeks=None` NoData guard becomes a per-basin partition at the frozen 0.20 bar (flowed guard unchanged, verbatim); bounded scene sweep (pair -> vetted alternates -> other sensor, `MAX_POST_SWAPS=6`) under one approval, frozen score-blind selection key, full attempt trail in `sweep_attempts.json`. |

---

## Owner-gated: drafted pre-registration appendix note (NOT applied)

Nothing below this line has been written to `validation/reports/P2_PREREGISTRATION.md` or
anywhere else. The text is drafted here for the owner to review and apply personally as an
**append-only** addition to the very end of that file — nothing above it in that file is
edited, and this addition is never described as a "correction":

> [A41 amendment note, 2026-08-0X -- appended, nothing above edited]: The flowed-basin sentence
> in §4 stands verbatim. Enforcement for creeks-absent (non-validation) fires is now specified
> by DECISIONS A41: per-basin refusal at the same frozen bar, never a whole-run abort. Not a
> correction; owner-directed, not result-blind.
