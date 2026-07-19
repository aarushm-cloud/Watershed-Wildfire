"""AA-2 (B4, Auto-Acquire Build-Plan Phase 2) -- the unified dNBR creator.

Known-answer + contract tests for dnbr_create.py: per-sensor SR scaling applied
BEFORE the ratio (S2 (DN-1000)/10000 with a numeric baseline >= 04.00 assert;
Landsat DN*0.0000275-0.2), NBR/dNBR band math on the scene's NATIVE grid (no
reprojection in creation -- the one resample is the frozen both-arms ingest),
SCL/QA union cloud masking to NoData (in-product, the ratified pre-reg choice),
the identical-native-grid fail-loud guard (STOP, never resample), cross-UTM-zone
fail-loud, and the output contract: float32 GTiff, raw scale |dNBR| <= 2, nodata
-9999, passes acquire.assert_raw_dnbr, quicklook + provenance.json alongside.

All hermetic: candidates carry local synthetic GeoTIFF paths as asset hrefs.

Run:  pytest tests/test_dnbr_create.py -v
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import acquire  # noqa: E402
from autoacquire import dnbr_create as dc  # noqa: E402
from src.grids import GateAbort  # noqa: E402

# Small AOI (~2 km) in UTM 10N; rasters below cover it with margin.
BBOX = (-122.02, 38.50, -122.00, 38.52)
CRS = "EPSG:32610"


def _grid(cell):
    """A native lattice (origin snapped to the cell size) covering BBOX + margin."""
    w, s, e, n = transform_bounds("EPSG:4326", CRS, *BBOX, densify_pts=21)
    x0 = (int(w // cell) - 10) * cell
    y1 = (int(n // cell) + 10) * cell
    cols = int((e - w) / cell) + 40
    rows = int((n - s) / cell) + 40
    return from_origin(x0, y1, cell, cell), rows, cols


def _write_band(path, arr, transform, dtype):
    profile = dict(
        driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
        dtype=dtype, crs=CRS, transform=transform,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(arr.astype(dtype), 1)
    return path


def _s2_scene(tmp_path, tag, *, nir_dn, swir_dn, scl_cls=4, baseline="05.12",
              scl_patch=None, shift_m=0.0):
    """Synthetic S2 candidate: constant-DN B8A/B12 + SCL, 20 m lattice, local hrefs.
    scl_patch: (row_slice, col_slice, cls) painted onto the SCL band."""
    transform, rows, cols = _grid(20.0)
    if shift_m:
        transform = from_origin(
            transform.c + shift_m, transform.f, 20.0, 20.0
        )
    d = tmp_path / tag
    nir = np.full((rows, cols), nir_dn, dtype=np.uint16)
    swir = np.full((rows, cols), swir_dn, dtype=np.uint16)
    scl = np.full((rows, cols), scl_cls, dtype=np.uint8)
    if scl_patch:
        rs, cs, cls = scl_patch
        scl[rs, cs] = cls
    return {
        "id": f"S2_{tag}",
        "sensor": "S2",
        "date": date(2026, 6, 4) if tag.startswith("pre") else date(2026, 7, 7),
        "tile_cloud_pct": 1.0,
        "processing_baseline": baseline,
        "epsg": 32610,
        "assets": {
            "nir08": str(_write_band(d / "nir08.tif", nir, transform, "uint16")),
            "swir22": str(_write_band(d / "swir22.tif", swir, transform, "uint16")),
            "scl": str(_write_band(d / "scl.tif", scl, transform, "uint8")),
        },
    }


def _ls_scene(tmp_path, tag, *, nir_dn, swir_dn, qa=0, qa_patch=None):
    """Synthetic Landsat candidate: constant-DN B5/B7 + QA_PIXEL, 30 m lattice."""
    transform, rows, cols = _grid(30.0)
    d = tmp_path / tag
    nir = np.full((rows, cols), nir_dn, dtype=np.uint16)
    swir = np.full((rows, cols), swir_dn, dtype=np.uint16)
    qa_arr = np.full((rows, cols), qa, dtype=np.uint16)
    if qa_patch:
        rs, cs, bits = qa_patch
        qa_arr[rs, cs] = bits
    return {
        "id": f"LS_{tag}",
        "sensor": "Landsat",
        "date": date(2026, 6, 4) if tag.startswith("pre") else date(2026, 7, 7),
        "tile_cloud_pct": 1.0,
        "processing_baseline": None,
        "epsg": 32610,
        "assets": {
            "nir08": str(_write_band(d / "nir08.tif", nir, transform, "uint16")),
            "swir22": str(_write_band(d / "swir22.tif", swir, transform, "uint16")),
            "qa_pixel": str(_write_band(d / "qa.tif", qa_arr, transform, "uint16")),
        },
    }


def _create(tmp_path, pre, post, sensor="S2", name="testfire"):
    return dc.create_dnbr(
        {"sensor": sensor, "pre": pre, "post": post}, BBOX, tmp_path / "out", name=name
    )


def _read_dnbr(result):
    with rasterio.open(result["dnbr_tif"]) as ds:
        arr = ds.read(1)
        return arr, ds


# ---- S2 adapter: known-answer band math (SR scaling BEFORE the ratio) ----


def test_s2_known_answer_dnbr():
    # pre : NIR DN 5000 -> SR 0.4 ; SWIR DN 2000 -> SR 0.1 ; NBR = 0.3/0.5 = 0.6
    # post: NIR DN 2000 -> SR 0.1 ; SWIR DN 4000 -> SR 0.3 ; NBR = -0.2/0.4 = -0.5
    # dNBR = NBR_pre - NBR_post = 1.1 (raw scale, positive = burned)
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pre = _s2_scene(tmp, "pre", nir_dn=5000, swir_dn=2000)
    post = _s2_scene(tmp, "post", nir_dn=2000, swir_dn=4000)
    result = _create(tmp, pre, post)
    arr, ds = _read_dnbr(result)
    interior = arr[arr != -9999.0]
    assert interior.size > 0
    assert np.allclose(interior, 1.1, atol=1e-5)
    assert ds.crs.to_string() == CRS          # native grid, no reprojection
    assert abs(ds.transform.a) == 20.0        # 20 m S2 lattice preserved
    assert ds.nodata == -9999.0
    assert arr.dtype == np.float32


def test_s2_scl_union_masks_to_nodata():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    # Cloud (SCL 9) on DIFFERENT areas of pre and post -> union is NoData.
    pre = _s2_scene(tmp, "pre", nir_dn=5000, swir_dn=2000,
                    scl_patch=(slice(0, 40), slice(None), 9))
    post = _s2_scene(tmp, "post", nir_dn=2000, swir_dn=4000,
                     scl_patch=(slice(None), slice(0, 40), 9))
    result = _create(tmp, pre, post)
    arr, _ = _read_dnbr(result)
    assert (arr == -9999.0).any()             # union masked
    good = arr[arr != -9999.0]
    assert good.size > 0 and np.allclose(good, 1.1, atol=1e-5)


def test_s2_baseline_below_4_fails_loud():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pre = _s2_scene(tmp, "pre", nir_dn=5000, swir_dn=2000, baseline="03.01")
    post = _s2_scene(tmp, "post", nir_dn=2000, swir_dn=4000)
    with pytest.raises(GateAbort) as e:
        _create(tmp, pre, post)
    msg = str(e.value)
    assert "baseline" in msg.lower() and "04.00" in msg


def test_s2_baseline_compare_is_numeric_not_lexical():
    # "10.00" < "04.00" lexically -- must NOT abort (field verified live as a string).
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pre = _s2_scene(tmp, "pre", nir_dn=5000, swir_dn=2000, baseline="10.00")
    post = _s2_scene(tmp, "post", nir_dn=2000, swir_dn=4000)
    result = _create(tmp, pre, post)
    assert Path(result["dnbr_tif"]).exists()


# ---- Landsat adapter: known-answer + the ratified in-product cloud mask ----


def test_landsat_known_answer_dnbr():
    # pre : NIR DN 20000 -> SR 0.35 ; SWIR DN 10000 -> SR 0.075 ; NBR = 0.275/0.425
    # post: NIR DN 10000 -> SR 0.075 ; SWIR DN 20000 -> SR 0.35 ; NBR = -0.275/0.425
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pre = _ls_scene(tmp, "pre", nir_dn=20000, swir_dn=10000)
    post = _ls_scene(tmp, "post", nir_dn=10000, swir_dn=20000)
    result = _create(tmp, pre, post, sensor="Landsat")
    arr, ds = _read_dnbr(result)
    expected = (0.275 / 0.425) - (-0.275 / 0.425)
    good = arr[arr != -9999.0]
    assert good.size > 0 and np.allclose(good, expected, atol=1e-5)
    assert abs(ds.transform.a) == 30.0        # 30 m Landsat lattice preserved


def test_landsat_qa_cloud_bits_mask_in_product():
    """The RATIFIED divergence from the p2/p3 lineage: QA bits 1-4 (cloud family)
    become NoData in the product, not report-only QC (pre-reg ratification 2026-07-17)."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pre = _ls_scene(tmp, "pre", nir_dn=20000, swir_dn=10000,
                    qa_patch=(slice(0, 30), slice(None), 1 << 3))  # cloud bit
    post = _ls_scene(tmp, "post", nir_dn=10000, swir_dn=20000)
    result = _create(tmp, pre, post, sensor="Landsat")
    arr, _ = _read_dnbr(result)
    assert (arr == -9999.0).any()
    assert (arr != -9999.0).any()


