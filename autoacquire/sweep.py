"""sweep.py -- bounded scene sweep (A41): recommended pair -> vetted alt_posts -> other
sensor, under ONE approval. First zero-refused attempt wins; else best by the frozen
score-blind key (fewest refused -> lowest total nodata -> earliest post). Winner's
artifacts are PROMOTED to out_dir; losers stay quarantined under attempts/."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

MAX_POST_SWAPS = 6   # A41 owned value


def _attempt_record(sensor, pre, post, outcome, refused=None, total=None, total_nodata=None):
    d = post.get("date")
    return {"sensor": sensor, "pre_id": pre.get("id"), "post_id": post.get("id"),
            "post_date": None if d is None else str(d), "outcome": outcome,
            "refused_count": None if refused is None else len(refused),
            "n_basins_total": total, "total_nodata_frac": total_nodata}


def _selection_key(entry):
    # FROZEN score-blind (A41): coverage only, never ranking content.
    return (entry["record"]["refused_count"], entry["record"]["total_nodata_frac"],
            entry["record"]["post_date"])


def run_sweep(bbox, *, ignition, containment, out_dir, name="fire", greenup_days=None,
              max_post_swaps=MAX_POST_SWAPS, sensors=("S2", "Landsat"), contour_m=None,
              approve=False, select_fn=None, create_fn=None, stage_fn=None,
              attach_fn=None, pipeline_fn=None, write_fn=None):
    import acquire
    from autoacquire import dnbr_create, scene_select
    from src import outputs, pipeline as _pipeline
    from src.grids import GateAbort

    select_fn = select_fn or scene_select.select
    create_fn = create_fn or dnbr_create.create_dnbr
    stage_fn = stage_fn or acquire.stage_fire
    attach_fn = attach_fn or acquire.attach_dnbr
    pipeline_fn = pipeline_fn or _pipeline.run_pipeline
    write_fn = write_fn or outputs.write_dnbr_outputs

    kw = {} if greenup_days is None else {"greenup_days": greenup_days}
    first = select_fn(bbox, ignition=ignition, containment=containment,
                      sensors=(sensors[0],), **kw)
    if first["status"] != "recommended":
        return first                       # honest selector states pass through (B1)
    if not approve:
        return first                       # machine proposes, human disposes (B4)

    out_dir = Path(out_dir)
    fire = stage_fn(bbox, out_dir, name=name)          # ONCE; fire-scoped aborts surface here
    attempts, candidates, n_attempt = [], [], 0

    def _run_attempt(sensor, pair):
        nonlocal n_attempt
        adir = out_dir / "attempts" / f"attempt_{n_attempt:02d}"
        n_attempt += 1
        adir.mkdir(parents=True, exist_ok=True)
        try:
            created = create_fn(pair, bbox, adir / "dnbr", name=name)
        except GateAbort as e:
            # A41: create_dnbr aborts are per-PAIR by construction (403/zone/baseline/grid), but
            # they carry the default "fire" scope -- re-tag here (spec 5), never in dnbr_create.
            if getattr(e, "scope", "fire") == "fire":
                raise GateAbort(str(e), scope="attempt") from e
            raise
        # A41: attach_fn read-modify-WRITES <fire out_dir>/acquisition_manifest.json (and aborts
        # fire-scoped if it is missing), so each attempt completes its OWN copy of the staged
        # manifest -- the winner's copy is promoted back over the fire-level one at the end.
        manifest = out_dir / "acquisition_manifest.json"
        if manifest.exists():
            shutil.copy2(manifest, adir / manifest.name)
        afire = attach_fn({**fire, "out_dir": adir}, created["dnbr_tif"])
        result = pipeline_fn(afire, contour_m=contour_m)
        if result.get("status") != "ranked":
            # terrain refusal etc.: DEM-deterministic -> fire-scoped, stop the sweep
            raise GateAbort(f"pipeline returned {result.get('status')!r} -- not scene-"
                            "recoverable; sweep stops (A41).", scope="fire")
        refused = result.get("refused_basins", [])
        clean = result["arms"]["arm_a"]["basins"]      # verified shape (pipeline.py:465-484)
        total = len(refused) + len(clean)
        # Direct read: Task 2 attaches nodata_frac to EVERY record, and a silent 0.0 default
        # would read a broken invariant as perfect coverage and mis-pick the winner (A8).
        total_nodata = sum(b["nodata_frac"] for b in refused) + \
            sum(b["nodata_frac"] for b in clean)
        paths = write_fn(result["arms"]["arm_a"], result["arms"]["arm_b"],
                         result["creek_nearest"], adir, afire["dem"],
                         validation_case=f"{name} (auto-acquire sweep, dNBR both-arms)",
                         incised=(result.get("terrain_mode") == "incised"),
                         subbasin_meta=result.get("subbasin_meta"),
                         refused=refused,
                         imagery={"sensor": pair["sensor"],
                                  "pre_id": pair["pre"].get("id"),
                                  "pre_date": str(pair["pre"].get("date")),
                                  "post_id": pair["post"].get("id"),
                                  "post_date": str(pair["post"].get("date"))})
        # Retain paths + refusal METADATA only -- never the result object or its masks (spec 4).
        return {"dir": adir, "paths": [str(p) for p in paths],
                "pre_date": str(pair["pre"].get("date")),
                "refused": [{"phase1_basin_id": b["basin_id"], "nodata_frac": b["nodata_frac"]}
                            for b in refused],
                "record": _attempt_record(sensor, pair["pre"], pair["post"], "ranked",
                                          refused, total, round(total_nodata, 4))}

    def _sweep_sensor(sensor, package):
        base = package["pair"]
        posts = [base["post"]] + list(package["alternatives"]["post"])[:max_post_swaps]
        for post in posts:
            pair = {**base, "post": post}
            try:
                cand = _run_attempt(sensor, pair)
            except GateAbort as e:
                if getattr(e, "scope", "fire") == "fire":
                    raise
                attempts.append(_attempt_record(sensor, pair["pre"], post,
                                                f"abort: {str(e)[:140]}"))
                continue
            attempts.append(cand["record"])
            candidates.append(cand)
            if cand["record"]["refused_count"] == 0:
                return cand                # zero-refused wins outright
        return None

    winner = _sweep_sensor(sensors[0], first)
    if winner is None and len(sensors) > 1:
        try:
            second = select_fn(bbox, ignition=ignition, containment=containment,
                               sensors=(sensors[1],), **kw)
        except GateAbort as e:             # mid-sweep selector infra failure: sensor-scoped
            second = {"status": "aborted", "message": str(e)}
        if second.get("status") == "recommended":
            winner = _sweep_sensor(sensors[1], second)
        else:
            attempts.append(_attempt_record(
                sensors[1], {}, {},
                f"selector: {second.get('status')} {second.get('message', '')[:100]}"))

    if winner is None:
        ranked = [c for c in candidates if c["record"]["refused_count"] is not None]
        if not ranked:
            return {"status": "aborted", "package": first, "attempts": attempts,
                    "message": "no attempt produced a ranking; see attempts."}
        winner = min(ranked, key=_selection_key)       # frozen score-blind key

    _promote(winner["dir"], out_dir)
    (out_dir / "sweep_attempts.json").write_text(json.dumps({
        "attempts": attempts,
        "chosen": {"sensor": winner["record"]["sensor"],
                   "pre_id": winner["record"]["pre_id"], "pre_date": winner["pre_date"],
                   "post_id": winner["record"]["post_id"],
                   "post_date": winner["record"]["post_date"]},
        "selection": "chosen by coverage only (fewest refused -> lowest total nodata -> "
                     "earliest post); ranking content never consulted (A41)."}, indent=2))
    status = "clean" if winner["record"]["refused_count"] == 0 else "degraded"
    return {"status": status, "package": first, "attempts": attempts,
            "chosen": winner["record"], "refused": winner["refused"],
            "result_paths": {"out_dir": str(out_dir),
                             "ranking_csv": str(out_dir / Path(winner["paths"][0]).name),
                             "basins_geojson": str(out_dir / Path(winner["paths"][1]).name)}}


def _promote(attempt_dir, out_dir):
    """Winner's artifacts copied to the fire level; losers stay under attempts/ (the
    fire-level dir must hold exactly ONE coherent pair's artifacts -- A39/A40 purge rule)."""
    from src.outputs import DUAL_RANK_MAP_NAME

    attempt_dir, out_dir = Path(attempt_dir), Path(out_dir)
    # Artifacts the writer emits only on some runs: a previous run's copy must never linger
    # beside a winner that has none of its own (same purge rule, one level up).
    for optional in ("refusal.json", "refused_basins.csv", "refused_basins.geojson",
                     DUAL_RANK_MAP_NAME):
        if not (attempt_dir / optional).exists():
            (out_dir / optional).unlink(missing_ok=True)
    for p in attempt_dir.iterdir():
        if p.is_file():
            shutil.copy2(p, out_dir / p.name)
        elif p.name == "dnbr":
            shutil.copytree(p, out_dir / "dnbr", dirs_exist_ok=True)
