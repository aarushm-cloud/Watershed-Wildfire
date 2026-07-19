"""AA-1 (B4, Auto-Acquire Build-Plan Phase 1) -- deterministic scene-pair selector.

Turns a drawn bbox + ignition/containment dates into a recommended clean pre/post
satellite scene pair (plus ranked alternatives + audit trail) for the auto-acquire
dNBR pathway. Pure structured optimization -- no LLM anywhere in this path (Feature
Spec section 9). The pair is proposed; a HUMAN approves it before any dNBR is built.

Lives in the autoacquire/ package, outside src/ (A35 pattern): this module is a
NETWORK boundary; src/ stays a pure no-network seam. All network seams are module-level functions
(_search_scenes, _candidate_valid_mask) so tests monkeypatch them (suite convention).

Every threshold below is FROZEN by the ratified pre-registration (vault:
"Auto-Acquire dNBR Pathway -- Pre-Registration", RATIFIED 2026-07-17). Never tune
one to make a run pass -- changing any value re-opens the pre-registration (Tier-1).

Honesty note (science guardrail section 0): no published external operational
cloud-fraction threshold exists for this gate; the 0.50 floor is DERIVED from the
pipeline's own frozen per-basin guard (DNBR_NODATA_FAILLOUD_FRAC = 0.20 -- a flowed
basin needs >= 80% valid), stated here rather than asserted as a literature value.

Failure taxonomy (fail loud, never fabricate -- A8/FM-10, the Elephant lesson):
  status='recommended'   clean pair found; human approval is the next gate.
  status='waiting'       Mode B: no clean post-scene YET (never differences pre/pre).
  status='window_closed' green-up ceiling passed without a clean post-scene.
  status='no_pre_scene'  no clean pre-scene within the 90 d archive window (rare).
  GateAbort              infrastructure/contract failure (STAC down, grid mismatch).
"""

from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.grids import GateAbort  # noqa: E402  (A8 fail-loud contract, same as acquire.py)

# ---------------------------------------------------------------------------
# Frozen pre-registration values (RATIFIED 2026-07-17). Tags per the pre-reg:
# [ADOPT] published convention verbatim / [DERIVE] from a frozen quantity /
# [BOUND] conservative worst-case bound.
# ---------------------------------------------------------------------------

PRE_WINDOW_DAYS = 90          # [BOUND] pre = most-recent clean scene <= 90 d before ignition
GREENUP_DEFAULT_DAYS = 90     # [ADOPT+BOUND] post ceiling default: containment + 90 d
GREENUP_MAX_DAYS = 180        # [BOUND] operator-extendable hard max (slow-recovery conifer)

TILE_CLOUD_MAX_PCT = 80.0     # [BOUND] STAC eo:cloud_cover pre-filter (whole-tile %, strict >).
                              # Coarse only -- NEVER the decisive gate (the Elephant lesson:
                              # ~5% tile / ~99.6% over the fire).

BOX_GATE_FLOOR = 0.50         # [DERIVE] combined pre-AND-post valid fraction over the drawn
                              # box (dimensionless, 0-1). Derived from the frozen per-basin
                              # DNBR_NODATA_FAILLOUD_FRAC = 0.20; lenient by construction.

# Rubric bands (pair-valid fraction over the box / per-scene cloud-over-AOI fraction).
RUBRIC_GOOD_PAIR = 0.90       # [DERIVE] >= 90% box-valid -> basins clear the 80% guard with margin
RUBRIC_OK_PAIR = 0.75         # [DERIVE]
RUBRIC_GOOD_SCENE_CLOUD = 0.05  # [DERIVE] per-scene cloud over the AOI (fraction, not tile %)
RUBRIC_OK_SCENE_CLOUD = 0.15    # [DERIVE]

# Technical facts [ADOPT], transcribed verbatim (pre-reg D; DATA_SOURCES section 2;
# working code putah_dnbr.py / p2_acquire_dnbr.py). Never reconstructed from memory.
S2_STAC = "https://earth-search.aws.element84.com/v1/search"
S2_COLLECTION = "sentinel-2-l2a"
S2_BAD_SCL = (0, 1, 3, 6, 8, 9, 10, 11)  # nodata, defective, cloud-shadow, water,
                                          # cloud-medium, cloud-high, cirrus, snow
S2_REVISIT_DAYS = 5           # ~5 d (2-satellite constellation)

