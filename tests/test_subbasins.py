"""A39 -- WhiteboxTools sub-basin delineation."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


def test_whitebox_binary_is_present_without_network():
    """The binary must already be installed -- src/ is a no-network seam (A35), so a
    runtime download would violate it. This asserts install-time provisioning."""
    import importlib.util
    import os
    import platform

    # Locate the package WITHOUT constructing WhiteboxTools -- its __init__ calls
    # download_wbt() unconditionally, which would perform the very network fetch this test
    # exists to forbid and silently turn a missing binary into a passing test.
    spec = importlib.util.find_spec("whitebox")
    assert spec is not None and spec.origin, "whitebox is not installed/importable"
    pkg_dir = os.path.dirname(spec.origin)
    exe_name = "whitebox_tools.exe" if platform.system() == "Windows" else "whitebox_tools"
    exe = os.path.join(pkg_dir, exe_name)
    assert os.path.exists(exe), (
        f"WhiteboxTools binary missing at {exe}. Run the install step in Task 2 -- it must "
        f"NOT be fetched lazily at runtime.")

    import whitebox
    wbt = whitebox.WhiteboxTools()
    version = wbt.version()
    assert version and "WhiteboxTools" in version
    # The A39 ratification evidence (AUC 0.887, 88 basins, 10/10 top-10 flowed) was produced
    # against engine v2.4.0, so segmentation output is coupled to that engine version -- pin it
    # here. NB: the pip wrapper (2.3.6) is versioned separately from the engine it provisions.
    assert "v2.4.0" in version, (
        f"WhiteboxTools engine version drifted from the A39-validated v2.4.0: {version!r}")


def _write_synthetic_dem(path, shape=(200, 200), cell=10.0):
    """A valley draining south with two tributary notches, so segmentation has real
    confluences to split on."""
    rows, cols = shape
    yy, xx = np.mgrid[0:rows, 0:cols].astype("float32")
    dem = 2000.0 - yy * 2.0 + np.abs(xx - cols / 2.0) * 3.0
    dem[60:70, : cols // 2] -= 25.0
    dem[130:140, cols // 2 :] -= 25.0
    prof = dict(driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
                crs="EPSG:32613", transform=from_origin(400000.0, 3700000.0, cell, cell),
                nodata=-9999.0)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(dem.astype("float32"), 1)
    return dem


def test_segment_subbasins_returns_labels_on_dem_grid(tmp_path):
    from src.subbasins import segment_subbasins
    dem_tif = tmp_path / "dem.tif"
    dem = _write_synthetic_dem(dem_tif)
    # a toy threshold: the frozen 3000-cell value is tuned for real fires, not a 200x200 toy
    labels, meta = segment_subbasins(str(dem_tif), str(tmp_path / "work"),
                                     acc_threshold_cells=300)
    assert labels.shape == dem.shape
    assert labels.dtype == np.int32
    assert labels.max() >= 2, "a valley with two tributaries must yield >=2 sub-basins"
    assert (labels >= 0).all()
    assert meta["engine"] == "whiteboxtools"
    assert meta["acc_threshold_cells"] == 300
    assert "wbt_version" in meta


def test_segment_subbasins_defaults_to_the_frozen_threshold(tmp_path):
    """The pipeline must get the frozen value, not a test value."""
    import inspect
    from src.subbasins import segment_subbasins
    from src.config import SUBBASIN_ACC_THRESHOLD_CELLS
    default = inspect.signature(segment_subbasins).parameters["acc_threshold_cells"].default
    assert default == SUBBASIN_ACC_THRESHOLD_CELLS == 3000


def test_segment_subbasins_raises_on_a_no_output_step(tmp_path, monkeypatch):
    """whitebox==2.3.6's run_tool() returns 0 even when the WBT binary panics and writes
    nothing -- rc==0 alone can't be trusted (A8). Stub a step to do exactly that (return 0,
    write no file) through the real code path and confirm segment_subbasins raises instead of
    quietly continuing."""
    import whitebox
    from src.grids import GateAbort
    from src.subbasins import segment_subbasins

    dem_tif = tmp_path / "dem.tif"
    _write_synthetic_dem(dem_tif)

    def breach_depressions_least_cost(self, *args, **kwargs):
        return 0  # simulates a WBT panic: reports success, writes no output file

    monkeypatch.setattr(whitebox.WhiteboxTools, "breach_depressions_least_cost",
                        breach_depressions_least_cost)

    with pytest.raises(GateAbort, match="breach_depressions_least_cost"):
        segment_subbasins(str(dem_tif), str(tmp_path / "work"), acc_threshold_cells=300)


def test_stale_subbasins_tif_is_not_returned_after_a_failed_step(tmp_path, monkeypatch):
    """The actual regression: A39's real call site reuses one fixed work_dir per fire across
    re-runs. Pre-seed it with a stale subbasins.tif standing in for a previous run, make the
    final WBT step silently no-op (rc==0, no write -- the reproduced panic), and confirm the
    stale file is gone rather than read back as this run's result."""
    import whitebox
    from src.grids import GateAbort
    from src.subbasins import segment_subbasins

    dem_tif = tmp_path / "dem.tif"
    _write_synthetic_dem(dem_tif)
    work = tmp_path / "work"
    work.mkdir()

    stale = np.full((200, 200), 77, dtype=np.int32)
    prof = dict(driver="GTiff", height=200, width=200, count=1, dtype="int32",
                crs="EPSG:32613", transform=from_origin(400000.0, 3700000.0, 10.0, 10.0))
    with rasterio.open(work / "subbasins.tif", "w", **prof) as dst:
        dst.write(stale, 1)

    def subbasins(self, *args, **kwargs):
        return 0  # simulates a WBT panic on the final step: rc==0, no new file written

    monkeypatch.setattr(whitebox.WhiteboxTools, "subbasins", subbasins)

    with pytest.raises(GateAbort, match="subbasins"):
        segment_subbasins(str(dem_tif), str(work), acc_threshold_cells=300)

    assert not (work / "subbasins.tif").exists(), (
        "a stale prior-run raster must not survive a failed step -- reading it back would "
        "fabricate a plausible-looking wrong result instead of failing loud (A8)")


