"""CF-6 (A35) -- bbox -> UTM zone + 10 m canonical grid, the reproject TARGET.

PRIMARY GATE for the whole coordinate-frontend build: the generalized `acquire.py`,
fed South Fork's committed bbox, must reproduce the *hardcoded* South Fork canonical
grid that `validation/p3_acquire_dem.py` froze (A24 S3) -- so every committed South
Fork artifact (DEM, dNBR, the P3 validation) still aligns to the same grid.

Frozen grid (A24 S3, transcribed verbatim from data/southfork/dem/dem_source.json this
session -- `stated`): CRS EPSG:32613, 966 rows x 1439 cols @ 10 m, upper-left corner
(426400.8, 3697312.6). It was hand-anchored directly in UTM (frozen_bbox_A24), NOT
derived from a lon/lat box -- so the exact reproduction is from the committed UTM bbox.

KEY GEODESY FINDING (why corner-point min/max, not rasterio.warp.transform_bounds):
transform_bounds densifies each edge and returns the *outward-bowing* enclosing box of
the reprojected quadrilateral; a UTM->geo->UTM round-trip through it inflates South Fork
by ~14 cols / ~20 rows. Transforming the 4 CORNER POINTS (exact inverses, no bowing) and
taking min/max reproduces the frozen box to sub-mm. The unique rounding rule that yields
BOTH 1439 (from 1439.33) and 966 (from 965.9) is round() (floor breaks rows, ceil breaks
cols).

Run:  pytest tests/test_acquire_grid.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rasterio.transform import Affine

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from acquire import utm_epsg, canonical_grid  # noqa: E402  (repo-root module, A35)

# Frozen South Fork canonical grid (A24 S3), EPSG:32613. L,B,R,T in metres.
SFK_FROZEN_BBOX = (426400.8, 3687653.6, 440794.1, 3697312.6)
SFK_FROZEN_UL = (426400.8, 3697312.6)          # (left/west x, top/north y)
SFK_FROZEN_ROWS, SFK_FROZEN_COLS = 966, 1439
# South Fork's bbox in lon/lat (WGS84): axis-aligned min/max of the frozen box's corners.
SFK_LONLAT = (-105.791562, 33.325515, -105.636136, 33.413521)  # W, S, E, N


def test_utm_epsg_northern_zones():
    # UTM zone from lon: zone = floor((lon+180)/6)+1; northern hemisphere -> 326xx.
    assert utm_epsg(-117.0, 34.4) == 32611   # SoCal / Montecito zone 11N
    assert utm_epsg(-105.71, 33.37) == 32613  # South Fork / Ruidoso NM zone 13N
    assert utm_epsg(-108.1, 33.4) == 32612   # zone 12N (boundary sanity)


def test_utm_epsg_southern_hemisphere():
    # Southern hemisphere -> 327xx (guards the hemisphere branch though v0 fires are all northern).
    assert utm_epsg(-70.0, -33.4) == 32719   # Santiago-ish, zone 19S


def test_canonical_grid_reproduces_southfork_frozen_grid():
    """PRIMARY GATE: the committed UTM bbox -> the exact frozen grid."""
    g = canonical_grid(*SFK_FROZEN_BBOX, src_crs="EPSG:32613", dst_crs="EPSG:32613")
    assert g.crs == "EPSG:32613"
    assert (g.height, g.width) == (SFK_FROZEN_ROWS, SFK_FROZEN_COLS)   # 966 x 1439
    # Upper-left anchored exactly; 10 m cells, north-up.
    assert g.transform == Affine(10.0, 0.0, SFK_FROZEN_UL[0], 0.0, -10.0, SFK_FROZEN_UL[1])
    # Bounds are the UL-anchored extent (right/bottom = UL +/- shape*cell).
    left, bottom, right, top = g.bounds
    assert (left, top) == SFK_FROZEN_UL
    assert right == pytest.approx(426400.8 + 1439 * 10.0)
    assert bottom == pytest.approx(3697312.6 - 966 * 10.0)


def test_canonical_grid_derives_utm_zone_from_lonlat_centroid():
    """dst_crs=None + a lon/lat bbox -> zone auto-derived from the centroid (EPSG:32613)."""
    g = canonical_grid(*SFK_LONLAT)   # src defaults to EPSG:4326, dst auto
    assert g.crs == "EPSG:32613"
    assert g.transform.a == 10.0 and g.transform.e == -10.0   # 10 m, north-up


def test_canonical_grid_lonlat_box_covers_the_frozen_extent():
    """A user-drawn axis-aligned lon/lat box legitimately covers slightly MORE ground than the
    hand-anchored frozen UTM box (its (min_lon,max_lat) corner isn't a real corner of the region),
    so the grid is a superset -- never a subset -- of the frozen extent. Honest, documented."""
    g = canonical_grid(*SFK_LONLAT)
    gl, gb, gr, gt = g.bounds
    fl, fb, fr, ft = SFK_FROZEN_BBOX
    assert gl <= fl and gb <= fb and gr >= fr and gt >= ft        # superset (covers frozen)
    # ...only slightly larger (a bounded ~2% geodesic effect, not a transform_bounds blow-up):
    # covers the frozen cell-count and is at most ~5% bigger on each axis.
    assert SFK_FROZEN_COLS <= g.width <= round(SFK_FROZEN_COLS * 1.05)
    assert SFK_FROZEN_ROWS <= g.height <= round(SFK_FROZEN_ROWS * 1.05)
