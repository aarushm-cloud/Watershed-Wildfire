"""A27 terrain-applicability detector -- acceptance tests (P3.4-build-1).

Covers the four acceptance groups from the build prompt:
  A -- mask parity (the _valid_dem_mask extract is bit-identical on Montecito) + Montecito PROCEEDS
       + corrected coastal check (p1 is land, not ocean) + a characterization showing why land-only
       extents are load-bearing.
  B -- incised REFUSAL: the committed synthetic fixture (BLOCKER, hermetic, always runs) + the real
       South Fork DEM (corroboration; non-blocking if the gitignored DEM is absent on a clean checkout).
  C -- detector + message unit tests on synthetic arrays, incl. the exact `== 50.0` boundary.
  D -- firewall tripwires (return shape, frozen constant, signature, no config override, no mutation).

The behavior-lock (tests/test_behavior_lock.py) is intentionally NOT edited (A16: read-only oracle);
its 7 locks are run separately and stay green after the _valid_dem_mask refactor. This file only ADDS.

PINNED ORACLES captured 2026-06-29 from the unmodified pre-refactor masking (mask parity) and the
committed inputs. Do NOT hand-edit these to make a failing change pass -- recapture deliberately.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
from pathlib import Path

import numpy as np
import pytest

from src import config
from src.delineate import (
    HYPSOMETRIC_SPAN_THRESHOLD_M,
    _valid_dem_mask,
    assess_hypsometric_applicability,
)
from src.grids import GateAbort
from src.ingest import load_dem
from src.outputs import build_refusal_message, write_refusal

_REPO = Path(__file__).resolve().parent.parent

# --- paths -----------------------------------------------------------------------------------------
MONTECITO_DEM = _REPO / "validation" / "data" / "dem.tif"
SYNTH_FIXTURE = _REPO / "tests" / "fixtures" / "incised_synthetic.tif"
SOUTHFORK_DEM = _REPO / "data" / "southfork" / "dem" / "dem.tif"
SF_MANIFEST = _REPO / "validation" / "p3_southfork" / "acquisition_manifest.json"

# --- pinned oracle values (2026-06-29) -------------------------------------------------------------
# Montecito valid-cell mask, computed with the PRE-refactor masking (np.isfinite & != nodata=0).
# The _valid_dem_mask extract must reproduce these bit-for-bit -> proves the refactor changed nothing.
MONTECITO_MASK_COUNT = 1689332
MONTECITO_MASK_SHA256 = "5133c9316cbacf408ed76a2756c2425f087c0f8758af97a08ce7c89124e24b84"
# Committed synthetic incised fixture (deterministic generator output); span ~70.6 m.
SYNTH_SHA256 = "01e15c68c263f7d8c6b630346dac8d52bc7d45646e40594f94d557c8a74ab1dc"

EXACT_KEYS = {"refuse", "reason_code", "span_m", "span_threshold_m", "n_valid"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_levels_array(low_value, high_value, n_high=99):
    """101-cell array = two `low_value` cells + n_high `high_value` cells. With method='linear'
    percentiles, sorted-index 1 (=p1) lands on low_value and sorted-index 10 (=p10) on high_value,
    so (p10 - p1) == high_value - low_value EXACTLY -- a clean knob for the boundary tests."""
    vals = np.array([low_value, low_value] + [high_value] * n_high, dtype=np.float64)
    return vals


# ===================================================================================================
# Test A -- behavior-lock guard + mask parity + Montecito proceeds + coastal
# ===================================================================================================
def test_A_mask_parity_montecito_bit_identical():
    """The shared _valid_dem_mask reproduces the pre-refactor valid-cell set bit-for-bit."""
    _grid, dem, dem_raw = load_dem(MONTECITO_DEM)
    mask = _valid_dem_mask(dem_raw, dem.nodata)
    count = int(mask.sum())
    chk = hashlib.sha256(mask.tobytes()).hexdigest()
    assert count == MONTECITO_MASK_COUNT, (
        f"valid-cell count {count} != pinned pre-refactor {MONTECITO_MASK_COUNT} -- "
        "the _valid_dem_mask extract changed the valid-cell set.")
    assert chk == MONTECITO_MASK_SHA256, (
        f"valid-cell mask checksum {chk} != pinned {MONTECITO_MASK_SHA256} -- mask drift.")


def test_A_montecito_proceeds_with_margin(caplog):
    """Montecito (the validated range-front case) does NOT refuse, with reported margin; and the
    detector LOGS p1/p10/n_valid (firewall: those are logged, never returned)."""
    _grid, dem, dem_raw = load_dem(MONTECITO_DEM)
    with caplog.at_level(logging.INFO, logger="src.delineate"):
        verdict = assess_hypsometric_applicability(dem_raw, dem.nodata)

    assert verdict["refuse"] is False
    assert verdict["reason_code"] == "OK_RANGE_FRONT_APPLICABLE"

    # margin visibility: report span / n_valid / p1 / p10 (p1,p10 computed here, NOT from the verdict)
    valid = _valid_dem_mask(dem_raw, dem.nodata)
    p1, p10 = np.percentile(dem_raw[valid], [1, 10], method="linear")
    print(f"\n[A] Montecito: span_m={verdict['span_m']:.4f}  n_valid={verdict['n_valid']}  "
          f"p1={p1:.4f}  p10={p10:.4f}  margin_below_50={50.0 - verdict['span_m']:.4f} m")
    assert verdict["span_m"] < HYPSOMETRIC_SPAN_THRESHOLD_M
    assert abs(verdict["span_m"] - float(p10 - p1)) < 1e-9   # span == p10 - p1

    # diagnostic logging present (the proceed-case diagnostics go to the logger only)
    assert "p1=" in caplog.text and "p10=" in caplog.text and "n_valid=" in caplog.text


def test_A_coastal_p1_is_land_not_ocean():
    """Corrected coastal check: the tool does NOT mask water; it relies on a land-clipped extent.
    So the honest assertion is that Montecito's p1 is a real LAND elevation (>= sea level), i.e.
    the extent is not ocean-contaminated. (Usage note: A27 assumes a land-only DEM extent.)"""
    _grid, dem, dem_raw = load_dem(MONTECITO_DEM)
    valid = _valid_dem_mask(dem_raw, dem.nodata)
    p1 = float(np.percentile(dem_raw[valid], 1, method="linear"))
    assert p1 >= 0.0, (f"Montecito p1={p1:.4f} m is below sea level -- the DEM extent looks "
                       "ocean-contaminated; A27's land-only-extent precondition is violated.")


def test_A_characterization_ocean_block_inflates_span():
    """Characterization (why land-only extents are load-bearing): inject a near/below-sea-level block
    into a COPY of the Montecito DEM (an unmasked-ocean stand-in). The span inflates and crosses 50,
    flipping the verdict to REFUSE -- which is exactly what a non-land-clipped extent would do."""
    _grid, dem, dem_raw = load_dem(MONTECITO_DEM)
    base = assess_hypsometric_applicability(dem_raw, dem.nodata)

    corrupted = dem_raw.copy()
    corrupted[0:300, 0:300] = -500.0          # finite and != nodata(0) -> counted as valid "ocean"
    bad = assess_hypsometric_applicability(corrupted, dem.nodata)
    print(f"\n[A] characterization: base span={base['span_m']:.2f} (refuse={base['refuse']}) -> "
          f"with ocean block span={bad['span_m']:.2f} (refuse={bad['refuse']})")
    assert bad["span_m"] > base["span_m"]
    assert bad["span_m"] > HYPSOMETRIC_SPAN_THRESHOLD_M
    assert bad["refuse"] is True


# ===================================================================================================
# Test B -- incised refusal: synthetic fixture (blocker) + South Fork (corroboration)
# ===================================================================================================
def test_B_synthetic_fixture_refuses_and_writes_refusal(tmp_path):
    """BLOCKER, hermetic: the committed synthetic incised fixture matches its pinned SHA, refuses
    cleanly, and write_refusal emits a complete refusal.json with NO ranking artifacts and NO
    exception."""
    assert SYNTH_FIXTURE.exists(), f"committed fixture missing: {SYNTH_FIXTURE}"
    assert _sha256(SYNTH_FIXTURE) == SYNTH_SHA256, "synthetic fixture SHA drift (regenerate + repin)"

    _grid, dem, dem_raw = load_dem(SYNTH_FIXTURE)
    verdict = assess_hypsometric_applicability(dem_raw, dem.nodata)
    assert verdict["refuse"] is True
    assert verdict["reason_code"] == "REFUSED_INCISED_TERRAIN"
    assert verdict["span_m"] > HYPSOMETRIC_SPAN_THRESHOLD_M

    refusal_path = write_refusal(verdict, tmp_path)           # must not raise
    assert refusal_path.exists()
    payload = json.loads(refusal_path.read_text())
    for key in ("status", "reason_code", "trigger", "span_m", "span_threshold_m", "n_valid",
                "message", "screening", "ranking_produced", "explanation"):
        assert key in payload, f"refusal.json missing required field: {key}"
    assert payload["status"] == "REFUSED"
    assert payload["ranking_produced"] is False
    assert payload["trigger"] == "hypsometric_span"
    # on REFUSE no ranking artifacts are written
    assert not (tmp_path / "ranking.csv").exists()
    assert not (tmp_path / "basins.geojson").exists()


def test_B_southfork_corroboration():
    """CORROBORATION (non-blocking if absent): the real South Fork DEM is gitignored, so it may not
    be present on a clean checkout. If present, it must match the manifest SHA and REFUSE with a span
    in (50, 200) m (logged, not pinned). If absent, skip loudly. If present and it does NOT refuse,
    that is a hard FAIL."""
    if not SOUTHFORK_DEM.exists():
        pytest.skip(f"CORROBORATION UNAVAILABLE: {SOUTHFORK_DEM} absent (gitignored). "
                    f"Pinned SHA lives in {SF_MANIFEST} (raster_sha256). Does not fail the suite.")
    manifest = json.loads(SF_MANIFEST.read_text())
    expected_sha = manifest["raster_sha256"]["data/southfork/dem/dem.tif"]
    assert _sha256(SOUTHFORK_DEM) == expected_sha, "South Fork DEM SHA != manifest (wrong/altered raster)"

    _grid, dem, dem_raw = load_dem(SOUTHFORK_DEM)
    verdict = assess_hypsometric_applicability(dem_raw, dem.nodata)
    print(f"\n[B] South Fork corroboration: span_m={verdict['span_m']:.4f}  refuse={verdict['refuse']}")
    assert verdict["refuse"] is True, "South Fork (incised) must REFUSE -- corroboration FAILED."
    assert 50.0 < verdict["span_m"] < 200.0, (
        f"South Fork span {verdict['span_m']:.2f} m outside the (50, 200) sanity band.")


# ===================================================================================================
# Test C -- detector + message unit tests (synthetic arrays)
# ===================================================================================================
def test_C_exact_boundary_50_is_not_refused():
    """EXACT boundary (strict >): (p10 - p1) == 50.0 -> refuse False (50 is in-bounds)."""
    vals = _build_levels_array(0.0, 50.0)            # span exactly 50.0
    verdict = assess_hypsometric_applicability(vals, None)
    assert verdict["span_m"] == 50.0, f"expected exact 50.0, got {verdict['span_m']!r}"
    assert verdict["refuse"] is False


def test_C_just_over_boundary_is_refused():
    """50.0 + eps -> refuse True."""
    vals = _build_levels_array(0.0, 50.0 + 1e-3)
    verdict = assess_hypsometric_applicability(vals, None)
    assert verdict["span_m"] > 50.0
    assert verdict["refuse"] is True


def test_C_narrow_proceeds_wide_refuses():
    narrow = assess_hypsometric_applicability(_build_levels_array(0.0, 30.0), None)
    wide = assess_hypsometric_applicability(_build_levels_array(0.0, 80.0), None)
    assert narrow["refuse"] is False and narrow["span_m"] == 30.0
    assert wide["refuse"] is True and wide["span_m"] == 80.0


def test_C_nodata_block_masked_before_percentiles():
    """FM-12 mirror: a nodata block is excluded by _valid_dem_mask BEFORE percentiles, so it does
    not corrupt the span or inflate n_valid."""
    clean = _build_levels_array(0.0, 30.0)           # 101 valid cells, span 30 -> proceed
    nodata = -9999.0
    polluted = np.concatenate([clean, np.full(500, nodata)])   # add 500 nodata cells

    # the mask must exclude exactly the nodata cells
    mask = _valid_dem_mask(polluted, nodata)
    assert int(mask.sum()) == clean.size
    assert not mask[clean.size:].any()               # every appended nodata cell excluded

    verdict = assess_hypsometric_applicability(polluted, nodata)
    assert verdict["n_valid"] == clean.size          # nodata not counted
    assert verdict["span_m"] == 30.0                 # nodata did not move the percentiles
    assert verdict["refuse"] is False


def test_C_message_is_span_based_no_mode_claim():
    """build_refusal_message cites the span and makes NO mode-count / 'single mode' claim."""
    msg = build_refusal_message("REFUSED_INCISED_TERRAIN", 70.6, 50.0)
    assert "71 m" in msg, f"message should cite the rounded span; got: {msg!r}"
    low = msg.lower()
    assert "mode" not in low, f"message must not make a mode-count claim; got: {msg!r}"
    assert "single mode" not in low and "bimodal" not in low and "modal" not in low


# ===================================================================================================
# Test D -- firewall (structural tripwires)
# ===================================================================================================
def test_D_return_shape_is_exactly_the_allowed_keys():
    """Returned dict keys are EXACTLY {refuse, reason_code, span_m, span_threshold_m, n_valid} --
    no p1_m/p10_m/contour/elevation-cutoff leak."""
    verdict = assess_hypsometric_applicability(_build_levels_array(0.0, 30.0), None)
    assert set(verdict.keys()) == EXACT_KEYS, f"return shape drifted: {sorted(verdict.keys())}"
    for forbidden in ("p1_m", "p10_m", "p1", "p10", "contour", "contour_m", "elevation_cutoff",
                      "cutoff_m"):
        assert forbidden not in verdict, f"firewall breach: detector leaked '{forbidden}'"


def test_D_threshold_is_frozen_50():
    assert HYPSOMETRIC_SPAN_THRESHOLD_M == 50.0


def test_D_signature_has_no_threshold_parameter():
    """Detector signature is (dem_raw, dem_nodata) only -- no threshold parameter, no override path."""
    params = list(inspect.signature(assess_hypsometric_applicability).parameters)
    assert params == ["dem_raw", "dem_nodata"], f"unexpected detector signature: {params}"


def test_D_no_config_override():
    """The frozen constant has no config.py entry / override path."""
    assert not hasattr(config, "HYPSOMETRIC_SPAN_THRESHOLD_M")
    for name in dir(config):
        assert "HYPSOMETRIC" not in name.upper(), f"config exposes an A27 override knob: {name}"


def test_D_detector_does_not_mutate_dem():
    """The detector takes a fancy-index copy; the caller's array is unchanged."""
    arr = _build_levels_array(0.0, 80.0)
    before = arr.copy()
    assess_hypsometric_applicability(arr, None)
    assert np.array_equal(arr, before), "detector mutated dem_raw"


def test_D_empty_dem_fails_loud():
    """A no-valid-cells DEM is a broken input, not an incised refusal -> fail loud (A8), not a verdict."""
    allnodata = np.full(100, -9999.0)
    with pytest.raises(GateAbort):
        assess_hypsometric_applicability(allnodata, -9999.0)