def test_segment_subbasins_succeeds_on_repeated_runs_of_the_same_work_dir(tmp_path):
    """The real call site is segment_subbasins(fire['dem'], out_dir/'_wbt') -- one fixed
    work_dir reused on every re-run of that fire. Purging stale output at entry must not break
    that legitimate, common case; two clean runs must both succeed with the same contract."""
    from src.subbasins import segment_subbasins

    dem_tif = tmp_path / "dem.tif"
    dem = _write_synthetic_dem(dem_tif)
    work = tmp_path / "work"

    labels1, meta1 = segment_subbasins(str(dem_tif), str(work), acc_threshold_cells=300)
    labels2, meta2 = segment_subbasins(str(dem_tif), str(work), acc_threshold_cells=300)

    for labels, meta in ((labels1, meta1), (labels2, meta2)):
        assert labels.shape == dem.shape
        assert labels.dtype == np.int32
        assert labels.max() >= 2
        assert (labels >= 0).all()
        assert meta["engine"] == "whiteboxtools"
    assert np.array_equal(labels1, labels2), (
        "same DEM through the same work_dir twice must reproduce the same labels")


def _write_buffered_dem(dem_tif, pad_top=40, pad_lr=40, cell=10.0):
    """Wraps _write_synthetic_dem with real margin -- needed because that fixture's valley
    has its true ridge/head divides sitting EXACTLY on the raster's own edges (zero buffer).
    build_geometry_records's edge-truncation filter then (correctly) drops every basin,
    since it cannot tell "true divide happens to be the array edge" apart from "this DEM
    tile doesn't cover the whole watershed" -- both look identical from inside the array.
    A real DEM download always carries margin beyond the fire's watershed, so its interior
    divides are NOT coincident with the tile boundary; mirror-padding here reproduces that
    margin without inventing new terrain math (np.pad reflect just extends the existing
    monotonic slope back down on the far side of each true ridge/head, i.e. "there is more
    terrain beyond the divide," same as the real case). No pad on the south side: the
    valley's south edge is its true outlet, and a basin containing the domain's pour point
    always touches SOME edge in any finite DEM -- that exclusion is correct, not a fixture
    artifact, so this helper does not try to hide it.
    """
    inner = dem_tif.parent / "_inner_dem.tif"
    dem = _write_synthetic_dem(inner, cell=cell)
    padded = np.pad(dem, ((pad_top, 0), (pad_lr, pad_lr)), mode="reflect").astype("float32")
    prof = dict(driver="GTiff", height=padded.shape[0], width=padded.shape[1], count=1,
                dtype="float32", crs="EPSG:32613",
                transform=from_origin(400000.0, 3700000.0, cell, cell), nodata=-9999.0)
    with rasterio.open(dem_tif, "w", **prof) as dst:
        dst.write(padded, 1)
    return padded


