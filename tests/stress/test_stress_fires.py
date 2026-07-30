"""Registry well-formedness for the auto-acquire stress test.

Pure data checks -- no network, no rasters. These exist so a bad bbox (the classic
"box clips the scar" failure) is caught before any expensive run, not after.
"""

import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from acquire import MAX_BBOX_DEG2, utm_epsg
from validation.stress_fires import FIRES, ZONE_CROSSING, bbox_area_deg2


def test_every_fire_has_the_required_keys():
    required = {"name", "bbox", "ignition", "containment", "reference",
                "frozen_pair", "bbox_source", "notes"}
    for key, fire in FIRES.items():
        assert required <= set(fire), f"{key} missing {required - set(fire)}"


@pytest.mark.parametrize("key", sorted(FIRES))
def test_bbox_is_ordered_and_under_the_frozen_cap(key):
    west, south, east, north = FIRES[key]["bbox"]
    assert west < east, f"{key}: west must be < east"
    assert south < north, f"{key}: south must be < north"
    area = bbox_area_deg2(FIRES[key]["bbox"])
    assert area <= MAX_BBOX_DEG2, (
        f"{key}: bbox is {area:.3f} deg2, over the frozen {MAX_BBOX_DEG2} cap -- "
        "acquire.build_fire_config would refuse this box."
    )


@pytest.mark.parametrize("key", sorted(FIRES))
def test_ignition_precedes_containment(key):
    fire = FIRES[key]
    assert isinstance(fire["ignition"], date), f"{key}: ignition must be a date"
    assert isinstance(fire["containment"], date), f"{key}: containment must be a date"
    assert fire["ignition"] < fire["containment"], f"{key}: dates out of order"


@pytest.mark.parametrize("key", sorted(FIRES))
def test_every_bbox_records_its_provenance(key):
    """A bbox with no stated source is not auditable, and an unauditable box is
    exactly how 'the tool failed' turns out to mean 'the operator drew it wrong'."""
    src = FIRES[key]["bbox_source"]
    assert isinstance(src, str) and len(src) > 20, f"{key}: bbox_source too thin"


@pytest.mark.parametrize("key", sorted(ZONE_CROSSING))
def test_declared_zone_crossers_really_do_cross(key):
    """ZONE_CROSSING is an empirical claim about the registry -- lock it."""
    west, south, east, north = FIRES[key]["bbox"]
    mid_lat = (south + north) / 2
    assert utm_epsg(west, mid_lat) != utm_epsg(east, mid_lat), (
        f"{key} is declared a UTM zone crosser but resolves to one zone"
    )


def test_non_crossers_are_not_silently_crossing():
    """The complement of the claim above: everything NOT declared must be single-zone."""
    for key, fire in FIRES.items():
        if key in ZONE_CROSSING:
            continue
        west, south, east, north = fire["bbox"]
        mid_lat = (south + north) / 2
        assert utm_epsg(west, mid_lat) == utm_epsg(east, mid_lat), (
            f"{key} spans two UTM zones but is not listed in ZONE_CROSSING"
        )


def test_south_fork_carries_the_frozen_pair_ids():
    frozen = FIRES["southfork"]["frozen_pair"]
    assert frozen["pre"] == "LC09_L2SP_033037_20240612"
    assert frozen["post"] == "LC09_L2SP_032037_20240707"
    assert frozen["sensor"] == "Landsat"


def test_frozen_post_scene_predates_containment():
    """F-2, asserted as a property of the registry rather than prose.

    post_start = containment (inclusive, scene_select.py:120), so a frozen post
    scene earlier than containment cannot be reached by the selector at the
    honest containment date. If this test ever fails, F-2 has been resolved and
    the stress-test plan needs revisiting.
    """
    fire = FIRES["southfork"]
    frozen_post_date = date(2024, 7, 7)   # encoded in LC09_L2SP_032037_20240707
    assert frozen_post_date < fire["containment"], (
        "the frozen post scene is now reachable -- F-2 no longer holds"
    )


@pytest.mark.parametrize("key", sorted(k for k in FIRES if FIRES[k]["reference"]))
def test_declared_reference_rasters_exist_on_disk(key):
    """References are gitignored local data. A missing one silently downgrades a
    numeric comparison to a vibes check, so fail loudly here instead.

    Scope: repo-tree references only follow the suite's existing local-data
    convention (test_behavior_lock etc. already require them). A reference
    OUTSIDE the repo tree (e.g. ~/Documents/nm-demo-dnbr) is machine-local --
    on any other machine that is an environment fact, not a defect, so it
    SKIPS with a visible reason instead of failing the suite."""
    ref = Path(FIRES[key]["reference"])
    if _REPO_ROOT not in ref.parents:
        if not ref.exists():
            pytest.skip(f"{key}: machine-local reference not present on this "
                        f"machine ({ref}); its numeric comparisons are unavailable here")
        return
    assert ref.exists(), f"{key}: reference raster missing at {ref}"
