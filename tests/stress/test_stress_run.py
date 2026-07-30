"""Runner serialization -- hermetic.

_search_scenes and _candidate_valid_mask are monkeypatched exactly as
tests/acquire/test_scene_select.py does, so no network is touched.
"""

import json
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pytest

from autoacquire import scene_select as ss
from validation import stress_run

_WORLD = {"type": "Polygon", "coordinates": [[
    [-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]]}


def _cand(cid, d, *, sensor="Landsat", cloud=1.0, epsg=32613, baseline=None):
    return {
        "id": cid, "sensor": sensor, "date": d, "tile_cloud_pct": cloud,
        "footprint": _WORLD, "processing_baseline": baseline, "epsg": epsg,
        "assets": {"nir08": "x", "swir22": "y", "qa_pixel": "z", "scl": "s"},
    }


@pytest.fixture
def clean_pair(monkeypatch):
    """A Landsat pre/post pair that passes every frozen gate."""
    pre = _cand("PRE_1", date(2024, 6, 12))
    post = _cand("POST_1", date(2024, 7, 20))

    def _fake_search(sensor, bbox, d0, d1):
        if sensor != "Landsat":
            return []
        return [c for c in (pre, post) if d0 <= c["date"] < d1]

    monkeypatch.setattr(ss, "_search_scenes", _fake_search)
    monkeypatch.setattr(ss, "_candidate_valid_mask",
                        lambda candidate, bbox: np.ones((20, 20), dtype=bool))
    return pre, post


def test_run_fire_serializes_the_full_package(clean_pair, tmp_path):
    out = stress_run.run_fire("southfork", out_root=tmp_path, today=date(2024, 9, 1))

    assert out["status"] == "recommended"
    assert out["_fire"] == "southfork"
    written = tmp_path / "southfork" / "package.json"
    assert written.exists()

    on_disk = json.loads(written.read_text())
    assert on_disk["status"] == "recommended"
    assert "rejected" in on_disk, "the audit trail must survive serialization"
    assert on_disk["_ignition"] == "2024-06-17"


def test_containment_override_is_recorded_and_renames_the_output(clean_pair, tmp_path):
    out = stress_run.run_fire(
        "southfork", containment=date(2024, 7, 1), out_root=tmp_path,
        today=date(2024, 9, 1),
    )
    assert out["_containment_used"] == "2024-07-01"
    assert (tmp_path / "southfork" / "package_2024-07-01.json").exists()
    assert not (tmp_path / "southfork" / "package.json").exists()


def test_non_recommended_states_are_serialized_not_swallowed(monkeypatch, tmp_path):
    """An honest failure state is a RESULT, not an error -- it must reach disk."""
    monkeypatch.setattr(ss, "_search_scenes", lambda *a, **k: [])

    out = stress_run.run_fire("southfork", out_root=tmp_path, today=date(2024, 9, 1))

    assert out["status"] != "recommended"
    assert (tmp_path / "southfork" / "package.json").exists()
    assert json.loads((tmp_path / "southfork" / "package.json").read_text())["status"] \
        == out["status"]


def test_serialization_is_json_safe_for_dates_and_tuples(clean_pair, tmp_path):
    """select() returns date objects and a tuple bbox; naive json.dumps would raise."""
    stress_run.run_fire("southfork", out_root=tmp_path, today=date(2024, 9, 1))
    raw = (tmp_path / "southfork" / "package.json").read_text()
    payload = json.loads(raw)
    assert isinstance(payload["bbox"], list)
    assert payload["pair"]["pre"]["date"] == "2024-06-12"


def test_a_fire_with_no_bbox_fails_loudly_before_any_network_call(monkeypatch, tmp_path):
    """Guard against the 'box clips the scar' failure reaching a real run."""
    from validation import stress_fires

    monkeypatch.setitem(stress_fires.FIRES, "broken",
                        {**stress_fires.FIRES["southfork"], "bbox": None})

    def _boom(*a, **k):
        raise AssertionError("network must not be reached when the bbox is missing")

    monkeypatch.setattr(ss, "_search_scenes", _boom)

    with pytest.raises(ValueError, match="no bbox"):
        stress_run.run_fire("broken", out_root=tmp_path)
