"""THE FIREWALL FUSE (P2.2b verification §0) -- assert the code's frozen dNBR constants
equal the P2.1/A20 pre-registration values, byte-for-byte.

WHY THIS TEST EXISTS (the under-weighted catch): the firewall (validation/P2_PREREGISTRATION.md,
ADR A20) froze the dNBR bin edges, the Arm-B clamp, and the 0.1 floor in a *document*. Until this
test existed, NOTHING checked that the *code* matched. A transcription slip -- `0.27 -> 0.207`, or
a clamp upper `1.3 -> 1.03` -- passes every other check: it still produces a ranking, the basin set
still matches, the coverage checksum still holds. It would silently corrupt the P2.3 agreement test
while looking completely healthy. This test is the missing fuse: it ties the code's literals to the
frozen pre-registration so a slip fails loudly here, first.

These values are TRANSCRIBED VERBATIM from validation/P2_PREREGISTRATION.md:
  - §2 / §8.1  Arm A binning edges (raw dNBR): -0.5, [0.100, 0.270, 0.440, 0.660], 1.300
               (the four interior breaks 0.1/0.27/0.44/0.66 are the bin edges used by the code).
  - §3 / §8.2  Arm B transfer: clamp [0.100, 1.300] -> linear [0,1].
  - §4 / §8.5  Outside-burn floor: dNBR < 0.100 -> non-covered.
  - science_reference §1 / config.BURN_WEIGHTS: 1->0.0, 2->0.33, 3->0.67, 4->1.0 (reused untouched).

NEVER edit these expectations to make a failing run pass -- they ARE the frozen firewall (A16/A20).

Run:  pytest tests/test_dnbr_frozen_constants.py -v   (or)   python tests/test_dnbr_frozen_constants.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src import config


def test_dnbr_bin_edges_match_prereg():
    """Arm A binning interior breaks (raw dNBR), P2.1 §2 -- left-closed, right-open."""
    assert config.DNBR_BIN_EDGES == (0.100, 0.270, 0.440, 0.660), (
        f"DNBR_BIN_EDGES {config.DNBR_BIN_EDGES} != frozen P2.1 §2 (0.100, 0.270, 0.440, 0.660)")


def test_dnbr_clamp_matches_prereg():
    """Arm B transfer clamp range, P2.1 §3."""
    assert config.DNBR_CLAMP == (0.100, 1.300), (
        f"DNBR_CLAMP {config.DNBR_CLAMP} != frozen P2.1 §3 (0.100, 1.300)")


def test_dnbr_floor_matches_prereg():
    """Outside-burn floor shared by both arms, P2.1 §4 (the same 0.100 break as §2/§3)."""
    assert config.DNBR_FLOOR == 0.100, (
        f"DNBR_FLOOR {config.DNBR_FLOOR} != frozen P2.1 §4 (0.100)")
    # the floor must BE the first bin edge and the lower clamp -- one number, three knobs (P2.1 §4)
    assert config.DNBR_FLOOR == config.DNBR_BIN_EDGES[0] == config.DNBR_CLAMP[0], (
        "DNBR_FLOOR must equal the first bin edge and the lower clamp -- the three knobs share one "
        "number (P2.1 §4); they have drifted apart")


def test_burn_weights_unchanged_for_arm_a_reuse():
    """Arm A reuses score._burn_weight_raster + BURN_WEIGHTS untouched (science_reference §1)."""
    assert config.BURN_WEIGHTS == {1: 0.0, 2: 0.33, 3: 0.67, 4: 1.0}, (
        f"BURN_WEIGHTS {config.BURN_WEIGHTS} != frozen {{1:0.0, 2:0.33, 3:0.67, 4:1.0}}")


# ---------------------------------------------------------------------------
# Standalone runner (no pytest required): python tests/test_dnbr_frozen_constants.py
# ---------------------------------------------------------------------------
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
    print(f"\n{len(tests) - failed}/{len(tests)} frozen-constant checks passed.")
    sys.exit(1 if failed else 0)