LANDSAT_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
LANDSAT_SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign?href="
LANDSAT_COLLECTION = "landsat-c2-l2"
LANDSAT_QA_CLOUD_BITS = (1, 2, 3, 4)  # dilated cloud, cirrus, cloud, cloud shadow
LANDSAT_QA_FILL_BIT = 0
LANDSAT_REVISIT_DAYS = 8      # ~8 d combined (Landsat 8 + 9)

_STAC_TIMEOUT_S = 60          # plumbing, not science (confidence: high)
_STAC_LIMIT = 200             # plumbing: max features per search (confidence: high)
_MAX_GRID_SLOP_PX = 3         # plumbing: gate-mask shape tolerance before GateAbort
                              # (fraction statistic only; NEVER used for band math)

ETA_CAVEAT = (
    "An overpass isn't necessarily clear -- the ETA is the next expected pass, "
    "not a guarantee of a usable scene (depends on weather)."
)


# ---------------------------------------------------------------------------
# Stage 1 -- windows (pre-reg B)
# ---------------------------------------------------------------------------


def derive_windows(*, ignition, containment, today, greenup_days=GREENUP_DEFAULT_DAYS):
    """Pre/post search windows from the fire dates (all datetime.date, half-open ends).

    pre  = [ignition - 90 d, ignition)              -- scene must strictly predate ignition
    post = [containment, containment + greenup_days] -- first clean scene wins (initial
           assessment, BAER convention); the green-up ceiling bounds the poll.
    """
    if not (0 < greenup_days <= GREENUP_MAX_DAYS):
        raise GateAbort(
            f"green-up ceiling override {greenup_days} d is outside (0, {GREENUP_MAX_DAYS}] -- "
            f"the operator hard max is containment + {GREENUP_MAX_DAYS} d (pre-reg B)."
        )
    if containment < ignition:
        raise GateAbort(
            f"containment {containment.isoformat()} predates ignition {ignition.isoformat()} -- "
            "check the fire dates."
        )
    post_end = containment + timedelta(days=greenup_days)
    return {
        "pre_start": ignition - timedelta(days=PRE_WINDOW_DAYS),
        "pre_end": ignition,                     # exclusive
        "post_start": containment,               # inclusive
        "post_end": post_end,                    # inclusive (the green-up deadline)
        "greenup_days": greenup_days,
        "window_closed": today > post_end,
    }


# ---------------------------------------------------------------------------
# Stage 3 -- coarse filter (metadata only; no pixels, no AI)
# ---------------------------------------------------------------------------


def coarse_filter(candidates, bbox, *, window):
    """Drop candidates on metadata alone: out-of-window date, partial-AOI footprint,
    tile-cloud > 80%. Returns (survivors, [(candidate, reason), ...]).

    window is half-open [start, end). A mixed-sensor pool is a caller bug -> GateAbort
    (pairs are internally one sensor, A2/A3; the pools are built per sensor).
    """
    from shapely.geometry import box as _box, shape as _shape

    sensors = {c["sensor"] for c in candidates}
    if len(sensors) > 1:
        raise GateAbort(
            f"coarse_filter got a mixed-sensor pool {sorted(sensors)} -- pools are per-sensor "
            "(one sensor per pair, A2/A3)."
        )
    aoi = _box(*bbox)
    d0, d1 = window
    survivors, rejected = [], []
    for c in candidates:
        if not (d0 <= c["date"] < d1):
            rejected.append((c, f"outside the search window [{d0.isoformat()}, {d1.isoformat()})"))
            continue
        if not _shape(c["footprint"]).covers(aoi):
            rejected.append((c, "footprint does not fully cover the AOI (partial-AOI reject)"))
            continue
        cloud = c.get("tile_cloud_pct")
        if cloud is not None and cloud > TILE_CLOUD_MAX_PCT:
            rejected.append(
                (c, f"tile-cloud {cloud:.1f}% > {TILE_CLOUD_MAX_PCT:.0f}% pre-filter "
                    "(whole-tile, coarse only)")
            )
            continue
        survivors.append(c)
    return survivors, rejected


# ---------------------------------------------------------------------------
# Stage 4 -- decisive box-gate (pixels over the drawn box)
# ---------------------------------------------------------------------------


def s2_valid_mask(scl):
    """Valid-pixel mask from a Sentinel-2 SCL array (uint8 classes, 20 m).
    Bad classes frozen: [0,1,3,6,8,9,10,11] (pre-reg D)."""
    return ~np.isin(scl.astype(np.uint8), np.array(S2_BAD_SCL, dtype=np.uint8))


