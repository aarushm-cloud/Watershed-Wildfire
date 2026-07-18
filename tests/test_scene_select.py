"""AA-1 (B4, Auto-Acquire Build-Plan Phase 1) -- deterministic scene-pair selector.

Known-answer + behavior tests for scene_select.py: the pre-registered windows
(pre <=90 d before ignition; post >= containment; green-up ceiling default +90 d,
operator max +180 d), the coarse tile-cloud pre-filter (>80% dropped), the decisive
lenient box-gate (combined pre-AND-post valid fraction over the drawn box >= 0.50),
freshness-priority selection (most-recent pre, first-clean post), the Landsat
pair-level fallback (never a mixed-sensor pair), and the honest failure taxonomy
(Mode B waiting / window-closed / no-pre-scene -- never a pre/pre pair, never a
fabricated result). Values frozen by the RATIFIED pre-registration (2026-07-17);
never adjust a threshold here to make a test pass.

All tests are hermetic: STAC search and mask reads are monkeypatched; masks are
tiny synthetic numpy arrays. The live selector is verified separately against the
Putah fire (see the Build Log), not in this suite.

Run:  pytest tests/test_scene_select.py -v
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scene_select as ss  # noqa: E402
from src.grids import GateAbort  # noqa: E402

# A small CONUS bbox (lon/lat W, S, E, N) -- geometry is irrelevant to these tests.
BBOX = (-122.145, 38.455, -121.985, 38.595)

# A footprint polygon that fully covers BBOX (with margin).
_COVERING_FOOTPRINT = {
    "type": "Polygon",
    "coordinates": [[
        [-122.5, 38.0], [-121.5, 38.0], [-121.5, 39.0], [-122.5, 39.0], [-122.5, 38.0],
    ]],
}
# A footprint that only covers the western half of BBOX.
_PARTIAL_FOOTPRINT = {
    "type": "Polygon",
    "coordinates": [[
        [-122.5, 38.0], [-122.05, 38.0], [-122.05, 39.0], [-122.5, 39.0], [-122.5, 38.0],
    ]],
}


def _cand(cid, d, *, sensor="S2", cloud=1.0, footprint=None, baseline="05.00"):
    """Candidate fixture matching the shape scene_select's search functions emit."""
    return {
        "id": cid,
        "sensor": sensor,
        "date": d,
        "tile_cloud_pct": cloud,
        "footprint": footprint if footprint is not None else _COVERING_FOOTPRINT,
        "processing_baseline": baseline if sensor == "S2" else None,
        "assets": {},
    }


def _mask_lookup(masks):
    """monkeypatch target for ss._candidate_valid_mask: candidate id -> bool array."""
    def _fake(candidate, bbox):
        return masks[candidate["id"]]
    return _fake


def _full(frac_valid, shape=(10, 10)):
    """Bool mask with the given valid fraction, invalid cells packed at the start."""
    m = np.ones(shape[0] * shape[1], dtype=bool)
    n_bad = round((1.0 - frac_valid) * m.size)
    m[:n_bad] = False
    return m.reshape(shape)


# ---- windows (pre-reg B: pre cap 90 d / post >= containment / green-up ceiling) ----


def test_windows_pre_cap_90_days_and_post_bounds():
    w = ss.derive_windows(
        ignition=date(2026, 6, 8), containment=date(2026, 6, 20), today=date(2026, 7, 17)
    )
    assert w["pre_start"] == date(2026, 3, 10)  # ignition - 90 d
    assert w["pre_end"] == date(2026, 6, 8)     # exclusive: scene must predate ignition
    assert w["post_start"] == date(2026, 6, 20)  # containment (inclusive)
    assert w["post_end"] == date(2026, 9, 18)    # containment + 90 d default ceiling
    assert w["window_closed"] is False


