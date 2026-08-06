"""select(sensors=) on the production selector (A41, Task 5): keyword-only, restricts which
sensor arms select() searches, validated a non-empty subset of {"S2", "Landsat"} else GateAbort
BEFORE any network-ish work. Default is byte-equivalent to the frozen S2-first, Landsat
pair-level-fallback behavior. Hermetic: _search_scenes and _candidate_valid_mask monkeypatched
(mirrors tests/acquire/test_scene_select.py's fixture pattern).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from autoacquire import scene_select  # noqa: E402
from src.grids import GateAbort  # noqa: E402

BBOX = (-122.145, 38.455, -121.985, 38.595)
IGN = date(2026, 6, 8)
CONT = date(2026, 6, 20)
TODAY = date(2026, 7, 17)

# Footprint fully covering BBOX (with margin) -- geometry is irrelevant to these tests.
_COVERING_FOOTPRINT = {
    "type": "Polygon",
    "coordinates": [[
        [-122.5, 38.0], [-121.5, 38.0], [-121.5, 39.0], [-122.5, 39.0], [-122.5, 38.0],
    ]],
}


def _cand(cid, d, *, sensor="S2", cloud=1.0, baseline="05.00"):
    """Candidate fixture matching the shape scene_select's search functions emit."""
    return {
        "id": cid,
        "sensor": sensor,
        "date": d,
        "tile_cloud_pct": cloud,
        "footprint": _COVERING_FOOTPRINT,
        "processing_baseline": baseline if sensor == "S2" else None,
        "assets": {},
    }


def _full(frac_valid, shape=(10, 10)):
    """Bool mask with the given valid fraction, invalid cells packed at the start."""
    m = np.ones(shape[0] * shape[1], dtype=bool)
    n_bad = round((1.0 - frac_valid) * m.size)
    m[:n_bad] = False
    return m.reshape(shape)


def _mask_lookup(masks):
    def _fake(candidate, bbox):
        return masks[candidate["id"]]
    return _fake


@pytest.fixture
def hermetic_pools(monkeypatch):
    """Two independently-recommendable arms: a clean S2 pre/post pair and a clean Landsat
    pre/post pair, so sensors= can restrict to either and still land on 'recommended'."""
    s2_pre, s2_post = _cand("S2_PRE", date(2026, 6, 4)), _cand("S2_POST", date(2026, 7, 7))
    ls_pre = _cand("LS_PRE", date(2026, 6, 1), sensor="Landsat")
    ls_post = _cand("LS_POST", date(2026, 6, 30), sensor="Landsat")

    def _fake_search(sensor, bbox, d0, d1):
        pool = [s2_pre, s2_post] if sensor == "S2" else [ls_pre, ls_post]
        return [c for c in pool if d0 <= c["date"] < d1]

    monkeypatch.setattr(scene_select, "_search_scenes", _fake_search)
    monkeypatch.setattr(scene_select, "_candidate_valid_mask", _mask_lookup({
        "S2_PRE": _full(1.0), "S2_POST": _full(1.0),
        "LS_PRE": _full(1.0), "LS_POST": _full(1.0),
    }))


def test_default_equals_explicit(monkeypatch, hermetic_pools):
    a = scene_select.select(BBOX, ignition=IGN, containment=CONT, today=TODAY)
    b = scene_select.select(BBOX, ignition=IGN, containment=CONT, today=TODAY,
                             sensors=("S2", "Landsat"))
    assert a == b
    assert a["status"] == "recommended"
    assert a["pair"]["sensor"] == "S2"  # default stays S2-first, break-on-found


def test_single_sensor_restricts(monkeypatch, hermetic_pools):
    seen = []
    real = scene_select._search_scenes

    def _wrapped(sensor, bbox, d0, d1):
        seen.append(sensor)
        return real(sensor, bbox, d0, d1)

    monkeypatch.setattr(scene_select, "_search_scenes", _wrapped)
    result = scene_select.select(BBOX, ignition=IGN, containment=CONT, today=TODAY,
                                  sensors=("Landsat",))
    assert set(seen) == {"Landsat"}
    assert result["status"] == "recommended"
    assert result["pair"]["sensor"] == "Landsat"


@pytest.mark.parametrize("bad", [(), ("s2",), ("S2", "MODIS")])
def test_invalid_sensors_fail_loud(bad):
    with pytest.raises(GateAbort, match="sensors"):
        scene_select.select(BBOX, ignition=IGN, containment=CONT, today=TODAY, sensors=bad)
