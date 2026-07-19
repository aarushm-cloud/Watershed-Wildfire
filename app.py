"""app.py -- local Streamlit frontend (A36), the thin UI over run_pipeline for non-developers.

Draw/enter a bounding box, upload a raw dNBR GeoTIFF, click run -> acquire.build_fire_config
(A35) auto-fetches DEM + buildings and assembles the A30 fire dict -> run_pipeline scores the
dNBR both-arms path (A34) -> a ranked map + CSV, or a legible refusal. A **local, single-user
tool that wraps the CLI** (A36 reconciles A7's "no live service"); not hosted, not multi-user.

Guardrail tier: Tier-2 (UI plumbing) -- no science here. The frozen formula, dNBR knobs, and
`src/` are untouched; this only orchestrates acquire + run_pipeline + the existing output writers.

Every artifact keeps the screening spine (A11: within-fire relative ranking, never a prediction)
and the dNBR n=1 framing (A34: triage-validated, not exact-rank-validated). EVERY failure renders
as a legible message, never a stack trace (F5): fail-loud aborts (GateAbort/ValueError) verbatim,
the A27 terrain refusal as an honest outcome, and anything else (network/GDAL/osmnx) via the
run_screening backstop with the exception NAMED -- translated loud, never swallowed. A stored
result is stamped with the inputs that produced it; editing the box/upload flags it stale (F8).

Testability: all logic lives in pure, importable helpers; the Streamlit UI is in `main()` behind
an `if __name__ == "__main__"` guard, so `import app` (tests) never executes the UI. See
tests/test_app.py. Run the app with:  streamlit run app.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import folium

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from src.grids import GateAbort
from src.outputs import SCREENING_STATEMENT

# |rankA - rankB| at/above which a basin is flagged "rank uncertain" (display heuristic, Tier-2, not
# a science value): the honest surfacing of Arm A / Arm B disagreement (A34 rank_delta).
RANK_UNCERTAIN_DELTA = 3

# Decimal places for BOTH the bbox number_input display AND the F8 staleness key -- one constant so they
# cannot drift apart (a key finer than the display flags stale after a visually no-op edit). 5 dp ~= 1 m
# at these latitudes, far below the 10 m analysis cell, so sub-key differences are screening-irrelevant.
_BBOX_DP = 5


# ---- pure helpers (no Streamlit; unit-tested in tests/test_app.py) ------------------------------

def validate_bbox(west, south, east, north) -> tuple:
    """Fail loud + legible on a malformed bbox BEFORE any network work (A8). Returns floats."""
    try:
        west, south, east, north = float(west), float(south), float(east), float(north)
    except (TypeError, ValueError):
        raise GateAbort("Bounding box must be four numbers: west, south, east, north (degrees).")
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        raise GateAbort(f"Longitude out of range (west={west}, east={east}); must be within -180..180.")
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        raise GateAbort(f"Latitude out of range (south={south}, north={north}); must be within -90..90.")
    if west >= east:
        raise GateAbort(f"West ({west}) must be less than East ({east}) -- check the box corners.")
    if south >= north:
        raise GateAbort(f"South ({south}) must be less than North ({north}) -- check the box corners.")
    return (west, south, east, north)


def result_to_view(result: dict) -> dict:
    """Map run_pipeline's polymorphic result to a small view model (kind ranked|refused|unknown)."""
    status = result.get("status")
    if status == "refused":
        return {"kind": "refused", "message": result.get("message", "Screening refused."),
                "reason_code": result.get("reason_code")}
    if status == "ranked":
        arms = result.get("arms")
        if arms is None:                               # minor: a non-dNBR (SBS-shaped) ranked result has
            return {"kind": "unknown",                 # no 'arms' -- degrade, never KeyError
                    "message": "Ranked result has no 'arms' (the UI expects the dNBR both-arms shape)."}
        arm_a = arms["arm_a"]
        return {"kind": "ranked", "n_basins": len(arm_a["basins"]),
                "headline_arm": result.get("headline_arm", "arm_a")}
    return {"kind": "unknown", "message": f"Unexpected pipeline result status: {status!r}."}