def test_landsat_fill_dn_zero_is_nodata():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pre = _ls_scene(tmp, "pre", nir_dn=0, swir_dn=10000)   # DN 0 = fill (p2)
    post = _ls_scene(tmp, "post", nir_dn=10000, swir_dn=20000)
    with pytest.raises(GateAbort):
        # every pixel fill -> all-NoData dNBR -> assert_raw_dnbr fails loud
        _create(tmp, pre, post, sensor="Landsat")


# ---- grid contract: identical native lattice or STOP (never resample) ----


def test_pre_post_lattice_mismatch_fails_loud():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pre = _s2_scene(tmp, "pre", nir_dn=5000, swir_dn=2000)
    post = _s2_scene(tmp, "post", nir_dn=2000, swir_dn=4000, shift_m=10.0)  # half-pixel
    with pytest.raises(GateAbort) as e:
        _create(tmp, pre, post)
    assert "grid" in str(e.value).lower()
    assert "resampl" in str(e.value).lower()  # the mandated response is STOP, not resample


def test_cross_utm_zone_pair_fails_loud():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pre = _s2_scene(tmp, "pre", nir_dn=5000, swir_dn=2000)
    post = _s2_scene(tmp, "post", nir_dn=2000, swir_dn=4000)
    post["epsg"] = 32611
    with pytest.raises(GateAbort) as e:
        _create(tmp, pre, post)
    assert "UTM" in str(e.value) or "zone" in str(e.value).lower()