def test_windows_greenup_operator_override_and_hard_max():
    w = ss.derive_windows(
        ignition=date(2026, 6, 8), containment=date(2026, 6, 20),
        today=date(2026, 7, 17), greenup_days=180,
    )
    assert w["post_end"] == date(2026, 12, 17)  # containment + 180 d (operator max)
    with pytest.raises(GateAbort) as e:
        ss.derive_windows(
            ignition=date(2026, 6, 8), containment=date(2026, 6, 20),
            today=date(2026, 7, 17), greenup_days=181,
        )
    assert "180" in str(e.value)
    with pytest.raises(GateAbort):
        ss.derive_windows(
            ignition=date(2026, 6, 8), containment=date(2026, 6, 20),
            today=date(2026, 7, 17), greenup_days=0,
        )


def test_windows_reject_containment_before_ignition():
    with pytest.raises(GateAbort) as e:
        ss.derive_windows(
            ignition=date(2026, 6, 8), containment=date(2026, 6, 1), today=date(2026, 7, 1)
        )
    assert "containment" in str(e.value).lower()


def test_windows_closed_when_today_past_ceiling():
    w = ss.derive_windows(
        ignition=date(2025, 6, 8), containment=date(2025, 6, 20), today=date(2026, 7, 17)
    )
    assert w["window_closed"] is True


# ---- coarse filter (metadata only; tile-cloud >80% dropped, strict >) ----


def test_coarse_filter_drops_tile_cloud_over_80():
    keep = _cand("keep", date(2026, 6, 4), cloud=80.0)   # boundary: 80.0 kept
    drop = _cand("drop", date(2026, 6, 4), cloud=80.1)   # strict >: dropped
    survivors, rejected = ss.coarse_filter(
        [keep, drop], BBOX, window=(date(2026, 3, 10), date(2026, 6, 8))
    )
    assert [c["id"] for c in survivors] == ["keep"]
    assert rejected and rejected[0][0]["id"] == "drop" and "cloud" in rejected[0][1]


def test_coarse_filter_drops_partial_footprint():
    part = _cand("part", date(2026, 6, 4), footprint=_PARTIAL_FOOTPRINT)
    survivors, rejected = ss.coarse_filter(
        [part], BBOX, window=(date(2026, 3, 10), date(2026, 6, 8))
    )
    assert survivors == []
    assert "footprint" in rejected[0][1]


def test_coarse_filter_enforces_window_dates():
    # A "pre" pool candidate dated on ignition day must be rejected (ordering guard:
    # the pre scene must strictly predate ignition -- the Elephant pre/pre lesson).
    on_ignition = _cand("on-ignition", date(2026, 6, 8))
    early = _cand("too-early", date(2026, 3, 9))
    ok = _cand("ok", date(2026, 6, 4))
    survivors, rejected = ss.coarse_filter(
        [on_ignition, early, ok], BBOX, window=(date(2026, 3, 10), date(2026, 6, 8))
    )
    assert [c["id"] for c in survivors] == ["ok"]
    reasons = {c["id"]: r for c, r in rejected}
    assert "window" in reasons["on-ignition"] and "window" in reasons["too-early"]


def test_coarse_filter_rejects_mixed_sensor_pool():
    s2 = _cand("a", date(2026, 6, 4), sensor="S2")
    ls = _cand("b", date(2026, 6, 4), sensor="Landsat")
    with pytest.raises(GateAbort) as e:
        ss.coarse_filter([s2, ls], BBOX, window=(date(2026, 3, 10), date(2026, 6, 8)))
    assert "sensor" in str(e.value).lower()


# ---- decisive box-gate (pixels; combined pre-AND-post valid fraction >= 0.50) ----


def test_scl_bad_classes_masked():
    # SCL bad set frozen by pre-reg D: [0,1,3,6,8,9,10,11]; everything else valid.
    scl = np.array([[0, 1, 3, 6], [8, 9, 10, 11], [2, 4, 5, 7]], dtype=np.uint8)
    valid = ss.s2_valid_mask(scl)
    assert not valid[0].any() and not valid[1].any()
    assert valid[2].all()


def test_qa_pixel_bits_masked():
    # Landsat QA_PIXEL: fill bit 0 + bits 1-4 (dilated cloud, cirrus, cloud, shadow).
    qa = np.array(
        [[1 << 0, 1 << 1, 1 << 2], [1 << 3, 1 << 4, 0], [1 << 5, 0, 0]], dtype=np.uint16
    )
    valid = ss.landsat_valid_mask(qa)
    assert not valid[0].any()
    assert not valid[1][:2].any() and valid[1][2]
    assert valid[2].all()  # bit 5+ (snow etc.) not in the frozen bad set for validity
    # NOTE: snow IS masked for S2 via SCL 11; Landsat validity here follows the frozen
    # pre-reg D bit list (1-4) + fill. Snow-over-Landsat arrives as cloud in practice.


