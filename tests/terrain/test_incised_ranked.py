"""A39 -- incised terrain ranks instead of refusing."""
from pathlib import Path

import numpy as np
import pytest
import rasterio

# The OLD A27 detector fixture (Task 8 note, tests/conftest.py): a single triangular channel with
# no tributaries -- WBT segmentation returns all-zero labels on it at the frozen
# SUBBASIN_ACC_THRESHOLD_CELLS, so it backs the phase-1-empty guard rather than a ranked result.
SYNTH_FIXTURE = Path("tests/fixtures/incised_synthetic.tif")


def test_incised_fire_returns_ranked_not_refused(incised_fire):
    from src.pipeline import run_pipeline
    result = run_pipeline(incised_fire)
    assert result["status"] == "ranked"
    assert result["terrain_mode"] == "incised"
    assert result["basin_engine"] == "whiteboxtools"
    assert result["basins"]


def test_incised_result_has_the_arms_shape_the_ui_requires(incised_fire):
    """app.py:75-77 degrades a ranked result without `arms` to kind='unknown'."""
    from src.pipeline import run_pipeline
    result = run_pipeline(incised_fire)
    assert set(result["arms"]) == {"arm_a", "arm_b"}
    assert result["headline_arm"] == "arm_a"


def test_both_arms_share_one_basin_set(incised_fire):
    """A39 clause 6: Arm A defines basins; Arm B must score the identical set or
    rank_delta is comparing different geometry."""
    from src.pipeline import run_pipeline
    result = run_pipeline(incised_fire)
    a = [b["basin_id"] for b in result["arms"]["arm_a"]["basins"]]
    b = [b["basin_id"] for b in result["arms"]["arm_b"]["basins"]]
    assert a == b


def test_incised_basins_carry_intensity(incised_fire):
    from src.pipeline import run_pipeline
    result = run_pipeline(incised_fire)
    for b in result["basins"]:
        assert b["intensity"] == pytest.approx(b["mean_burn"] * b["mean_slope"])
        assert b["intensity_rank"] >= 1


def test_incised_writes_no_refusal(incised_fire):
    from src.pipeline import run_pipeline
    run_pipeline(incised_fire)
    assert not (incised_fire["out_dir"] / "refusal.json").exists()


def test_incised_with_sbs_fails_loud(incised_fire):
    """A39 v1: incised+SBS must abort, never emit an undisclaimed ranking."""
    from src.grids import GateAbort
    from src.pipeline import run_pipeline
    fire = dict(incised_fire)
    fire["dnbr"] = None
    fire["sbs"] = "data/southfork/burn/arm_a_cls.tif"   # any real SBS raster
    with pytest.raises(GateAbort, match="incised"):
        run_pipeline(fire)


def test_range_front_still_uses_pysheds():
    """Two tiers, two engines."""
    from src.pipeline import run_pipeline, MONTECITO_DNBR_FIRE
    result = run_pipeline(MONTECITO_DNBR_FIRE)
    assert result["status"] == "ranked"
    assert result["terrain_mode"] == "range_front"
    assert result.get("basin_engine") is None


def test_incised_phase1_empty_fails_loud(tmp_path):
    """Zero sub-basins surviving phase-1 geometry filtering must abort, never emit an empty
    ranking. SYNTH_FIXTURE segments to all-zero labels at the frozen threshold (Task 8)."""
    from src.grids import GateAbort
    from src.pipeline import run_pipeline

    with rasterio.open(SYNTH_FIXTURE) as ds:
        profile = ds.profile.copy()
        shape = (ds.height, ds.width)

    dnbr = np.zeros(shape, dtype="float32")
    dnbr[: shape[0] // 2, :] = 0.55
    dnbr_path = tmp_path / "dnbr.tif"
    profile.update(count=1, dtype="float32", nodata=-9999.0)
    with rasterio.open(dnbr_path, "w", **profile) as dst:
        dst.write(dnbr, 1)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    fire = {"name": "phase1_empty_probe",
            "dem": str(SYNTH_FIXTURE),
            "sbs": None,
            "dnbr": str(dnbr_path),
            "assets": None,
            "creeks": None,
            "out_dir": out_dir,
            "expected_crs": "EPSG:32613",
            "validation_case": "phase1_empty_probe"}

    with pytest.raises(GateAbort, match="no sub-basins survive geometry filtering"):
        run_pipeline(fire)


def test_incised_label_shape_mismatch_fails_loud(monkeypatch, incised_fire):
    """A truncated/misaligned label grid from segment_subbasins must never be silently indexed
    -- this is the explicit stand-in for assert_aligned, which takes rasterio profiles, not
    bare label arrays."""
    import src.subbasins as subbasins_mod
    from src.grids import GateAbort
    from src.pipeline import run_pipeline

    def _fake_segment_subbasins(dem_tif, work_dir, **kwargs):
        labels = np.zeros((5, 5), dtype=np.int32)
        meta = {"engine": "whiteboxtools", "wbt_version": "fake",
                "acc_threshold_cells": 3000, "breach_dist_cells": 100,
                "_acc": np.zeros((5, 5), dtype=np.float64)}
        return labels, meta

    monkeypatch.setattr(subbasins_mod, "segment_subbasins", _fake_segment_subbasins)

    with pytest.raises(GateAbort, match="subbasin labels shape"):
        run_pipeline(incised_fire)


def test_incised_phase2_empty_fails_loud(incised_fire, tmp_path):
    """Zero sub-basins surviving phase-2 burn+steepness filtering must abort, never emit an
    empty ranking. An all-zero dNBR guarantees the drop at any frozen burn-fraction floor."""
    from src.grids import GateAbort
    from src.pipeline import run_pipeline

    with rasterio.open(incised_fire["dnbr"]) as src:
        profile = src.profile.copy()
        shape = (src.height, src.width)

    all_zero = np.zeros(shape, dtype="float32")   # nothing burned
    dnbr_path = tmp_path / "dnbr_allzero.tif"
    with rasterio.open(dnbr_path, "w", **profile) as dst:
        dst.write(all_zero, 1)

    fire = dict(incised_fire)
    fire["dnbr"] = str(dnbr_path)

    with pytest.raises(GateAbort, match="sufficiently burned and steep"):
        run_pipeline(fire)


def test_incised_creeks_layer_fails_loud(incised_fire):
    """Latent renumbering trap: creek matching runs on phase-1 basin numbering, but
    filter_burned_steep (phase 2) renumbers survivors from 0 -- a creek match would silently
    attach to the wrong basin. Unreachable today (every incised fire sets creeks=None); this
    locks the guard that keeps it that way."""
    from src.grids import GateAbort
    from src.pipeline import run_pipeline

    fire = dict(incised_fire)
    fire["creeks"] = "any_nonNone_value.geojson"
    with pytest.raises(GateAbort, match="creek/truth matching is not supported"):
        run_pipeline(fire)
