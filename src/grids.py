"""grids.py -- the inter-stage data contract: GateAbort, CRS helpers, alignment assertions.

Depends only on src.config; importing any other project module here would invert the contract.
"""
from __future__ import annotations

import numpy as np

from src.config import CANONICAL_CRS, ALLOWED_UTM_ZONES


class GateAbort(RuntimeError):
    """Raised when a stage precondition is violated -- fail loud, never degrade (FM-10)."""


def _assert_metric_crs(layer_crs, name: str) -> None:
    """Fail loud unless `layer_crs` is an allowed metric UTM zone -- never compute distances in degrees."""
    allowed = {f"EPSG:{z}" for z in ALLOWED_UTM_ZONES}
    if layer_crs is None or str(layer_crs).upper() not in allowed:
        raise GateAbort(f"{name} CRS is {layer_crs}, not in the allowed metric UTM zones "
                        f"{sorted(ALLOWED_UTM_ZONES)} (A25/A37 allowlist). Refusing to compute "
                        "distances in a non-metric / non-CONUS CRS.")


def _rc_to_xy(rows: np.ndarray, cols: np.ndarray, transform) -> np.ndarray:
    """Cell (row, col) -> projected (x, y) cell-centre coords (metres, in the layer's CRS)."""
    a, _, c, _, e, f = (transform.a, transform.b, transform.c,
                        transform.d, transform.e, transform.f)
    return np.column_stack([c + a * (cols + 0.5), f + e * (rows + 0.5)])


def assert_aligned(ref_profile, other_profile, *, ref_name: str = "DEM",
                   other_name: str = "SBS", expected_crs=CANONICAL_CRS) -> None:
    """Fail loud (GateAbort) unless two rasters share one grid: same CRS, shape, and affine.
    `expected_crs` is the zone `ref` must be in (pass the fire's own DEM CRS for a per-fire run)."""
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