def landsat_valid_mask(qa):
    """Valid-pixel mask from a Landsat QA_PIXEL array (uint16 bitfield, 30 m).
    Bad = fill bit 0 OR any of bits 1-4 (dilated cloud, cirrus, cloud, shadow) --
    the frozen pre-reg D list. (Snow is masked on S2 via SCL 11; the Landsat gate
    follows the frozen bit list verbatim.)"""
    qa_u = qa.astype(np.uint16)
    bad = ((qa_u >> LANDSAT_QA_FILL_BIT) & 1).astype(bool)
    for b in LANDSAT_QA_CLOUD_BITS:
        bad |= ((qa_u >> b) & 1).astype(bool)
    return ~bad


def pair_metrics(pre_valid, post_valid):
    """Combined-pair statistics over the drawn box. The decisive number is the
    INTERSECTION valid fraction (two scenes each 90% clean but cloudy in different
    places can still fail) -- never each scene alone, never averaged.

    Shape slop up to _MAX_GRID_SLOP_PX per axis is trimmed (fraction statistic only;
    the creator's band math has its own strict same-grid guard). Larger mismatch is
    a grid contract violation -> GateAbort.
    """
    if pre_valid.shape != post_valid.shape:
        dr = abs(pre_valid.shape[0] - post_valid.shape[0])
        dc = abs(pre_valid.shape[1] - post_valid.shape[1])
        if dr > _MAX_GRID_SLOP_PX or dc > _MAX_GRID_SLOP_PX:
            raise GateAbort(
                f"pre/post gate masks differ by ({dr}, {dc}) px -- more than the "
                f"{_MAX_GRID_SLOP_PX} px window slop; scene grids are inconsistent (A8)."
            )
        r = min(pre_valid.shape[0], post_valid.shape[0])
        c = min(pre_valid.shape[1], post_valid.shape[1])
        pre_valid, post_valid = pre_valid[:r, :c], post_valid[:r, :c]
    n = pre_valid.size
    if n == 0:
        raise GateAbort("empty gate window over the AOI -- no pixels to assess (A8).")
    return {
        "pre_valid_frac": float(pre_valid.sum()) / n,
        "post_valid_frac": float(post_valid.sum()) / n,
        "pair_valid_frac": float((pre_valid & post_valid).sum()) / n,
    }


def passes_box_gate(pair_valid_frac):
    """Lenient floor: combined pre-AND-post valid fraction >= 0.50 (frozen, [DERIVE])."""
    return pair_valid_frac >= BOX_GATE_FLOOR


# ---------------------------------------------------------------------------
# Interpretation -- deterministic threshold -> verdict -> templated prose
# ---------------------------------------------------------------------------


def rubric_verdict(pair_valid_frac, scene_cloud_fracs):
    """Good/OK/Marginal/Below-bar verdict from the frozen rubric bands (pre-reg C).

    Two axes -- pair-valid fraction and worst per-scene cloud-over-AOI fraction --
    and the verdict is the WORSE of the two, never blended (the B1 anti-composite
    ethos). Identical metrics always yield the identical verdict + prose.
    """
    worst_cloud = max(scene_cloud_fracs) if scene_cloud_fracs else 0.0
    if pair_valid_frac >= RUBRIC_GOOD_PAIR:
        pair_band = "good"
    elif pair_valid_frac >= RUBRIC_OK_PAIR:
        pair_band = "ok"
    elif pair_valid_frac >= BOX_GATE_FLOOR:
        pair_band = "marginal"
    else:
        pair_band = "below_bar"
    if worst_cloud <= RUBRIC_GOOD_SCENE_CLOUD:
        cloud_band = "good"
    elif worst_cloud <= RUBRIC_OK_SCENE_CLOUD:
        cloud_band = "ok"
    elif pair_valid_frac >= BOX_GATE_FLOOR:
        cloud_band = "marginal"
    else:
        cloud_band = "below_bar"
    order = ("good", "ok", "marginal", "below_bar")
    verdict = order[max(order.index(pair_band), order.index(cloud_band))]
    label = {
        "good": "clean; dNBR covers essentially the whole fire",
        "ok": "usable; minor NoData gaps",
        "marginal": "passes the gate but approve with caution",
        "below_bar": "below the bar -- best available so far; recommend waiting/polling",
    }[verdict]
    summary = (
        f"dNBR will cover ~{pair_valid_frac * 100:.0f}% of your fire area. "
        f"Worst per-scene cloud over your fire: {worst_cloud * 100:.0f}%. "
        f"Verdict: {verdict.upper()} -- {label}."
    )
    return {"verdict": verdict, "summary": summary}