def test_pair_metrics_gate_on_intersection():
    pre = _full(0.90)   # invalid cells packed at the start
    post = np.flip(_full(0.90))  # invalid cells packed at the end -> disjoint
    m = ss.pair_metrics(pre, post)
    assert m["pre_valid_frac"] == pytest.approx(0.90)
    assert m["post_valid_frac"] == pytest.approx(0.90)
    assert m["pair_valid_frac"] == pytest.approx(0.80)  # intersection, not min


def test_box_gate_floor_boundary():
    assert ss.passes_box_gate(0.50) is True    # >= floor passes
    assert ss.passes_box_gate(0.4999) is False


# ---- rubric (deterministic verdicts; thresholds frozen by pre-reg C) ----


def test_rubric_bands():
    good = ss.rubric_verdict(0.96, [0.02, 0.04])
    ok_pair = ss.rubric_verdict(0.80, [0.02, 0.10])
    ok_cloud = ss.rubric_verdict(0.96, [0.02, 0.12])  # pair Good-range, scene cloud OK-range
    marginal = ss.rubric_verdict(0.60, [0.02, 0.30])
    below = ss.rubric_verdict(0.40, [0.02, 0.55])
    assert good["verdict"] == "good"
    assert ok_pair["verdict"] == "ok"
    assert ok_cloud["verdict"] == "ok"      # verdict = worst axis, never averaged (B1 ethos)
    assert marginal["verdict"] == "marginal"
    assert below["verdict"] == "below_bar"
    assert "%" in good["summary"]  # templated plain-language prose exists


def test_rubric_is_deterministic():
    a = ss.rubric_verdict(0.87, [0.03, 0.09])
    b = ss.rubric_verdict(0.87, [0.03, 0.09])
    assert a == b  # identical metrics -> identical verdict + prose, always


# ---- selection (freshness priority: most-recent pre, first-clean post) ----


def _patch_search(monkeypatch, s2_pre=(), s2_post=(), ls_pre=(), ls_post=()):
    def _fake_search(sensor, bbox, d0, d1):
        if sensor == "S2":
            pool = list(s2_pre) + list(s2_post)
        else:
            pool = list(ls_pre) + list(ls_post)
        return [c for c in pool if d0 <= c["date"] < d1]
    monkeypatch.setattr(ss, "_search_scenes", _fake_search)


def test_putah_known_answer(monkeypatch):
    """Reproduce putah_dnbr.py's hand-picked pair (pre 2026-06-04 / post 2026-07-07)
    from coords + dates alone.

    Scene pool mirrors the real Putah record: pre scenes 05-20 + 06-04 (both clear;
    06-04 is more recent), post scenes 06-27 (~60% cloud over the AOI -> fails the
    0.50 box-gate) and 07-07 (clear). Containment 2026-06-20 is a fixture assumption
    (test-only; the known answer is the selection logic, not the containment date).
    """
    pre_a = _cand("S2_0520", date(2026, 5, 20))
    pre_b = _cand("S2_0604", date(2026, 6, 4))
    post_bad = _cand("S2_0627", date(2026, 6, 27), cloud=60.0)
    post_good = _cand("S2_0707", date(2026, 7, 7))
    _patch_search(monkeypatch, s2_pre=[pre_a, pre_b], s2_post=[post_bad, post_good])
    monkeypatch.setattr(ss, "_candidate_valid_mask", _mask_lookup({
        "S2_0520": _full(1.0), "S2_0604": _full(1.0),
        "S2_0627": _full(0.40), "S2_0707": _full(1.0),
    }))
    result = ss.select(
        BBOX, ignition=date(2026, 6, 8), containment=date(2026, 6, 20),
        today=date(2026, 7, 17),
    )
    assert result["status"] == "recommended"
    assert result["pair"]["pre"]["id"] == "S2_0604"    # most-recent clean pre
    assert result["pair"]["post"]["id"] == "S2_0707"   # first clean post (0627 gated out)
    assert result["pair"]["sensor"] == "S2"
    # The rejected 06-27 scene must be in the audit trail with its reason.
    rejected_ids = [c["id"] for c, _ in result["rejected"]]
    assert "S2_0627" in rejected_ids