def test_geometry_records_match_the_scorer_contract(tmp_path):
    from src.subbasins import segment_subbasins, build_geometry_records
    from src.config import MIN_BASIN_KM2
    dem_tif = tmp_path / "dem.tif"
    dem = _write_buffered_dem(dem_tif)
    labels, meta = segment_subbasins(str(dem_tif), str(tmp_path / "w"),
                                     acc_threshold_cells=300)
    recs = build_geometry_records(labels, dem, -9999.0, meta["_acc"])
    assert recs
    for r in recs:
        assert set(r) == {"outlet", "mask", "area_km2", "asset_m", "basin_id"}
        assert r["mask"].dtype == bool and r["mask"].shape == dem.shape
        assert r["mask"].any(), "A32: a zero-cell mask would abort scoring"
        assert not r["mask"].flags.writeable
        assert r["area_km2"] >= MIN_BASIN_KM2
        assert r["asset_m"] is None
    assert [r["basin_id"] for r in recs] == list(range(len(recs)))


def test_geometry_records_drop_footprint_truncated_basins(tmp_path):
    """Non-vacuous: gouging nodata through the middle must remove basins."""
    from src.subbasins import segment_subbasins, build_geometry_records
    dem_tif = tmp_path / "dem.tif"
    dem = _write_buffered_dem(dem_tif)
    labels, meta = segment_subbasins(str(dem_tif), str(tmp_path / "w"),
                                     acc_threshold_cells=300)
    full = build_geometry_records(labels, dem, -9999.0, meta["_acc"])
    holed = dem.copy()
    holed[:, 135:145] = -9999.0   # the original fixture's [:, 95:105] gouge, shifted +40 (pad_lr)
    assert len(build_geometry_records(labels, holed, -9999.0, meta["_acc"])) < len(full)


def test_outlet_is_the_max_accumulation_cell(tmp_path):
    """The pour point is where flow leaves the basin -- the max-accumulation cell. The
    minimum-elevation cell can be an interior artifact after breach-carving."""
    from src.subbasins import segment_subbasins, build_geometry_records
    dem_tif = tmp_path / "dem.tif"
    dem = _write_buffered_dem(dem_tif)
    labels, meta = segment_subbasins(str(dem_tif), str(tmp_path / "w"),
                                     acc_threshold_cells=300)
    recs = build_geometry_records(labels, dem, -9999.0, meta["_acc"])
    assert recs, "vacuous otherwise -- the loop below would silently pass on zero basins"
    for r in recs:
        acc_in = np.where(r["mask"], meta["_acc"], -np.inf)
        assert meta["_acc"][r["outlet"]] == acc_in.max()


def _one_record(shape=(20, 20)):
    m = np.zeros(shape, bool); m[5:15, 5:15] = True; m.flags.writeable = False
    return [{"outlet": (14, 14), "mask": m, "area_km2": 1.0,
             "asset_m": None, "basin_id": 0}]


def test_filter_keeps_burned_and_steep():
    from src.subbasins import filter_burned_steep
    recs = _one_record()
    burn = np.ones((20, 20), "float32")
    slope = np.full((20, 20), 0.5, "float32")
    out = filter_burned_steep(recs, burn, slope)
    assert len(out) == 1
    assert out[0]["burn_frac"] == pytest.approx(1.0)