# ---- output contract (feeds the UNCHANGED validated ingest) ----


def test_output_passes_assert_raw_dnbr_and_carries_provenance():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pre = _s2_scene(tmp, "pre", nir_dn=5000, swir_dn=2000)
    post = _s2_scene(tmp, "post", nir_dn=2000, swir_dn=4000)
    result = _create(tmp, pre, post)
    # The real input gate the pipeline runs (F1 guard) accepts the artifact.
    stats = acquire.assert_raw_dnbr(result["dnbr_tif"])
    assert stats["p99_abs"] <= 2.0
    assert Path(result["quicklook_png"]).exists()
    prov = json.loads(Path(result["provenance_json"]).read_text())
    assert prov["scenes"]["pre"]["id"] == pre["id"]
    assert prov["scenes"]["post"]["id"] == post["id"]
    assert prov["sensor"] == "S2"
    assert "NBR" in prov["nbr_formula"] or "NIR" in prov["nbr_formula"]
    assert prov["scale"].startswith("raw")
    # Framing carried verbatim from the single source (never re-minted).
    from src.outputs import DNBR_FRAMING
    assert prov["framing"]["dnbr"] == DNBR_FRAMING
    assert prov["gate_stats"]["p99_abs"] <= 2.0


def test_all_nodata_output_fails_loud():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    # Everything cloud-masked -> all-NoData product must abort, never hand a raster on.
    pre = _s2_scene(tmp, "pre", nir_dn=5000, swir_dn=2000, scl_cls=9)
    post = _s2_scene(tmp, "post", nir_dn=2000, swir_dn=4000)
    with pytest.raises(GateAbort):
        _create(tmp, pre, post)
