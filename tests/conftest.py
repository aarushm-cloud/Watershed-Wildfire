"""Shared fixtures. Introduced by A39 -- the suite previously used plain functions."""
from pathlib import Path

import numpy as np
import pytest
import rasterio

# NOTE (A39 Task 8): incised_synthetic.tif (the A27 *detector* fixture) is a single triangular
# channel with no tributaries -- WhiteboxTools segmentation returns all-zero labels on it at the
# frozen SUBBASIN_ACC_THRESHOLD_CELLS=3000, so it cannot back a *ranked* result. incised_dendritic.tif
# is a larger, real-tributaried DEM (tests/fixtures/make_incised_dendritic.py) that forms an actual
# dendritic network at that SAME frozen threshold. incised_synthetic.tif is untouched and still backs
# the A27 detector tests (Task 11).
INCISED_DEM = Path("tests/fixtures/incised_dendritic.tif")


@pytest.fixture
def incised_fire(tmp_path):
    """A hermetic incised fire: the dendritic incised DEM + a synthetic dNBR on its grid.

    NOTE: the older hermetic config in test_a31_reorder.py has no `dnbr` key and
    `assets: None`. That worked only because the gate refused before a burn input was
    needed. Once the gate is a router, execution continues, so a real burn input and a
    real assets path are required.
    """
    with rasterio.open(INCISED_DEM) as ds:
        profile = ds.profile.copy()
        shape = (ds.height, ds.width)

    dnbr = np.zeros(shape, dtype="float32")
    dnbr[: shape[0] // 2, :] = 0.55          # moderate-high severity over the upper half
    dnbr_path = tmp_path / "dnbr.tif"
    profile.update(count=1, dtype="float32", nodata=-9999.0)
    with rasterio.open(dnbr_path, "w", **profile) as dst:
        dst.write(dnbr, 1)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    return {"name": "hermetic_incised",
            "dem": str(INCISED_DEM),
            "sbs": None,
            "dnbr": str(dnbr_path),
            "assets": None,       # incised path must not load assets (A39)
            "creeks": None,
            "out_dir": out_dir,   # Path, not str -- outputs calls .mkdir()
            "expected_crs": "EPSG:32613",
            "validation_case": "hermetic_incised_a39"}
