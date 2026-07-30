"""Raster agreement statistics for the auto-acquire stress test.

Three entry points, deliberately separate, ordered by how much they assume:

  compare_same_grid  -- identical CRS, transform and shape. Any difference RAISES.
  compare_aligned    -- same CRS and resolution, origins offset by WHOLE pixels.
                        Compares the intersection with NO resampling. This is the
                        Gate 0 case: the same scenes windowed to a slightly
                        different box land on the same lattice at a different
                        offset, which is not a defect.
  compare_regridded  -- anything else. Nearest-neighbour reprojection of b onto
                        a's grid; nearest (never bilinear) so the burn signal is
                        not smoothed. The returned caveat says geolocation error
                        is folded in and not separable.

shuffled_null supplies a floor anchor when two fires' scars do not overlap (so a
cross-fire comparison has zero co-valid pixels and cannot establish one).

Statistics are computed over CO-VALID pixels only -- valid in both rasters.
A constant array yields pearson/spearman None, not 0.0: zero variance means the
correlation is undefined, and reporting 0.0 would read as "no agreement" for two
rasters that are in fact identical.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from scipy.stats import pearsonr, spearmanr

_ATOL = 1e-6


def _read(path):
    with rasterio.open(path) as src:
        arr = src.read(1).astype("float64")
        nodata = src.nodata
        profile = {
            "crs": src.crs, "transform": src.transform,
            "height": src.height, "width": src.width,
            "res": src.res, "nodata": nodata,
        }
    valid = np.isfinite(arr)
    if nodata is not None:
        valid &= arr != nodata
    return arr, valid, profile


def _stats(a, b, valid, *, regridded, caveat, extra=None):
    av, bv = a[valid], b[valid]
    n = int(av.size)
    out = {
        "n_covalid": n, "pearson_r": None, "spearman_rho": None,
        "max_abs_diff": None, "mean_abs_diff": None,
        "regridded": regridded, "caveat": caveat,
    }
    if extra:
        out.update(extra)
    if n < 2:
        return out

    diff = np.abs(av - bv)
    out["max_abs_diff"] = float(diff.max())
    out["mean_abs_diff"] = float(diff.mean())
    # Zero variance -> correlation undefined, NOT zero.
    if np.ptp(av) > 0 and np.ptp(bv) > 0:
        out["pearson_r"] = float(pearsonr(av, bv).statistic)
        out["spearman_rho"] = float(spearmanr(av, bv).statistic)
    return out


def compare_same_grid(path_a, path_b) -> dict:
    """Agreement between two rasters already on an identical lattice.

    Raises ValueError on any grid difference -- silently resampling here would
    hide exactly the drift this function exists to detect.
    """
    a, a_valid, pa = _read(Path(path_a))
    b, b_valid, pb = _read(Path(path_b))

    same = (
        pa["crs"] == pb["crs"]
        and (pa["height"], pa["width"]) == (pb["height"], pb["width"])
        and np.allclose(tuple(pa["transform"])[:6], tuple(pb["transform"])[:6], atol=_ATOL)
    )
    if not same:
        raise ValueError(
            f"grid mismatch: a is {pa['width']}x{pa['height']} {pa['crs']} @ "
            f"{tuple(pa['transform'])[:6]}, b is {pb['width']}x{pb['height']} "
            f"{pb['crs']} @ {tuple(pb['transform'])[:6]}. Use compare_aligned for a "
            "whole-pixel offset, or compare_regridded if the difference is expected."
        )
    return _stats(a, b, a_valid & b_valid, regridded=False, caveat=None)


def compare_aligned(path_a, path_b) -> dict:
    """Agreement over the intersection of two pixel-aligned grids. No resampling.

    Requires identical CRS and resolution, and origins differing by a whole number
    of pixels. Anything else raises -- a sub-pixel offset cannot be compared
    without resampling, which is what compare_regridded is for.
    """
    a, a_valid, pa = _read(Path(path_a))
    b, b_valid, pb = _read(Path(path_b))

    if pa["crs"] != pb["crs"]:
        raise ValueError(f"CRS differs: {pa['crs']} vs {pb['crs']} -- use compare_regridded")
    if not np.allclose(pa["res"], pb["res"], atol=_ATOL):
        raise ValueError(
            f"resolution differs: {pa['res']} vs {pb['res']} -- use compare_regridded"
        )

    ta, tb = pa["transform"], pb["transform"]
    px, py = pa["res"][0], pa["res"][1]
    dx = (tb.c - ta.c) / px           # b origin relative to a, in pixels
    dy = (ta.f - tb.f) / py           # north-up: f decreases going south
    if abs(dx - round(dx)) > 1e-3 or abs(dy - round(dy)) > 1e-3:
        raise ValueError(
            f"grids are not pixel-aligned: origin offset is ({dx:.4f}, {dy:.4f}) px. "
            "Use compare_regridded."
        )
    dx, dy = int(round(dx)), int(round(dy))

    # Intersection in a's pixel coordinates.
    r0 = max(0, dy)
    c0 = max(0, dx)
    r1 = min(pa["height"], dy + pb["height"])
    c1 = min(pa["width"], dx + pb["width"])
    if r1 <= r0 or c1 <= c0:
        raise ValueError(
            f"grids do not overlap: offset ({dx}, {dy}) px, a is "
            f"{pa['width']}x{pa['height']}, b is {pb['width']}x{pb['height']}"
        )

    a_win = a[r0:r1, c0:c1]
    av_win = a_valid[r0:r1, c0:c1]
    b_win = b[r0 - dy:r1 - dy, c0 - dx:c1 - dx]
    bv_win = b_valid[r0 - dy:r1 - dy, c0 - dx:c1 - dx]

    return _stats(
        a_win, b_win, av_win & bv_win, regridded=False, caveat=None,
        extra={"offset_px": (dx, dy),
               "intersection_shape": (int(r1 - r0), int(c1 - c0))},
    )


def compare_regridded(path_a, path_b) -> dict:
    """Agreement after nearest-neighbour reprojection of b onto a's grid."""
    a, a_valid, pa = _read(Path(path_a))
    b, b_valid, pb = _read(Path(path_b))

    dst = np.full((pa["height"], pa["width"]), np.nan, dtype="float64")
    reproject(
        source=np.where(b_valid, b, np.nan), destination=dst,
        src_transform=pb["transform"], src_crs=pb["crs"],
        dst_transform=pa["transform"], dst_crs=pa["crs"],
        src_nodata=np.nan, dst_nodata=np.nan,
        resampling=Resampling.nearest,
    )
    return _stats(
        a, dst, a_valid & np.isfinite(dst), regridded=True,
        caveat="b reprojected onto a's grid (nearest neighbour, no smoothing). "
               "Cross-scene geolocation error is folded into the residual and is "
               "NOT separable from real dNBR disagreement.",
    )


def shuffled_null(path_a, *, seed=0) -> dict:
    """Within-raster null: a compared against a spatial shuffle of itself.

    Substitutes for a cross-fire floor when two scars do not overlap. Preserves
    the value DISTRIBUTION exactly and destroys only the spatial arrangement, so
    it isolates "does agreement come from structure or from both rasters simply
    having dNBR-shaped histograms".
    """
    a, a_valid, _ = _read(Path(path_a))
    rng = np.random.default_rng(seed)
    shuffled = a.copy()
    vals = a[a_valid].copy()
    rng.shuffle(vals)
    shuffled[a_valid] = vals
    return _stats(
        a, shuffled, a_valid, regridded=False,
        caveat="within-raster shuffled null: same value distribution, spatial "
               "structure destroyed. Use as the floor anchor when a cross-fire "
               "comparison has no overlapping pixels.",
    )