# ---------------------------------------------------------------------------
# Same-day tile grouping (pure; the Elephant 10SGJ+10TGK case)
# ---------------------------------------------------------------------------


def group_candidates(items):
    """Merge same-sensor, same-day STAC items (adjacent tiles of one overpass) into
    ONE candidate, so a fire spanning two tiles in a UTM zone is not false-dead-ended
    by per-tile partial-footprint rejects (spec 6B: native-grid mosaic works within a
    zone; cross-zone fails loud at mask-read time).

    Group fields: footprint = union of member footprints; tile_cloud_pct = MIN member
    value (lenient on purpose -- a cloudy neighbor tile barely touching the AOI must
    not veto the group; the decisive gate reads actual pixels); processing_baseline =
    numeric MIN (worst case, what the creator's assert cares about).
    """
    from shapely.geometry import mapping as _mapping, shape as _shape
    from shapely.ops import unary_union

    groups = {}
    for it in items:
        groups.setdefault((it["sensor"], it["date"]), []).append(it)
    out = []
    for (sensor, d), members in sorted(groups.items(), key=lambda kv: kv[0][1]):
        if len(members) == 1:
            out.append(members[0])
            continue
        members = sorted(members, key=lambda m: m["id"])
        clouds = [m["tile_cloud_pct"] for m in members if m.get("tile_cloud_pct") is not None]
        baselines = [
            m["processing_baseline"] for m in members
            if _baseline_num(m.get("processing_baseline")) is not None
        ]
        out.append({
            "id": "+".join(m["id"] for m in members),
            "sensor": sensor,
            "date": d,
            "tile_cloud_pct": min(clouds) if clouds else None,
            "footprint": _mapping(unary_union([_shape(m["footprint"]) for m in members])),
            "processing_baseline": (
                min(baselines, key=_baseline_num) if baselines else None
            ),
            "items": members,
        })
    return out


def _baseline_num(b):
    """Numeric processing baseline ('05.12' -> 5.12). STRING compare would be wrong
    (verified live 2026-07-17: earth-search serves the field as a string)."""
    try:
        return float(b)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Network seams (monkeypatched in tests; live implementations below)
# ---------------------------------------------------------------------------


def _search_scenes(sensor, bbox, d0, d1):
    """STAC search -> candidate dicts for one sensor over [d0, d1) (dates).

    Candidate shape: {id, sensor, date, tile_cloud_pct, footprint, processing_baseline,
    assets}. S2 via Earth Search v1 (sentinel-2-l2a); Landsat 8/9 via Microsoft
    Planetary Computer (landsat-c2-l2, token-free; asset hrefs are SAS-signed at
    read time). Network failure -> GateAbort (A8), never a silent empty pool.
    """
    import requests

    if sensor == "S2":
        url, collections, extra = S2_STAC, [S2_COLLECTION], {}
    else:
        url, collections = LANDSAT_STAC, [LANDSAT_COLLECTION]
        extra = {"query": {"platform": {"in": ["landsat-8", "landsat-9"]}}}
    body = {
        "collections": collections,
        "bbox": list(bbox),
        "datetime": f"{d0.isoformat()}T00:00:00Z/{(d1 - timedelta(days=1)).isoformat()}T23:59:59Z",
        "limit": _STAC_LIMIT,
        **extra,
    }
    try:
        r = requests.post(url, json=body, timeout=_STAC_TIMEOUT_S)
        r.raise_for_status()
        features = r.json().get("features", [])
    except requests.RequestException as e:
        raise GateAbort(
            f"STAC search failed for {sensor} at {url}: {type(e).__name__}: {e} (A8)"
        ) from e
    out = []
    for f in features:
        props = f.get("properties", {})
        iso = (props.get("datetime") or "")[:10]
        if not iso:
            continue
        y, m, d = (int(x) for x in iso.split("-"))
        epsg = props.get("proj:epsg")
        if epsg is None and isinstance(props.get("proj:code"), str):
            code = props["proj:code"]
            epsg = int(code.rsplit(":", 1)[-1]) if code.upper().startswith("EPSG") else None
        out.append({
            "id": f["id"],
            "sensor": sensor,
            "date": date(y, m, d),
            "tile_cloud_pct": props.get("eo:cloud_cover"),
            "footprint": f.get("geometry"),
            "processing_baseline": props.get("s2:processing_baseline"),
            "epsg": epsg,
            "assets": {
                k: v.get("href")
                for k, v in f.get("assets", {}).items()
                if k in ("scl", "qa_pixel", "red", "green", "blue", "nir08", "swir22")
            },
        })
    # Same-day adjacent tiles of one overpass become ONE candidate (Elephant case).
    return group_candidates(out)


