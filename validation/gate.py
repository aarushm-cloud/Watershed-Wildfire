"""gate.py -- the Week-0 validation gate, reconstructed in P0.5 from
VALIDATION_REPORT.md (the behavior oracle) using the same data sources and
parameters. Reproduces the ranking ORDER + both pre-registered pass criteria
(top-tercile 6/6, #1 = Cold Spring, flowed); the reconstruction lands at 36
basins / rank-AUC 0.9722 / 44.73 km2 master outlet -- the AOI-shift findings the
behavior lock anchors on, NOT the report's documented 32 / 0.987 / 39.19 (see
tests/test_behavior_lock.py). Not to be edited to make a run pass. See DECISIONS A16.

The PIPELINE itself (run_pipeline + the stage wiring + the A27/A31 refusal +
MONTECITO_FIRE/SOUTHFORK_FIRE + the reconstruction I/O anchors) was PROMOTED verbatim
into src/pipeline.py (behavior-neutral import churn); this module now re-exports those
names (backward-compat shim) so `gate.run_pipeline` / `from validation.gate import ...`
call sites are unchanged, and keeps ONLY the validation-report HARNESS (main() + its
perturbation/determinism helpers) -- the part that is validation-specific, not pipeline.

Sub-stages, single script (P1 modularised into src/):
  2a hydrology  -- pysheds fill pits -> fill depressions -> resolve flats ->
                   D8 flow dir -> accumulation; inline master-outlet FM-1 check
  2b outlets    -- channel cells (acc > thresh) crossing the CONTOUR_M mountain-front
                   contour going downhill (canyon mouths)
  2c delineate  -- upslope catchment per outlet (INDEX mode); discard tiny; keep
                   asset-draining; dedup (larger basins claim cells first).
                   Deterministic: stable basin_id + tie-breaks by outlet (row,col).
  2d slope      -- mean_slope = tan(theta) (OWNER-CONFIRMED), raw metric DEM
  2e score+rank -- mean_burn x mean_slope x area_km2; within-fire ordinal rank
  2f truth+metrics -- creek->outlet match (<=250 m); tercile; rank-AUC; means
Outputs: validation/out/{ranking.csv, basins.geojson}, stamped SBS + screening.

All distances are metric (EPSG:32611, UTM 11N). Fail loud, never degrade (FM-10).
"""

from __future__ import annotations

import sys
from pathlib import Path

# --- P1.1 bootstrap: make the project root importable so `from src...` resolves in EVERY
# context. This file is loaded by the standalone run (`python validation/gate.py`), by the
# pytest behavior-lock, and by the lock's standalone runner -- all three import THIS module,
# so keying the path off __file__ here is the single shared mechanism. gate.py lives at
# <root>/validation/gate.py, hence root = parents[1].
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Config scalars main() prints (the pipeline body imports its own subset in src/pipeline.py).
from src.config import TRUTH_MATCH_M, MASTER_KNOWN_KM2, BURN_LOW_COVERAGE
# Shared fail-loud exception (main()'s __main__ handler catches it).
from src.grids import GateAbort
# write_outputs (+ the SCREENING_STATEMENT A11 framing it emits) -- main() writes the reconstruction
# artifacts through it. write_refusal/build_refusal_message moved with the A27 gate into src/pipeline.py.
from src.outputs import write_outputs
# --- backward-compat shim (behavior-neutral promotion): the pipeline + its I/O anchors now live in
# src/pipeline.py. Re-export them so existing `gate.X` / `from validation.gate import X` call sites
# (run.py, the p2_* scripts, test_behavior_lock, test_entrypoint, test_a27_wired, test_a31_reorder)
# resolve unchanged. Values are byte-identical to the pre-move gate.py definitions.
from src.pipeline import (
    run_pipeline, dispatch_result, evaluate,
    _load_dem_artifacts, stage_2a_hydrology, mean_slope_tan,
    compute_creek_nearest, _terrain_applicability_gate,
    MONTECITO_FIRE, SOUTHFORK_FIRE, CELL_AREA_KM2,
    ROOT, DATA, OUT, DEM_TIF, SBS_TIF, ASSETS_GJ, CREEKS_GJ,
)


