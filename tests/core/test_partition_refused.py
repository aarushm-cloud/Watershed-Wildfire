"""A41 partition tests: creeks=None dNBR runs refuse per-basin instead of aborting the run.
Flowed path (creeks present) is UNCHANGED -- Task 1 locks pin it."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import DNBR_NODATA_FAILLOUD_FRAC
from src.grids import GateAbort
from src.pipeline import _partition_refused


def _b(bid, mask):
    return {"basin_id": bid, "mask": np.asarray(mask, dtype=bool)}


def _grid(rows_cloudy):
    nd = np.zeros((10, 10), dtype=bool)
    nd[:rows_cloudy, :] = True
    return nd


def test_partition_splits_at_frozen_bar_and_attaches_frac():
    m_all = np.ones((10, 10), dtype=bool)
    m_low = np.zeros((10, 10), dtype=bool); m_low[8:, :] = True   # clean rows only
    clean, refused = _partition_refused([_b(0, m_all), _b(1, m_low)], _grid(3))
    assert [b["basin_id"] for b in refused] == [0]                # 30% > 0.20
    assert [b["basin_id"] for b in clean] == [1]
    assert refused[0]["nodata_frac"] == pytest.approx(0.30)
    assert clean[0]["nodata_frac"] == pytest.approx(0.0)


def test_partition_boundary_is_strictly_greater():
    m = np.ones((10, 10), dtype=bool)
    clean, refused = _partition_refused([_b(0, m)], _grid(2))     # exactly 20%
    assert refused == [] and len(clean) == 1                      # frozen '>' comparison


def test_partition_never_scores_refused():
    """Refused records must carry no rank/score keys -- they were never scored."""
    m = np.ones((10, 10), dtype=bool)
    _, refused = _partition_refused([_b(0, m)], _grid(5))
    assert "score" not in refused[0] and "rank" not in refused[0]


def test_gateabort_scope_default_and_attempt():
    assert GateAbort("x").scope == "fire"                         # unclassified = stop loud
    assert GateAbort("x", scope="attempt").scope == "attempt"


def test_erasure_channel_closed_partition_precedes_burn_filter():
    """A cloud-swamped BURNED basin must be refused, not vanish via filter_burned_steep
    (cloud counts as unburned there -- subbasins.py:141). 80% cloud is the TRUE erasure
    regime: burn_frac 0.20 < SUBBASIN_BURN_FRAC_MIN, so the filter DELETES the basin with no
    trace. Partition-first refuses it first. (50% is the milder regime the filter keeps.)"""
    from src.config import SUBBASIN_BURN_FRAC_MIN
    from src.subbasins import filter_burned_steep
    m = np.ones((10, 10), dtype=bool)
    slope = np.full((10, 10), 0.3)

    nd80 = _grid(8)                                   # 80% cloud
    wt80 = np.where(nd80, 0.0, 1.0)                   # cloud -> weight 0 (frozen mapping)
    assert (1.0 - 0.80) < SUBBASIN_BURN_FRAC_MIN      # erasure regime, stated from the frozen floor
    assert filter_burned_steep([_b(0, m)], wt80, slope) == []    # the filter ERASES it, silently
    assert len(_partition_refused([_b(0, m)], nd80)[1]) == 1     # partition-first REFUSES it instead

    nd50 = _grid(5)                                   # milder: filter keeps it, still over the bar
    wt50 = np.where(nd50, 0.0, 1.0)
    assert len(filter_burned_steep([_b(0, m)], wt50, slope)) == 1
    assert len(_partition_refused([_b(0, m)], nd50)[1]) == 1


def _clouded_incised_fire(fire, tmp_path):
    """The hermetic incised fire with NoData punched over the upper third of its dNBR --
    refuses 2 sub-basins and still leaves 2 clean to rank."""
    with rasterio.open(fire["dnbr"]) as ds:
        dnbr = ds.read(1)
        profile = ds.profile.copy()
    dnbr[: dnbr.shape[0] // 3, :] = profile["nodata"]
    clouded_path = tmp_path / "dnbr_clouded.tif"
    with rasterio.open(clouded_path, "w", **profile) as dst:
        dst.write(dnbr, 1)
    clouded = dict(fire)
    clouded["dnbr"] = str(clouded_path)
    return clouded


def test_run_pipeline_partitions_before_filter_burned_steep(incised_fire, tmp_path, monkeypatch):
    """The ORDER lock (the erasure channel is an ordering property, not a helper property):
    filter_burned_steep may only ever see POST-partition records -- every one carrying
    nodata_frac, none over the bar. Moving the partition back below the filter turns this RED."""
    import src.subbasins as subbasins_mod
    from src.pipeline import run_pipeline

    seen = []
    real_filter = subbasins_mod.filter_burned_steep

    def spy(records, burn_weight, slope_tan):
        seen.append([dict(r) for r in records])       # snapshot AT CALL TIME
        return real_filter(records, burn_weight, slope_tan)

    monkeypatch.setattr(subbasins_mod, "filter_burned_steep", spy)

    R = run_pipeline(_clouded_incised_fire(incised_fire, tmp_path))

    assert len(seen) == 1                             # the filter really ran (never vacuously green)
    handed = seen[0]
    assert handed and all("nodata_frac" in r for r in handed)          # the partition ran FIRST
    assert all(r["nodata_frac"] <= DNBR_NODATA_FAILLOUD_FRAC for r in handed)
    assert R["refused_basins"]                                          # and it did refuse basins
    # phase-1 id space on both sides here (the filter renumbers only its own copies)
    assert {b["basin_id"] for b in R["refused_basins"]}.isdisjoint({r["basin_id"] for r in handed})


def test_refused_basins_are_logged_not_silent(incised_fire, tmp_path, caplog):
    """A refused basin is ABSENT from the ranking -- the run must say so out loud, or a
    clouded fire returns a silently shortened 'ranked' result (A41)."""
    from src.pipeline import run_pipeline

    with caplog.at_level(logging.WARNING, logger="src.pipeline"):
        R = run_pipeline(_clouded_incised_fire(incised_fire, tmp_path))

    assert R["refused_basins"]
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    hit = [m for m in warned if "REFUSED, not ranked" in m]
    assert len(hit) == 1
    assert str(len(R["refused_basins"])) in hit[0]
    for b in R["refused_basins"]:
        assert str(b["basin_id"]) in hit[0]
