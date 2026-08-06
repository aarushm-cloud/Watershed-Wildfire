"""acquire.build_fire_config split into stage_fire / attach_dnbr (A41): a later sweep task stages
DEM/buildings ONCE per fire, then attaches a different dNBR per attempt. This locks the seam:

  * stage_fire  -- grid + A37 zone check + DEM/buildings fetch + a dnbr-less manifest; fire["dnbr"]=None.
  * attach_dnbr -- CF-9 raw-scale guard, then fire["dnbr"] + the manifest's dnbr_upload stats.
  * build_fire_config == attach_dnbr(stage_fire(...))  -- byte-equivalent for existing callers.

Run:  pytest tests/acquire/test_stage_fire_seam.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import acquire  # noqa: E402
from src.grids import GateAbort  # noqa: E402

BBOX = (-105.7916, 33.3255, -105.6361, 33.4135)   # South Fork AOI, zone 13 (onboarded)


def _write_raster(path, array, *, crs="EPSG:32613", nodata=-9999.0,
                  transform=from_origin(426400.8, 3697312.6, 10.0, 10.0)):
    array = np.asarray(array, dtype="float32")
    profile = dict(driver="GTiff", height=array.shape[0], width=array.shape[1], count=1,
                   dtype="float32", crs=crs, transform=transform, nodata=nodata)
    with rasterio.open(path, "w", **profile) as d:
        d.write(array, 1)
    return path


@pytest.fixture
def tiny_raw_dnbr(tmp_path):
    # raw-scale dNBR (~-1..1.2), passes assert_raw_dnbr -- same pattern as test_acquire_fetch.py.
    return _write_raster(tmp_path / "raw_dnbr.tif",
                         np.linspace(-1.0, 1.2, 400, dtype="float32").reshape(20, 20))


def _touch(p):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    return p


def test_split_equals_wrapper(monkeypatch, tmp_path, tiny_raw_dnbr):
    calls = []
    monkeypatch.setattr(acquire, "fetch_dem", lambda bbox, grid, p: (calls.append("dem"), _touch(p))[1])
    monkeypatch.setattr(acquire, "fetch_buildings",
                        lambda bbox, crs, p, buf_deg: (calls.append("bld"), (_touch(p), 0))[1])

    def _norm(v, root):
        # path values are relative-to-out_dir so the two (different) out_dirs compare equal.
        if isinstance(v, Path):
            base = tmp_path / root
            try:
                return v.relative_to(base)
            except ValueError:
                return v
        return v

    fire_a = acquire.build_fire_config(BBOX, tiny_raw_dnbr, tmp_path / "a", name="x")
    staged = acquire.stage_fire(BBOX, tmp_path / "b", name="x")
    assert staged["dnbr"] is None and calls.count("dem") == 2       # staged once per call
    fire_b = acquire.attach_dnbr(staged, tiny_raw_dnbr)
    keys = set(fire_a) | set(fire_b)
    assert {k: _norm(fire_a[k], "a") for k in keys} == {k: _norm(fire_b[k], "b") for k in keys}


def test_attach_dnbr_validates_first(monkeypatch, tmp_path):
    monkeypatch.setattr(acquire, "fetch_dem", lambda bbox, grid, p: _touch(p))
    monkeypatch.setattr(acquire, "fetch_buildings", lambda bbox, crs, p, buf_deg: (_touch(p), 0))
    staged = acquire.stage_fire(BBOX, tmp_path / "c", name="x")
    with pytest.raises(GateAbort):                                  # CF-9 still bites
        acquire.attach_dnbr(staged, tmp_path / "not_a_dnbr.tif")


def test_stage_fire_manifest_has_no_dnbr_stats_yet(monkeypatch, tmp_path):
    # the intermediate manifest (before attach_dnbr) is a half-state by design (A41): dnbr_upload
    # is null, not absent-stats-that-looks-like-a-bug. Must not confuse a reader mid-sweep.
    monkeypatch.setattr(acquire, "fetch_dem", lambda bbox, grid, p: _touch(p))
    monkeypatch.setattr(acquire, "fetch_buildings", lambda bbox, crs, p, buf_deg: (_touch(p), 0))
    out_dir = tmp_path / "d"
    acquire.stage_fire(BBOX, out_dir, name="x")
    import json
    manifest = json.loads((out_dir / "acquisition_manifest.json").read_text())
    assert manifest["dnbr_upload"] is None


def test_attach_dnbr_completes_manifest_matching_wrapper(monkeypatch, tmp_path, tiny_raw_dnbr):
    # the self-review focus: final manifest content after split+attach must equal what
    # build_fire_config wrote in one shot, field-for-field (dnbr_upload included).
    import json

    def _fetch_dem(bbox, grid, p):
        return _touch(p)

    def _fetch_buildings(bbox, crs, p, buf_deg):
        return (_touch(p), 0)

    monkeypatch.setattr(acquire, "fetch_dem", _fetch_dem)
    monkeypatch.setattr(acquire, "fetch_buildings", _fetch_buildings)
    acquire.build_fire_config(BBOX, tiny_raw_dnbr, tmp_path / "wrapper", name="x")
    wrapper_manifest = json.loads((tmp_path / "wrapper" / "acquisition_manifest.json").read_text())

    staged = acquire.stage_fire(BBOX, tmp_path / "split", name="x")
    acquire.attach_dnbr(staged, tiny_raw_dnbr)
    split_manifest = json.loads((tmp_path / "split" / "acquisition_manifest.json").read_text())

    assert split_manifest == wrapper_manifest


def test_attach_dnbr_missing_manifest_fails_loud(tmp_path, tiny_raw_dnbr):
    # review fix (finding 1): attach_dnbr on an out_dir stage_fire never staged (no manifest on
    # disk) must fail loud as GateAbort, not leak a bare FileNotFoundError/JSONDecodeError -- every
    # other acquire.py precondition translates to GateAbort, this one should too.
    out_dir = tmp_path / "never_staged"
    out_dir.mkdir()
    fire = {"name": "x", "out_dir": out_dir}
    with pytest.raises(GateAbort, match="acquisition manifest"):
        acquire.attach_dnbr(fire, tiny_raw_dnbr)


def test_build_fire_config_refuses_invalid_dnbr_before_any_fetch(monkeypatch, tmp_path):
    # review fix (finding 2): CF-9 ordering restored -- build_fire_config validates the dNBR
    # BEFORE paying for the DEM/buildings network fetch (byte-equivalent to pre-A41 behavior for
    # this caller); attach_dnbr's own re-validation is cheap, harmless double-checking.
    calls = []
    monkeypatch.setattr(acquire, "fetch_dem", lambda *a, **k: calls.append("dem"))
    monkeypatch.setattr(acquire, "fetch_buildings", lambda *a, **k: calls.append("bld"))
    bad_dnbr = _write_raster(tmp_path / "bad_x1000.tif",
                             np.linspace(-1285.0, 1238.0, 400, dtype="float32").reshape(20, 20))
    with pytest.raises(GateAbort):
        acquire.build_fire_config(BBOX, bad_dnbr, tmp_path / "out", name="x")
    assert calls == []                                               # zero fetches before the refusal
