"""scene_select.py -- deterministic scene-pair selector (B4): bbox + fire dates -> a
recommended clean pre/post pair + ranked alternatives + audit trail. No LLM anywhere in this
path; a HUMAN approves the pair before any dNBR is built.

Every threshold below is FROZEN by the auto-acquire pre-registration (vault). Never tune one
to make a run pass.

Statuses: recommended | waiting (no clean post YET; never pre/pre) | window_closed |
no_pre_scene; GateAbort = infrastructure failure.
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

# --- frozen pre-registration values ([ADOPT] published / [DERIVE]d / [BOUND] conservative) ---

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
S2_MIN_BASELINE = 4.0         # [ADOPT] S2 processing baseline >= 04.00 (in operations since
                              # 25 Jan 2022) -- the frozen SR offset (-1000) in the creator
                              # is wrong below it. Value moved here VERBATIM from dnbr_create
                              # (single source; the creator now imports it) so the SELECTOR
                              # also enforces it: the stress test (2026-07-27, F-1) showed a
                              # pre-2022 fire getting a graded scorecard the creator then
                              # aborted on AFTER the human approval gate.

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
    """Pre/post search windows: pre = [ignition - 90 d, ignition), post = [containment,
    containment + greenup_days]. All datetime.date."""
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
    """Drop candidates on metadata alone (window, footprint, tile-cloud > 80%). Returns
    (survivors, [(candidate, reason), ...]); a mixed-sensor pool -> GateAbort."""
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
    """Combined-pair statistics over the drawn box; the decisive number is the INTERSECTION
    valid fraction -- never each scene alone, never averaged."""
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
    """Good/OK/Marginal/Below-bar from the frozen rubric bands; the verdict is the WORSE of
    the two axes, never blended. Deterministic: identical metrics -> identical prose."""
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
    """Merge same-sensor same-day STAC items (adjacent tiles of one overpass) into ONE
    candidate: footprint = union, tile_cloud_pct = min member, baseline = numeric min."""
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
    """Numeric processing baseline ('05.12' -> 5.12). earth-search serves the field as a
    STRING, so a string compare would be wrong."""
    try:
        return float(b)
    except (TypeError, ValueError):
        return None


def s2_baseline_eligible(baseline) -> bool:
    """True iff an S2 member meets the frozen >= 04.00 baseline floor. Mirrors the creator's
    assert exactly -- a scene the creator would abort on must never enter the pool."""
    num = _baseline_num(baseline)
    return num is not None and num >= S2_MIN_BASELINE


# ---------------------------------------------------------------------------
# Network seams (monkeypatched in tests; live implementations below)
# ---------------------------------------------------------------------------


def _search_scenes(sensor, bbox, d0, d1):
    """STAC search -> candidate dicts for one sensor over [d0, d1). S2 via Earth Search;
    Landsat via Planetary Computer. Network failure -> GateAbort, never a silent empty pool."""
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
        # F-1 (stress test 2026-07-27): drop S2 members below the frozen baseline
        # floor BEFORE grouping, so a stale _0_L2A twin cannot poison a group whose
        # _1_L2A reprocessing is usable, and a pre-2022 fire's empty S2 pool falls
        # through to the Landsat arm instead of a scorecard the creator aborts on.
        if sensor == "S2" and not s2_baseline_eligible(props.get("s2:processing_baseline")):
            continue
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
    """Windowed cloud-mask read over the drawn box -> bool valid mask (S2 SCL / Landsat
    QA_PIXEL; grouped tiles OR-merged; cross-UTM-zone fails loud). Gate fraction only --
    the creator's band math does its own strict reads."""
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
# F-4 zone eligibility (stress test 2026-07-27) -- MGRS tiles overlap at UTM zone
# boundaries, so a same-day S2 group can span two zones even when the FIRE sits
# entirely in one. Such a group is unbuildable as a single native-lattice product
# (dnbr_create's _zones guard refuses it), and the mask-read abort it used to hit
# ESCAPED select() from inside the S2 iteration -- so the documented Landsat
# pair-level fallback never ran (measured: Montecito/Trout/Buck/Black all refused
# with 10-30 clean single-zone Landsat candidates available). Zone-spanning groups
# are now REJECTED into the audit trail at selection time; the mask-read abort
# remains as a defense-in-depth backstop for non-select callers.
# ---------------------------------------------------------------------------


def _zones_of(candidate):
    """Distinct member UTM zones (mirrors dnbr_create._zones; unknown -> empty set)."""
    members = candidate.get("items") or [candidate]
    return {m.get("epsg") for m in members if m.get("epsg") is not None}


