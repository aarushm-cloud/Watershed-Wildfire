"""Landsat-vs-Sentinel basin-ranking divergence driver (F-2c, generalized).

For one fire: force each sensor arm through the PRODUCTION selector (the other
sensor's pool emptied at the search seam, so the frozen selection rules pick the
pair -- never a hand-picked scene), build each arm's dNBR, run the pipeline on
the SAME staged DEM/assets, and compare the two rankings.

Basins are joined BY GEOMETRY (WKB): `basin_id` in basins.geojson is a
post-filter re-index, so joining on it pairs unrelated basins (locked by
tests/stress/test_stress_divergence.py -- the trap fired on the South Fork run
and the area cross-check caught it).

Swap discipline mirrors the Trout e2e: if the frozen per-basin NoData guard
refuses an arm's chosen pair, up to MAX_POST_SWAPS pre-vetted alternative posts
are tried IN ORDER, every attempt recorded. Selection among vetted alternatives
is the designed operator workflow, not tuning; no threshold moves.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
from scipy.stats import spearmanr

from autoacquire import dnbr_create, scene_select
from src.grids import GateAbort
from validation.stress_fires import FIRES
from validation.stress_run import _jsonable

_OUT_ROOT = _REPO_ROOT / "out" / "stress_test"
MAX_POST_SWAPS = 2


# ---------------------------------------------------------------------------
# Sensor-restricted selection (production rules, one arm's pool emptied)
# ---------------------------------------------------------------------------

def select_single_sensor(fire_key, sensor, *, today=None):
    """Run the PRODUCTION selector with only `sensor`'s archive visible.

    Implemented by wrapping the _search_scenes seam (the exact seam the hermetic
    suite monkeypatches), so windows, coarse filter, zone eligibility, box gate,
    freshness priority and alternatives all run unmodified.

    today: the selector's own documented injectable-for-reproducibility clock.
    Passing a historical date reproduces the pool an operator had on that day --
    used when a later archive scene is unreadable at the provider (observed:
    MPC 403 on one 2022 asset persistently DoS-ing an arm via the A8 abort).
    No gate is relaxed."""
    fire = FIRES[fire_key]
    real = scene_select._search_scenes

    def _one_sensor(s, bbox, d0, d1):
        return real(s, bbox, d0, d1) if s == sensor else []

    scene_select._search_scenes = _one_sensor
    try:
        return scene_select.select(
            fire["bbox"], ignition=fire["ignition"], containment=fire["containment"],
            today=today)
    finally:
        scene_select._search_scenes = real


# ---------------------------------------------------------------------------
# Ranking comparison (pure; hermetically tested)
# ---------------------------------------------------------------------------

def compare_rankings(a, b, *, k=10):
    """Two basins GeoDataFrames (rank/score/area_km2 + geometry) -> agreement dict.

    Geometry-keyed join; raises on an area mismatch across a matched geometry
    (inconsistent inputs must never pass silently into a published number)."""
    a = a.copy()
    b = b.copy()
    a["gkey"] = a.geometry.apply(lambda g: g.wkb.hex())
    b["gkey"] = b.geometry.apply(lambda g: g.wkb.hex())
    j = a.merge(b.drop(columns="geometry"), on="gkey", suffixes=("_a", "_b"))

    if len(j) and not np.allclose(j["area_km2_a"], j["area_km2_b"]):
        raise ValueError(
            "area mismatch on geometry-matched basins -- the two runs' inputs are "
            "inconsistent (different DEM or corrupt join)."
        )

    out = {
        "n_a": int(len(a)), "n_b": int(len(b)), "n_matched": int(len(j)),
        "only_a": int(len(a) - len(j)), "only_b": int(len(b) - len(j)),
        "spearman_score": None, "spearman_intensity": None,
        "top10_overlap": None, "rank_move_median": None,
        "rank_move_p90": None, "rank_move_max": None,
    }
    if len(j) >= 2:
        out["spearman_score"] = float(spearmanr(j["score_a"], j["score_b"]).statistic)
        if "intensity_a" in j and "intensity_b" in j:
            out["spearman_intensity"] = float(
                spearmanr(j["intensity_a"], j["intensity_b"]).statistic)
        kk = min(k, len(j))
        top_a = set(j.nsmallest(kk, "rank_a")["gkey"])
        top_b = set(j.nsmallest(kk, "rank_b")["gkey"])
        out["top10_overlap"] = int(len(top_a & top_b))
        out["top10_k"] = kk
        moves = (j["rank_a"] - j["rank_b"]).abs()
        out["rank_move_median"] = float(moves.median())
        out["rank_move_p90"] = float(moves.quantile(0.9))
        out["rank_move_max"] = float(moves.max())
    return out


# ---------------------------------------------------------------------------
# One arm: pair -> dNBR -> pipeline -> basins.geojson (bounded post-swaps)
# ---------------------------------------------------------------------------

def _run_arm(fire_key, package, arm_dir, *, shared_fire=None, max_post_swaps=None):
    """Build + rank one sensor arm. Returns (record, basins_path|None, fire|None).

    shared_fire: an already-built fire dict whose staged DEM/assets are reused
    (same bbox -> same rasters), guaranteeing identical WBT geometry across arms."""
    from acquire import build_fire_config
    from src.outputs import write_dnbr_outputs
    from src.pipeline import run_pipeline

    fire_cfg = FIRES[fire_key]
    rec = {"sensor": package["pair"]["sensor"], "attempts": []}
    budget = MAX_POST_SWAPS if max_post_swaps is None else max_post_swaps
    posts = [package["pair"]["post"]] + list(package["alternatives"]["post"])[:budget]

    for post in posts:
        pair = {"sensor": package["pair"]["sensor"], "pre": package["pair"]["pre"],
                "post": post}
        attempt = {"pre": pair["pre"].get("id"), "post": post.get("id")}
        try:
            created = dnbr_create.create_dnbr(pair, fire_cfg["bbox"], arm_dir / "dnbr",
                                              name=fire_key)
            if shared_fire is None:
                fire = build_fire_config(fire_cfg["bbox"], created["dnbr_tif"], arm_dir,
                                         name=fire_key)
            else:
                fire = {**shared_fire, "dnbr": Path(created["dnbr_tif"]),
                        "out_dir": arm_dir}
                Path(arm_dir).mkdir(parents=True, exist_ok=True)
            # B2: per-fire operator contour where the registry documents one
            # (range-front fires; incised ignores it). Same value on BOTH arms,
            # so the cross-sensor comparison is internally consistent regardless.
            result = run_pipeline(fire, contour_m=fire_cfg.get("contour_m"))
            if result["status"] != "ranked":
                attempt["outcome"] = f"pipeline: {result['status']}"
                rec["attempts"].append(attempt)
                continue
            write_dnbr_outputs(
                result["arms"]["arm_a"], result["arms"]["arm_b"],
                result["creek_nearest"], fire["out_dir"], fire["dem"],
                validation_case=f"{fire_key} divergence ({rec['sensor']})",
                incised=(result.get("terrain_mode") == "incised"),
                subbasin_meta=result.get("subbasin_meta"),
            )
            attempt["outcome"] = "ranked"
            rec["attempts"].append(attempt)
            rec["terrain"] = result.get("terrain_mode")
            rec["n_basins"] = len(result["arms"]["arm_a"]["basins"])
            return rec, Path(fire["out_dir"]) / "basins.geojson", fire
        except GateAbort as e:
            attempt["outcome"] = f"abort: {str(e)[:140]}"
            rec["attempts"].append(attempt)
            continue
    return rec, None, None


def run_divergence(fire_key, *, today=None, max_post_swaps=None):
    """Full two-arm divergence for one fire. Serialized to out/stress_test/<fire>/."""
    import geopandas as gpd

    root = (_OUT_ROOT / fire_key / "divergence").resolve()
    out = {"fire": fire_key, "arms": {}, "comparison": None, "verdict": None,
           "instrument": {"today": str(today) if today else None,
                          "max_post_swaps": max_post_swaps}}

    packages = {}
    for sensor in ("Landsat", "S2"):
        pkg = select_single_sensor(fire_key, sensor, today=today)
        if pkg["status"] != "recommended":
            out["arms"][sensor] = {"selector": pkg["status"],
                                   "message": pkg.get("message", "")[:160]}
        else:
            packages[sensor] = pkg
            out["arms"][sensor] = {"selector": "recommended"}

    if len(packages) < 2:
        out["verdict"] = ("N/A -- divergence needs both sensors to recommend; "
                          f"only {sorted(packages)} did.")
        _save(root, out)
        return out

    paths = {}
    shared = None
    for sensor, pkg in packages.items():
        rec, gj, fire = _run_arm(fire_key, pkg, root / sensor.lower(),
                                 shared_fire=shared, max_post_swaps=max_post_swaps)
        out["arms"][sensor].update(rec)
        if gj is not None:
            paths[sensor] = gj
            if shared is None:
                shared = fire            # second arm reuses this DEM/assets staging

    if len(paths) < 2:
        out["verdict"] = ("N/A -- both sensors recommended but only "
                          f"{sorted(paths)} produced a ranking (see attempts).")
        _save(root, out)
        return out

    out["comparison"] = compare_rankings(
        gpd.read_file(paths["Landsat"]), gpd.read_file(paths["S2"]))
    c = out["comparison"]
    out["verdict"] = (
        f"measured: spearman(score)={c['spearman_score']:+.4f}, "
        f"top-{c.get('top10_k', 10)} overlap {c['top10_overlap']}/{c.get('top10_k', 10)}, "
        f"matched {c['n_matched']} (Landsat-only {c['only_a']}, S2-only {c['only_b']})"
    )
    _save(root, out)
    return out


def _save(root, payload):
    root.mkdir(parents=True, exist_ok=True)
    (root / "divergence.json").write_text(json.dumps(_jsonable(payload), indent=2))


if __name__ == "__main__":
    for key in sys.argv[1:]:
        r = run_divergence(key)
        print(f"{key}: {r['verdict']}")
