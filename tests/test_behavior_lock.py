"""P1 BEHAVIOR LOCK -- the executable oracle for the Phase-1 refactor.

These assertions freeze the OBSERVABLE BEHAVIOR of the reconstructed
`validation/gate.py` so that the P1 refactor (monolith -> seven `src/` modules)
can be proven behavior-preserving. If a refactor changes any value locked here,
THE REFACTOR IS WRONG -- this file is NEVER edited to make a failing refactor pass
(DECISIONS A16: the gate + report are read-only behavior anchors).

CAPTURED: 2026-06-10, from the unmodified reconstructed `validation/gate.py`
  (committed at P0.5; A17 coverage-weighted mean_burn treatment).
STRATEGY: A (live run). `gate.run_pipeline()` is a clean importable entrypoint,
  reads only LOCAL cached inputs under `validation/data/` (no network), has NO
  file-side-effects (only `main()` writes outputs), is deterministic (the gate's
  own end-to-end determinism check passes), and runs in ~2s. So the lock runs the
  gate live and asserts on its returned dict -- hermetic, no frozen fixture needed.

WHY THESE VALUES DIVERGE FROM THE REPORT (read before "fixing" them):
  The Week-0 report documents AUC 0.987 and a 39.19/39.4 km^2 master outlet. The
  reconstructed gate reproduces the *ranked order* and both pre-registered pass
  criteria (6/6 flowed in top tercile, #1 = Cold Spring) but, because the original
  AOI is unrecoverable, lands at AUC 0.9722 and master outlet 44.73 km^2. Those are
  the gate's REAL outputs and are recorded as P0.5 findings (PASS-WITH-FINDINGS),
  NOT tuned toward 0.987. This lock therefore anchors on what the gate PRODUCES
  (44.73, 0.9722), not on the documented report numbers. See the vault note
  "P0.5 -- Gate Reconstruction Findings" and DECISIONS A16/A17.

COVERAGE LOCK added 2026-06-10 (POST-fix, reviewed values). The original oracle did NOT assert
  `low_coverage`, which is precisely why the masked-basin bug (C8/A18) could exist silently: a
  100%-Developed basin read `low_coverage=False` ("fully assessed, low hazard") while scoring
  0.0. The A18 fix redefined coverage as SBS class in {1,2,3,4} (a real burn assessment),
  flipping b3/b13 False->True with the ranking bit-identical. This lock freezes the CORRECTED
  per-basin `low_coverage` bool (26 True / 10 False), reviewed against the before/after table.
  CAVEAT: `low_coverage=True` currently CONFLATES "outside burn footprint" (NoData class 15) and
  "masked developed land" (class 0) -- both map to True. That conflation is the present contract,
  not an endorsement; if the deferred reason-code split (C8) ever lands, RE-CAPTURE this mapping
  -- never hand-edit it to make a refactor pass.

Run:  pytest tests/test_behavior_lock.py -v     (or)   python tests/test_behavior_lock.py
"""
from __future__ import annotations

import functools
import importlib.util
import sys
from pathlib import Path

# --- load validation/gate.py as an importable module, cwd-independent ----------
# gate.py anchors its own DATA/OUT paths to __file__, so importing + calling
# run_pipeline() reads the right local inputs no matter where pytest is invoked.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_GATE_PATH = _REPO_ROOT / "validation" / "gate.py"
_spec = importlib.util.spec_from_file_location("gate", _GATE_PATH)
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate"] = gate
_spec.loader.exec_module(gate)


@functools.lru_cache(maxsize=1)
def pipeline():
    """Run the gate ONCE per test session and cache the returned results dict.

    lru_cache makes this run-once whether driven by pytest (many test functions)
    or by the standalone __main__ runner below.
    """
    return gate.run_pipeline()


# ============================================================================
# FROZEN EXPECTED BASELINE -- captured 2026-06-10 from the unmodified gate.
# Do NOT edit these to make a failing refactor pass (A16).
# ============================================================================

# PRIMARY: the full within-fire ordinal ranking, basin_id in exact rank order
# (rank 1 .. rank 36). Ranks 26-36 share score 0.0 and are ordered by the
# deterministic ascending-basin_id tie-break -- locked exactly all the same.
EXPECTED_RANKED_BASIN_IDS = [
    6, 9, 23, 34, 14, 21, 30, 28, 4, 15, 20, 12,
    11, 10, 17, 26, 0, 8, 19, 16, 7, 5, 1, 18, 2,
    3, 13, 22, 24, 25, 27, 29, 31, 32, 33, 35,
]