def _reject_zone_spanning(candidates, rejected):
    """Partition out groups whose member tiles span >1 UTM zone (audit-trailed)."""
    kept = []
    for c in candidates:
        zones = _zones_of(c)
        if len(zones) > 1:
            rejected.append((c, (
                f"tile group spans UTM zones {sorted(zones)} (MGRS overlap at a zone "
                "boundary) -- unbuildable as one native-lattice product; single-zone "
                "candidates and the Landsat fallback still compete"
            )))
        else:
            kept.append(c)
    return kept


def _pair_zone_ok(pre, post):
    """A pair must live in ONE zone or the creator refuses it after approval.

    Unknown zones (hermetic fixtures; a STAC item missing proj:epsg) pass -- the
    creator's _zones union check remains the authoritative backstop there."""
    za, zb = _zones_of(pre), _zones_of(post)
    return not za or not zb or za == zb


# ---------------------------------------------------------------------------
# Stages 5-7 -- select, rank alternatives, package (the driver)
# ---------------------------------------------------------------------------


def select(bbox, *, ignition, containment, today=None, greenup_days=GREENUP_DEFAULT_DAYS,
           sensors=("S2", "Landsat")):
    """The deterministic selector: bbox + dates -> recommendation package or an honest failure
    state. S2 first, Landsat pair-level fallback, never mixed; pre = most-recent clean,
    post = first clean. `today` injectable for tests. `sensors` restricts the arms tried,
    in order (default: unchanged S2-first behavior)."""
    # A41: validate before any network-ish work -- a typo'd sensor name must fail loud here,
    # never fall through to _search_scenes' `== "S2"` else-Landsat branch and silently query
    # the wrong STAC.
    if not sensors or not set(sensors) <= {"S2", "Landsat"}:
        raise GateAbort(
            f"sensors={sensors!r} must be a non-empty subset of ('S2', 'Landsat')."
        )
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
    for sensor in sensors:
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
        # F-4a: zone-spanning groups are rejections, never aborts -- the sensor
        # loop must survive to try Landsat (see the F-4 block above).
        pre_surv = _reject_zone_spanning(pre_surv, all_rejected)
        post_surv = _reject_zone_spanning(post_surv, all_rejected)
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
            zone_blocked = False
            for pre in pres:
                # F-4b: a cross-zone (pre, post) is exactly what the creator's
                # _zones guard refuses AFTER approval -- skip before any mask read.
                if not _pair_zone_ok(pre, post):
                    zone_blocked = True
                    continue
                m = pair_metrics(_mask(pre), _mask(post))
                if passes_box_gate(m["pair_valid_frac"]):
                    chosen = {"sensor": sensor, "pre": pre, "post": post, "metrics": m}
                    break
                best_frac = m["pair_valid_frac"] if best_frac is None else max(
                    best_frac, m["pair_valid_frac"]
                )
            if chosen:
                break
            if best_frac is None and zone_blocked:
                all_rejected.append((post, (
                    f"UTM-zone mismatch: no pre-scene shares this post-scene's zone "
                    f"{sorted(_zones_of(post))} -- a cross-zone pair is unbuildable "
                    "(mirrors the creator's _zones guard)"
                )))
            else:
                all_rejected.append((post, (
                    f"box-gate: best combined pre-AND-post valid fraction "
                    f"{(best_frac or 0.0) * 100:.0f}% < {BOX_GATE_FLOOR * 100:.0f}% floor"
                )))
        if not chosen:
            continue

        # Ranked, pre-vetted alternatives for the independent pre/post swap (spec 7):
        # each option re-gated against the chosen partner, order preserved.
        # F-4b applies to swaps too: an alternative the creator refuses would
        # reopen the approve-then-abort seam through the side door.
        alt_pre = [
            c for c in pres if c["id"] != chosen["pre"]["id"]
            and _pair_zone_ok(c, chosen["post"])
            and passes_box_gate(pair_metrics(_mask(c), _mask(chosen["post"]))["pair_valid_frac"])
        ]
        alt_post = [
            c for c in posts if c["id"] != chosen["post"]["id"]
            and _pair_zone_ok(chosen["pre"], c)
            and passes_box_gate(pair_metrics(_mask(chosen["pre"]), _mask(c))["pair_valid_frac"])
        ]
        chosen["alt_pre"], chosen["alt_post"] = alt_pre, alt_post
        chosen["masks"] = masks
        break  # pair found for this sensor -> stop; remaining sensors in `sensors` are not tried (pair-level fallback)

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
    """AOI-clipped true-color preview -> PNG bytes. DISPLAY ONLY: never feeds any metric or
    gate."""
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
