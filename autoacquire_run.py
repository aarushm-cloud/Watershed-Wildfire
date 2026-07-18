"""AA-3 (B4, Auto-Acquire Build-Plan Phase 3) -- thin wiring into the validated pipeline.

Composes the auto-acquire pathway end-to-end:

    scene_select.select  ->  [HUMAN APPROVAL GATE]  ->  dnbr_create.create_dnbr
        ->  acquire.build_fire_config  ->  src.pipeline.run_pipeline

No new ingest code and no new science: the created raw dNBR converges with the
upload path at the UNCHANGED acquire.assert_raw_dnbr / ingest_dnbr_both_arms seam
(A34) -- the single resample in the pathway is the frozen both-arms ingest.

The approval gate defaults CLOSED: machine proposes, human disposes (Feature Spec
section 7). approve=True is the explicit, logged approval of the recommended pair
(the Phase-4 UI captures it with a button; this CLI captures it with --approve).
Honest selector states (waiting / window_closed / no_pre_scene) pass through
untouched -- nothing is built, and per B1 no score or rank of any kind exists in
those states. A pipeline refusal is returned verbatim, never softened (FM-10).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import acquire  # noqa: E402
import dnbr_create  # noqa: E402
import scene_select  # noqa: E402
from src import outputs, pipeline  # noqa: E402


def run_autoacquire(bbox, *, ignition, containment, out_dir, name="fire",
                    greenup_days=scene_select.GREENUP_DEFAULT_DAYS,
                    approve=False, today=None):
    """bbox + dates -> recommendation package (default) or, on explicit approval,
    the full ranked/refused pipeline result.

    Returns the selector's honest state dict unchanged when no clean pair exists,
    the recommendation package when approve is False (the human decides next), or
    {"status": "ran", "package", "created", "pipeline"} after an approved build.
    All calls go through module attributes (monkeypatchable, suite convention).
    """
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
        )
    return ran


if __name__ == "__main__":
    import argparse
    import json
    from datetime import date

    ap = argparse.ArgumentParser(
        description="Auto-acquire dNBR end-to-end driver (AA-3; approval-gated)"
    )
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    ap.add_argument("--ignition", required=True, help="YYYY-MM-DD")
    ap.add_argument("--containment", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--name", default="fire")
    ap.add_argument("--greenup-days", type=int, default=scene_select.GREENUP_DEFAULT_DAYS)
    ap.add_argument("--approve", action="store_true",
                    help="explicitly approve the recommended pair and run the pipeline")
    args = ap.parse_args()

    out = run_autoacquire(
        tuple(args.bbox),
        ignition=date.fromisoformat(args.ignition),
        containment=date.fromisoformat(args.containment),
        out_dir=Path(args.out), name=args.name,
        greenup_days=args.greenup_days, approve=args.approve,
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
    else:
        print(out["status"] + ":", out.get("message", ""))

    def _js(o):
        from datetime import date as _d
        return o.isoformat() if isinstance(o, _d) else str(o)

    (Path(args.out) / "autoacquire_result.json").parent.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in out.items() if k not in ("pipeline", "masks")}
    Path(args.out, "autoacquire_result.json").write_text(
        json.dumps(slim, default=_js, indent=2)
    )
