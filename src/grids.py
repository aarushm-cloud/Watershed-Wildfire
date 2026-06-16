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

from src.config import CANONICAL_CRS


class GateAbort(RuntimeError):
    """Raised when a stage precondition is violated -- fail loud, never degrade (FM-10)."""


def _assert_metric_crs(layer_crs, name: str) -> None:
    """Fail loud unless `layer_crs` is the canonical metric CRS (EPSG:32611)."""
    if layer_crs is None or str(layer_crs).upper() != CANONICAL_CRS:
        raise GateAbort(f"{name} CRS is {layer_crs}, expected {CANONICAL_CRS} (metric). "
                        "Refusing to compute distances in a non-metric CRS.")


def _rc_to_xy(rows: np.ndarray, cols: np.ndarray, transform) -> np.ndarray:
    """Cell (row, col) -> projected (x, y) cell-centre coords (metres, EPSG:32611)."""
    a, _, c, _, e, f = (transform.a, transform.b, transform.c,
                        transform.d, transform.e, transform.f)
    return np.column_stack([c + a * (cols + 0.5), f + e * (rows + 0.5)])


def assert_aligned(ref_profile, other_profile, *, ref_name: str = "DEM",
                   other_name: str = "SBS") -> None:
    """Fail loud unless two rasters share the canonical grid (CRS / shape / affine).

    EXTRACTED VERBATIM (P2.2a) from the inline DEM/SBS check in gate.stage_2a_hydrology --
    same three checks, same GateAbort, same messages. Operates on rasterio PROFILE dicts
    (`crs`, `width`/`height`, `transform`) so it is reusable for any layer pair (P2.2b reuses
    it for the reprojected-dNBR-vs-DEM check). NOTE: only the FIRST profile's CRS is checked
    against canonical -- exactly as the original did (DEM CRS; SBS is tied to the same grid via
    the shape + transform equality). The DEM-resolution check (transform.a == CELL_M) is a
    single-layer property, NOT a pairwise alignment check, so it stays at the call site.

    ref_name/other_name default to DEM/SBS so the existing messages stay byte-identical; pass
    other_name='dNBR' (etc.) for other pairs. Raises GateAbort on any mismatch (FM-10)."""
    if str(ref_profile["crs"]).upper() != CANONICAL_CRS:
        raise GateAbort(f"{ref_name} CRS {ref_profile['crs']} != {CANONICAL_CRS}.")
    if (ref_profile["height"], ref_profile["width"]) != (other_profile["height"], other_profile["width"]):
        raise GateAbort(f"{ref_name} shape {(ref_profile['height'], ref_profile['width'])} != "
                        f"{other_name} shape {(other_profile['height'], other_profile['width'])} "
                        "(alignment broken).")
    if not ref_profile["transform"].almost_equals(other_profile["transform"]):
        raise GateAbort(f"{ref_name}/{other_name} affine transforms differ (alignment broken).")