# SECONDARY: rank-AUC (strict pairwise concordance, ties count against). 175/180.
EXPECTED_AUC = 0.9722222222222222

# TRUTH SET: the basin_ids flagged `flowed` (creek match <= 250 m). This is the
# ground-truth set the AUC is computed against -- locked explicitly so a refactor
# can't silently change WHICH basins are "truth" while leaving AUC ~unchanged.
EXPECTED_TRUTH_FLOWED_IDS = {4, 6, 9, 14, 21, 23}

# AREA GUARD: master-outlet area (FM-1 anti-0km^2 linchpin). Captured 44.7273 km^2;
# band centered on the CAPTURED value (NOT the report's 39.19/39.4). Wide enough to
# absorb float jitter, tight enough to catch the 0-km^2 FM-1 regression or a
# materially different delineation.
EXPECTED_MASTER_KM2 = 44.7273
MASTER_BAND_KM2 = 0.5

# Pre-registered pass criteria (the headline behavior anchors).
EXPECTED_RANK1_BASIN_ID = 6                 # Cold Spring Creek
EXPECTED_RANK1_CREEK = "Cold Spring Creek"
EXPECTED_FLOWED_IN_TOP_TERCILE = 6          # 6/6
EXPECTED_N_FLOWED = 6
EXPECTED_N_BASINS = 36                       # AOI-shift delta vs report's 32 (a finding, not a bug)

# COVERAGE: per-basin `low_coverage` bool, POST the A18/C8 fix (covered = SBS class in {1,2,3,4}).
# Locked as BOOLEANS only -- raw cov_frac is intentionally NOT asserted (float-drift fragility,
# the same reason AUC uses a tolerance). Captured + cross-checked 2026-06-10; 26 True / 10 False;
# the fix flipped exactly b3, b13 (100% Developed) False->True. RE-CAPTURE if the C8 reason-code
# split lands -- never hand-edit to pass.
EXPECTED_LOW_COVERAGE = {
    0: True, 1: True, 2: True, 3: True, 4: False, 5: True, 6: False, 7: True, 8: False,
    9: False, 10: True, 11: True, 12: False, 13: True, 14: False, 15: False, 16: True,
    17: False, 18: True, 19: True, 20: True, 21: False, 22: True, 23: False, 24: True,
    25: True, 26: True, 27: True, 28: True, 29: True, 30: True, 31: True, 32: True,
    33: True, 34: True, 35: True,
}
# Non-locked cov_frac reference (3dp), for human diff-review of sub-threshold drift the bool
# cannot see -- NOT asserted: 0:0.236 1:0.123 2:0.013 3:0.000 4:0.997 5:0.060 6:0.924 7:0.280
# 8:0.931 9:0.994 10:0.783 11:0.602 12:0.974 13:0.000 14:0.974 15:0.819 16:0.407 17:0.955
# 18:0.428 19:0.737 20:0.632 21:0.966 22:0.000 23:0.930 24:0.000 25:0.000 26:0.218 27:0.005
# 28:0.656 29:0.000 30:0.699 31:0.000 32:0.000 33:0.000 34:0.528 35:0.000


# ============================================================================
# THE LOCKS
# ============================================================================

def test_primary_lock_exact_ranked_order():
    """PRIMARY: the within-fire ordinal ranking is identical, basin-for-basin."""
    R = pipeline()
    actual = [b["basin_id"] for b in R["ranked"]]
    assert actual == EXPECTED_RANKED_BASIN_IDS, (
        "Ranked basin order changed -- the refactor altered ranking behavior.\n"
        f"  expected: {EXPECTED_RANKED_BASIN_IDS}\n"
        f"  actual:   {actual}"
    )


def test_secondary_lock_rank_auc():
    """SECONDARY: rank-AUC within 1e-3 of the captured 0.9722 (NOT the report's 0.987)."""
    R = pipeline()
    auc = R["metrics"]["auc"]
    assert abs(auc - EXPECTED_AUC) < 1e-3, (
        f"rank-AUC drifted: expected ~{EXPECTED_AUC:.6f}, got {auc:.6f}"
    )


def test_truth_set_lock_flowed_basin_ids():
    """TRUTH-SET: the documented-flow (truth) basin set is exactly the captured set."""
    R = pipeline()
    actual = {b["basin_id"] for b in R["basins"] if b["flowed"]}
    assert actual == EXPECTED_TRUTH_FLOWED_IDS, (
        "The ground-truth (flowed) basin set changed -- truth labelling drifted.\n"
        f"  expected: {sorted(EXPECTED_TRUTH_FLOWED_IDS)}\n"
        f"  actual:   {sorted(actual)}"
    )


