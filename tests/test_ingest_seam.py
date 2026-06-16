"""P2.2a INGEST-SEAM tests -- prove the A15 seam (src/ingest.select_burn_source) is real, not
merely present.

The behavior lock (tests/test_behavior_lock.py) exercises only the SBS path, so it can never reach
the inert dNBR branch. These tests pin that branch directly:
  - the dNBR arm raises the EXACT string NotImplementedError("dNBR arm: P2.2b") (so P2.2b can grep
    for it), and
  - codeset-covered SBS resolves to "SBS" (the negative control that proves the inert test isn't
    vacuously passing because selection always raises).

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


def test_dnbr_arm_is_inert_and_raises_exact_string():
    """THE SEAM: a single out-of-codeset cell -> SBS does NOT cover the AOI -> dNBR arm (P2.2b).

    Pins the EXACT message so P2.2b can find the branch; the dNBR arm must stay inert until then."""
    sbs = np.full((4, 4), 3, dtype="uint8")
    sbs[0, 0] = 200                       # 200 is outside SBS_CODESET {0,1,2,3,4,15} -> genuine NoData
    assert 200 not in SBS_CODESET         # guard: the fixture really is out-of-codeset
    try:
        select_burn_source(sbs)
    except NotImplementedError as exc:
        assert str(exc) == "dNBR arm: P2.2b", f"wrong inert-branch message: {str(exc)!r}"
    else:
        raise AssertionError("dNBR arm did not raise -- the seam's partial-SBS branch is not wired")


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