def _candidate_valid_mask(candidate, bbox):
    """Windowed cloud-mask read over the drawn box -> bool valid mask.

    S2: SCL asset (20 m, uint8 classes). Landsat: QA_PIXEL (30 m, uint16 bits),
    SAS-signed first. Grouped candidates (same-day adjacent tiles) are read
    boundless per member and OR-merged: a pixel is valid if ANY member tile sees
    it cleanly (first-valid-wins mosaic semantics; boundless fill = the sensor's
    nodata/fill code, so uncovered pixels stay invalid). Cross-UTM-zone groups
    fail loud (spec 6B, v1). This feeds the GATE FRACTION only -- band math in
    the creator does its own strict windowed reads (Phase 2). Failure -> GateAbort.
    """
    members = candidate.get("items") or [candidate]
    epsgs = {m.get("epsg") for m in members if m.get("epsg") is not None}
    if len(epsgs) > 1:
        raise GateAbort(
            f"scene group {candidate['id']} spans multiple UTM zones {sorted(epsgs)} -- "
            "a cross-UTM-zone fire is unsupported in v1; aborting loud rather than "
            "resampling across zones (spec 6B)."
        )
    is_s2 = candidate["sensor"] == "S2"
    key = "scl" if is_s2 else "qa_pixel"
    # Boundless fill: SCL 0 = nodata class -> invalid; QA bit 0 set = fill -> invalid.
    fill = 0 if is_s2 else 1
    merged = None
    for m in members:
        arr = _read_mask_window(m, bbox, key, candidate["id"], fill)
        valid = s2_valid_mask(arr) if is_s2 else landsat_valid_mask(arr)
        if merged is None:
            merged = valid
        else:
            r = min(merged.shape[0], valid.shape[0])
            c = min(merged.shape[1], valid.shape[1])
            merged = merged[:r, :c] | valid[:r, :c]
    return merged