def test_area_guard_master_outlet_km2():
    """AREA GUARD: master outlet within +/-0.5 km^2 of the captured 44.73 (FM-1 linchpin)."""
    R = pipeline()
    area = R["hydro"]["master_km2"]
    assert abs(area - EXPECTED_MASTER_KM2) < MASTER_BAND_KM2, (
        f"Master-outlet area {area:.4f} km^2 outside the sane band "
        f"{EXPECTED_MASTER_KM2} +/- {MASTER_BAND_KM2} -- possible FM-1 (0 km^2) regression "
        "or a materially different delineation."
    )


def test_preregistered_pass_criteria():
    """The two pre-registered pass criteria + basin count, as belt-and-suspenders anchors."""
    R = pipeline()
    m = R["metrics"]
    assert len(R["basins"]) == EXPECTED_N_BASINS, (
        f"basin count {len(R['basins'])} != captured {EXPECTED_N_BASINS}")
    assert m["rank1_id"] == EXPECTED_RANK1_BASIN_ID, (
        f"#1 basin id {m['rank1_id']} != captured {EXPECTED_RANK1_BASIN_ID}")
    assert m["rank1_is_flowed"] is True, "#1 basin is no longer a flowed basin"
    assert m["rank1_creek"] == EXPECTED_RANK1_CREEK, (
        f"#1 creek {m['rank1_creek']!r} != {EXPECTED_RANK1_CREEK!r}")
    assert m["n_flowed"] == EXPECTED_N_FLOWED, (
        f"n_flowed {m['n_flowed']} != {EXPECTED_N_FLOWED}")
    assert m["flowed_in_top"] == EXPECTED_FLOWED_IN_TOP_TERCILE, (
        f"flowed-in-top-tercile {m['flowed_in_top']} != {EXPECTED_FLOWED_IN_TOP_TERCILE}")


def test_coverage_lock():
    """COVERAGE LOCK (A18/C8): the per-basin `low_coverage` bool mapping is frozen exactly.

    Locks the CORRECTED coverage flag (covered = SBS class in {1,2,3,4}). Bool-only, no
    tolerance -- the bool is the behavioral contract; raw cov_frac is deliberately not asserted.
    SHAPE GUARD runs first so a key-set drift fails distinctly from a value drift.
    """
    R = pipeline()
    actual = {int(b["basin_id"]): bool(b["low_coverage"]) for b in R["basins"]}
    # SHAPE GUARD: exactly 36 basins, ids {0..35}
    assert len(actual) == 36 and set(actual) == set(range(36)), (
        f"coverage mapping shape changed: {len(actual)} keys, ids={sorted(actual)} "
        "(expected 36 ids {0..35})"
    )
    # COVERAGE LOCK: full per-basin bool mapping, exact (the count is locked implicitly)
    diff = {k: (EXPECTED_LOW_COVERAGE[k], actual[k])
            for k in actual if EXPECTED_LOW_COVERAGE.get(k) != actual[k]}
    assert actual == EXPECTED_LOW_COVERAGE, (
        "low_coverage mapping changed vs the frozen A18/C8 oracle "
        f"(basin: expected->actual): {diff}"
    )


def test_coverage_lock_is_nonvacuous():
    """Negative control (PERMANENT, not throwaway): prove the coverage lock can actually bite.

    Guarantees the lock cannot silently rot into a vacuous pass. Exercises the SAME comparison
    + shape-guard logic test_coverage_lock uses, against deliberately-wrong expectations.
    """
    R = pipeline()
    actual = {int(b["basin_id"]): bool(b["low_coverage"]) for b in R["basins"]}
    # precondition: the live mapping matches the oracle (else the controls below are moot)
    assert actual == EXPECTED_LOW_COVERAGE

    # (a) value bite: flip exactly one bool -> the lock's equality must reject it
    corrupted = dict(EXPECTED_LOW_COVERAGE)
    corrupted[3] = not corrupted[3]
    assert actual != corrupted, "coverage equality is vacuous -- a flipped bool did not register"

    # (b) shape bite: drop one key -> the shape-guard condition must reject it
    missing = dict(EXPECTED_LOW_COVERAGE)
    del missing[0]
    assert not (len(missing) == 36 and set(missing) == set(range(36))), (
        "shape guard is vacuous -- a missing key did not register"
    )


# ============================================================================
# Standalone runner (no pytest required): python tests/test_behavior_lock.py
# ============================================================================
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}\n      {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} locks passed.")
    sys.exit(1 if failed else 0)