def basin_rows(fc: dict, *, uncertain_delta: int = RANK_UNCERTAIN_DELTA) -> list:
    """basins.geojson -> display rows sorted by the Arm A headline rank. Columns are ordered so the
    frozen score reads left-to-right as its own inputs -- mean_burn x mean_slope x area_km2 -> score
    -- making the ranking auditable. Burn is the Arm A binned value (the term the headline score uses);
    slope + area are identical across arms. Carries the rank_delta 'uncertain' flag (A34)."""
    rows = []
    for feat in fc.get("features", []):
        p = feat.get("properties", {})
        delta = p.get("rank_delta", abs((p.get("rank") or 0) - (p.get("rank_b") or 0)))
        rows.append({"basin_id": p.get("basin_id"), "rank": p.get("rank"),
                     "mean_burn": p.get("mean_burn_a", p.get("mean_burn")),   # Arm A binned burn (headline)
                     "mean_slope": p.get("mean_slope"), "area_km2": p.get("area_km2"),
                     "score": p.get("score"),
                     "rank_b": p.get("rank_b"), "score_b": p.get("score_b"),
                     "rank_delta": delta, "uncertain": delta >= uncertain_delta})
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"]))
    return rows


def rank_fill_color(rank: int, n_basins: int) -> str:
    """Hot->cool ramp: rank 1 (highest screening priority) = hot red, last rank = pale. Hex string."""
    frac = 0.0 if n_basins <= 1 else max(0.0, min(1.0, (rank - 1) / (n_basins - 1)))
    r = int(215 + frac * (255 - 215))
    g = int(48 + frac * (255 - 48))
    b = int(39 + frac * (178 - 39))
    return f"#{r:02x}{g:02x}{b:02x}"


def _iter_coords(coords):
    """Yield (x, y) from arbitrarily-nested GeoJSON coordinate arrays (Polygon or MultiPolygon)."""
    if coords and isinstance(coords[0], (int, float)):
        yield coords[0], coords[1]
    else:
        for c in coords:
            yield from _iter_coords(c)