def test_alternatives_are_ranked_and_gate_passing(monkeypatch):
    pre_a = _cand("S2_0520", date(2026, 5, 20))
    pre_b = _cand("S2_0604", date(2026, 6, 4))
    post_a = _cand("S2_0625", date(2026, 6, 25))
    post_b = _cand("S2_0707", date(2026, 7, 7))
    _patch_search(monkeypatch, s2_pre=[pre_a, pre_b], s2_post=[post_a, post_b])
    monkeypatch.setattr(ss, "_candidate_valid_mask", _mask_lookup({
        "S2_0520": _full(1.0), "S2_0604": _full(1.0),
        "S2_0625": _full(0.95), "S2_0707": _full(1.0),
    }))
    result = ss.select(
        BBOX, ignition=date(2026, 6, 8), containment=date(2026, 6, 20),
        today=date(2026, 7, 17),
    )
    assert result["pair"]["pre"]["id"] == "S2_0604"
    assert result["pair"]["post"]["id"] == "S2_0625"  # earliest clean post wins
    # Independent pre/post alternatives (spec section 7 override path), each pre-vetted.
    assert [c["id"] for c in result["alternatives"]["pre"]] == ["S2_0520"]
    assert [c["id"] for c in result["alternatives"]["post"]] == ["S2_0707"]


def test_landsat_fallback_never_mixes_sensors(monkeypatch):
    # S2 has a clean pre but NO clean post; Landsat has a clean pair.
    s2_pre = _cand("S2_0604", date(2026, 6, 4))
    s2_post_bad = _cand("S2_0627", date(2026, 6, 27))
    ls_pre = _cand("LS_0601", date(2026, 6, 1), sensor="Landsat")
    ls_post = _cand("LS_0630", date(2026, 6, 30), sensor="Landsat")
    _patch_search(
        monkeypatch,
        s2_pre=[s2_pre], s2_post=[s2_post_bad], ls_pre=[ls_pre], ls_post=[ls_post],
    )
    monkeypatch.setattr(ss, "_candidate_valid_mask", _mask_lookup({
        "S2_0604": _full(1.0), "S2_0627": _full(0.10),
        "LS_0601": _full(1.0), "LS_0630": _full(0.95),
    }))
    result = ss.select(
        BBOX, ignition=date(2026, 6, 8), containment=date(2026, 6, 20),
        today=date(2026, 7, 17),
    )
    assert result["status"] == "recommended"
    assert result["pair"]["sensor"] == "Landsat"
    assert result["pair"]["pre"]["id"] == "LS_0601"
    assert result["pair"]["post"]["id"] == "LS_0630"
    # Never S2-pre + Landsat-post, even though that combination is "cleaner sooner".
    assert result["pair"]["pre"]["sensor"] == result["pair"]["post"]["sensor"]


# ---- failure taxonomy (honest, fail-loud; the Elephant case) ----


def test_mode_b_waiting_when_no_clean_post(monkeypatch):
    """Elephant known-answer: clean pre scenes exist, the only post pass is smoke-
    covered -> Mode B waiting state. NEVER a pre/pre pair, NEVER a fabricated pair."""
    pre_a = _cand("S2_0624", date(2026, 6, 24))
    pre_b = _cand("S2_0709", date(2026, 7, 9))
    # The only pass at/after containment is ~99.6% smoke (the Elephant regime).
    post_smoke = _cand("S2_0714", date(2026, 7, 14), cloud=99.6)
    _patch_search(monkeypatch, s2_pre=[pre_a, pre_b], s2_post=[post_smoke])
    monkeypatch.setattr(ss, "_candidate_valid_mask", _mask_lookup({
        "S2_0624": _full(1.0), "S2_0709": _full(1.0),
    }))
    result = ss.select(
        BBOX, ignition=date(2026, 7, 11), containment=date(2026, 7, 13),
        today=date(2026, 7, 17),
    )
    assert result["status"] == "waiting"
    assert "pair" not in result
    assert result["greenup_deadline"] == date(2026, 10, 11)  # containment + 90 d
    assert result["passes_tried"] >= 1
    assert result["next_overpass_eta"] >= date(2026, 7, 12)
    assert "clear" in result["eta_caveat"].lower()  # "an overpass isn't necessarily clear"