def perturbation_probe(basins, ranked, creek_nearest):
    """Re-run the creek->outlet match at several TRUTH_MATCH_M values (the circularity probe)."""
    rows = []
    for m in (150, 200, 300, 350):
        ev = evaluate(basins, ranked, creek_nearest, m)
        rows.append((m, ev["matched_flowed_count"],
                     f"{ev['flowed_in_top']}/{ev['n_flowed']}",
                     ev["flowed_in_top"] == ev["n_flowed"] and ev["n_flowed"] >= 6,
                     ev["rank1_is_flowed"]))
    return rows


def _ranking_signature(basins):
    """Stable signature of the ranking for determinism diffing."""
    return [(b["basin_id"], b["rank"], round(b["score"], 9), round(b["area_km2"], 9))
            for b in sorted(basins, key=lambda x: x["basin_id"])]


# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 74)
    print("P0.5 gate.py -- full reconstruction run (2a -> 2f), SBS / Thomas Fire / Montecito")
    print("=" * 74)

    R = run_pipeline()
    # A27 dispatch (P3.4-build-2): run_pipeline is now polymorphic. On a terrain-applicability
    # refusal, emit the message and exit 0 (honest answer, not a crash); on an unknown status,
    # dispatch_result raises GateAbort (fail-loud, caught below -> exit 2). Montecito -> "ranked"
    # -> falls through to the existing body unchanged.
    if R["status"] != "ranked":
        sys.exit(dispatch_result(R))
    hydro, basins, ranked, m = R["hydro"], R["basins"], R["ranked"], R["metrics"]

    # --- 2a/2b/2c summary ---
    _master_frac = hydro['master_km2'] / hydro['valid_area_km2']
    print(f"\n[2a] master outlet = {hydro['master_km2']:.2f} km^2 (known {MASTER_KNOWN_KM2}) "
          f"= {_master_frac:.1%} of valid AOI  [row,col={hydro['master_rowcol']}, index mode, FM-1 scale-free guard]")
    print(f"[2b] canyon-mouth outlets detected = {len(R['outlets'])}")
    print(f"[2c] candidate basins = {len(basins)} (report: 32); no exact score ties: {R['n_ties']==0}")

    # --- 2e score decomposition (ranked) ---
    print("\n[2e] SCORE = mean_burn x mean_slope(tan) x area_km2   (A17: coverage-weighted, class 15 -> 0.0 incl)")
    print(f"     {'rk':>2} {'id':>3} {'score':>7} {'burn':>5} {'slope':>6} {'area':>6} "
          f"{'cov':>5} {'flowed':>6} creek")
    for b in ranked[:10]:
        print(f"     {b['rank']:2d} {b['basin_id']:3d} {b['score']:7.3f} {b['mean_burn']:5.3f} "
              f"{b['mean_slope']:6.3f} {b['area_km2']:6.2f} {b['burn_coverage_frac']:5.2f} "
              f"{str(b['flowed']):>6} {b['matched_creek']}")
    print("     report top-8 scores: 3.199, 3.158, 1.846, 0.707, 0.673, 0.672, 0.630, 0.131")
    print(f"     low-coverage basins (<{BURN_LOW_COVERAGE:.0%} SBS): {m['low_coverage_basins']}")

    # A17 coverage-weighting: documented flowed basins are well-covered, so mean_burn is reliable
    print("\n     flowed-basin coverage (A17 mean_burn reliable where coverage ~1.0):")
    for b in sorted([x for x in basins if x["flowed"]], key=lambda x: x["rank"]):
        print(f"       {b['matched_creek']:<18} mean_burn={b['mean_burn']:.3f}  coverage={b['burn_coverage_frac']:.2f}")

    # --- 2f creek match table ---
    print("\n[2f] CREEK -> OUTLET MATCH (whole-line min distance, <= 250 m -> flowed)")
    for _, creek in R["creeks"].iterrows():
        info = R["creek_nearest"][creek["name"]]
        status = "MATCHED" if info["dist_m"] <= TRUTH_MATCH_M else "UNMATCHED"
        print(f"     {creek['name']:<18} -> basin {info['basin_id']:>3}  {info['dist_m']:7.1f} m  {status}")
    for creek, dist in m["unmatched"]:
        print(f"     !! UNMATCHED creek: {creek} (nearest outlet {dist:.1f} m > {TRUTH_MATCH_M} m) -- FINDING")

    # --- gate-value block (quoted verbatim into the report) ---
    print("\n----- GATE VALUES -----")
    print(f"matched_flowed_count = {m['matched_flowed_count']} of 6")
    print(f"flowed_in_top_tercile = {m['flowed_in_top']} of {m['n_flowed']}          # top {m['tercile_k']}")
    print(f"rank1_is_flowed = {m['rank1_is_flowed']}")
    print(f"rank1_creek = {m['rank1_creek']}")
    print(f"master_area_km2 = {hydro['master_km2']:.2f}")
    print(f"auc = {m['auc']:.4f}   n_pairs = {m['n_pairs']}")
    print(f"discordant_pairs = {m['n_discordant']}   discordant_are_fm3 = {m['discordant_are_fm3']}")
    print(f"flowed_mean_score = {m['flowed_mean_score']:.3f}   nonflowed_mean_score = {m['nonflowed_mean_score']:.3f}")
    print(f"low_coverage_basins = {m['low_coverage_basins']}")
    print("-----------------------")

    # discordant pairs (the AUC-costing pairs)
    print("\n     discordant pairs (non-flowed score >= flowed score):")
    if not m["discordant"]:
        print("       (none)")
    for fid, fcreek, farea, fscore, nfid, nfarea, nfscore in m["discordant"]:
        print(f"       flowed b{fid} ({fcreek}, {farea:.2f} km^2, {fscore:.3f}) "
              f"<= non-flowed b{nfid} ({nfarea:.2f} km^2, {nfscore:.3f})")

    # --- perturbation probe (unconditional) ---
    print("\n[probe] TRUTH_MATCH_M sweep (frozen reported value = 250):")
    print(f"     {'match_m':>7} {'flowed':>6} {'in_top':>7} {'6/6_top':>8} {'#1_flowed':>9}")
    for mm, cnt, intop, sixsix, r1 in perturbation_probe(basins, ranked, R["creek_nearest"]):
        print(f"     {mm:7d} {cnt:6d} {intop:>7} {str(sixsix):>8} {str(r1):>9}")

    # --- outputs ---
    csv_path, gj_path, _ = write_outputs(basins, R["creek_nearest"], OUT, DEM_TIF,
                                         R["provenance"]["burn_source"])   # A4/A15: from the seam
    print(f"\n[out] wrote {csv_path.relative_to(ROOT.parent)} and {gj_path.relative_to(ROOT.parent)}")

    # --- determinism: actual second end-to-end run, diffed ---
    print("\n[determinism] second end-to-end run, diffed against the first:")
    R2 = run_pipeline()
    sig1, sig2 = _ranking_signature(basins), _ranking_signature(R2["basins"])
    if sig1 == sig2:
        print(f"     IDENTICAL: {len(sig1)} basins, ranks/scores/areas match exactly.")
    else:
        ndiff = sum(1 for a, b in zip(sig1, sig2) if a != b) + abs(len(sig1) - len(sig2))
        print(f"     DIFFER in {ndiff} rows (non-deterministic!).")

    print("\n" + "=" * 74)
    print("END OF RUN -- classify against success bands (see report). STOP for owner decision.")
    print("=" * 74)


if __name__ == "__main__":
    try:
        main()
    except GateAbort as exc:
        print(f"\nGATE ABORT (fail-loud, FM-10): {exc}", file=sys.stderr)
        sys.exit(2)
