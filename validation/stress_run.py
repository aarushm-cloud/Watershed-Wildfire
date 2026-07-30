"""Selector runner: one fire -> the complete selector package, on disk.

A thin wrapper that adds NO judgement of its own. The point is to capture what
scene_select.select() actually returns, including the full `rejected` audit trail
-- the rejections are where false-positive risk hides, and they are the first
thing thrown away by any summary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from autoacquire import scene_select  # noqa: E402
from src.grids import GateAbort  # noqa: E402
from validation.stress_fires import FIRES  # noqa: E402

_OUT_ROOT = _REPO_ROOT / "out" / "stress_test"


def _jsonable(obj):
    """Dates, Paths and tuples -> JSON-safe. Never drops a key."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def run_fire(fire_key, *, containment=None, out_root=None, today=None,
             greenup_days=scene_select.GREENUP_DEFAULT_DAYS):
    """Run the selector for one registry fire and serialize the package.

    containment overrides the registry value (used by the sweeps). today is
    injectable so a run over a historical fire is reproducible.
    """
    fire = FIRES[fire_key]
    if fire["bbox"] is None:
        raise ValueError(
            f"{fire_key} has no bbox -- derive it from the perimeter before running. "
            "A clipped box measures the operator, not the tool."
        )
    used = containment if containment is not None else fire["containment"]

    # app.py:generate_package reduces GateAbort to {"kind": "error"} rather than
    # letting it escape, so an abort is what an OPERATOR actually experiences.
    # Mirror that here as a synthetic status, otherwise one aborting fire would
    # take down a whole arm and we would measure the harness, not the tool.
    try:
        package = dict(scene_select.select(
            fire["bbox"], ignition=fire["ignition"], containment=used,
            greenup_days=greenup_days, today=today,
        ))
    except GateAbort as e:
        package = {"status": "gate_abort", "error": str(e), "pair": None,
                   "rejected": [], "provenance": {}}
    package["_fire"] = fire_key
    package["_ignition"] = fire["ignition"].isoformat()
    package["_containment_used"] = used.isoformat()
    package["_containment_registry"] = fire["containment"].isoformat()
    package["_greenup_days"] = int(greenup_days)

    out_dir = Path(out_root or _OUT_ROOT) / fire_key
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if containment is None else f"_{used.isoformat()}"
    (out_dir / f"package{suffix}.json").write_text(
        json.dumps(_jsonable(package), indent=2)
    )
    return package
