"""Hermetic tests for the divergence comparison core.

The one real trap this locks: basins.geojson `basin_id` is a POST-FILTER
re-index, so joining two runs on it pairs unrelated basins. The join must be
by geometry, and mismatched areas on a joined pair must raise.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import geopandas as gpd
import pytest
from shapely.geometry import box

from validation.stress_divergence import compare_rankings


def _gdf(rows):
    """rows: (basin_id, rank, score, cell) with cell an integer grid slot."""
    recs = []
    for bid, rank, score, cell in rows:
        recs.append({
            "basin_id": bid, "rank": rank, "score": score,
            "area_km2": 1.0,
            "geometry": box(cell * 10, 0, cell * 10 + 9, 9),
        })
    return gpd.GeoDataFrame(recs, crs="EPSG:32613")


def test_identical_rankings_score_perfect_agreement():
    a = _gdf([(0, 1, 0.9, 0), (1, 2, 0.5, 1), (2, 3, 0.1, 2)])
    out = compare_rankings(a, a.copy())
    assert out["n_matched"] == 3
    assert out["spearman_score"] == pytest.approx(1.0)
    assert out["top10_overlap"] == 3          # min(k, n) basins all shared
    assert out["only_a"] == 0 and out["only_b"] == 0


def test_join_is_by_geometry_not_basin_id():
    """Same three basins, but b's basin_id numbering is shifted (the re-index
    trap). A basin_id join would scramble ranks; the geometry join must not."""
    a = _gdf([(0, 1, 0.9, 0), (1, 2, 0.5, 1), (2, 3, 0.1, 2)])
    b = _gdf([(5, 1, 0.9, 0), (6, 2, 0.5, 1), (7, 3, 0.1, 2)])
    out = compare_rankings(a, b)
    assert out["n_matched"] == 3
    assert out["spearman_score"] == pytest.approx(1.0)


def test_set_difference_is_reported_not_silently_dropped():
    a = _gdf([(0, 1, 0.9, 0), (1, 2, 0.5, 1), (2, 3, 0.1, 2)])
    b = _gdf([(0, 1, 0.9, 0), (1, 2, 0.5, 1)])          # b dropped one basin
    out = compare_rankings(a, b)
    assert out["n_matched"] == 2
    assert out["only_a"] == 1 and out["only_b"] == 0


def test_reversed_ranking_scores_negative():
    a = _gdf([(0, 1, 0.9, 0), (1, 2, 0.5, 1), (2, 3, 0.1, 2)])
    b = _gdf([(0, 3, 0.1, 0), (1, 2, 0.5, 1), (2, 1, 0.9, 2)])
    out = compare_rankings(a, b)
    assert out["spearman_score"] == pytest.approx(-1.0)


def test_area_mismatch_on_matched_geometry_raises():
    """Identical WKB with different areas means the inputs are inconsistent --
    that must never pass silently into a published number."""
    a = _gdf([(0, 1, 0.9, 0)])
    b = _gdf([(0, 1, 0.9, 0)])
    b.loc[0, "area_km2"] = 2.0
    with pytest.raises(ValueError, match="area"):
        compare_rankings(a, b)