def test_filter_drops_unburned():
    from src.subbasins import filter_burned_steep
    assert filter_burned_steep(_one_record(), np.zeros((20, 20), "float32"),
                               np.full((20, 20), 0.5, "float32")) == []


def test_filter_drops_flat():
    from src.subbasins import filter_burned_steep
    assert filter_burned_steep(_one_record(), np.ones((20, 20), "float32"),
                               np.zeros((20, 20), "float32")) == []


def test_filter_renumbers_basin_ids_contiguously():
    from src.subbasins import filter_burned_steep
    m1 = np.zeros((20, 20), bool); m1[0:5, 0:5] = True; m1.flags.writeable = False
    m2 = np.zeros((20, 20), bool); m2[10:15, 10:15] = True; m2.flags.writeable = False
    recs = [{"outlet": (4, 4), "mask": m1, "area_km2": 1.0, "asset_m": None, "basin_id": 0},
            {"outlet": (14, 14), "mask": m2, "area_km2": 1.0, "asset_m": None, "basin_id": 1}]
    burn = np.zeros((20, 20), "float32"); burn[10:15, 10:15] = 1.0   # only the 2nd is burned
    slope = np.full((20, 20), 0.5, "float32")
    out = filter_burned_steep(recs, burn, slope)
    assert len(out) == 1
    assert out[0]["basin_id"] == 0, "ids must be contiguous from 0 after filtering"


def test_filter_partial_nan_in_slope_uses_finite_only_stats():
    """A33: real slope rasters carry a NaN ring. 40 of the 100 masked cells are unburned with
    NaN slope; excluding them from `finite` (rather than all 100) is what keeps burn_frac
    above SUBBASIN_BURN_FRAC_MIN here -- burned/100 would be 0.20 (dropped)."""
    from src.subbasins import filter_burned_steep
    recs = _one_record()
    burn = np.zeros((20, 20), "float32")
    burn[5:7, 5:15] = 1.0            # 20 cells: burned, finite slope
    slope = np.full((20, 20), 0.5, "float32")
    slope[11:15, 5:15] = np.nan      # 40 cells: unburned, NaN slope -- excluded from finite
    # remaining 40 cells (rows 7:11): unburned, finite slope
    out = filter_burned_steep(recs, burn, slope)
    assert len(out) == 1
    assert out[0]["burn_frac"] == pytest.approx(20 / 60), "burned/finite, not burned/100"


def test_filter_partial_nan_in_burn_uses_finite_only_stats():
    """Same coupling, NaN on the other input: 60 of the 100 masked cells have NaN burn_weight;
    excluding them raises burn_frac to 0.5 -- naive burned/100 would give 0.20 (dropped)."""
    from src.subbasins import filter_burned_steep
    recs = _one_record()
    burn = np.zeros((20, 20), "float32")
    burn[5:7, 5:15] = 1.0            # 20 cells: burned, finite
    burn[9:15, 5:15] = np.nan        # 60 cells: NaN burn -- excluded from finite
    # remaining 20 cells (rows 7:9): unburned, finite
    slope = np.full((20, 20), 0.5, "float32")
    out = filter_burned_steep(recs, burn, slope)
    assert len(out) == 1
    assert out[0]["burn_frac"] == pytest.approx(20 / 40)


def test_filter_all_nan_slope_drops_the_basin():
    from src.subbasins import filter_burned_steep
    burn = np.ones((20, 20), "float32")
    slope = np.full((20, 20), np.nan, "float32")
    assert filter_burned_steep(_one_record(), burn, slope) == []


def test_filter_all_nan_burn_drops_the_basin():
    from src.subbasins import filter_burned_steep
    burn = np.full((20, 20), np.nan, "float32")
    slope = np.full((20, 20), 0.5, "float32")
    assert filter_burned_steep(_one_record(), burn, slope) == []


def test_filter_burn_frac_at_threshold_is_kept():
    from src.subbasins import filter_burned_steep
    from src.config import SUBBASIN_BURN_FRAC_MIN
    assert SUBBASIN_BURN_FRAC_MIN == 0.25, "fixture below is hand-built for the frozen 0.25"
    recs = _one_record()
    burn = np.zeros((20, 20), "float32")
    burn[5:7, 5:15] = 1.0    # 20
    burn[7, 5:10] = 1.0      # 5 -> 25/100 == 0.25 exactly
    slope = np.full((20, 20), 0.5, "float32")
    out = filter_burned_steep(recs, burn, slope)
    assert len(out) == 1
    assert out[0]["burn_frac"] == pytest.approx(0.25)