def _fc_center(fc: dict):
    xs, ys = [], []
    for feat in fc.get("features", []):
        for x, y in _iter_coords(feat.get("geometry", {}).get("coordinates", [])):
            xs.append(x)
            ys.append(y)
    if not xs:
        return [39.0, -100.0]   # CONUS-ish default
    return [(min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2]   # folium is [lat, lon]


def bbox_from_draw(draw: dict):
    """Extract (west, south, east, north) from a streamlit-folium draw payload, or None."""
    if not draw:
        return None
    feat = draw.get("last_active_drawing") or (draw.get("all_drawings") or [None])[-1]
    geom = (feat or {}).get("geometry", {})
    if geom.get("type") != "Polygon":
        return None
    ring = geom["coordinates"][0]
    xs = [c[0] for c in ring]
    ys = [c[1] for c in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def screen_inputs_key(west, south, east, north, dnbr_file, *, mode="upload", gen=None):
    """Identity of the inputs a screening result was produced from (F8 staleness check).

    A stored result is only "current" for the exact bbox + upload that produced it; after the user
    edits the box or swaps the file, the old map/CSV must be flagged, never silently posed as the
    new inputs' screening. The upload identity prefers Streamlit's per-upload file_id (a re-upload
    of a same-named file gets a fresh id), falling back to (name, size). No upload -> None.

    Generate mode (AA-4): pass mode="generate" and gen=(ignition_iso, containment_iso,
    greenup_days, pre_scene_id, post_scene_id) -- the dates AND the approved pair are part of the
    result's identity, so editing a date or swapping a scene flags the old result stale. The
    default (upload, gen=None) returns the legacy 5-tuple unchanged, so existing stamps and the
    upload path are byte-identical."""
    f = None
    if dnbr_file is not None:
        fid = getattr(dnbr_file, "file_id", None)         # Streamlit sets a fresh uuid per upload
        # explicit is-not-None (not `or`): an empty-but-present id must not silently fall back. The
        # (name, size) fallback is defensive for non-Streamlit file-likes only; it can collide for a
        # same-name/same-size re-export, so it never activates on a real Streamlit UploadedFile.
        f = fid if fid is not None else (getattr(dnbr_file, "name", None), getattr(dnbr_file, "size", None))
    base = (round(float(west), _BBOX_DP), round(float(south), _BBOX_DP),
            round(float(east), _BBOX_DP), round(float(north), _BBOX_DP), f)
    if mode == "upload" and gen is None:
        return base
    return base + (mode, tuple(gen) if gen is not None else None)


def run_screening(bbox_raw, dnbr_file, *, name="frontend", contour_m=150.0):
    """One screening run end-to-end -> the screen dict main() stores in session_state
    (kind: ranked | refused | error).

    EVERY failure reduces to a legible {"kind": "error"} -- never a raise, never a stack trace to
    the user (F5): GateAbort/ValueError carry their domain message verbatim; ANY other exception
    (network drop, GDAL/rasterio on a wrong upload, osmnx/requests -- RasterioIOError is an OSError,
    which the old narrow except let straight through to a Streamlit traceback) hits the backstop and
    is prefixed with its type. Not a swallow: the failure is NAMED in the message and nothing is
    retried or defaulted (A8 -- the sin is silence, not scope). Pure orchestration, no st.* calls,
    so tests drive it directly with fakes (tests/test_app.py)."""
    out_dir = None
    try:
        # deferred imports INSIDE the try (still keeping `import app` light for unit tests): an
        # import-time failure in the heavy geo stack then reduces to a legible {"kind":"error"} dict
        # too, honoring the "EVERY failure" contract above rather than escaping as a raw traceback.
        from acquire import build_fire_config
        from src.pipeline import run_pipeline
        from src.outputs import write_dnbr_outputs
        bbox = validate_bbox(*bbox_raw)
        if dnbr_file is None:
            return {"kind": "error", "message": "Upload a raw-scale dNBR GeoTIFF before running."}
        out_dir = Path(tempfile.mkdtemp(prefix="wws_frontend_"))
        dnbr_path = out_dir / "dnbr_upload.tif"
        dnbr_path.write_bytes(dnbr_file.getvalue())
        fire = build_fire_config(bbox, dnbr_path, out_dir, name=name)
        result = run_pipeline(fire, contour_m=contour_m)
        view = result_to_view(result)
        if view["kind"] == "ranked":
            csv_path, gj_path = write_dnbr_outputs(
                result["arms"]["arm_a"], result["arms"]["arm_b"], result["creek_nearest"],
                fire["out_dir"], fire["dem"],
                validation_case=f"{fire['name']} (coordinate entry, dNBR both-arms)")
            try:
                fc = json.loads(Path(gj_path).read_text())
            except json.JSONDecodeError as e:   # a truncated geojson WE wrote is an internal fault, not
                # a domain bbox/scale error -- route it to the backstop (type-named + logged) rather than
                # render the cryptic JSONDecodeError verbatim through the domain-message catch below.
                raise RuntimeError(f"wrote an unreadable basins.geojson at {gj_path}: {e}") from e
            return {"kind": "ranked", "n": view["n_basins"], "fc": fc,
                    "csv": Path(csv_path).read_bytes()}
        if view["kind"] == "refused":
            return {"kind": "refused", "message": view["message"]}
        return {"kind": "error", "message": view.get("message", "Unexpected pipeline result.")}
    except (GateAbort, ValueError) as e:
        return {"kind": "error", "message": str(e)}     # legible domain message (bbox/scale/zone), verbatim
    except Exception as e:                              # F5 backstop: never a traceback to the user
        import traceback
        traceback.print_exc(file=sys.stderr)           # preserve the dev debugging channel (the console)
        return {"kind": "error", "message": f"unexpected {type(e).__name__} during screening: {e}"}
    finally:
        if out_dir is not None:                        # minor: no per-run temp-dir leak -- the outputs
            shutil.rmtree(out_dir, ignore_errors=True)  # are already read into memory before the return


# ---- Generate-from-dates helpers (AA-4, Auto-Acquire Phase 4; pure, no st.*) --------------------

# Verdict icons for the deterministic rubric (display only; the rubric itself is frozen
# in scene_select -- identical metrics always render the identical scorecard).
_VERDICT_ICONS = {"good": "✅", "ok": "\U0001f7e1", "marginal": "\U0001f7e0",
                  "below_bar": "\U0001f534"}


def generate_package(bbox_raw, ignition, containment, greenup_days=90):
    """Run the deterministic selector -> {"kind": "package"} | {"kind": "error"}.

    Same failure contract as run_screening (F5): GateAbort/ValueError verbatim, anything
    else backstopped with its type named. The package's honest non-pair states (waiting /
    window_closed / no_pre_scene) are NOT errors -- they pass through inside the package."""
    try:
        from autoacquire import scene_select
        bbox = validate_bbox(*bbox_raw)
        package = scene_select.select(
            bbox, ignition=ignition, containment=containment, greenup_days=greenup_days)
        return {"kind": "package", "package": package}
    except (GateAbort, ValueError) as e:
        return {"kind": "error", "message": str(e)}
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {"kind": "error", "message": f"unexpected {type(e).__name__} during scene search: {e}"}


def scorecard_view(package) -> dict:
    """Recommendation package -> a pure view model for the approval scorecard (spec section 7).

    Cloud-over-YOUR-fire is the headline number; tile-cloud is shown but explicitly
    de-emphasized (teaching the right mental model is half the value). The timing flag is
    derived from FROZEN values only: a post-scene is flagged when it sits beyond the
    conservative default green-up ceiling (only reachable via the operator override) --
    no new judgment threshold is invented here."""
    from datetime import date as _date, timedelta as _td

    pair = package["pair"]
    m = pair["metrics"]
    v = pair["verdict"]
    scenes = []
    for role, cand, cloud in (("Pre-fire", pair["pre"], 1.0 - m["pre_valid_frac"]),
                              ("Post-fire", pair["post"], 1.0 - m["post_valid_frac"])):
        d = cand["date"]
        tile = cand.get("tile_cloud_pct")
        scenes.append({
            "role": role, "id": cand["id"], "sensor": cand["sensor"],
            "date": d.isoformat() if isinstance(d, _date) else str(d),
            "cloud_over_fire_pct": round(cloud * 100.0, 1),
            "tile_cloud_pct": round(tile, 1) if tile is not None else None,
            "tile_note": (f"Scene is {tile:.0f}% cloudy overall — but that's the whole "
                          "tile, not your fire." if tile is not None else None),
        })
    windows = package["provenance"]["windows"]
    post_start = _date.fromisoformat(windows["post"][0])
    post_date = pair["post"]["date"]
    beyond_default = (windows["greenup_days"] > 90
                      and post_date > post_start + _td(days=90))
    return {
        "icon": _VERDICT_ICONS.get(v["verdict"], "❓"),
        "verdict": v["verdict"], "summary": v["summary"],
        "pair_valid_pct": round(m["pair_valid_frac"] * 100.0, 1),
        "sensor": pair["sensor"], "scenes": scenes,
        "timing_flag": ("⚠️ post-scene is beyond the conservative default "
                        "green-up ceiling (operator-extended window) — regrowth may "
                        "mute the burn signal." if beyond_default else None),
        "n_alternatives": {"pre": len(package["alternatives"]["pre"]),
                           "post": len(package["alternatives"]["post"])},
    }


def run_generated_screening(bbox_raw, pair, *, name="frontend", contour_m=150.0):
    """Approved pair -> dNBR (creator) -> the SAME validated downstream as an upload
    (build_fire_config -> run_pipeline -> write_dnbr_outputs) -> the screen dict.

    Identical failure contract to run_screening (F5): every failure reduces to a legible
    {"kind": "error"}. Extra keys on success: quicklook (PNG bytes) + dnbr_provenance
    (the creator's audit record) so the UI can show what was built. No science here --
    the creator + pipeline own their own gates (AA-2/AA-3). contour_m mirrors run_screening:
    the per-fire mountain-front elevation (B2) is threaded into run_pipeline so the Generate
    path honors the operator's contour too, not just the Upload path."""
    out_dir = None
    try:
        from autoacquire import dnbr_create
        from acquire import build_fire_config
        from src.pipeline import run_pipeline
        from src.outputs import write_dnbr_outputs
        bbox = validate_bbox(*bbox_raw)
        out_dir = Path(tempfile.mkdtemp(prefix="wws_autoacq_"))
        created = dnbr_create.create_dnbr(pair, bbox, out_dir / "dnbr", name=name)
        fire = build_fire_config(bbox, created["dnbr_tif"], out_dir, name=name)
        result = run_pipeline(fire, contour_m=contour_m)
        view = result_to_view(result)
        if view["kind"] == "ranked":
            csv_path, gj_path = write_dnbr_outputs(
                result["arms"]["arm_a"], result["arms"]["arm_b"], result["creek_nearest"],
                fire["out_dir"], fire["dem"],
                validation_case=f"{fire['name']} (auto-acquire, dNBR both-arms)")
            try:
                fc = json.loads(Path(gj_path).read_text())
            except json.JSONDecodeError as e:
                raise RuntimeError(f"wrote an unreadable basins.geojson at {gj_path}: {e}") from e
            return {"kind": "ranked", "n": view["n_basins"], "fc": fc,
                    "csv": Path(csv_path).read_bytes(),
                    "quicklook": Path(created["quicklook_png"]).read_bytes(),
                    "dnbr_provenance": json.loads(Path(created["provenance_json"]).read_text())}
        if view["kind"] == "refused":
            return {"kind": "refused", "message": view["message"]}
        return {"kind": "error", "message": view.get("message", "Unexpected pipeline result.")}
    except (GateAbort, ValueError) as e:
        return {"kind": "error", "message": str(e)}
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {"kind": "error", "message": f"unexpected {type(e).__name__} during screening: {e}"}
    finally:
        if out_dir is not None:
            shutil.rmtree(out_dir, ignore_errors=True)


def build_basin_map(fc: dict, *, uncertain_delta: int = RANK_UNCERTAIN_DELTA) -> folium.Map:
    """A folium map of the ranked basins: fill by Arm A rank (hot=priority); a blue dashed outline
    flags basins where Arm A / Arm B disagree (rank-uncertain, A34)."""
    rows = {r["basin_id"]: r for r in basin_rows(fc, uncertain_delta=uncertain_delta)}
    n = max(len(rows), 1)
    m = folium.Map(location=_fc_center(fc), zoom_start=12, tiles="OpenStreetMap")

    def _style(feat):
        p = feat["properties"]
        r = rows.get(p.get("basin_id"), {})
        unc = r.get("uncertain", False)
        return {"fillColor": rank_fill_color(p.get("rank") or n, n),
                "color": "#1f78ff" if unc else "#2b2b2b", "weight": 3 if unc else 1,
                "dashArray": "5,5" if unc else None, "fillOpacity": 0.6}

    gj = folium.GeoJson(
        fc, style_function=_style,
        tooltip=folium.GeoJsonTooltip(
            fields=["basin_id", "rank", "score", "rank_b", "rank_delta"],
            aliases=["Basin", "Rank (Arm A)", "Score", "Rank (Arm B)", "Rank Δ"]),
    )
    gj.add_to(m)
    try:
        bounds = gj.get_bounds()
        if bounds and bounds[0][0] is not None:
            m.fit_bounds(bounds)
    except Exception:
        pass
    return m


# ---- Streamlit UI (guarded; never runs on import) ----------------------------------------------

def _draw_map():
    from folium.plugins import Draw
    m = folium.Map(location=[39.0, -100.0], zoom_start=4, tiles="OpenStreetMap")
    Draw(export=False,
         draw_options={"rectangle": True, "polygon": False, "polyline": False,
                       "circle": False, "marker": False, "circlemarker": False},
         edit_options={"edit": False}).add_to(m)
    return m


def _render_generate_panel(gen_box, bbox_raw, inputs_key, screen_box, *, contour_m=150.0):
    """The AA-4 approval surface: scorecard + previews + approve / burn-map / swap
    actions, or the honest non-pair states (Mode B waiting / window-closed / no-pre).
    Machine proposes, human disposes -- nothing is built without the Approve click.
    UI-side (imports streamlit); all decisions live in the pure helpers + scene_select."""
    import streamlit as st
    from autoacquire import scene_select

    outcome = gen_box.get("outcome") or {}
    if outcome.get("kind") == "error":
        st.error(f"Could not search scenes: {outcome['message']}")
        return
    package = (outcome.get("package") or {})
    status = package.get("status")
    if status == "waiting":
        # Mode B (Q3a v1): honest waiting state + user-driven re-check. NEVER a
        # burn-less ranking; the B1 exploratory-layers viewer is its own queued item.
        st.warning(f"**No usable post-fire scene yet.** {package['message']}")
        st.markdown(
            f"- Satellite passes checked since containment: **{package['passes_tried']}**\n"
            f"- Next-overpass ETA: **{package['next_overpass_eta']}** — {package['eta_caveat']}\n"
            f"- Valid-assessment window closes (green-up deadline): "
            f"**{package['greenup_deadline']}**\n\n"
            "Come back later and click **Find scene pair** again — each re-check re-runs "
            "the full search."
        )
        with st.expander("Why each pass was rejected (audit trail)"):
            for cand, reason in package.get("rejected", []):
                st.markdown(f"- `{cand['id']}` ({cand['date']}) — {reason}")
        return
    if status == "window_closed":
        st.error(f"**This fire's valid-assessment window has closed.** {package['message']}")
        return
    if status == "no_pre_scene":
        st.error(f"**No clean pre-fire scene found.** {package['message']}")
        return
    if status != "recommended":
        return

    sc = scorecard_view(package)
    st.subheader(f"Recommended pair — {sc['icon']} {sc['verdict'].upper()}")
    st.markdown(sc["summary"])
    st.caption("Any basin missing more than 20% of its burn data fails loud downstream, "
               "so NoData gaps can't silently corrupt a ranking.")
    if sc["timing_flag"]:
        st.warning(sc["timing_flag"])

    cols = st.columns(2)
    for col, scene in zip(cols, sc["scenes"]):
        with col:
            st.markdown(f"**{scene['role']}** · {scene['sensor']} · {scene['date']}")
            st.markdown(f"Cloud over your fire: **{scene['cloud_over_fire_pct']}%**")
            if scene["tile_note"]:
                st.caption(scene["tile_note"])          # tile-cloud shown but de-emphasized
            cache = gen_box.setdefault("previews", {})
            if scene["id"] not in cache:
                cand = (package["pair"]["pre"] if scene["role"] == "Pre-fire"
                        else package["pair"]["post"])
                try:
                    with st.spinner("Rendering preview..."):
                        cache[scene["id"]] = scene_select.render_rgb_preview(
                            cand, validate_bbox(*bbox_raw))
                except Exception as e:   # a preview failure never blocks approval; named, not silent
                    cache[scene["id"]] = None
                    st.caption(f"(preview unavailable: {type(e).__name__})")
            if cache.get(scene["id"]):
                st.image(cache[scene["id"]], use_container_width=True,
                         caption="Your box, true color — judge YOUR fire area, not the tile.")

    a1, a2 = st.columns(2)
    approve = a1.button("Approve & build dNBR → screen", type="primary")
    show_map = a2.button("Show me the burn map first")

    alts = package["alternatives"]
    with st.expander(f"See other candidates (pre: {sc['n_alternatives']['pre']}, "
                     f"post: {sc['n_alternatives']['post']})"):
        st.caption("Every option below already passed the deterministic clean-gate; swap "
                   "pre and post independently. The swapped pair is re-gated before use.")
        pre_opts = [package["pair"]["pre"]["id"]] + [c["id"] for c in alts["pre"]]
        post_opts = [package["pair"]["post"]["id"]] + [c["id"] for c in alts["post"]]
        pre_pick = st.selectbox("Pre-fire scene", pre_opts)
        post_pick = st.selectbox("Post-fire scene", post_opts)
        if st.button("Use this pair") and (
            pre_pick != package["pair"]["pre"]["id"]
            or post_pick != package["pair"]["post"]["id"]
        ):
            byid = {c["id"]: c for c in
                    [package["pair"]["pre"], package["pair"]["post"]] + alts["pre"] + alts["post"]}
            try:
                with st.spinner("Re-gating the swapped pair..."):
                    ev = scene_select.evaluate_pair(
                        byid[pre_pick], byid[post_pick], validate_bbox(*bbox_raw))
                package["pair"] = {"sensor": package["pair"]["sensor"],
                                   "pre": byid[pre_pick], "post": byid[post_pick],
                                   "metrics": ev["metrics"], "verdict": ev["verdict"]}
                gen_box.pop("burnmap", None)            # stale quicklook of the old pair
                st.rerun()
            except (GateAbort, ValueError) as e:
                st.error(str(e))

    if show_map:
        # On-demand quicklook for the recommended pair ONLY (spec 7: lean by default,
        # the powerful scar-vs-news-map eyeball one click away).
        import tempfile as _tf
        tmp = Path(_tf.mkdtemp(prefix="wws_burnmap_"))
        try:
            from autoacquire import dnbr_create
            with st.spinner("Computing the dNBR quicklook..."):
                created = dnbr_create.create_dnbr(
                    package["pair"], validate_bbox(*bbox_raw), tmp, name="preview")
                gen_box["burnmap"] = Path(created["quicklook_png"]).read_bytes()
        except (GateAbort, ValueError) as e:
            st.error(f"Could not build the burn map: {e}")
        except Exception as e:
            st.error(f"unexpected {type(e).__name__} building the burn map: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if gen_box.get("burnmap"):
        st.image(gen_box["burnmap"], width=480,
                 caption="dNBR quicklook (raw severity bins; provisional, within-fire, "
                         "UNVALIDATED for ranking — A34). Sanity-check: does the scar sit "
                         "where the news maps say the fire is?")

    if approve:
        with st.spinner("Building the dNBR, fetching DEM + buildings, scoring both arms..."):
            screen = run_generated_screening(bbox_raw, package["pair"], contour_m=contour_m)
            screen["inputs"] = inputs_key
            screen_box.clear(); screen_box.update(screen)   # same F1 store pattern as upload


def main():
    import streamlit as st
    from streamlit_folium import st_folium
    # (the network-layer + pipeline imports live inside run_screening, keeping `import app` light)

    st.set_page_config(page_title="Post-Fire Watershed Screening", layout="wide")
    st.title("Post-Fire Debris-Flow Watershed Screening")
    st.info(SCREENING_STATEMENT)                 # the spine (A11), always visible
    st.markdown("**1.** Draw a box snug around the burn + its downslope drainage · **2.** upload "
                "a raw dNBR GeoTIFF *or* generate one from the fire dates · **3.** run. "
                "Elevation and buildings are fetched automatically.")

    col_map, col_form = st.columns([2, 1])
    with col_map:
        draw = st_folium(_draw_map(), height=460, use_container_width=True, key="draw")
    drawn = bbox_from_draw(draw) or (-105.79156, 33.32552, -105.63614, 33.41352)  # South Fork default

    with col_form:
        st.subheader("Bounding box")
        west = st.number_input("West (lon)", value=float(drawn[0]), format=f"%.{_BBOX_DP}f")
        south = st.number_input("South (lat)", value=float(drawn[1]), format=f"%.{_BBOX_DP}f")
        east = st.number_input("East (lon)", value=float(drawn[2]), format=f"%.{_BBOX_DP}f")
        north = st.number_input("North (lat)", value=float(drawn[3]), format=f"%.{_BBOX_DP}f")
        # B2: per-fire mountain-front contour (m) -- the elevation where canyons discharge onto the
        # depositional plain. Guard-checked against THIS fire's DEM range (not a frozen scalar; an
        # operator input defaulted to the Montecito value). Inland high-elevation fires need ~1900.
        # Shared across BOTH modes (a terrain parameter, not upload-specific), so it sits above the
        # toggle and applies to the Upload run and the Generate approval alike.
        contour_m = st.number_input("Mountain-front contour (m)", value=150.0, step=10.0,
                                    help="Range-front break elevation for THIS fire "
                                         "(Montecito ~150; Cooks Peak ~1900; Deer Canyon ~1910).")
        # AA-4: the [Upload | Generate] toggle (spec section 10) -- one panel at a time;
        # Upload stays the DEFAULT during the build + demos (the proven path). Rendered as
        # a horizontal radio (same one-panel affordance, AppTest-drivable).
        mode_label = st.radio("Burn severity input",
                              ["Upload a dNBR", "Generate from dates"], horizontal=True)
        dnbr_file = None
        run = find = False
        ignition = containment = None
        greenup_days = 90
        if mode_label == "Upload a dNBR":
            dnbr_file = st.file_uploader("dNBR GeoTIFF (raw scale, ~ -1..1)", type=["tif", "tiff"])
            run = st.button("Run screening", type="primary")
        else:
            from datetime import date as _date, timedelta as _td
            ignition = st.date_input("Ignition date", value=_date.today() - _td(days=30),
                                     max_value=_date.today())
            containment = st.date_input("Containment date", value=_date.today() - _td(days=7),
                                        max_value=_date.today())
            with st.expander("Advanced: green-up ceiling"):
                greenup_days = st.number_input(
                    "Days after containment to keep looking for a clean post-fire scene",
                    value=90, min_value=1, max_value=180, step=10,
                    help="Frozen default 90 d protects fast-greening grassland; extend toward "
                         "180 d only for slow-recovery forest/conifer (the pre-registered "
                         "operator override).")
            find = st.button("Find scene pair", type="primary")

    # Generate-mode session container (same plain-mutation pattern as `screen`, F1).
    if "gen" not in st.session_state:
        st.session_state["gen"] = {}
    gen_box = st.session_state["gen"]

    # Identity of the CURRENT form inputs -- computed every rerun, used both to stamp a fresh result
    # and to detect a stale one (F8). In Generate mode the dates + the currently-selected pair are
    # part of the identity (a swapped scene or edited date flags the old result stale).
    if mode_label == "Upload a dNBR":
        inputs_key = screen_inputs_key(west, south, east, north, dnbr_file)
    else:
        pkg = (gen_box.get("outcome") or {}).get("package")
        pair_ids = ((pkg["pair"]["pre"]["id"], pkg["pair"]["post"]["id"])
                    if pkg and pkg.get("status") == "recommended" else (None, None))
        inputs_key = screen_inputs_key(
            west, south, east, north, None, mode="generate",
            gen=(ignition.isoformat(), containment.isoformat(), int(greenup_days), *pair_ids))

    if find:
        with st.spinner("Searching the Sentinel-2 / Landsat archives and gating clouds "
                        "over your box..."):
            outcome = generate_package((west, south, east, north), ignition, containment,
                                       int(greenup_days))
            gen_box.clear()
            gen_box.update({"outcome": outcome})

    # F1: hold the screen in a PERSISTENT container so storing a COMPLETED run is a plain dict mutation,
    # never a SafeSessionState.__setitem__ (whose _yield_callback fires BEFORE the store -- so a rerun
    # queued mid-fetch, e.g. an st_folium map click during the tens-of-seconds fetch, would raise
    # RerunException between the finished run and its store and silently DISCARD the result). Established
    # pre-run so the yield here is harmless (nothing computed yet to lose).
    if "screen" not in st.session_state:
        st.session_state["screen"] = {}
    box = st.session_state["screen"]

    # On click: compute the outcome (run_screening reduces EVERY failure to a legible dict -- F5) and
    # store it stamped with the inputs that produced it, via a plain mutation of `box`. Rendered from
    # the container below so it persists across the reruns st_folium/download trigger.
    if run:
        with st.spinner("Fetching DEM + buildings and scoring both dNBR arms..."):
            screen = run_screening((west, south, east, north), dnbr_file, contour_m=contour_m)
            screen["inputs"] = inputs_key
            box.clear(); box.update(screen)          # store INSIDE the spinner, BEFORE its exit yields --
            #   plain dict mutation with no yield point between the finished run and the store (F1)

    # AA-4: the Generate panel (scorecard / Mode B waiting / honest hard-fails) renders
    # full-width below the form; an approval inside it stores into `box` like an upload run.
    if mode_label == "Generate from dates" and gen_box:
        _render_generate_panel(gen_box, (west, south, east, north), inputs_key, box,
                               contour_m=contour_m)

    screen = box
    if not screen:                                    # empty container -> nothing screened yet
        return
    # F8: a stored result is only current for the inputs that produced it. After the user edits the box
    # or swaps the upload without re-running, keep the result visible but clearly labeled stale. An
    # ABSENT stamp (a pre-F8 result surviving a dev hot-reload) counts as stale too -- unknown provenance
    # renders flagged, not silently clean (fail-loud); a screening artifact must never pose as current.
    if screen.get("inputs") != inputs_key:
        st.warning("**Inputs changed since this result was produced** -- the box/upload above no "
                   "longer match what is shown below. Click **Run screening** to re-screen.")
    if screen["kind"] == "error":
        st.error(f"Could not screen this area: {screen['message']}")
        return
    if screen["kind"] == "refused":
        st.warning(f"**Screening refused.** {screen['message']}")
        return

    fc = screen["fc"]
    st.success(f"Ranked {screen['n']} basins — Arm A (binned) is the headline; "
               f"Arm B (continuous) rides alongside.")
    st_folium(build_basin_map(fc), height=520, use_container_width=True, key="result_map")
    st.caption("Fill = Arm A screening rank (hot = higher priority). **Blue dashed outline = Arm A "
               "and Arm B disagree on rank** — treat that basin as rank-uncertain.")

    with st.expander("How to read this"):
        st.markdown(
            f"- **What this is** — {SCREENING_STATEMENT}\n"
            "- **Map fill** — hot red = rank 1 (highest screening priority); pale = lowest rank.\n"
            "- **Blue dashed outline** — Arm A (binned) and Arm B (continuous) disagree on rank; "
            "treat that basin as rank-uncertain.\n"
            "- **Score** — the frozen `mean burn × mean slope × contributing area`, a within-fire "
            "ordinal ranking only (not comparable across fires).\n"
            "- **A refusal instead of a ranking** — on incised-valley terrain with no mountain-front "
            "break there are no canyon mouths to anchor to, so the tool refuses rather than force a "
            "ranking. A known boundary of the method, not a failure."
        )

    # B: surface the frozen score's inputs (burn x slope x area) beside the score so the ranking is
    # auditable; readable headers via column_config (the score carries the formula as a tooltip).
    st.dataframe(
        basin_rows(fc), use_container_width=True,
        column_config={
            "basin_id": "Basin", "rank": "Rank (Arm A)",
            "mean_burn": st.column_config.NumberColumn("Mean burn (Arm A)", format="%.4f"),
            "mean_slope": st.column_config.NumberColumn("Mean slope", format="%.4f"),
            "area_km2": st.column_config.NumberColumn("Area (km²)", format="%.4f"),
            "score": st.column_config.NumberColumn(
                "Score", help="= mean burn × mean slope × area (frozen; within-fire ordinal)"),
            "rank_b": "Rank (Arm B)", "score_b": "Score (Arm B)",
            "rank_delta": "Rank Δ", "uncertain": "Rank-uncertain",
        },
    )
    st.caption("Screening score = mean burn severity × mean slope × contributing area (km²) — the "
               "frozen formula, ranked within this fire only. Not a probability or a prediction.")
    st.download_button("Download ranking.csv", screen["csv"],
                       file_name="ranking.csv", mime="text/csv")

    # AA-4: when this ranking came from an auto-acquired dNBR, show what was built --
    # the quicklook + the creator's audit record (scenes, dates, scaling, masks).
    if screen.get("quicklook"):
        with st.expander("The dNBR this screening used (auto-acquired)"):
            st.image(screen["quicklook"], width=420,
                     caption="Raw-dNBR quicklook (provisional, within-fire, UNVALIDATED "
                             "for ranking — A34).")
            st.json(screen.get("dnbr_provenance", {}))


if __name__ == "__main__":
    main()
