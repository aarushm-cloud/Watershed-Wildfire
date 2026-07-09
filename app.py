"""app.py -- local Streamlit frontend (A36), the thin UI over run_pipeline for non-developers.

Draw/enter a bounding box, upload a raw dNBR GeoTIFF, click run -> acquire.build_fire_config
(A35) auto-fetches DEM + buildings and assembles the A30 fire dict -> run_pipeline scores the
dNBR both-arms path (A34) -> a ranked map + CSV, or a legible refusal. A **local, single-user
tool that wraps the CLI** (A36 reconciles A7's "no live service"); not hosted, not multi-user.

Guardrail tier: Tier-2 (UI plumbing) -- no science here. The frozen formula, dNBR knobs, and
`src/` are untouched; this only orchestrates acquire + run_pipeline + the existing output writers.

Every artifact keeps the screening spine (A11: within-fire relative ranking, never a prediction)
and the dNBR n=1 framing (A34: triage-validated, not exact-rank-validated). Fail-loud aborts
(GateAbort/ValueError) and the A27 terrain refusal render as messages, never a stack trace.

Testability: all logic lives in pure, importable helpers; the Streamlit UI is in `main()` behind
an `if __name__ == "__main__"` guard, so `import app` (tests) never executes the UI. See
tests/test_app.py. Run the app with:  streamlit run app.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import folium

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from src.grids import GateAbort
from src.outputs import SCREENING_STATEMENT, DNBR_FRAMING

# |rankA - rankB| at/above which a basin is flagged "rank uncertain" (display heuristic, Tier-2, not
# a science value): the honest surfacing of Arm A / Arm B disagreement (A34 rank_delta).
RANK_UNCERTAIN_DELTA = 3


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
        arm_a = result["arms"]["arm_a"]
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

    # deferred (network-layer + pipeline) imports so `import app` stays light for unit tests
    from acquire import build_fire_config
    from src.pipeline import run_pipeline
    from src.outputs import write_dnbr_outputs

    st.set_page_config(page_title="Post-Fire Watershed Screening", layout="wide")
    st.title("Post-Fire Debris-Flow Watershed Screening")
    st.info(SCREENING_STATEMENT)                 # the spine (A11), always visible
    st.caption(DNBR_FRAMING)                      # dNBR n=1 framing (A34)
    st.markdown("**1.** Draw a box on the map (or type coordinates) · **2.** upload a raw dNBR "
                "GeoTIFF · **3.** run. Elevation and buildings are fetched automatically.")

    col_map, col_form = st.columns([2, 1])
    with col_map:
        draw = st_folium(_draw_map(), height=460, use_container_width=True, key="draw")
    drawn = bbox_from_draw(draw) or (-105.79156, 33.32552, -105.63614, 33.41352)  # South Fork default

    with col_form:
        st.subheader("Bounding box")
        west = st.number_input("West (lon)", value=float(drawn[0]), format="%.5f")
        south = st.number_input("South (lat)", value=float(drawn[1]), format="%.5f")
        east = st.number_input("East (lon)", value=float(drawn[2]), format="%.5f")
        north = st.number_input("North (lat)", value=float(drawn[3]), format="%.5f")
        dnbr_file = st.file_uploader("dNBR GeoTIFF (raw scale, ~ -1..1)", type=["tif", "tiff"])
        run = st.button("Run screening", type="primary")

    # On click: compute the outcome and STORE it in session_state. It must NOT be rendered only inside
    # this `if run:` block -- st_folium and the download button each trigger a rerun on which `run` is
    # False, so results rendered here would flash and vanish. Render from session_state below instead.
    if run:
        try:
            bbox = validate_bbox(west, south, east, north)
            if dnbr_file is None:
                st.session_state["screen"] = {"kind": "error",
                    "message": "Upload a raw-scale dNBR GeoTIFF before running."}
            else:
                with st.spinner("Fetching DEM + buildings and scoring both dNBR arms..."):
                    out_dir = Path(tempfile.mkdtemp(prefix="wws_frontend_"))
                    dnbr_path = out_dir / "dnbr_upload.tif"
                    dnbr_path.write_bytes(dnbr_file.getvalue())
                    fire = build_fire_config(bbox, dnbr_path, out_dir, name="frontend")
                    result = run_pipeline(fire)
                view = result_to_view(result)
                if view["kind"] == "ranked":
                    csv_path, gj_path = write_dnbr_outputs(
                        result["arms"]["arm_a"], result["arms"]["arm_b"], result["creek_nearest"],
                        fire["out_dir"], fire["dem"],
                        validation_case=f"{fire['name']} (coordinate entry, dNBR both-arms)")
                    st.session_state["screen"] = {"kind": "ranked", "n": view["n_basins"],
                        "fc": json.loads(Path(gj_path).read_text()),
                        "csv": Path(csv_path).read_bytes()}
                elif view["kind"] == "refused":
                    st.session_state["screen"] = {"kind": "refused", "message": view["message"]}
                else:
                    st.session_state["screen"] = {"kind": "error",
                        "message": view.get("message", "Unexpected pipeline result.")}
        except (GateAbort, ValueError) as e:
            st.session_state["screen"] = {"kind": "error", "message": str(e)}   # legible, no stack trace

    # Render the stored outcome EVERY run, so it persists across the reruns st_folium/download cause.
    screen = st.session_state.get("screen")
    if not screen:
        return
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
