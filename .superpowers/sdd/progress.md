# Scene Sweep + Per-Basin Refusal — Execution Ledger

**READ THIS FIRST in any session working this branch.** Then read repo `CLAUDE.md` (Tier-1
rules), then the spec, then the plan. Spec > plan on conflict — HALT and reconcile.

- Worktree: `~/Documents/Wildfire-Watershed-sweep` · branch `feature/scene-sweep-refusal` (off `fb8b3ee`)
- Spec: `docs/superpowers/specs/2026-08-03-scene-sweep-basin-refusal-design.md` (rev 2, 3-reviewer-hardened)
- Plan: `docs/superpowers/plans/2026-08-03-scene-sweep-basin-refusal.md`
- Env: `~/miniconda3/envs/wildfire-watershed/bin/python` · suite: `python -m pytest tests/ -q`
- ⛔ STAGE ONLY — never `git commit`/`git push`; the owner commits.
- ⛔ Frozen: 0.20 bar (strictly `>`), score formula, flowed fatal guard, S2_BAD_SCL, selector rubric.
- Rule: every task ends suite-GREEN and staged. **Any task boundary is a safe session stop; mid-task is not.** If resuming and the tree has unstaged mid-task work, finish or revert that task before proceeding.

## Task status

| # | Task | Status | Suite | Notes |
|---|------|--------|-------|-------|
| 1 | Pre-refactor locks (`test_nodata_guard_locks.py`) | DONE | 371 | locks pin CURRENT fatal behavior; green-on-HEAD |
| 2 | GateAbort.scope + partition in pipeline | DONE | 378 | partition BEFORE filter_burned_steep (erasure channel); refused keep PHASE-1 ids on incised (Task 3 renders `phase1_basin_id`) |
| 3 | outputs.py refusal artifacts | DONE | 390 | do with 2 in one sitting if possible (rendering completes the honesty story) |
| 4 | acquire.py seam split | DONE | 396 | build_fire_config = assert_raw_dnbr(dnbr_path) then attach_dnbr(stage_fire(...), dnbr_path) -- CF-9 fail-before-fetch restored per controller ruling (review fix); attach_dnbr's manifest read/parse now translates missing/corrupt manifest to GateAbort (review fix) |
| 5 | select(sensors=) | DONE | 401 | independent |
| 6 | autoacquire/sweep.py | DONE | 423 | +22 hermetic tests; review fix: create_fn GateAborts re-tagged attempt-scoped at the call site (dnbr_create raises them fire-scoped by default) + nodata_frac read directly, never defaulted (all seams injected); per-attempt manifest COPY is the attach_dnbr handoff; promote purges conditional artifacts one level up; `chosen` carries pre_date (spec §8) |
| 7 | app.py wiring (+ upload-path refused= passthrough) | DONE | 444 | run_generated_screening now sweep-backed (approve triggers run_sweep, not a single pair); idempotence guard on inputs_key+kind; degraded banner + trail expander + refused hatching render path-agnostically off screen["fc"]["provenance"]/screen["refused_geojson"] (upload path gets it too); "chosen" preferentially read from sweep_attempts.json (carries pre_date; the return value's copy doesn't); review fix round 1 (3 Important): greenup_days now threads through Approve to run_sweep; test_incised_disclaimer.py registry now walks autoacquire.sweep.run_sweep's real writer call site (was a coverage hole + a false claim in my report that test_sweep.py covered it); refused_basins.csv read before rmtree on both paths + download button (banner referenced a file the app had already deleted) |
| 8 | CLI flags + JSON slim | DONE | 454 | `run_autoacquire`/argparse wrapped in a new `main(argv=None)` (was inline under `if __name__`) purely so tests can invoke it directly with monkeypatch, same style as the other seams; `--max-swaps 0` routes to legacy `run_autoacquire` unchanged (no new kwargs), else routes to `sweep.run_sweep(..., max_post_swaps=args.max_swaps, contour_m=args.contour_m)`; `--contour-m` default 150.0, sweep path only; slim JSON filter now also strips `"result"`; print block added for `clean`/`degraded` (chosen pair + refused count, degraded also prints the hazard-unknown/refused_basins.csv pointer) and `aborted` (message + attempt count); note `out["chosen"]` (the in-memory return value) has no `pre_date` — only `sweep_attempts.json`'s own copy does (Task 6 D4) — so the CLI print only uses fields the return value actually carries |
| 9 | docs + A41 draft (pre-reg note OWNER-GATED) | DONE | 454 | doc-only, count unchanged (454 + 1 pre-existing skip); README (ranking.csv bullet, Output line, new auto-acquire bullet) + ALGORITHMS.md (after dNBR-seam paragraph) got the refusal/sweep sentences verbatim; stress_divergence.py got the compare_rankings comment above `out = {`; A41_DRAFT.md written full per spec §11 (1)-(7) + owner-gated pre-reg appendix note drafted inside it, NOT applied to P2_PREREGISTRATION.md |

**ALL 9 TASKS DONE. Build complete, suite green (454 passed / 1 pre-existing skip), everything staged, nothing committed.**

## Session log
- 2026-08-03: scaffold created (worktree, spec+plan copied in, this ledger). Design/reviews/spec done in prior session on claude-fable-5; build not started. Recommended build model: fable-5 for T1–T6, downshift ok for T7–T9.
- 2026-08-05: Task 9 (docs + A41 draft) done. Full build (T1-T9) complete.

## Owed to owner at the end
1. **Review + commit staged work** (this worktree, `feature/scene-sweep-refusal`, off `fb8b3ee`; nothing committed by any task).
2. **Transcribe `docs/superpowers/A41_DRAFT.md` → vault DECISIONS.md** (canonical) as entry A41 + its log row; edit wording as desired — it is a draft, not final vault prose.
3. **Approve + apply the pre-reg appendix note** drafted at the bottom of `A41_DRAFT.md` — append-only, to the END of `validation/reports/P2_PREREGISTRATION.md`. Not staged, not applied by any task (owner-gated).
4. **Repo DECISIONS.md mirror** — once the vault entry is final, mirror it into a repo DECISIONS.md (none exists yet in this tree).
5. **Optional acceptance re-run**: Laguna via the app — expect Landsat-clean or degraded-with-hatching, never a dead end (this is the scene-recoverable case that motivated the build).

== BUILD COMPLETE 2026-08-06 ==
All 9 tasks + final whole-branch review (fable) + fix wave + re-review: CLEAN. Suite 457 passed / 1 expected skip (controller-verified). 28 files staged, nothing committed.
Owner actions owed: (1) review + commit staged branch; (2) transcribe docs/superpowers/A41_DRAFT.md -> vault DECISIONS.md + log row (note the [OWNER-DECISION] flag in clause 3: incised tiebreak sum wording vs phase-1 clean set); (3) apply the pre-reg appendix note (drafted in A41_DRAFT.md, owner-gated); (4) repo DECISIONS.md mirror (file absent in worktree - untracked in main repo); (5) acceptance re-run: Laguna via app - expect Landsat-clean or degraded-with-hatching, never a dead end.