def _read_mask_window(member, bbox, key, group_id, fill):
    """One member tile's mask band, windowed boundless over the bbox (gate only)."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    href = (member.get("assets") or {}).get(key)
    if not href:
        raise GateAbort(
            f"scene {group_id} has no {key} asset -- cannot run the decisive "
            "cloud gate on it (A8)."
        )
    if member["sensor"] != "S2":
        href = _sign_mpc(href)
    try:
        with rasterio.open(href) as ds:
            wsen = transform_bounds("EPSG:4326", ds.crs, *bbox, densify_pts=21)
            win = from_bounds(*wsen, transform=ds.transform)
            win = Window(  # snap to whole pixels (gate fraction only)
                int(math.floor(win.col_off)),
                int(math.floor(win.row_off)),
                int(math.ceil(win.width)),
                int(math.ceil(win.height)),
            )
            arr = ds.read(1, window=win, boundless=True, fill_value=fill)
    except (rasterio.errors.RasterioError, OSError) as e:
        raise GateAbort(
            f"cloud-mask read failed for scene {group_id} ({key}): "
            f"{type(e).__name__}: {e} (A8)"
        ) from e
    if arr.size == 0:
        raise GateAbort(
            f"scene {group_id} {key} window over the AOI is empty -- footprint/"
            "grid mismatch (A8)."
        )
    return arr


def _sign_mpc(href):
    """SAS-sign a Planetary Computer asset href (token-free; p2_acquire_dnbr pattern)."""
    import requests
    from urllib.parse import quote

    try:
        r = requests.get(LANDSAT_SIGN + quote(href, safe=""), timeout=_STAC_TIMEOUT_S)
        r.raise_for_status()
        return r.json()["href"]
    except requests.RequestException as e:
        raise GateAbort(f"MPC SAS signing failed: {type(e).__name__}: {e} (A8)") from e


# ---------------------------------------------------------------------------
# Stages 5-7 -- select, rank alternatives, package (the driver)
# ---------------------------------------------------------------------------


def select(bbox, *, ignition, containment, today=None, greenup_days=GREENUP_DEFAULT_DAYS):
    """The deterministic selector: bbox + dates -> recommendation package or an honest
    failure state. Sentinel-2 first, Landsat pair-level fallback, never mixed (A2/A3).

    Deterministic given the same scene pool + constants: pre = most-recent gate-passing
    scene, post = FIRST gate-passing scene at/after containment (freshness priority,
    initial-assessment framing). `today` is injectable for reproducibility/tests.
    """
    today = today if today is not None else date.today()
    windows = derive_windows(
        ignition=ignition, containment=containment, today=today, greenup_days=greenup_days
    )
    post_search_end = min(today, windows["post_end"]) + timedelta(days=1)  # half-open

    all_rejected = []       # (candidate, reason) audit trail across sensors
    passes_tried = 0        # post-window passes examined (Mode B honesty counter)
    latest_post_seen = None  # for the next-overpass ETA
    any_clean_pre = False

    chosen = None
    for sensor in ("S2", "Landsat"):
        pre_pool = _search_scenes(sensor, bbox, windows["pre_start"], windows["pre_end"])
        post_pool = _search_scenes(sensor, bbox, windows["post_start"], post_search_end)
        passes_tried += len(post_pool)
        for c in post_pool:
            if latest_post_seen is None or c["date"] > latest_post_seen:
                latest_post_seen = c["date"]

        pre_surv, pre_rej = coarse_filter(
            pre_pool, bbox, window=(windows["pre_start"], windows["pre_end"])
        )
        post_surv, post_rej = coarse_filter(
            post_pool, bbox, window=(windows["post_start"], post_search_end)
        )
        all_rejected.extend(pre_rej)
        all_rejected.extend(post_rej)
        any_clean_pre = any_clean_pre or bool(pre_surv)
        if not pre_surv or not post_surv:
            continue

        # Freshness priority: posts earliest-first, pres most-recent-first.
        posts = sorted(post_surv, key=lambda c: c["date"])
        pres = sorted(pre_surv, key=lambda c: c["date"], reverse=True)
        masks = {}

        def _mask(c):
            if c["id"] not in masks:
                masks[c["id"]] = _candidate_valid_mask(c, bbox)
            return masks[c["id"]]

        for post in posts:
            best_frac = None
            for pre in pres:
                m = pair_metrics(_mask(pre), _mask(post))
                if passes_box_gate(m["pair_valid_frac"]):
                    chosen = {"sensor": sensor, "pre": pre, "post": post, "metrics": m}
                    break
                best_frac = m["pair_valid_frac"] if best_frac is None else max(
                    best_frac, m["pair_valid_frac"]
                )
            if chosen:
                break
            all_rejected.append((post, (
                f"box-gate: best combined pre-AND-post valid fraction "
                f"{(best_frac or 0.0) * 100:.0f}% < {BOX_GATE_FLOOR * 100:.0f}% floor"
            )))
        if not chosen:
            continue

        # Ranked, pre-vetted alternatives for the independent pre/post swap (spec 7):
        # each option re-gated against the chosen partner, order preserved.
        alt_pre = [
            c for c in pres if c["id"] != chosen["pre"]["id"]
            and passes_box_gate(pair_metrics(_mask(c), _mask(chosen["post"]))["pair_valid_frac"])
        ]
        alt_post = [
            c for c in posts if c["id"] != chosen["post"]["id"]
            and passes_box_gate(pair_metrics(_mask(chosen["pre"]), _mask(c))["pair_valid_frac"])
        ]
        chosen["alt_pre"], chosen["alt_post"] = alt_pre, alt_post
        chosen["masks"] = masks
        break  # S2 pair found -> no Landsat fallback needed (pair-level fallback)

    if chosen is None:
        return _failure_state(
            windows, today, any_clean_pre, passes_tried, latest_post_seen, all_rejected
        )
    return _package(bbox, windows, chosen, all_rejected)


def _failure_state(windows, today, any_clean_pre, passes_tried, latest_post_seen, rejected):
    """The honest no-pair outcomes. Order: no-pre (hard, rare) -> window-closed (hard)
    -> Mode B waiting. Never a pair, never a ranking (B1 hard invariant)."""
    base = {
        "windows": _windows_prov(windows),
        "greenup_deadline": windows["post_end"],
        "rejected": rejected,
        "framing": _framing(),
    }
    if not any_clean_pre:
        return {
            **base,
            "status": "no_pre_scene",
            "message": (
                f"No clean pre-fire scene found within {PRE_WINDOW_DAYS} d before ignition "
                "(rare). A trustworthy dNBR cannot be built without one (A8)."
            ),
        }
    if windows["window_closed"]:
        return {
            **base,
            "status": "window_closed",
            "message": (
                "The green-up ceiling has passed without a clean post-fire scene -- this "
                "fire's valid initial-assessment window has closed (pre-reg B). "
                "No dNBR will be fabricated from out-of-window imagery."
            ),
        }
    eta_base = latest_post_seen if latest_post_seen is not None else windows["post_start"]
    return {
        **base,
        "status": "waiting",
        "message": (
            "No usable post-fire scene yet -- every pass so far is cloud/smoke-covered "
            "or none has occurred since containment. Re-check later; the selector will "
            "re-run in full (Mode B, manual re-check v1)."
        ),
        "passes_tried": passes_tried,
        "next_overpass_eta": eta_base + timedelta(days=S2_REVISIT_DAYS),
        "eta_caveat": ETA_CAVEAT,
    }


def _package(bbox, windows, chosen, rejected):
    """Stage-7 recommendation package: pair + verdicts + ranked alternatives +
    provenance. The dNBR is NOT computed here -- that happens after human approval."""
    m = chosen["metrics"]
    pre, post = chosen["pre"], chosen["post"]
    pre_cloud = 1.0 - m["pre_valid_frac"]
    post_cloud = 1.0 - m["post_valid_frac"]
    return {
        "status": "recommended",
        "pair": {
            "sensor": chosen["sensor"],
            "pre": pre,
            "post": post,
            "metrics": m,
            "verdict": rubric_verdict(m["pair_valid_frac"], [pre_cloud, post_cloud]),
        },
        "alternatives": {"pre": chosen["alt_pre"], "post": chosen["alt_post"]},
        "rejected": rejected,
        "provenance": {
            "pre": _scene_prov(pre),
            "post": _scene_prov(post),
            "pair_valid_frac": m["pair_valid_frac"],
            "pre_cloud_over_aoi": pre_cloud,
            "post_cloud_over_aoi": post_cloud,
            "windows": _windows_prov(windows),
            "rejected": [{"id": c["id"], "reason": r} for c, r in rejected],
            "selector": "scene_select.select (AA-1, pre-reg RATIFIED 2026-07-17)",
        },
        "framing": _framing(),
        "bbox": tuple(bbox),
    }


def _scene_prov(c):
    return {
        "id": c["id"],
        "date": c["date"].isoformat(),
        "sensor": c["sensor"],
        "tile_cloud_pct": c.get("tile_cloud_pct"),   # shown but de-emphasized (spec 7)
        "processing_baseline": c.get("processing_baseline"),
    }


def _windows_prov(w):
    return {
        "pre": (w["pre_start"].isoformat(), w["pre_end"].isoformat()),
        "post": (w["post_start"].isoformat(), w["post_end"].isoformat()),
        "greenup_days": w["greenup_days"],
        "widened": False,  # v1 never auto-widens (window-adjust deferred, spec 7)
    }


def _framing():
    """A34 framing carried verbatim from the single source of truth (src/outputs.py);
    never re-minted here (owner's framing reconciliation stays single-site)."""
    from src.outputs import DNBR_FRAMING, SCREENING_STATEMENT

    return {"screening": SCREENING_STATEMENT, "dnbr": DNBR_FRAMING}


# ---------------------------------------------------------------------------
# Pair re-evaluation + previews (consumed by the Phase-4 approval UI)
# ---------------------------------------------------------------------------


def evaluate_pair(pre, post, bbox):
    """Re-gate + re-verdict one explicit pre/post combination (the spec-7 independent
    swap path: the user picks among pre-vetted alternatives; every displayed option
    is re-run through the SAME deterministic gate + rubric, never hand-waved)."""
    if pre["sensor"] != post["sensor"]:
        raise GateAbort(
            f"cannot pair {pre['sensor']} pre with {post['sensor']} post -- a pair is "
            "internally ONE sensor, never blended (A2/A3)."
        )
    if not (pre["date"] < post["date"]):
        raise GateAbort(
            f"pre scene {pre['id']} ({pre['date'].isoformat()}) does not predate post "
            f"scene {post['id']} ({post['date'].isoformat()}) -- ordering violation; a "
            "pre/pre difference is structurally forbidden (the Elephant lesson)."
        )
    m = pair_metrics(_candidate_valid_mask(pre, bbox), _candidate_valid_mask(post, bbox))
    return {
        "metrics": m,
        "verdict": rubric_verdict(
            m["pair_valid_frac"],
            [1.0 - m["pre_valid_frac"], 1.0 - m["post_valid_frac"]],
        ),
        "passes_gate": passes_box_gate(m["pair_valid_frac"]),
    }


_PREVIEW_MAX_PX = 800   # display-only decimation bound (plumbing, not science)


def render_rgb_preview(candidate, bbox):
    """AOI-clipped true-color preview -> PNG bytes (S2/Landsat red/green/blue).

    DISPLAY ONLY: decimated read + 2-98 percentile stretch; never feeds any metric
    or gate. Seeing their fire area (thin haze included) is the most intuitive
    quality signal for a non-remote-sensing user (spec 7). Grouped candidates
    mosaic first-valid-wins; uncovered/fill pixels render dark gray.
    """
    import io

    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds
    from PIL import Image

    members = candidate.get("items") or [candidate]
    bands = None
    for m in members:
        got = []
        for key in ("red", "green", "blue"):
            href = (m.get("assets") or {}).get(key)
            if not href:
                raise GateAbort(
                    f"scene {candidate['id']} has no {key} asset -- cannot render a preview."
                )
            if m["sensor"] != "S2" and not Path(str(href)).exists():
                href = _sign_mpc(href)
            try:
                with rasterio.open(href) as ds:
                    wsen = transform_bounds("EPSG:4326", ds.crs, *bbox, densify_pts=21)
                    win = from_bounds(*wsen, transform=ds.transform)
                    win = Window(
                        int(math.floor(win.col_off)), int(math.floor(win.row_off)),
                        int(math.ceil(win.width)), int(math.ceil(win.height)),
                    )
                    dec = max(1, int(math.ceil(max(win.width, win.height) / _PREVIEW_MAX_PX)))
                    out_shape = (
                        max(1, int(win.height // dec)), max(1, int(win.width // dec))
                    )
                    arr = ds.read(1, window=win, boundless=True, fill_value=0,
                                  out_shape=out_shape)
            except (rasterio.errors.RasterioError, OSError) as e:
                raise GateAbort(
                    f"preview read failed for scene {candidate['id']} ({key}): "
                    f"{type(e).__name__}: {e}"
                ) from e
            got.append(arr.astype("float64"))
        if bands is None:
            bands = got
        else:
            have = bands[0] > 0
            for i in range(3):
                r = min(bands[i].shape[0], got[i].shape[0])
                c = min(bands[i].shape[1], got[i].shape[1])
                bands[i] = np.where(have[:r, :c], bands[i][:r, :c], got[i][:r, :c])

    rgb = np.zeros((*bands[0].shape, 3), dtype="uint8")
    valid = bands[0] > 0
    for i, band in enumerate(bands):
        vals = band[valid]
        if vals.size:
            lo, hi = np.percentile(vals, 2), np.percentile(vals, 98)
            hi = hi if hi > lo else lo + 1.0
            rgb[..., i] = np.clip((band - lo) / (hi - lo) * 255.0, 0, 255).astype("uint8")
    rgb[~valid] = (30, 30, 30)
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Standalone CLI (Build-Plan Phase 1 deliverable; the UI wraps select() in Phase 4)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Deterministic dNBR scene-pair selector (AA-1)")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    ap.add_argument("--ignition", required=True, help="YYYY-MM-DD")
    ap.add_argument("--containment", required=True, help="YYYY-MM-DD")
    ap.add_argument("--greenup-days", type=int, default=GREENUP_DEFAULT_DAYS)
    args = ap.parse_args()

    res = select(
        tuple(args.bbox),
        ignition=date.fromisoformat(args.ignition),
        containment=date.fromisoformat(args.containment),
        greenup_days=args.greenup_days,
    )

    def _js(o):
        if isinstance(o, date):
            return o.isoformat()
        if isinstance(o, np.ndarray):
            return None  # masks are not serialized
        return str(o)

    res.pop("masks", None)
    print(json.dumps(res, default=_js, indent=2))