def test_window_closed_when_ceiling_reached_without_clean_post(monkeypatch):
    pre_b = _cand("S2_0604", date(2025, 6, 4))
    _patch_search(monkeypatch, s2_pre=[pre_b], s2_post=[])
    monkeypatch.setattr(ss, "_candidate_valid_mask", _mask_lookup({"S2_0604": _full(1.0)}))
    result = ss.select(
        BBOX, ignition=date(2025, 6, 8), containment=date(2025, 6, 20),
        today=date(2026, 7, 17),
    )
    assert result["status"] == "window_closed"
    assert "pair" not in result


def test_no_clean_pre_scene_hard_fail(monkeypatch):
    post = _cand("S2_0707", date(2026, 7, 7))
    _patch_search(monkeypatch, s2_pre=[], s2_post=[post])
    monkeypatch.setattr(ss, "_candidate_valid_mask", _mask_lookup({"S2_0707": _full(1.0)}))
    result = ss.select(
        BBOX, ignition=date(2026, 6, 8), containment=date(2026, 6, 20),
        today=date(2026, 7, 17),
    )
    assert result["status"] == "no_pre_scene"
    assert "pair" not in result


def test_stac_failure_is_loud(monkeypatch):
    def _boom(sensor, bbox, d0, d1):
        raise GateAbort("STAC search failed: HTTP 503 from earth-search (A8)")
    monkeypatch.setattr(ss, "_search_scenes", _boom)
    with pytest.raises(GateAbort) as e:
        ss.select(
            BBOX, ignition=date(2026, 6, 8), containment=date(2026, 6, 20),
            today=date(2026, 7, 17),
        )
    assert "STAC" in str(e.value)


# ---- same-day tile grouping (the Elephant 10SGJ+10TGK case; spec 6B in-zone mosaic) ----


def test_group_items_unions_same_day_tiles():
    # Two partial tiles, same sensor + day -> ONE candidate whose footprint is the
    # union (so a two-tile fire is not false-dead-ended by per-tile partial rejects).
    east_half = {
        "type": "Polygon",
        "coordinates": [[
            [-122.06, 38.0], [-121.5, 38.0], [-121.5, 39.0], [-122.06, 39.0], [-122.06, 38.0],
        ]],
    }
    a = _cand("T1", date(2026, 6, 4), cloud=40.0, footprint=_PARTIAL_FOOTPRINT)
    b = _cand("T2", date(2026, 6, 4), cloud=10.0, footprint=east_half)
    c = _cand("T3", date(2026, 6, 9), cloud=5.0)
    groups = ss.group_candidates([a, b, c])
    assert len(groups) == 2
    g = next(g for g in groups if g["date"] == date(2026, 6, 4))
    assert len(g["items"]) == 2
    # Lenient pre-filter uses the MIN member tile-cloud (the decisive gate is pixels).
    assert g["tile_cloud_pct"] == 10.0
    survivors, _ = ss.coarse_filter(
        groups, BBOX, window=(date(2026, 3, 10), date(2026, 6, 10))
    )
    assert {s["date"] for s in survivors} == {date(2026, 6, 4), date(2026, 6, 9)}


def test_grouped_mask_read_rejects_cross_utm_zone():
    a = _cand("T1", date(2026, 6, 4))
    b = _cand("T2", date(2026, 6, 4))
    a["epsg"], b["epsg"] = 32610, 32611
    (g,) = ss.group_candidates([a, b])
    with pytest.raises(GateAbort) as e:
        ss._candidate_valid_mask(g, BBOX)
    assert "UTM" in str(e.value)


# ---- pair re-evaluation (the spec-7 independent pre/post swap path) ----


