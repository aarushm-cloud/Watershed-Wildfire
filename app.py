"""app.py -- local single-user Streamlit UI over the pipeline (A36): draw a bbox, upload or
generate a dNBR -> ranked map + CSV, or a legible refusal. No science here.

Every failure renders as a legible message, never a stack trace; logic lives in pure helpers
(the UI is in main() behind __main__, so `import app` never executes it).
Run:  streamlit run app.py
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
from src.outputs import SCREENING_STATEMENT, DUAL_RANK_MAP_NAME

# Rank-uncertain display cutoff: max(DELTA, round(FRAC * n)) -- see _uncertain_threshold.
RANK_UNCERTAIN_DELTA = 3
RANK_UNCERTAIN_FRAC = 0.06

_BBOX_DP = 5   # decimal places for BOTH the bbox display AND the staleness key (must match)


# ---- pure helpers (no Streamlit) ----

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
        view = {"kind": "ranked", "n_basins": len(arm_a["basins"]),
                "headline_arm": result.get("headline_arm", "arm_a")}
        view["incised"] = (result.get("terrain_mode") == "incised")
        return view
    return {"kind": "unknown", "message": f"Unexpected pipeline result status: {status!r}."}


def _uncertain_threshold(n_basins: int, *, floor: int = RANK_UNCERTAIN_DELTA,
                         frac: float = RANK_UNCERTAIN_FRAC) -> int:
    """|rankA-rankB| at/above which a basin is flagged rank-uncertain: max(floor, round(frac*n)).
    Floor keeps small fires at the validated cutoff; the fraction stops saturation on large fires."""
    return max(floor, round(frac * n_basins))


def basin_rows(fc: dict, *, uncertain_delta: int = RANK_UNCERTAIN_DELTA) -> list:
    """basins.geojson -> display rows in Arm A rank order, with the rank_delta 'uncertain' flag."""
    features = fc.get("features", [])
    threshold = _uncertain_threshold(len(features), floor=uncertain_delta)
    rows = []
    for feat in features:
        p = feat.get("properties", {})
        delta = p.get("rank_delta", abs((p.get("rank") or 0) - (p.get("rank_b") or 0)))
        row = {"basin_id": p.get("basin_id"), "rank": p.get("rank"),
               "mean_burn": p.get("mean_burn_a", p.get("mean_burn")),   # Arm A binned burn (headline)
               "mean_slope": p.get("mean_slope"), "area_km2": p.get("area_km2"),
               "score": p.get("score"),
               "rank_b": p.get("rank_b"), "score_b": p.get("score_b"),
               "rank_delta": delta, "uncertain": delta >= threshold}
        if "intensity" in p:   # A39: only present on incised (WhiteboxTools sub-basin) features
            row["intensity"] = p["intensity"]
            row["intensity_rank"] = p["intensity_rank"]
        rows.append(row)
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
    """Identity of the inputs a screening result was produced from (staleness check): a stored
    result is only current for the exact bbox + upload (or generate dates + approved pair) that
    produced it."""
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
    """One screening run end-to-end -> {"kind": ranked | refused | error}. EVERY failure
    reduces to a legible error dict, never a raise (the failure type is NAMED, nothing is
    swallowed). Pure orchestration, no st.* calls."""
    out_dir = None
    try:
        # deferred imports inside the try: an import-time failure also reduces to a legible error
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
                validation_case=f"{fire['name']} (coordinate entry, dNBR both-arms)",
                incised=(result.get("terrain_mode") == "incised"),
                subbasin_meta=result.get("subbasin_meta"),
                refused=result.get("refused_basins", []))
            try:
                fc = json.loads(Path(gj_path).read_text())
            except json.JSONDecodeError as e:   # our own truncated geojson = internal fault -> backstop
                raise RuntimeError(f"wrote an unreadable basins.geojson at {gj_path}: {e}") from e
            screen = {"kind": "ranked", "n": view["n_basins"], "fc": fc,
                      "csv": Path(csv_path).read_bytes(), "incised": view["incised"]}
            rgj_path = Path(fire["out_dir"]) / "refused_basins.geojson"   # A41: only on a degraded run
            if rgj_path.exists():
                screen["refused_geojson"] = json.loads(rgj_path.read_text())
            rcsv_path = Path(fire["out_dir"]) / "refused_basins.csv"
            if rcsv_path.exists():   # F3: bytes read BEFORE the finally rmtree, never a dangling reference
                screen["refused_csv"] = rcsv_path.read_bytes()
            map_png = Path(fire["out_dir"]) / DUAL_RANK_MAP_NAME   # A39: incised runs only
            if view["incised"] and map_png.exists():
                screen["map_png"] = map_png.read_bytes()
            return screen
        if view["kind"] == "refused":
            return {"kind": "refused", "message": view["message"]}
        return {"kind": "error", "message": view.get("message", "Unexpected pipeline result.")}
    except (GateAbort, ValueError) as e:
        return {"kind": "error", "message": str(e)}     # domain message, verbatim
    except Exception as e:                              # backstop: never a traceback to the user
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {"kind": "error", "message": f"unexpected {type(e).__name__} during screening: {e}"}
    finally:
        if out_dir is not None:
            shutil.rmtree(out_dir, ignore_errors=True)


# ---- Generate-from-dates helpers (pure, no st.*) ----

_VERDICT_ICONS = {"good": "✅", "ok": "\U0001f7e1", "marginal": "\U0001f7e0",
                  "below_bar": "\U0001f534"}


def generate_package(bbox_raw, ignition, containment, greenup_days=90):
    """Run the deterministic selector -> {"kind": "package"} | {"kind": "error"}. Honest
    non-pair states (waiting / window_closed / no_pre_scene) pass through inside the package."""
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
    """Recommendation package -> the approval-scorecard view model. Cloud-over-YOUR-fire is
    the headline; tile-cloud de-emphasized; timing flag derived from frozen values only."""
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


def run_generated_screening(bbox_raw, sweep_inputs, *, name="frontend", contour_m=150.0):
    """Approve-gated sweep (A41): one approval covers the vetted family (recommended pair,
    vetted alt-posts, other sensor) -- never just the single displayed pair. Failure contract
    mirrors run_screening: EVERY failure reduces to a legible dict, never a raise. Extra
    success keys beyond the ranked shape: quicklook + dnbr_provenance (the WINNER's, re-read
    off disk) + sweep_status/attempts/chosen (the trail) + refused_geojson (degraded only).

    NO st.* calls in here -- preemption safety: the panel calls this inside a spinner behind
    an idempotence guard, and a queued Streamlit rerun must never re-enter or duplicate this
    pure function (see app.py's SafeSessionState note near the persistent `screen` store)."""
    try:
        bbox = validate_bbox(*bbox_raw)
    except GateAbort as e:
        # An input error, never a science "refusal" -- the refused vocabulary is Tier-1
        # language for the science declining, and a typo'd box must not dilute it.
        return {"kind": "error", "message": str(e)}
    out_dir = None
    try:
        # deferred import inside the try (matches every sibling orchestrator in this file): an
        # import-time failure also reduces to a legible error, and it makes autoacquire.sweep.
        # run_sweep the correct, ALWAYS-live monkeypatch seam for tests (a module-level import
        # would bind a private copy into whichever module namespace is executing -- AppTest
        # execs app.py into a fresh module object per run, so a top-level import here would be
        # unpatchable from an AppTest-driven test).
        from autoacquire.sweep import run_sweep
        out_dir = Path(tempfile.mkdtemp(prefix="wws_sweep_"))
        sw = run_sweep(bbox, ignition=sweep_inputs["ignition"],
                       containment=sweep_inputs["containment"], out_dir=out_dir,
                       name=name, contour_m=contour_m,
                       greenup_days=sweep_inputs.get("greenup_days"), approve=True)
        if sw["status"] not in ("clean", "degraded"):
            # selector state changed since the scorecard was shown, or every attempt failed to
            # rank (aborted) -- an honest refusal, never a ranking built from stale intent.
            return {"kind": "refused", "message": sw.get("message", sw["status"]),
                    "attempts": sw.get("attempts", [])}
        # read EVERYTHING before rmtree (bytes/parsed content, not paths -- the dir dies in finally)
        fire_dir = Path(sw["result_paths"]["out_dir"])
        try:
            fc = json.loads((fire_dir / "basins.geojson").read_text())
        except json.JSONDecodeError as e:   # our own truncated geojson = internal fault -> backstop
            raise RuntimeError(f"wrote an unreadable basins.geojson at {fire_dir}: {e}") from e
        screen = {"kind": "ranked", "n": len(fc.get("features", [])), "fc": fc,
                  "csv": (fire_dir / "ranking.csv").read_bytes(),
                  "incised": bool(fc.get("provenance", {}).get("incised_framing")),
                  "sweep_status": sw["status"], "attempts": sw["attempts"],
                  "chosen": sw["chosen"]}
        rgj_path = fire_dir / "refused_basins.geojson"   # only on a degraded run
        if rgj_path.exists():
            screen["refused_geojson"] = json.loads(rgj_path.read_text())
        rcsv_path = fire_dir / "refused_basins.csv"
        if rcsv_path.exists():   # F3: bytes read BEFORE the finally rmtree, never a dangling reference
            screen["refused_csv"] = rcsv_path.read_bytes()
        ql = sorted((fire_dir / "dnbr").glob("dnbr_*_quicklook.png"))
        if ql:
            screen["quicklook"] = ql[0].read_bytes()          # the WINNER's -- re-READ, never re-render
        prov = sorted((fire_dir / "dnbr").glob("dnbr_*_provenance.json"))
        if prov:
            screen["dnbr_provenance"] = json.loads(prov[0].read_text())
        map_png = fire_dir / DUAL_RANK_MAP_NAME   # A39: incised runs only
        if map_png.exists():
            screen["map_png"] = map_png.read_bytes()
        return screen
    except GateAbort as e:
        return {"kind": "refused", "message": str(e)}
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {"kind": "error", "message": f"unexpected {type(e).__name__} during screening: {e}"}
    finally:
        if out_dir is not None:
            shutil.rmtree(out_dir, ignore_errors=True)


def _basin_centroid(feat: dict):
    """Representative interior point [lat, lon] for a basin polygon (stays inside concave shapes)."""
    from shapely.geometry import shape
    pt = shape(feat["geometry"]).representative_point()
    return [pt.y, pt.x]


def _top_k_markers(fc: dict, k: int) -> list:
    """[(rank, [lat, lon]), ...] for the k highest-priority basins, in rank order."""
    marked = []
    for feat in fc.get("features", []):
        rank = feat.get("properties", {}).get("rank")
        if rank is not None and rank <= k:
            marked.append((rank, _basin_centroid(feat)))
    return sorted(marked, key=lambda rc: rc[0])


def build_basin_map(fc: dict, *, uncertain_delta: int = RANK_UNCERTAIN_DELTA,
                    top_k: int = 10, focus_basin_id=None, refused_fc: dict | None = None) -> folium.Map:
    """Folium map of the ranked basins: fill by Arm A rank, dashed outline = rank-uncertain,
    numbered markers on the top_k, focus_basin_id zooms to one basin. refused_fc (A41): an
    optional refused_basins.geojson FeatureCollection, hatched in AFTER the ranked layer --
    their hazard is UNKNOWN, never rendered as if it were low."""
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

    if refused_fc and refused_fc.get("features"):
        folium.GeoJson(
            refused_fc,
            style_function=lambda f: {"fillColor": "#888888", "color": "#555555",
                                      "dashArray": "4", "fillOpacity": 0.35},
            tooltip="REFUSED -- insufficient cloud-free data (hazard UNKNOWN, not low)",
        ).add_to(m)

    for rank, latlon in _top_k_markers(fc, top_k):
        folium.Marker(
            latlon,
            icon=folium.DivIcon(
                icon_size=(20, 20), icon_anchor=(10, 10),
                html=(f'<div style="font:bold 12px sans-serif;color:#fff;background:#d7302b;'
                      f'border-radius:50%;width:20px;height:20px;line-height:20px;text-align:center;'
                      f'border:1px solid #fff">{rank}</div>')),
        ).add_to(m)

    focus_bounds = None
    if focus_basin_id is not None:
        for feat in fc.get("features", []):
            if feat.get("properties", {}).get("basin_id") == focus_basin_id:
                pts = list(_iter_coords(feat.get("geometry", {}).get("coordinates", [])))
                if pts:
                    xs = [x for x, _y in pts]
                    ys = [y for _x, y in pts]
                    focus_bounds = [[min(ys), min(xs)], [max(ys), max(xs)]]
                break
    try:
        bounds = focus_bounds if focus_bounds is not None else gj.get_bounds()
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


def _render_attempts_expander(screen):
    """A41 fix-wave IMPORTANT 3: the sweep's audit trail, shared by the ranked AND refused/aborted
    branches -- an aborted sweep has `attempts` but no `chosen`, so `chosen` is optional here."""
    import streamlit as st

    attempts = screen.get("attempts")
    if not attempts:
        return
    chosen = screen.get("chosen")
    label = f"Sweep: {len(attempts)} attempt(s)"
    if chosen:
        label += f", chose {chosen['sensor']} {chosen['post_id']}"
    with st.expander(label):
        st.dataframe(attempts, use_container_width=True)


def _render_generate_panel(gen_box, bbox_raw, inputs_key, screen_box, *, ignition, containment,
                           greenup_days, contour_m=150.0, retry=False):
    """The Generate-mode approval surface. Machine proposes, human disposes -- nothing is
    built without the Approve click. ignition/containment/greenup_days are the CURRENT form
    values (the SAME inputs the selector ran with) -- Approve triggers a bounded sweep over the
    vetted family, not just the single displayed pair. greenup_days must thread through: the
    sweep re-runs the selector internally, and a mismatch (operator-extended window silently
    dropped to the 90d default) can re-select over a DIFFERENT post-window than the one the
    human just approved."""
    import streamlit as st
    from autoacquire import scene_select

    outcome = gen_box.get("outcome") or {}
    if outcome.get("kind") == "error":
        st.error(f"Could not search scenes: {outcome['message']}")
        return
    package = (outcome.get("package") or {})
    status = package.get("status")
    if status == "waiting":
        # Honest waiting state + user-driven re-check. NEVER a burn-less ranking.
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
    # `retry` re-enters the already-approved sweep after a transient error (the error branch's
    # Retry button) -- the same vetted family, not a new approval decision.
    approve = a1.button("Approve & build dNBR → screen", type="primary") or retry
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
                if not ev["passes_gate"]:
                    # R1 (spec 7): the INDEPENDENT double-swap can build a pair below the box-gate
                    # floor -- a pre+post combination the selector never vetted together (each alt
                    # was gated only against the recommended partner). passes_gate is the same
                    # BOX_GATE_FLOOR select() accepts on; honor it here, never offer a sub-floor
                    # pair for Approve & build. The recommended pair stays in place.
                    st.error(
                        f"That pre+post combination covers only "
                        f"{ev['metrics']['pair_valid_frac'] * 100:.0f}% of your fire area -- below "
                        f"the {scene_select.BOX_GATE_FLOOR * 100:.0f}% clean-gate floor. Keeping "
                        "the recommended pair; pick another combination or wait for a clearer scene."
                    )
                else:
                    package["pair"] = {"sensor": package["pair"]["sensor"],
                                       "pre": byid[pre_pick], "post": byid[post_pick],
                                       "metrics": ev["metrics"], "verdict": ev["verdict"]}
                    gen_box.pop("burnmap", None)            # stale quicklook of the old pair
                    st.rerun()
            except (GateAbort, ValueError) as e:
                st.error(str(e))

    if show_map:
        # On-demand quicklook for the recommended pair only.
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
        # Idempotence guard: a queued second Approve click (a Streamlit double-submit race)
        # for the SAME inputs must never re-run the sweep -- it is expensive (up to
        # (1+6)*2 = 14 attempts, both sensors) and the first run's result is still current.
        if screen_box.get("inputs") == inputs_key and screen_box.get("kind") in (
                "ranked", "refused", "error"):
            st.info("Already ran for these inputs -- change inputs to re-run.")
        else:
            with st.spinner("Sweeping the vetted scene family (recommended pair, vetted "
                            "alt-posts, other sensor): building the dNBR, fetching DEM + "
                            "buildings, scoring both arms..."):
                screen = run_generated_screening(
                    bbox_raw, {"ignition": ignition, "containment": containment,
                              "greenup_days": greenup_days},
                    contour_m=contour_m)
                screen["inputs"] = inputs_key
                screen_box.clear(); screen_box.update(screen)   # same store pattern as upload
            # The pre-approval preview may belong to a LOSING pair; the winner's own quicklook
            # (screen["quicklook"], re-read off disk) renders instead -- never stack the two.
            gen_box.pop("burnmap", None)


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
        # Per-fire mountain-front contour (m), operator input (B2); shared across both modes.
        contour_m = st.number_input("Mountain-front contour (m)", value=150.0, step=10.0,
                                    help="Range-front break elevation for THIS fire "
                                         "(Montecito ~150; Cooks Peak ~1900; Deer Canyon ~1910). "
                                         "Used by the validated canyon-mouth tier only -- ignored "
                                         "when terrain routes to the incised sub-basin tier "
                                         "(the default South Fork box is incised).")
        # The [Upload | Generate] toggle; Upload is the default (the proven path).
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

    if "gen" not in st.session_state:
        st.session_state["gen"] = {}
    gen_box = st.session_state["gen"]

    # Identity of the CURRENT form inputs: stamps a fresh result / flags a stale one.
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

    # Persistent container: storing a completed run must be a plain dict mutation -- a
    # SafeSessionState store can yield to a queued rerun mid-fetch and silently DISCARD the result.
    if "screen" not in st.session_state:
        st.session_state["screen"] = {}
    box = st.session_state["screen"]

    # Set by the error branch's Retry button on the PREVIOUS run: re-run the same inputs
    # through whichever mode is active (popped unconditionally so it can never go stale).
    retry = st.session_state.pop("_retry", False)

    if run or (retry and mode_label == "Upload a dNBR"):
        with st.spinner("Fetching DEM + buildings and scoring both dNBR arms..."):
            screen = run_screening((west, south, east, north), dnbr_file, contour_m=contour_m)
            screen["inputs"] = inputs_key
            box.clear(); box.update(screen)   # plain mutation, no yield between run and store

    # The Generate panel renders below the form; an approval stores into `box` like an upload run.
    if mode_label == "Generate from dates" and gen_box:
        _render_generate_panel(gen_box, (west, south, east, north), inputs_key, box,
                               ignition=ignition, containment=containment,
                               greenup_days=int(greenup_days), contour_m=contour_m,
                               retry=retry)

    screen = box
    if not screen:
        return
    # A stored result is only current for the inputs that produced it; an absent stamp counts
    # as stale too -- a screening artifact must never pose as current.
    if screen.get("inputs") != inputs_key:
        st.warning("**Inputs changed since this result was produced** -- the box/upload above no "
                   "longer match what is shown below. Click **Run screening** to re-screen.")
    if screen["kind"] == "error":
        st.error(f"Could not screen this area: {screen['message']}")
        # The idempotence guard treats a stored error as complete for these inputs, so a
        # transient failure would otherwise require perturbing an input to re-run.
        if st.button("Retry"):
            box.clear()
            st.session_state["_retry"] = True
            st.rerun()
        return
    if screen["kind"] == "refused":
        st.warning(f"**Screening refused.** {screen['message']}")
        _render_attempts_expander(screen)   # A41: an aborted sweep's trail, not just "see attempts."
        return

    fc = screen["fc"]
    provenance = fc.get("provenance", {})
    if screen.get("incised"):
        st.warning(
            "**Exploratory result — incised terrain.** This fire lacks the "
            "range-front-over-plain shape the validated method assumes, so basins are "
            "whole-network sub-basins split at confluences rather than canyon-mouth "
            "catchments; individual boundaries may be approximate. Read it as relative "
            "**source susceptibility** for triage — it does not indicate runout, deposition, "
            "or which fan is threatened. The method has **not been validated on this terrain "
            "class**. Rows are ranked by the frozen **score** (as on range-front fires); an "
            "**intensity** companion (burn × slope, area-independent) rides alongside because the "
            "score's area term is a segmentation artifact here -- and intensity scored higher on the "
            "one validation case, so treat both as exploratory."
        )
    if provenance.get("refused_count"):   # A41: path-agnostic -- upload and generate both land here
        st.warning(f"{provenance['refused_count']} of {provenance['n_basins_total']} basins "
                   "could not be assessed (insufficient cloud-free imagery). Their hazard is "
                   "UNKNOWN -- not low. Any refused basin could rank high if data existed; "
                   "see refused_basins.csv.")
    st.success(f"Ranked {screen['n']} basins — Arm A (binned) is the headline; "
               f"Arm B (continuous) rides alongside.")
    _render_attempts_expander(screen)   # A41: the sweep's audit trail
    # Basin lookup: numbered top-K markers + zoom-to-rank for large fires.
    n_basins = screen["n"]
    focus_id = None
    if n_basins > 1:
        rank_to_id = {r["rank"]: r["basin_id"] for r in basin_rows(fc) if r["rank"] is not None}
        jump_rank = st.number_input("Jump to rank", min_value=0, max_value=n_basins, value=0, step=1,
                                    key="jump_rank",
                                    help="0 = show the whole fire; 1..N zooms the map to that Arm A rank.")
        if jump_rank >= 1:
            focus_id = rank_to_id.get(int(jump_rank))
    st_folium(build_basin_map(fc, focus_basin_id=focus_id, refused_fc=screen.get("refused_geojson")),
              height=520, use_container_width=True, key="result_map")
    st.caption("Fill = Arm A screening rank (hot = higher priority). **Blue dashed outline = Arm A "
               "and Arm B disagree on rank** — treat that basin as rank-uncertain."
               + (" **Gray hatching = refused** — insufficient cloud-free data; hazard UNKNOWN, "
                  "not low." if screen.get("refused_geojson") else ""))

    with st.expander("How to read this"):
        st.markdown(
            f"- **What this is** — {SCREENING_STATEMENT}\n"
            "- **Map fill** — hot red = rank 1 (highest screening priority); pale = lowest rank.\n"
            "- **Blue dashed outline** — Arm A (binned) and Arm B (continuous) disagree on rank; "
            "treat that basin as rank-uncertain.\n"
            "- **Score** — the frozen `mean burn × mean slope × contributing area`, a within-fire "
            "ordinal ranking only (not comparable across fires).\n"
            "- **Two terrain tiers** — range-front fires (canyon mouths draining onto a plain) get "
            "the validated `score` ranking; incised-valley fires (no range-front break to anchor "
            "to) get an exploratory sub-basin ranking by that same frozen `score`, with an "
            "**intensity** (burn × slope) companion column — area is a segmentation artifact there, so "
            "intensity is a secondary lens (it scored higher on the one validation case), not the headline."
        )

    # Surface the score's inputs beside it so the ranking is auditable.
    rows = basin_rows(fc)
    column_config = {
        "basin_id": "Basin", "rank": "Rank (Arm A)",
        "mean_burn": st.column_config.NumberColumn("Mean burn (Arm A)", format="%.4f"),
        "mean_slope": st.column_config.NumberColumn("Mean slope", format="%.4f"),
        "area_km2": st.column_config.NumberColumn("Area (km²)", format="%.4f"),
        "score": st.column_config.NumberColumn(
            "Score", help="= mean burn × mean slope × area (frozen; within-fire ordinal)"),
        "rank_b": "Rank (Arm B)", "score_b": "Score (Arm B)",
        "rank_delta": "Rank Δ", "uncertain": "Rank-uncertain",
    }
    if screen.get("incised"):   # incised adds the intensity companion columns (A40)
        column_config["intensity"] = st.column_config.NumberColumn(
            "Intensity", format="%.4f",
            help="= mean burn × mean slope, area-independent — an exploratory companion lens on incised "
                 "terrain (scored higher than score on the one validation case); the headline ranking is score")
        column_config["intensity_rank"] = "Intensity rank"
    st.dataframe(rows, use_container_width=True, column_config=column_config)
    st.caption("Screening score = mean burn severity × mean slope × contributing area (km²) — the "
               "frozen formula, ranked within this fire only. Not a probability or a prediction.")
    st.download_button("Download ranking.csv", screen["csv"],
                       file_name="ranking.csv", mime="text/csv")
    if screen.get("refused_csv"):   # F3: the degraded banner names this file -- it must exist to download
        st.download_button("Download refused_basins.csv", screen["refused_csv"],
                           file_name="refused_basins.csv", mime="text/csv")
    if screen.get("map_png"):   # A39: the static dual-rank map travels on incised runs only
        st.download_button("Download dual-rank map (PNG)", screen["map_png"],
                           file_name=DUAL_RANK_MAP_NAME, mime="image/png")

    # When this ranking came from an auto-acquired dNBR, show what was built --
    # the quicklook + the creator's audit record (scenes, dates, scaling, masks).
    if screen.get("quicklook"):
        with st.expander("The dNBR this screening used (auto-acquired)"):
            caption = ("Raw-dNBR quicklook (provisional, within-fire, UNVALIDATED "
                       "for ranking — A34).")
            chosen = screen.get("chosen")   # A41: the sweep's winner, re-read off disk
            if chosen:
                pre_when = f" ({chosen['pre_date']})" if chosen.get("pre_date") else ""
                caption += (f" Winning pair: {chosen['sensor']} pre {chosen['pre_id']}"
                           f"{pre_when} -> post {chosen['post_id']} ({chosen['post_date']}).")
            st.image(screen["quicklook"], width=420, caption=caption)
            st.json(screen.get("dnbr_provenance", {}))


if __name__ == "__main__":
    main()
