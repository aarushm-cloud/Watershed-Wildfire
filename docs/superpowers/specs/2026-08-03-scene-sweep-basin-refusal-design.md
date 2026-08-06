# Scene Sweep + Per-Basin Cloud Refusal — Design (rev 2, post-review; pending owner ratification)

Status: DRAFT rev 2. Rev 1 was reviewed by three independent specialty passes (correctness /
silent-error, domain epistemics / governance, integration / wiring); all three returned
sound-with-fixes. Every accepted finding is folded in below; verification spot-checks
confirmed each load-bearing claim against code. No code written.

Owner rulings already taken: bounded-sweep approval scope; automatic loudly-labeled degraded
mode; shared boundary placement + CLI exposure. Threshold **0.20 stays frozen**. Flowed-basin
fatal guard stays verbatim.

## 1. Problem (evidence-backed)

Auto-acquire runs on Post / Laguna / Eaton refused with `dNBR NoData covers N% of basin K
(> 20%)`. Investigation (2026-08-03) established:

- The per-basin guard (`src/pipeline.py:414-421`) is **fatal on ALL basins** when
  `creeks=None` (every real fire). One over-bar basin kills the whole run.
- The nodata is true raster nodata (pre ∪ post cloud-mask union; `src/ingest.py:149-160`);
  below-floor/unburned pixels do NOT count.
- The refusals were **scene-recoverable** (stress divergence data, 2026-07-28): Post ranked
  on the next S2 post (+3 d); Eaton on the next Landsat post; Laguna on Landsat outright
  (S2 failed 6/6 October posts — SCL class-3 dark-pixel/shadow false positives on incised
  terrain are the leading explanation).
- The app (`app.py:523`) runs the recommended pair exactly once; alternatives exist but only
  behind a manual swap expander (`app.py:472-497`).
- Threshold sensitivity (measured, one-basin deflation on real rankings; NoData scores as
  0.0 burn, A17 `src/score.py:19-20`): at 35% nodata a basin drops a median 12–16 ranks on
  incised fires. Raising the threshold is not an option.

## 2. What changes (two coupled features)

### F1 — Bounded scene sweep (boundary layer)

