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
    """basins.geojson -> display rows sorted by the Arm A headline rank, with the rank_delta
    'uncertain' flag (Arm A vs Arm B disagreement, A34)."""
    rows = []
    for feat in fc.get("features", []):
        p = feat.get("properties", {})
        delta = p.get("rank_delta", abs((p.get("rank") or 0) - (p.get("rank_b") or 0)))
        rows.append({"basin_id": p.get("basin_id"), "rank": p.get("rank"), "score": p.get("score"),
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


def screen_inputs_key(west, south, east, north, dnbr_file):
    """Identity of the inputs a screening result was produced from (F8 staleness check).

    A stored result is only "current" for the exact bbox + upload that produced it; after the user
    edits the box or swaps the file, the old map/CSV must be flagged, never silently posed as the
    new inputs' screening. The upload identity prefers Streamlit's per-upload file_id (a re-upload
    of a same-named file gets a fresh id), falling back to (name, size). No upload -> None."""
    f = None
    if dnbr_file is not None:
        fid = getattr(dnbr_file, "file_id", None)         # Streamlit sets a fresh uuid per upload
        # explicit is-not-None (not `or`): an empty-but-present id must not silently fall back. The
        # (name, size) fallback is defensive for non-Streamlit file-likes only; it can collide for a
        # same-name/same-size re-export, so it never activates on a real Streamlit UploadedFile.
        f = fid if fid is not None else (getattr(dnbr_file, "name", None), getattr(dnbr_file, "size", None))
    return (round(float(west), _BBOX_DP), round(float(south), _BBOX_DP),
            round(float(east), _BBOX_DP), round(float(north), _BBOX_DP), f)


def run_screening(bbox_raw, dnbr_file, *, name="frontend"):
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
        result = run_pipeline(fire)
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


def main():
    import streamlit as st
    from streamlit_folium import st_folium
    # (the network-layer + pipeline imports live inside run_screening, keeping `import app` light)

    st.set_page_config(page_title="Post-Fire Watershed Screening", layout="wide")
    st.title("Post-Fire Debris-Flow Watershed Screening")
    st.info(SCREENING_STATEMENT)                 # the spine (A11), always visible
    st.markdown("**1.** Draw a box on the map (or type coordinates) · **2.** upload a raw dNBR "
                "GeoTIFF · **3.** run. Elevation and buildings are fetched automatically.")

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
        dnbr_file = st.file_uploader("dNBR GeoTIFF (raw scale, ~ -1..1)", type=["tif", "tiff"])
        run = st.button("Run screening", type="primary")

    # Identity of the CURRENT form inputs -- computed every rerun, used both to stamp a fresh result
    # and to detect a stale one (F8).
    inputs_key = screen_inputs_key(west, south, east, north, dnbr_file)

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
            screen = run_screening((west, south, east, north), dnbr_file)
            screen["inputs"] = inputs_key
            box.clear(); box.update(screen)          # store INSIDE the spinner, BEFORE its exit yields --
            #   plain dict mutation with no yield point between the finished run and the store (F1)

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
    st.dataframe(basin_rows(fc), use_container_width=True)
    st.download_button("Download ranking.csv", screen["csv"],
                       file_name="ranking.csv", mime="text/csv")


if __name__ == "__main__":
    main()
