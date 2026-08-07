"""autoacquire_run.py -- thin wiring: scene_select.select -> [HUMAN APPROVAL GATE] ->
dnbr_create.create_dnbr -> acquire.build_fire_config -> run_pipeline.

The approval gate defaults CLOSED (machine proposes, human disposes); honest selector states
pass through untouched with nothing built; a pipeline refusal is returned verbatim.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import acquire  # noqa: E402
from autoacquire import dnbr_create, scene_select, sweep  # noqa: E402
from src import outputs, pipeline  # noqa: E402


def run_autoacquire(bbox, *, ignition, containment, out_dir, name="fire",
                    greenup_days=scene_select.GREENUP_DEFAULT_DAYS,
                    approve=False, today=None):
    """bbox + dates -> recommendation package (default), or the full pipeline result on
    explicit approval. Honest selector states pass through unchanged."""
    package = scene_select.select(
        bbox, ignition=ignition, containment=containment,
        greenup_days=greenup_days, today=today,
    )
    if package["status"] != "recommended":
        return package                       # waiting / closed / no-pre: nothing built (B1)
    if not approve:
        return package                       # approval gate: machine proposes, human disposes

    out_dir = Path(out_dir)
    created = dnbr_create.create_dnbr(package["pair"], bbox, out_dir / "dnbr", name=name)
    fire = acquire.build_fire_config(bbox, created["dnbr_tif"], out_dir, name=name)
    result = pipeline.run_pipeline(fire)
    ran = {"status": "ran", "package": package, "created": created, "pipeline": result}
    if result.get("status") == "ranked":
        # Persist via the REUSED validated writer (A34 framing + provenance stamped
        # there, single-site). A refusal writes NO ranking artifacts (B1/A28).
        ran["outputs"] = outputs.write_dnbr_outputs(
            result["arms"]["arm_a"], result["arms"]["arm_b"], result["creek_nearest"],
            fire["out_dir"], fire["dem"],
            validation_case=f"{name} (auto-acquire, dNBR both-arms)",
            incised=(result.get("terrain_mode") == "incised"),
            subbasin_meta=result.get("subbasin_meta"),
            refused=result.get("refused_basins"),   # A41 fix-wave CRITICAL 1: never drop refusals silently
        )
    return ran


def _parse_args(argv=None):
    import argparse

    ap = argparse.ArgumentParser(
        description="Auto-acquire dNBR end-to-end driver (AA-3; approval-gated)"
    )
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    ap.add_argument("--ignition", required=True, help="YYYY-MM-DD")
    ap.add_argument("--containment", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--name", default="fire")
    ap.add_argument("--greenup-days", type=int, default=scene_select.GREENUP_DEFAULT_DAYS)
    ap.add_argument("--max-swaps", type=int, default=sweep.MAX_POST_SWAPS,
                    help="post-scene swap budget for the sweep path; 0 = legacy "
                         "single-attempt run with no sensor fallback (default: %(default)s)")
    ap.add_argument("--contour-m", type=float, default=150.0,
                    help="contour interval in meters, sweep path only (default: %(default)s)")
    ap.add_argument("--approve", action="store_true",
                    help="explicitly approve the recommended pair and run the pipeline")
    args = ap.parse_args(argv)
    if args.max_swaps < 0:                     # A41 fix-wave T8: budget, not a signed offset
        ap.error("--max-swaps must be >= 0")
    return args


def main(argv=None):
    import json
    from datetime import date

    args = _parse_args(argv)
    # Absolutized ONCE at the CLI boundary: a relative --out reaches WBT's breach step on
    # incised fires and dies with a misleading "returned 0 but did not write" GateAbort.
    out_root = Path(args.out).resolve()

    if args.max_swaps == 0:
        out = run_autoacquire(
            tuple(args.bbox),
            ignition=date.fromisoformat(args.ignition),
            containment=date.fromisoformat(args.containment),
            out_dir=out_root, name=args.name,
            greenup_days=args.greenup_days, approve=args.approve,
        )
    else:
        out = sweep.run_sweep(
            tuple(args.bbox),
            ignition=date.fromisoformat(args.ignition),
            containment=date.fromisoformat(args.containment),
            out_dir=out_root, name=args.name,
            greenup_days=args.greenup_days, max_post_swaps=args.max_swaps,
            contour_m=args.contour_m, approve=args.approve,
        )

    if out["status"] == "recommended":
        pair = out["pair"]
        print(pair["verdict"]["summary"])
        print(f"pre : {pair['pre']['id']} ({pair['pre']['date']})")
        print(f"post: {pair['post']['id']} ({pair['post']['date']})")
        print("Re-run with --approve to accept this pair and build the dNBR.")
    elif out["status"] == "ran":
        print("pipeline:", out["pipeline"].get("status"))
        print("dNBR:", out["created"]["dnbr_tif"])
        # A41 fix-wave CRITICAL 1: surface refusals on the legacy "ran" path too
        print(f"{len(out['pipeline'].get('refused_basins', []) or [])} basin(s) refused")
    elif out["status"] in ("clean", "degraded"):
        chosen = out["chosen"]
        print(f"pre : {chosen['pre_id']} ({chosen['pre_date']})")
        print(f"post: {chosen['post_id']} ({chosen['post_date']})")
        refused = out.get("refused", [])
        print(f"{len(refused)} basin(s) refused")
        if out["status"] == "degraded":
            print(f"{len(refused)} of {chosen['n_basins_total']} basins could not be "
                  "assessed (insufficient cloud-free imagery). Their hazard is UNKNOWN "
                  "-- not low. Any refused basin could rank high if data existed; see "
                  "refused_basins.csv.")
            print("refused (phase-1 basin ids): "
                  + ", ".join(str(b["phase1_basin_id"]) for b in refused))
    elif out["status"] == "aborted":
        print(out.get("message", ""))
        print(f"attempts: {len(out.get('attempts', []))}")
    else:
        print(out["status"] + ":", out.get("message", ""))

    def _js(o):
        from datetime import date as _d
        return o.isoformat() if isinstance(o, _d) else str(o)

    out_root.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in out.items() if k not in ("pipeline", "masks", "result")}
    (out_root / "autoacquire_result.json").write_text(
        json.dumps(slim, default=_js, indent=2)
    )
    return out


if __name__ == "__main__":
    main()
