"""P2.2a INGEST-SEAM tests -- prove the A15 seam (src/ingest.select_burn_source) is real, not
merely present.

The behavior lock (tests/test_behavior_lock.py) exercises only the SBS path, so it can never reach
the dNBR branch. These tests pin that branch directly:
  - partial-SBS (a cell outside the codeset) resolves to "dNBR" (P2.2b WIRED this branch -- it
    previously raised NotImplementedError("dNBR arm: P2.2b"); the arm now exists, so the seam
    selects dNBR instead of raising), and
  - codeset-covered SBS resolves to "SBS" (the negative control that proves the dNBR test isn't
    vacuously passing because selection always returns one answer).

P2.2b UPDATE (verification §1, fix 8): the previous version of the third test asserted the branch
RAISED. P2.2b makes it stop raising (the arm is built), so that assertion is replaced -- NOT deleted
-- with one that the branch now resolves to "dNBR". The SBS lock itself stays 7/7, untouched.

Coverage definition (owner decision 2026-06-16): a cell is valid SBS data iff its value is in the
known BAER codeset {0,1,2,3,4,15}; class 15 (outside-perimeter) COUNTS as covered. "Covers the AOI"
= every cell in-codeset. sbs.tif declares no rasterio nodata, so codeset membership -- not a GDAL
mask -- defines validity. See src/ingest.select_burn_source and DATA_SOURCES.md s5.

Run:  pytest tests/test_ingest_seam.py -v   (or)   python tests/test_ingest_seam.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# repo root on sys.path so `from src...` resolves no matter where this is invoked
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.ingest import select_burn_source, SBS_CODESET


def test_sbs_selected_when_codeset_covers_aoi():
    """NEGATIVE CONTROL: an all-in-codeset SBS grid (incl. class 15) resolves to 'SBS'.

    Without this, the inert-branch test could pass vacuously (e.g. if selection always raised)."""
    sbs = np.array([[0, 1, 2], [3, 4, 15]], dtype="uint8")   # every value in SBS_CODESET
    assert select_burn_source(sbs) == "SBS"


def test_class15_counts_as_covered():
    """An all-class-15 (entirely outside-perimeter) SBS still COVERS the AOI -> 'SBS', not dNBR.

    Class 15 is a valid assessment ('outside the burn'), not missing data (owner decision)."""
    sbs = np.full((5, 5), 15, dtype="uint8")
    assert select_burn_source(sbs) == "SBS"


def test_dnbr_arm_resolves_when_sbs_partial():
    """THE SEAM (P2.2b WIRED): a single out-of-codeset cell -> SBS does NOT cover the AOI -> "dNBR".

    Previously this branch raised NotImplementedError("dNBR arm: P2.2b"); P2.2b built the arm
    (ingest_dnbr_both_arms), so the seam now SELECTS dNBR rather than raising (A3 precedence: partial
    SBS -> dNBR for the whole AOI, never blended)."""
    sbs = np.full((4, 4), 3, dtype="uint8")
    sbs[0, 0] = 200                       # 200 is outside SBS_CODESET {0,1,2,3,4,15} -> genuine NoData
    assert 200 not in SBS_CODESET         # guard: the fixture really is out-of-codeset
    assert select_burn_source(sbs) == "dNBR", (
        "partial-SBS must resolve to 'dNBR' (the P2.2b arm); it no longer raises")


# ---------------------------------------------------------------------------
# Standalone runner (no pytest required): python tests/test_ingest_seam.py
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
    print(f"\n{len(tests) - failed}/{len(tests)} seam tests passed.")
    sys.exit(1 if failed else 0)
