"""P2.3 -- the A12 swap test: APPLY the frozen P2.1 §7 criteria to the dNBR arms vs the SBS control.

This script DECIDES NOTHING NEW. Every threshold, metric, and pass/fail rule was frozen in P2.1
(§7) and amended in A21/A23. P2.3 computes the numbers and applies the frozen rules *mechanically*,
builds the human-readable side-by-side, and reports the verdict -- pass or fail -- without softening.

It does NOT modify the pipeline, the formula, the normalizations, or any frozen value. It reuses,
read-only:
  - validation/gate.py      -- the SBS control pipeline (run FRESH on HEAD) + its evaluate() (AUC/tercile)
  - src.ingest.ingest_dnbr_both_arms -- the P2.2b reproject+normalize seam (Arm A binning / Arm B continuous)
  - src.score.stage_2e_score          -- the FROZEN burn x slope x area formula + tie-break
  - validation/p2_run_dnbr.py helpers -- the tested basin-copy / identity / NoData / A23 helpers

Frozen §7 facts transcribed as literal constants below (do NOT paraphrase or round):
  control     : 36 basins, top tercile = n//3 = 12, AUC 0.9722, Cold Spring (basin 6) = #1
  truth set   : flowed basins {4, 6, 9, 14, 21, 23}
  criterion 1 : ALL 6/6 flowed basins in the top 12 under dNBR-A          (integer bar, not >=70%)
  criterion 2 : Cold Spring (basin 6) is #1 AND flowed under dNBR-A       (BINARY, no escape hatch)
  criterion 3 : spearmanr(SBS-control order, dNBR-A order) over 36 basins >= 0.80
  tie-break   : score desc, ties -> ascending basin_id (the control's deterministic order, all arms)
  spearman    : scipy.stats.spearmanr over the RANK vectors (default average-rank tie handling)

Run: python validation/p2_3_swap_test.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from scipy.stats import spearmanr

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import TRUTH_MATCH_M, DNBR_NODATA_FAILLOUD_FRAC
from src.grids import GateAbort
from src.ingest import ingest_dnbr_both_arms
from src.score import stage_2e_score

# Load gate.py + the P2.2b harness as modules (cwd-independent, same mechanism as the behavior lock).
_GATE_PATH = _REPO_ROOT / "validation" / "gate.py"
_gspec = importlib.util.spec_from_file_location("gate", _GATE_PATH)
gate = importlib.util.module_from_spec(_gspec)
sys.modules["gate"] = gate
_gspec.loader.exec_module(gate)

_RUN_PATH = _REPO_ROOT / "validation" / "p2_run_dnbr.py"
_rspec = importlib.util.spec_from_file_location("p2_run_dnbr", _RUN_PATH)
p2run = importlib.util.module_from_spec(_rspec)
sys.modules["p2_run_dnbr"] = p2run
_rspec.loader.exec_module(p2run)

# ---- FROZEN §7 constants (transcribed verbatim; not reconstructed) -----------------------------
TRUTH_FLOWED = {4, 6, 9, 14, 21, 23}      # documented-flow basins (the truth set)
COLD_SPRING_ID = 6                        # Cold Spring Creek -- the control's #1
N_BASINS = 36
TERCILE_K = N_BASINS // 3                 # top tercile = top 12
SPEARMAN_FLOOR = 0.80                     # criterion-3 floor (frozen, §7 / §9)
CONTROL_AUC_LOCK = 0.9722222222222222     # behavior-lock AUC the fresh control must reproduce
OUT_BASE = _REPO_ROOT / "validation" / "out" / "montecito_dnbr"   # FM-9 namespaced, never flat out/


def _by_id(basins):
    return {int(b["basin_id"]): b for b in basins}


def _build_arm(cbasins, D, arm_key, slope):
    """Score one dNBR arm on a fresh copy of the control delineation with the FROZEN formula +
    tie-break. Returns (ranked, basins). Identical machinery to p2_run_dnbr (reused, not re-derived)."""
    basins = p2run._basin_copies(cbasins)                 # carries control's flowed/matched_creek labels
    p2run._assert_basin_set_identity(cbasins, basins)     # outlet-by-outlet identity (A12)
    ranked, _n_ties = stage_2e_score(D[arm_key]["wt"], D[arm_key]["covered"], slope, basins)
    p2run._attach_a23_diagnostic(basins, D["covered_interp"])
    return ranked, basins


def main():
    print("=" * 80)
    print("P2.3 -- SWAP TEST: apply the FROZEN P2.1 §7 criteria to dNBR-A / dNBR-B vs the SBS control.")
    print("=" * 80)

    # ---------------------------------------------------------------------------------------------
    # PRECONDITION 2 -- re-run the SBS control FRESH on HEAD (not the stale committed out/ artifacts)
    #                   and confirm it REPRODUCES the lock (Cold Spring #1 + AUC 0.9722) before trust.
    # ---------------------------------------------------------------------------------------------
    control = gate.run_pipeline()
    cbasins = control["basins"]
    cmetrics = control["metrics"]
    cranked = control["ranked"]
    ctrl_auc = cmetrics["auc"]
    ctrl_rank1 = cmetrics["rank1_id"]

    if len(cbasins) != N_BASINS:
        raise GateAbort(f"Fresh control has {len(cbasins)} basins, expected {N_BASINS} -- HALT.")
    if ctrl_rank1 != COLD_SPRING_ID or not cmetrics["rank1_is_flowed"]:
        raise GateAbort(f"Fresh control #1 = basin {ctrl_rank1} (flowed={cmetrics['rank1_is_flowed']}), "
                        f"expected Cold Spring (basin {COLD_SPRING_ID}) flowed -- control drifted, HALT.")
    if abs(ctrl_auc - CONTROL_AUC_LOCK) >= 1e-3:
        raise GateAbort(f"Fresh control AUC {ctrl_auc:.6f} != locked {CONTROL_AUC_LOCK:.6f} -- control "
                        "drifted from the lock; the dNBR comparison would be meaningless. HALT.")
    ctrl_flowed = {int(b["basin_id"]) for b in cbasins if b["flowed"]}
    if ctrl_flowed != TRUTH_FLOWED:
        raise GateAbort(f"Fresh control flowed set {sorted(ctrl_flowed)} != frozen {sorted(TRUTH_FLOWED)} -- HALT.")
    print(f"\n[precondition] fresh SBS control on HEAD: {len(cbasins)} basins; Cold Spring (b6) = #1 "
          f"(flowed); AUC = {ctrl_auc:.6f} == lock; flowed = {sorted(ctrl_flowed)}. REPRODUCED -- trusted.")

    # ---------------------------------------------------------------------------------------------
    # Build BOTH dNBR arms in THIS single execution, keyed to the fresh control delineation.
    # ---------------------------------------------------------------------------------------------
    with rasterio.open(gate.DEM_TIF) as d:
        dem_profile = d.profile
    D = ingest_dnbr_both_arms(p2run.NATIVE_DNBR, dem_profile)

    # NoData/cloud fail-loud guard on flowed basins (P2.1 §4 path 1; A8).
    nd_fracs = p2run._nodata_fail_loud_guard(cbasins, D["nodata_mask"])

    slope = gate.mean_slope_tan(control["hydro"]["dem_raw"])
    a_ranked, a_basins = _build_arm(cbasins, D, "arm_a", slope)
    b_ranked, b_basins = _build_arm(cbasins, D, "arm_b", slope)

    # THREE-WAY basin-set alignment in this run (control == arm_a == arm_b: same IDs + (row,col) outlets).
    p2run._assert_basin_set_identity(cbasins, a_basins)
    p2run._assert_basin_set_identity(cbasins, b_basins)
    p2run._assert_basin_set_identity(a_basins, b_basins)
    print(f"[precondition] three-way basin-set alignment asserted: control == dNBR-A == dNBR-B "
          f"({N_BASINS} basins, same IDs + (row,col) outlets). Truth set aligns.")
    print(f"[precondition] flowed-basin NoData fraction (<= {DNBR_NODATA_FAILLOUD_FRAC:.0%} required): "
          + ", ".join(f"b{k}={v:.1%}" for k, v in sorted(nd_fracs.items())) + " -- guard passed.")

    C = _by_id(cbasins)       # SBS control, by basin_id
    A = _by_id(a_basins)      # dNBR-A
    B = _by_id(b_basins)      # dNBR-B

    # ---------------------------------------------------------------------------------------------
    # PRIMARY CRITERIA -- gated on dNBR-A, each computed + reported INDIVIDUALLY (frozen tie-break).
    # ---------------------------------------------------------------------------------------------
    print("\n" + "-" * 80)
    print("PRIMARY PASS CRITERIA (dNBR-A) -- each applied mechanically; tie-break = score desc, id asc")
    print("-" * 80)

    # --- Criterion 1: tercile recovery (all 6/6 flowed in top 12 under dNBR-A) -------------------
    a_in_top = {bid for bid in TRUTH_FLOWED if A[bid]["rank"] <= TERCILE_K}
    a_out = sorted(TRUTH_FLOWED - a_in_top)
    crit1_pass = (len(a_in_top) == len(TRUTH_FLOWED))
    print(f"\n[C1] Tercile recovery (top {TERCILE_K} of {N_BASINS}):")
    for bid in sorted(TRUTH_FLOWED):
        b = A[bid]
        print(f"       flowed b{bid:<2d} ({b['matched_creek']:<18}) dNBR-A rank {b['rank']:>2d}  "
              f"{'IN top tercile' if b['rank'] <= TERCILE_K else 'OUT of top tercile'}")
    print(f"     => {len(a_in_top)}/6 flowed in top tercile" + (f" (out: {a_out})" if a_out else "")
          + f".  C1 = {'PASS' if crit1_pass else 'FAIL'} (bar = 6/6).")

    # --- Criterion 2: Cold Spring #1-and-flowed under dNBR-A (BINARY) ----------------------------
    cs = A[COLD_SPRING_ID]
    a_rank1 = a_ranked[0]
    gap_abs = a_rank1["score"] - cs["score"]
    gap_pct = 100.0 * gap_abs / a_rank1["score"] if a_rank1["score"] else float("nan")
    crit2_pass = (cs["rank"] == 1 and bool(cs["flowed"]))
    print(f"\n[C2] Cold Spring #1-and-flowed (BINARY -- no escape hatch):")
    print(f"       Cold Spring (b{COLD_SPRING_ID}) dNBR-A rank = {cs['rank']}, score = {cs['score']:.6f}, "
          f"flowed = {cs['flowed']}")
    print(f"       dNBR-A #1 = b{a_rank1['basin_id']} ({a_rank1['matched_creek'] or 'n/a'}), "
          f"score = {a_rank1['score']:.6f}")
    if cs["rank"] != 1:
        print(f"       gap to #1 = {gap_abs:.6f} ({gap_pct:.2f}% of #1's score)  [FACT, not a softener]")
    print(f"     => C2 = {'PASS' if crit2_pass else 'FAIL'} "
          f"(Cold Spring is{'' if cs['rank'] == 1 else ' NOT'} #1).")

    # --- Criterion 3: Spearman rho(SBS order, dNBR-A order) over all 36, on RANK vectors ---------
    ids = sorted(C)                                   # stable basin_id order for paired vectors
    sbs_ranks = [C[i]["rank"] for i in ids]
    a_ranks = [A[i]["rank"] for i in ids]
    rho_a, p_a = spearmanr(sbs_ranks, a_ranks)        # scipy.stats.spearmanr, named; rank vectors
    crit3_pass = (rho_a >= SPEARMAN_FLOOR)
    print(f"\n[C3] Spearman rho(SBS-control order, dNBR-A order) over all {N_BASINS} basins:")
    print(f"       rho = {rho_a:.6f}  (p = {p_a:.3e})   floor = {SPEARMAN_FLOOR:.2f}")
    print(f"     => C3 = {'PASS' if crit3_pass else 'FAIL'} (rho {'>=' if crit3_pass else '<'} {SPEARMAN_FLOOR}).")

    # --- The verdict -- ALL THREE must hold; FAIL if ANY fails. No softening. --------------------
    verdict_pass = crit1_pass and crit2_pass and crit3_pass
    failed = [name for name, ok in [("C1 tercile", crit1_pass), ("C2 Cold-Spring-#1", crit2_pass),
                                    ("C3 spearman", crit3_pass)] if not ok]
    print("\n" + "=" * 80)
    print(f"VERDICT: {'PASS' if verdict_pass else 'FAIL'}"
          + ("" if verdict_pass else f"  -- failed: {', '.join(failed)}"))
    print("  (A12: a documented fail is a legitimate, valuable result -- NOT a project failure.)")
    print("=" * 80)

    # ---------------------------------------------------------------------------------------------
    # RANK-CHANGE MAGNITUDE + mechanistic WHY for the #1/#2 swap (evidence for P2.4, not interpretation)
    # slope and area are terrain/delineation -> IDENTICAL across arms; only mean_burn moves.
    # ---------------------------------------------------------------------------------------------
    print("\n--- #1/#2 swap: rank-change magnitude + component-level why (FACTS for P2.4) ---")
    sy_id = a_rank1["basin_id"]
    for bid, who in [(COLD_SPRING_ID, "Cold Spring"), (sy_id, A[sy_id]["matched_creek"])]:
        c, a = C[bid], A[bid]
        print(f"  b{bid} ({who}):  SBS rank {c['rank']} score {c['score']:.4f}  ->  "
              f"dNBR-A rank {a['rank']} score {a['score']:.4f}")
        print(f"        mean_burn {c['mean_burn']:.4f} -> {a['mean_burn']:.4f} "
              f"(delta {a['mean_burn']-c['mean_burn']:+.4f}) ; "
              f"slope {c['mean_slope']:.4f} -> {a['mean_slope']:.4f} ; area {c['area_km2']:.4f} (unchanged)")
    cs_burn_d = A[COLD_SPRING_ID]["mean_burn"] - C[COLD_SPRING_ID]["mean_burn"]
    sy_burn_d = A[sy_id]["mean_burn"] - C[sy_id]["mean_burn"]
    print(f"  WHY (one line): slope & area are identical across arms (terrain/delineation unchanged), so the "
          f"flip is ENTIRELY a mean_burn effect -- dNBR raised San Ysidro's mean_burn by {sy_burn_d:+.4f} "
          f"vs Cold Spring's {cs_burn_d:+.4f}, enough to overtake at the top. SBS gap was "
          f"{C[COLD_SPRING_ID]['score']-C[sy_id]['score']:+.4f}; dNBR-A gap is "
          f"{A[sy_id]['score']-A[COLD_SPRING_ID]['score']:+.4f}.")

    # ---------------------------------------------------------------------------------------------
    # SECONDARY METRICS (NOT gating -- reported, clearly labeled).
    # ---------------------------------------------------------------------------------------------
    print("\n--- SECONDARY METRICS (non-gating; reported, not judged against) ---")
    # rank-AUC for each dNBR arm vs the same 6-flowed truth set -- reuse the gate's own evaluate().
    a_eval = gate.evaluate(a_basins, a_ranked, control["creek_nearest"], TRUTH_MATCH_M)
    b_eval = gate.evaluate(b_basins, b_ranked, control["creek_nearest"], TRUTH_MATCH_M)
    print(f"  rank-AUC: SBS control {ctrl_auc:.4f} | dNBR-A {a_eval['auc']:.4f} | dNBR-B {b_eval['auc']:.4f} "
          "(gate strict-pairwise AUC, same 6-flowed truth set).")

    # A<->B agreement (robustness headline) -- spearman over the two dNBR rank vectors.
    b_ranks = [B[i]["rank"] for i in ids]
    rho_ab, p_ab = spearmanr(a_ranks, b_ranks)
    print(f"  A<->B agreement: spearman rho(dNBR-A order, dNBR-B order) = {rho_ab:.6f} (p = {p_ab:.3e}) "
          "-- robustness to the normalization-function choice (NOT signal validation, §1).")

    # rho(SBS, dNBR-B) reported for completeness (non-gating; B is the companion arm).
    rho_b, _ = spearmanr(sbs_ranks, b_ranks)
    print(f"  rho(SBS order, dNBR-B order) = {rho_b:.6f} (non-gating; B is companion, not the primary).")

    # Per-basin coverage fraction under dNBR for the 6 flowed basins (confirms §4 / A21 assumption).
    print("  flowed-basin coverage fraction (operational burn_coverage_frac), §4/§7 secondary:")
    for bid in sorted(TRUTH_FLOWED):
        print(f"      b{bid} ({A[bid]['matched_creek']:<18}) dNBR-A {A[bid]['burn_coverage_frac']:.3f}  "
              f"dNBR-B {B[bid]['burn_coverage_frac']:.3f}  (SBS {C[bid]['burn_coverage_frac']:.3f})")
    a_low = sum(1 for b in a_basins if b["low_coverage"])
    b_low = sum(1 for b in b_basins if b["low_coverage"])
    c_low = sum(1 for b in cbasins if b["low_coverage"])
    print(f"  A23 low_coverage counts: SBS {c_low}/36 | dNBR-A {a_low}/36 | dNBR-B {b_low}/36 -- dNBR flags "
          "MORE (weaker instrument). This is EXPECTED (A23), NOT a criterion and NOT a discrepancy.")

    # ---------------------------------------------------------------------------------------------
    # FM-3: confirm the discordant pair under dNBR is the SAME Oak/Toro `x area` signature as control.
    # ---------------------------------------------------------------------------------------------
    print("\n--- FM-3 discordant-pair check (confirm the KNOWN weakness, not a new one) ---")
    def _disc_summary(label, ev):
        pairs = [(fid, fcreek, nfid) for (fid, fcreek, farea, fscore, nfid, nfarea, nfscore) in ev["discordant"]]
        print(f"  {label}: discordant pairs = {ev['n_discordant']}, discordant_are_fm3 = {ev['discordant_are_fm3']}"
              + (f"  pairs(flowed<larger nonflowed): {pairs}" if pairs else ""))
        return set((fid, nfid) for fid, _c, nfid in pairs)
    c_pairs = _disc_summary("SBS control", cmetrics)
    a_pairs = _disc_summary("dNBR-A     ", a_eval)
    b_pairs = _disc_summary("dNBR-B     ", b_eval)
    same_a = a_eval["discordant_are_fm3"] and a_pairs == c_pairs
    print(f"  => dNBR-A discordance is the SAME as control (Oak/Toro `x area`, C1 DO-NOT-FIX): {same_a}"
          + ("" if a_pairs == c_pairs else f"  [control {sorted(c_pairs)} vs dNBR-A {sorted(a_pairs)}]"))

    # ---------------------------------------------------------------------------------------------
    # REQUIRED DELIVERABLE -- the human-readable side-by-side (FROZEN §7 output).
    # ---------------------------------------------------------------------------------------------
    side_csv, side_md = _write_side_by_side(C, A, B, control, a_eval, b_eval, rho_a, p_a, rho_ab,
                                            verdict_pass, crit1_pass, crit2_pass, crit3_pass,
                                            len(a_in_top), cs, a_rank1, gap_abs, gap_pct)
    print(f"\n[deliverable] side-by-side written:\n   {side_csv.relative_to(_REPO_ROOT)}\n   "
          f"{side_md.relative_to(_REPO_ROOT)}")

    print("\n" + "=" * 80)
    print("P2.3 COMPLETE -- frozen §7 criteria applied; verdict + evidence + side-by-side produced.")
    print("P2.4 (interpretation) is a SEPARATE chunk. No criterion moved; no tuning; no pipeline change.")
    print("=" * 80)


SCREENING = ("Within-fire relative screening ranking of watersheds warranting closer assessment -- "
             "NOT a prediction of where debris will go. Within-fire ordinal only; not cross-fire comparable (A5/A11).")
IMAGERY = ("dNBR burn severity from satellite imagery ~6 months after the fire (extended assessment; "
           "post-scene 2018-06-19) -- the messy-real-fire condition the tool is built for (A21).")


def _write_side_by_side(C, A, B, control, a_eval, b_eval, rho_a, p_a, rho_ab, verdict_pass,
                        c1, c2, c3, n_top, cs, a_rank1, gap_abs, gap_pct):
    """Build the FROZEN §7 deliverable: a single legible table over all 36 basins showing SBS rank vs
    dNBR-A rank vs dNBR-B rank, the 6 flowed basins marked, and the score components per arm. Captions
    state ranks/gaps as FACTS (no softening adjectives -- interpretation is P2.4). Written to the
    namespaced validation/out/montecito_dnbr/ (FM-9)."""
    ids = sorted(C)
    rows = []
    for i in ids:
        c, a, b = C[i], A[i], B[i]
        rows.append({
            "basin_id": i,
            "flowed": "FLOWED" if c["flowed"] else "",
            "creek": c["matched_creek"],
            "sbs_rank": c["rank"], "dnbrA_rank": a["rank"], "dnbrB_rank": b["rank"],
            "sbs_score": round(c["score"], 6), "dnbrA_score": round(a["score"], 6), "dnbrB_score": round(b["score"], 6),
            "sbs_burn": round(c["mean_burn"], 4), "dnbrA_burn": round(a["mean_burn"], 4), "dnbrB_burn": round(b["mean_burn"], 4),
            "mean_slope": round(c["mean_slope"], 4),     # identical across arms (terrain)
            "area_km2": round(c["area_km2"], 4),         # identical across arms (delineation)
            "sbs_cov": round(c["burn_coverage_frac"], 3), "dnbrA_cov": round(a["burn_coverage_frac"], 3),
            "dnbrB_cov": round(b["burn_coverage_frac"], 3),
        })
    df = pd.DataFrame(rows).sort_values("sbs_rank").reset_index(drop=True)

    OUT_BASE.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_BASE / "p2_3_side_by_side.csv"
    with open(csv_path, "w") as fh:
        fh.write(f"# {SCREENING}\n# {IMAGERY}\n")
        fh.write(f"# P2.3 swap test -- SBS control vs dNBR-A (primary) vs dNBR-B (companion). "
                 f"VERDICT: {'PASS' if verdict_pass else 'FAIL'}.\n")
        fh.write("# mean_slope + area_km2 are terrain/delineation -> IDENTICAL across all three arms; "
                 "only mean_burn (and coverage) move with the burn input.\n")
        df.to_csv(fh, index=False)

    # Markdown version (legible to a non-developer / the O4 target).
    md = OUT_BASE / "p2_3_side_by_side.md"
    flowed_mark = lambda i: " 🚩" if C[i]["flowed"] else ""
    lines = []
    lines.append("# P2.3 Swap Test — SBS vs dNBR side-by-side (Montecito, Thomas Fire 2017)\n")
    lines.append(f"> {SCREENING}\n")
    lines.append(f"> {IMAGERY}\n")
    lines.append("**What this is:** the validated tool ranks burned watersheds by how much they warrant a "
                 "closer look. Here we re-run the *same* fire with the free, available-anywhere satellite "
                 "input (**dNBR**) instead of the field-validated input (**SBS**) and ask: *does the ranking "
                 "survive the swap?* Only the burn input changes — terrain (slope) and watershed size (area) "
                 "are identical across all three columns. 🚩 marks the 6 watersheds that actually produced "
                 "debris flows on 2018-01-09 (the ground truth).\n")
    verdict_line = "✅ **PASS**" if verdict_pass else "❌ **FAIL**"
    lines.append(f"## Verdict: {verdict_line}\n")
    lines.append("| Frozen criterion (dNBR-A) | Result | Pass? |")
    lines.append("|---|---|---|")
    lines.append(f"| C1 — all 6 flowed watersheds in the top tercile (top 12 of 36) | {n_top}/6 in top 12 "
                 f"| {'✅' if c1 else '❌'} |")
    lines.append(f"| C2 — Cold Spring is the #1 watershed (and flowed) | Cold Spring rank: {cs['rank']} "
                 f"(score {cs['score']:.3f} vs {a_rank1['matched_creek'] or 'b'+str(a_rank1['basin_id'])} "
                 f"{a_rank1['score']:.3f}) | {'✅' if c2 else '❌'} |")
    lines.append(f"| C3 — overall ranking correlation ρ ≥ 0.80 | ρ = {rho_a:.3f} (p = {p_a:.1e}) "
                 f"| {'✅' if c3 else '❌'} |")
    lines.append("")
    if not c2:
        lines.append(f"**C2 fact (no softening):** Cold Spring rank: {cs['rank']} (score {cs['score']:.3f} vs "
                     f"{a_rank1['matched_creek']} {a_rank1['score']:.3f}); gap {gap_abs:.3f} = {gap_pct:.2f}% of "
                     "the #1 score. Slope and area are unchanged, so the #1/#2 order is set entirely by mean_burn "
                     f"(dNBR read San Ysidro's burn {A[a_rank1['basin_id']]['mean_burn']:.3f} vs Cold Spring's "
                     f"{cs['mean_burn']:.3f}). Interpretation of what this means for shipping is P2.4, not here.\n")
    lines.append(f"**Secondary (non-gating):** rank-AUC SBS {control['metrics']['auc']:.4f} / dNBR-A "
                 f"{a_eval['auc']:.4f} / dNBR-B {b_eval['auc']:.4f}; A↔B agreement ρ = {rho_ab:.3f}.\n")
    lines.append("## All 36 watersheds — rank under each input\n")
    lines.append("| SBS rank | dNBR-A rank | dNBR-B rank | basin | creek | SBS burn | dNBR-A burn | dNBR-B burn | slope | area km² |")
    lines.append("|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|")
    for _, r in df.iterrows():
        i = int(r["basin_id"])
        lines.append(f"| {r['sbs_rank']} | {r['dnbrA_rank']} | {r['dnbrB_rank']} | b{i}{flowed_mark(i)} | "
                     f"{r['creek']} | {r['sbs_burn']} | {r['dnbrA_burn']} | {r['dnbrB_burn']} | "
                     f"{r['mean_slope']} | {r['area_km2']} |")
    md.write_text("\n".join(lines) + "\n")
    return csv_path, md


if __name__ == "__main__":
    try:
        main()
    except GateAbort as exc:
        print(f"\nGATE ABORT (fail-loud, A8): {exc}", file=sys.stderr)
        sys.exit(2)