def test_filter_burn_frac_just_below_threshold_is_dropped():
    from src.subbasins import filter_burned_steep
    from src.config import SUBBASIN_BURN_FRAC_MIN
    assert SUBBASIN_BURN_FRAC_MIN == 0.25, "fixture below is hand-built for the frozen 0.25"
    recs = _one_record()
    burn = np.zeros((20, 20), "float32")
    burn[5:7, 5:15] = 1.0    # 20
    burn[7, 5:9] = 1.0       # 4 -> 24/100 == 0.24, just below
    slope = np.full((20, 20), 0.5, "float32")
    assert filter_burned_steep(recs, burn, slope) == []


def test_filter_slope_mean_at_floor_is_kept():
    from src.subbasins import filter_burned_steep
    from src.config import SUBBASIN_SLOPE_FLOOR_TAN
    assert SUBBASIN_SLOPE_FLOOR_TAN == 0.05, "fixture below is hand-built for the frozen 0.05"
    recs = _one_record()
    burn = np.ones((20, 20), "float32")
    slope = np.full((20, 20), 0.05, "float32")
    out = filter_burned_steep(recs, burn, slope)
    assert len(out) == 1


def test_filter_slope_mean_just_below_floor_is_dropped():
    from src.subbasins import filter_burned_steep
    from src.config import SUBBASIN_SLOPE_FLOOR_TAN
    assert SUBBASIN_SLOPE_FLOOR_TAN == 0.05, "fixture below is hand-built for the frozen 0.05"
    recs = _one_record()
    burn = np.ones((20, 20), "float32")
    slope = np.full((20, 20), 0.049, "float32")
    assert filter_burned_steep(recs, burn, slope) == []


def test_filter_does_not_mutate_input_records():
    """Locks the dict(rec) copy: output basin_id is renumbered, input basin_id/mask are not."""
    from src.subbasins import filter_burned_steep
    recs = _one_record()
    recs[0]["basin_id"] = 7
    mask = recs[0]["mask"]
    burn = np.ones((20, 20), "float32")
    slope = np.full((20, 20), 0.5, "float32")
    out = filter_burned_steep(recs, burn, slope)
    assert len(out) == 1
    assert out[0] is not recs[0]
    assert out[0]["basin_id"] == 0
    assert recs[0]["basin_id"] == 7
    assert "burn_frac" not in recs[0]
    assert recs[0]["mask"] is mask
    assert not mask.flags.writeable


def test_intensity_is_area_independent():
    from src.score import add_intensity_rank
    basins = [{"basin_id": 0, "mean_burn": 0.30, "mean_slope": 0.30, "area_km2": 10.0},
              {"basin_id": 1, "mean_burn": 0.70, "mean_slope": 0.60, "area_km2": 0.5}]
    order = add_intensity_rank(basins)
    assert basins[1]["intensity"] == pytest.approx(0.42)
    assert basins[0]["intensity"] == pytest.approx(0.09)
    assert basins[1]["intensity_rank"] == 1
    assert [b["basin_id"] for b in order] == [1, 0]


def test_intensity_ties_break_by_basin_id():
    from src.score import add_intensity_rank
    basins = [{"basin_id": 5, "mean_burn": 0.5, "mean_slope": 0.5, "area_km2": 1.0},
              {"basin_id": 2, "mean_burn": 0.5, "mean_slope": 0.5, "area_km2": 9.0}]
    add_intensity_rank(basins)
    assert basins[1]["intensity_rank"] == 1


def test_intensity_does_not_touch_frozen_score():
    from src.score import add_intensity_rank
    b = [{"basin_id": 0, "mean_burn": 0.4, "mean_slope": 0.5, "area_km2": 2.0,
          "score": 0.4, "rank": 1}]
    add_intensity_rank(b)
    assert b[0]["score"] == 0.4 and b[0]["rank"] == 1
