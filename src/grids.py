"""grids.py -- the inter-stage data contract: CRS, affine convention, the
(row, col) outlet rule, dtype/nodata, and boundary-validation assertions
(anti-0km2 guard, sane-area check, alignment check). Not an orchestrator.
See ARCHITECTURE.md and DECISIONS A7.

P1.1: holds ONLY the fail-loud exception + the shared coordinate/CRS helpers that
ALREADY existed in validation/gate.py, extracted verbatim. No dataclasses and no new
assertion helpers (both deferred to later phases). Depends ONLY on src.config (the
dependency leaf) -- the single allowed intra-project import direction; importing anything
else here would invert the contract.
"""
from __future__ import annotations

import numpy as np

from src.config import CANONICAL_CRS, ALLOWED_UTM_ZONES


class GateAbort(RuntimeError):
    """Raised when a stage precondition is violated -- fail loud, never degrade (FM-10)."""


def _assert_metric_crs(layer_crs, name: str) -> None:
    """Fail loud unless `layer_crs` is an ALLOWED metric UTM zone (A25 allowlist).

    The job is to refuse a non-metric CRS so we never compute distances in degrees. A25 makes
    this per-fire: instead of pinning the single validation zone (EPSG:32611), it accepts any
    zone in config.ALLOWED_UTM_ZONES (32611 Montecito, 32613 South Fork). A zone NOT in the
    allowlist -- including any geographic CRS like EPSG:4326 -- fails loud (block the fire),
    never silently proceeds; onboarding a new fire means adding its zone to the allowlist.
    String-normalised (str().upper()) exactly as before, so the Montecito 32611 path is
    unchanged. (Both 32611 and 32613 are metric UTM, so allowlist membership == metric+allowed.)"""
    allowed = {f"EPSG:{z}" for z in ALLOWED_UTM_ZONES}
    if layer_crs is None or str(layer_crs).upper() not in allowed:
        raise GateAbort(f"{name} CRS is {layer_crs}, not in the allowed metric UTM zones "
                        f"{sorted(ALLOWED_UTM_ZONES)} (A25 allowlist). Refusing to compute "
                        "distances in a non-metric / un-onboarded CRS.")


def _rc_to_xy(rows: np.ndarray, cols: np.ndarray, transform) -> np.ndarray:
    """Cell (row, col) -> projected (x, y) cell-centre coords (metres, in the layer's CRS)."""
    a, _, c, _, e, f = (transform.a, transform.b, transform.c,
                        transform.d, transform.e, transform.f)
    return np.column_stack([c + a * (cols + 0.5), f + e * (rows + 0.5)])


def assert_aligned(ref_profile, other_profile, *, ref_name: str = "DEM",
                   other_name: str = "SBS", expected_crs=CANONICAL_CRS) -> None:
    """Fail loud unless two rasters share ONE grid (CRS / shape / affine).

    EXTRACTED VERBATIM (P2.2a) from the inline DEM/SBS check in gate.stage_2a_hydrology. A25
    makes the fixed-zone check per-fire and closes the unguarded-`other` gap:
      * `expected_crs` (kwarg, DEFAULTS to config.CANONICAL_CRS) is the fixed zone ref must be in.
        Sourced from `dem_profile["crs"]` for a per-fire run; left at the default for the
        Montecito gate, so that path is byte-for-byte unchanged (no test asserts a CRS literal).
      * NEW: ref's CRS must also equal other's CRS. Previously `other`'s CRS was NEVER examined
        (the comment below used to say SBS was "tied via shape+transform"); this is STRICTLY
        STRONGER -- two layers with equal shape/affine but different CRS would have slipped
        through, and now fail loud.
    CRS comparison is str().upper() on BOTH sides so 32613 (int), "epsg:32613" and "EPSG:32613"
    compare equal. The shape + transform equality asserts (what actually pin dNBR-A/B to the DEM
    grid) are UNCHANGED. The DEM-resolution check (transform.a == CELL_M) is a single-layer
    property, NOT a pairwise alignment check, so it stays at the call site.

    ref_name/other_name default to DEM/SBS so the existing messages stay byte-identical; pass
    other_name='dNBR' (etc.) for other pairs. Raises GateAbort on any mismatch (FM-10)."""
    exp = str(expected_crs).upper()
    if str(ref_profile["crs"]).upper() != exp:
        raise GateAbort(f"{ref_name} CRS {ref_profile['crs']} != {exp}.")
    if str(ref_profile["crs"]).upper() != str(other_profile["crs"]).upper():   # NEW: guard `other`'s CRS
        raise GateAbort(f"{ref_name}/{other_name} CRS differ: {ref_profile['crs']} != "
                        f"{other_profile['crs']} (alignment broken).")
    if (ref_profile["height"], ref_profile["width"]) != (other_profile["height"], other_profile["width"]):
        raise GateAbort(f"{ref_name} shape {(ref_profile['height'], ref_profile['width'])} != "
                        f"{other_name} shape {(other_profile['height'], other_profile['width'])} "
                        "(alignment broken).")
    if not ref_profile["transform"].almost_equals(other_profile["transform"]):
        raise GateAbort(f"{ref_name}/{other_name} affine transforms differ (alignment broken).")