def test_evaluate_pair_returns_metrics_and_verdict(monkeypatch):
    pre = _cand("S2_0604", date(2026, 6, 4))
    post = _cand("S2_0707", date(2026, 7, 7))
    monkeypatch.setattr(ss, "_candidate_valid_mask", _mask_lookup({
        "S2_0604": _full(0.98), "S2_0707": _full(0.95),
    }))
    ev = ss.evaluate_pair(pre, post, BBOX)
    assert ev["passes_gate"] is True
    assert ev["metrics"]["pair_valid_frac"] <= 0.98
    assert ev["verdict"]["verdict"] in ("good", "ok", "marginal", "below_bar")


def test_evaluate_pair_rejects_bad_ordering_and_mixed_sensor(monkeypatch):
    monkeypatch.setattr(ss, "_candidate_valid_mask", _mask_lookup({}))
    pre = _cand("S2_0707", date(2026, 7, 7))
    post = _cand("S2_0604", date(2026, 6, 4))
    with pytest.raises(GateAbort):          # post must postdate pre (never pre/pre)
        ss.evaluate_pair(pre, post, BBOX)
    ls_post = _cand("LS_0707", date(2026, 7, 7), sensor="Landsat")
    with pytest.raises(GateAbort):          # one sensor per pair (A2/A3)
        ss.evaluate_pair(_cand("S2_0604", date(2026, 6, 4)), ls_post, BBOX)


# ---- AOI-clipped RGB preview (display-only; the human's most intuitive signal) ----


def test_render_rgb_preview_from_local_assets(tmp_path):
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds

    w, s, e, n = transform_bounds("EPSG:4326", "EPSG:32610", *BBOX, densify_pts=21)
    transform = from_origin(w - 200, n + 200, 10.0, 10.0)
    rows = int((n - s) / 10) + 40
    cols = int((e - w) / 10) + 40
    assets = {}
    for i, band in enumerate(("red", "green", "blue")):
        arr = np.full((rows, cols), 2000 + i * 500, dtype=np.uint16)
        p = tmp_path / f"{band}.tif"
        with rasterio.open(
            p, "w", driver="GTiff", height=rows, width=cols, count=1,
            dtype="uint16", crs="EPSG:32610", transform=transform,
        ) as ds:
            ds.write(arr, 1)
        assets[band] = str(p)
    cand = _cand("S2_prev", date(2026, 6, 4))
    cand["assets"] = assets
    png = ss.render_rgb_preview(cand, BBOX)
    assert isinstance(png, bytes) and png[:8] == b"\x89PNG\r\n\x1a\n"


# ---- provenance / audit trail (spec section 8) ----


def test_recommendation_package_provenance(monkeypatch):
    pre_b = _cand("S2_0604", date(2026, 6, 4), cloud=12.0)
    post_g = _cand("S2_0707", date(2026, 7, 7), cloud=45.0)
    _patch_search(monkeypatch, s2_pre=[pre_b], s2_post=[post_g])
    monkeypatch.setattr(ss, "_candidate_valid_mask", _mask_lookup({
        "S2_0604": _full(0.98), "S2_0707": _full(0.91),
    }))
    result = ss.select(
        BBOX, ignition=date(2026, 6, 8), containment=date(2026, 6, 20),
        today=date(2026, 7, 17),
    )
    prov = result["provenance"]
    assert prov["pre"]["id"] == "S2_0604" and prov["post"]["id"] == "S2_0707"
    assert prov["pre"]["tile_cloud_pct"] == 12.0    # shown but de-emphasized
    assert prov["pair_valid_frac"] == pytest.approx(0.98 * 1.0, abs=0.1)  # intersection stat present
    assert prov["windows"]["pre"] and prov["windows"]["post"]
    assert prov["windows"]["widened"] is False       # v1 never auto-widens
    # A34 framing carried verbatim from the single source (src/outputs.py), never re-minted.
    from src.outputs import DNBR_FRAMING, SCREENING_STATEMENT
    assert result["framing"]["screening"] == SCREENING_STATEMENT
    assert result["framing"]["dnbr"] == DNBR_FRAMING