One human approval covers: the recommended pair → its pre-vetted `alternatives["post"]`
(cap `MAX_POST_SWAPS = 6` — an **owned new value**; the stress harness's constant is 2, a
harness budget, and Laguna's evidence shows 6 in-window posts needed exhausting) → the other
sensor's package (same sweep shape). First attempt with **zero refused basins** wins
outright. If none is fully clean, the best attempt is kept: **fewest refused basins → lowest
total nodata fraction → earliest post date** (selection key; see §7 for its epistemic
constraints). Every attempt is recorded in a **written artifact** (§8), not just the UI.

### F2 — Per-basin refusal (src/; owner-ruled A41 amendment)

`creeks=None` case only: the fatal all-basins guard becomes a **partition** at the frozen
0.20 bar. Clean basins are scored/ranked exactly as today; over-bar basins are refused
individually — never scored, never ranked, excluded from top-K, rendered hatched with
binding wording (§9). Zero clean basins in an attempt → recorded, sweep continues; zero
clean basins in the BEST attempt → `GateAbort` (never an empty ranking, B1). The partition
applies to EVERY creeks=None dNBR run, including the app's upload path and `run.py` — those
paths get refusal-with-rendering but NO sweep (there is no scene family to retry; the
operator swaps the uploaded raster instead).

**Partition point (closes the basin-erasure channel — reviewer-unanimous blocker):**
the partition runs immediately after `ingest_dnbr_both_arms`, on the **scene-independent
basin geometry** — range-front: the stage-2c basins; incised: the **phase-1** geometry
records — and **BEFORE `filter_burned_steep`**. Rationale: `filter_burned_steep` computes
`burn_frac` from Arm-A weights where cloud ⇒ weight 0 (its own docstring: "clouded/NoData
counts as unburned", `src/subbasins.py:139-151`), so partitioning after it lets a clouded
burned basin exit the set silently — absent, not refused — and the sweep would then
*optimize toward* cloudy scenes ("0 refused" by deletion). Partition-first means: clouded
basins are refused before the burn filter can hide them; the burn filter then runs on clean
basins only (its drops are genuine low-burn signal, as today); and "fewest refused" is
compared on an **identical phase-1 denominator across attempts** (same DEM ⇒ same geometry),
restoring comparability.

**Frozen-text integrity:** the P2 pre-reg sentence (">20% of any *flowed* basin → errors
loudly", `validation/reports/P2_PREREGISTRATION.md` §4) is untouched — flowed basins (creeks
present) keep the fatal guard verbatim. The creeks=None all-basins fatal arm was unratified
engineering (the code's own comment anticipates this revisit: "A P4 truth fire must widen
the guard or pre-screen the scene", `pipeline.py:424-427`); the pre-reg's own per-basin
phrasing ("for that basin rather than ranking it") makes the partition arguably MORE
faithful to its letter. Governance in §11.

## 3. Architecture

```
acquire.py                — build_fire_config SPLIT (new seam; compat wrapper kept):
                              stage_fire(bbox, out_dir, name)      grid + DEM + buildings +
                                                                   partial manifest; NO dNBR
                              attach_dnbr(fire, dnbr_path)         assert_raw_dnbr + manifest
                                                                   dnbr_stats
                            build_fire_config = stage_fire + attach_dnbr (existing callers
                            unchanged, byte-equivalent behavior)

autoacquire/sweep.py      — NEW: run_sweep(bbox, ignition, containment, out_dir, *, name,
                            greenup_days, max_post_swaps=6, sensors=("S2","Landsat"),
                            contour_m) -> SweepResult
                            status vocabulary: clean | degraded | aborted  (NEVER "refused" —
                            that string is taken by the terrain-refusal status,
                            pipeline.py:249-263, app.py:53-54)

autoacquire/scene_select.py — select() gains keyword-only sensors=("S2","Landsat");
                            validates sensors ⊆ {"S2","Landsat"} and non-empty, GateAbort
                            otherwise (a wrong string would silently query the Landsat STAC:
                            scene_select.py:303 branches `== "S2"` else Landsat). Default is
                            byte-equivalent to today (verified: loop at :504, S2-first break
                            at :586; all four callers keyword-safe; monkeypatch sites target
                            _search_scenes, unaffected)

src/grids.py              — GateAbort gains an optional scope attribute:
                            GateAbort(msg, scope="fire"); default "fire" (conservative:
                            unclassifiable aborts stop the sweep LOUDLY, never get retried
                            past). scope="attempt" is set ONLY at scene-dependent raise
                            sites (§5). Message/raise behavior unchanged everywhere.

src/pipeline.py           — creeks=None: partition (at the §2 partition point) instead of
                            fatal guard; result gains refused_basins; flowed path unchanged.
                            Order stated once (silent-error defense): delineate →
                            [incised: phase-1 geometry] → ingest → PARTITION → [incised:
                            filter_burned_steep on clean set + renumber] → stage_2e_score on
                            clean set only → add_intensity_rank on clean set only →
                            outlets rebuilt from the final clean set. Refused basins are
                            never scored, never ranked, never renumbered — they keep the ids
                            the partition saw, with geometry captured at partition time.

src/outputs.py            — write_dnbr_outputs(..., refused=None): refused defaults None;
                            ALL new output (sidecar, banner sentence, provenance counts) is
                            emitted ONLY when refused is non-empty — a zero-refused run's
                            ranking.csv + basins.geojson stay byte-identical to today
                            (locked by test, §10). basins.geojson remains CLEAN-ONLY (all
                            existing consumers safe: app.basin_rows sort would TypeError on
                            two rank-None rows, app.py:94; compare_rankings would corrupt
                            spearman/only_a/only_b). Refused geometry gets its own sidecar:
                            refused_basins.geojson (features: basin_id, nodata_frac, reason,
                            area_km2, mean_slope — burn-independent facts only; NO
                            score/rank/burn keys) + refused_basins.csv (same columns).
                            Both map renderers consume it (§9).
                            basins.geojson provenance member gains refused_count /
                            n_basins_total + a schema note.
                            A21 stamping: ranking.csv header + map banner gain the winning
                            imagery line — sensor, pre id/date, post id/date (pre-reg A21:
                            imagery date "plainly stated... not buried in metadata"; the
                            sweep makes the post date attempt-dependent, so this becomes
                            load-bearing).
                            Every ranking.csv row gains a non-gating nodata_frac column
                            (A23 diagnostic precedent): clean basins may carry ≤20% nodata
                            scored as 0.0 burn; deflation-suspect ranks must be inspectable.

app.py                    — generate flow calls run_sweep inside `if approve:` under ONE
                            static st.spinner; ZERO st.* calls mid-sweep (preemption safety,
                            app.py:604-606 rationale). SweepResult (scalar trail, refused
                            metadata, winner quicklook BYTES read before rmtree —
                            app.py:315-317 pattern) stored via screen_box.clear()/update()
                            stamped with inputs_key. Idempotence guard: skip the sweep when
                            box.get("inputs") == inputs_key and kind is terminal (a queued
                            second Approve click must not re-run ~14 network attempts).
                            gen_box.pop("burnmap") on sweep completion — the pre-approval
                            quicklook may belong to a LOSING pair; the winner's quicklook is
                            RE-READ from its attempt dir (never re-downloaded). Attempt
                            trail + degraded banner render from the box on every rerun.

autoacquire/autoacquire_run.py — CLI: --max-swaps N (default 6); --max-swaps 0 = exactly
                            today's single-attempt run INCLUDING no sensor fallback.
                            --contour-m M (default 150) so the CLI can pass what run_sweep
                            accepts — without it every high-terrain fire dies fire-scoped
                            on A25, and the app would be the only usable entry point.
                            --approve remains the only gate. autoacquire_result.json slim
                            filter extended to strip "result" (today it strips only
                            pipeline/masks, autoacquire_run.py:97 — a SweepResult would
                            str()-mangle ndarrays into multi-MB JSON). Attempt records
                            serialize as scalars only (§8 shape).
```

## 4. Data flow (one fire, worst case)

select(S2) → approve → **stage_fire once** (fire-scoped aborts here: DEM fetch, zone
coverage, Overpass) → attempts loop: [S2 pair₀, alt_post₁…₆, then select(Landsat) → its
pair₀, alt_post₁…₆] — each attempt: create_dnbr into `attempts/attempt_NN/` → attach_dnbr →
run_pipeline → partition → outcome recorded. Stop on zero-refused. Else best attempt =
fewest refused → lowest total nodata → earliest post. Ranked attempts write their outputs
into their own attempt dir; at sweep end the **winner's artifacts are promoted (copied) to
the fire-level out_dir** — dnbr tif, provenance, quicklook, ranking.csv, basins.geojson,
refused sidecars, maps — plus sweep_attempts.json. Losing attempts remain quarantined under
attempts/ (A39/A40 stale-artifact-purge precedent: the fire-level dir contains exactly one
coherent pair's artifacts). In-memory the sweep retains **paths + refusal metadata only** —
never full pipeline result objects (per-basin full-grid masks ≈ GB-scale on 126-basin
fires).

## 5. Attempt-outcome classification (fail-loud preserved)

Mechanism: `GateAbort.scope` + call-site wrapping. Anything not explicitly attempt- or
sensor-scoped is fire-scoped by default — unclassifiable failures stop the sweep loudly.

| Outcome | Class | Sweep behavior |
|---|---|---|
| ranked, 0 refused | clean | stop, win |
| ranked, ≥1 refused | degraded candidate | record, continue |
| zero clean basins after partition (scene-dependent) | degraded candidate (worst) | record, continue; sweep aborts only if the BEST attempt is zero-clean |
| GateAbort from create_dnbr (download/403, zone pair, baseline floor) | attempt-scoped (call-site) | record, continue |
| GateAbort from ingest alignment (`pipeline.py:400` path) | attempt-scoped (scope tag) | record, continue |
| GateAbort from a mid-sweep per-sensor select() (MPC 403 during mask reads, `scene_select.py:359-436`; the stress log's observed DoS mode) | sensor-scoped | record {sensor, outcome}, skip sensor, continue |
| GateAbort from stage_fire (DEM, buildings, coverage) | fire-scoped | abort sweep, surface verbatim |
| GateAbort from run_pipeline DEM-deterministic stages (contour A25, terrain A39 SBS/creeks, FM-1 master outlet, WBT alignment) | fire-scoped (default scope) | abort sweep, surface verbatim |
| any non-GateAbort exception | fire-scoped | abort, surface verbatim (A8) |

Rationale correction from rev 1: fire-scoped run_pipeline aborts are fire-scoped because
they are **DEM-deterministic and will recur identically on every attempt** — NOT because
they precede the loop (hydrology runs inside run_pipeline, per attempt; a contour abort
surfaces on attempt 1 and short-circuits the sweep there). Only the first sensor's
pre-approval select() surfaces failures to the user as today; hydrology re-running per
attempt is accepted for v1 (a hoist is a pipeline-structure change — deferred, D0).

## 6. Basin-identity guarantees (silent-error defenses)

- Same staged DEM per fire ⇒ identical phase-1 geometry across attempts (delineation is
  burn-independent); the partition denominator is therefore attempt-invariant.
- Refused records carry basin_id + geometry captured at partition time from the same
  in-memory records — no re-derivation, no remapping.
- Incised renumbering (A39 trap): `filter_burned_steep` renumbers only the CLEAN survivors;
  refused basins keep partition-time ids. On incised fires those two id spaces can carry
  COLLIDING VALUES (renumbered clean id 7 ≠ phase-1 refused id 7) — so the refused sidecar's
  id column is named `phase1_basin_id` (never `basin_id`), its header comment states the id
  space, and no artifact ever mixes the two columns in one table. The geometry is the
  authoritative join; the map renders from geometry, not ids.
- Both arms share ONE valid footprint (`ingest.py:141-160`) ⇒ one refusal verdict per basin.
- `n_ties`, intensity ranks, metrics, outlets: all computed on the clean set only (§3 order).

## 7. Selection-key epistemics (bias defenses)

- The selection key (fewest refused → lowest total nodata → earliest post) is **frozen
  score-blind** in the A41 entry: it may never read scores, ranks, or burn statistics.
  Refusal count on the attempt-invariant phase-1 denominator is a pure coverage property.
- Within-basin sampling bias remains for CLEAN basins (≤20% nodata scored as 0.0 burn) —
  disclosed per-row via the nodata_frac column; the 20% bar bounds it (frozen).
- Coverage-over-recency ordering (total-nodata above earliest-post) is deliberate: a
  marginally later post inside the frozen window costs green-up signal gradually, while
  worse coverage costs refused basins discretely; the window ceiling (containment +
  greenup ≤ 180 d, GateAbort-enforced) bounds the recency cost. The attempt trail shows all
  dates so a late-window winner is visible. `greenup_days` extension stays a deliberate
  per-fire operator act — never a sweep default.
- Window integrity is verified: alt_post ⊂ posts ⊂ coarse-filtered pool ⊂ [containment,
  post_end]; the Landsat fallback runs the identical window dict (`scene_select.py:496-529`).
  The sweep cannot reach outside the frozen post window.

## 8. The attempt trail (written artifact)

`sweep_attempts.json` beside ranking.csv (and echoed in autoacquire_result.json): per
attempt `{sensor, pre_id, post_id, post_date, outcome, refused_count, n_basins_total,
total_nodata_frac}`; `chosen = {sensor, pre_id, pre_date, post_id, post_date}`; plus one
provenance line: "chosen by coverage only (fewest refused → lowest total nodata → earliest
post); ranking content never consulted." Scalars only — no result objects, no geometry, no
ndarrays (the `_js` str() fallback would mangle them silently).

## 9. Refusal rendering (binding wording)

- Banner (app + map + ranking.csv `#` header — framing travels with the CSV alone, A11/A34
  precedent): "N of M basins could not be assessed (insufficient cloud-free imagery). Their
  hazard is UNKNOWN — not low. Any refused basin could rank high if data existed; see
  refused_basins.csv." Emitted only when N > 0.
- Map: refused basins hatched from refused_basins.geojson, legend entry adjacent to the
  color ramp and visually distinct from the low-score ramp end (refused basins are
  plausibly hazard-skewed — SCL shadow FPs concentrate on steep terrain; absence-reads-as-
  safe is the exact failure this forbids).
- Top-K numbered markers: clean basins only; the banner's UNKNOWN sentence is the mitigation
  for the headline blind spot.
- run.py / CLI prints report "N ranked, M refused", never a bare basin count.

## 10. Testing

Ordering matters — locks BEFORE refactor:
1. NEW flowed-path fatal lock FIRST (creeks present, flowed basin >20% → GateAbort,
   message text pinned). **No lock exists today on either fatal arm** (verified: the suite
   only tests the non-fatal `_dnbr_nodata_flags`, `tests/acquire/test_dnbr_pipeline.py:72,92`)
   — this lock protects the frozen pre-reg sentence through the refactor. The A41
   adjudication note records "no prior fatal-arm lock existed; new locks added" — there is
   no RED→GREEN retarget (rev 1 cited a phantom test).
2. Partition tests: clean/refused split, refused never scored/ranked/renumbered, id
   integrity incised + range-front, zero-clean attempt recorded, zero-clean best → abort.
3. Basin-erasure regression: an incised fixture where cloud sits over a burned basin —
   partition-first must refuse it; asserting it neither silently vanishes nor wins the
   attempt "clean".
4. Projection-identity lock (supersedes rev 1's byte-identity, which the nodata_frac column
   makes impossible): a zero-refused creeks=None run must produce identical rank order,
   identical scores, and byte-identical values for every PRE-EXISTING column and header
   line. Permitted additions, exhaustively: the nodata_frac column (all dNBR runs); the
   imagery-date header line (only when pair provenance exists — the upload path has none);
   refusal banner + provenance counts (only when refused is non-empty). Anything else
   appearing in the diff fails the lock.
5. Sweep classification: one test per §5 row (scope tags, sensor-skip, zero-clean-best
   abort, unclassifiable-defaults-to-fire).
6. Selection key: deterministic tie-breaks; score-blindness (a monkeypatched score change
   must not change the chosen attempt).
7. select(sensors=): default-equivalence lock on hermetic fixtures; invalid/empty sensors
   fail loud.
8. Serialization: sweep_attempts.json + autoacquire_result.json round-trip, no ndarray
   reprs; winner-artifact promotion (fire-level dir contains exactly the winning pair's
   artifacts).
9. App smoke via existing test_app_generate patterns: idempotence guard, burnmap pop,
   trail/banner survive rerun.
Suite fully green before handoff; no oracle files edited (A16).

## 11. Governance (A41)

- New entry **A41** in the **vault** DECISIONS.md (canonical; repo mirror owner-owed per the
  A39 session rule): Context / numbered Decision clauses / Reasoning + honest cost / Status
  + relation arrows + dated log row. Clauses must cover: (1) creeks=None partition semantics
  (flowed fatal arm verbatim-unchanged); (2) the B4 approval-gate scope change — one
  approval covers the bounded vetted family (pair → alternates → other sensor), attempts
  disclosed; (3) the frozen score-blind selection key; (4) MAX_POST_SWAPS=6 as an owned
  value; (5) the GateAbort scope attribute; (6) the nodata_frac ranking column; (7) explicit
  "NOT result-blind" provenance block (motivated by observed refusals + stress data — A39
  precedent; never described as an A21-style correction).
- Append-only amendment note in `P2_PREREGISTRATION.md` cross-referencing A41: flowed
  sentence untouched; creeks=None enforcement now specified by A41. The pre-reg is never
  edited in place and this is never called a "correction."
- Docs touch list: README.md:44-45, :140, :277; docs/ALGORITHMS.md:82; run.py basin-count
  print; compare_rankings semantics comment (refused basins change only_a/only_b meaning);
  DECISIONS log row.

## 12. Out of scope

Threshold value; flowed-basin guard; SCL mask composition (frozen pre-reg D); selector
rubric; mixed-sensor pairs; parallel attempt execution; hydrology hoisting/caching across
attempts (deferred, D0); stress-harness refactors (it keeps its own loop).

## 13. Review provenance

Three independent review passes (2026-08-03): correctness/silent-error, domain-epistemics/
governance, integration/wiring. Verdicts: 3× sound-with-fixes. Unanimous blockers folded:
basin-erasure channel (§2 partition point), staging/classification premise (§3 seam split +
§5 scope mechanism). All other accepted findings folded where cited. Spot-check
verifications: subbasins.py:139-151 docstring; acquire.py stage order; app.py:94 sort;
MAX_POST_SWAPS=2; bare GateAbort; slim filter.
