"""Containment-sensitivity sweep for the auto-acquire selector.

post_start = containment (inclusive, scene_select.py), so the containment date --
an operator input that is often never published as a clean number (South Fork
tops out at 99%, Trout 96%, Buck 91%; the Bridge Fire's formal declaration came
NINE MONTHS after fire activity ended) -- hard-gates which post scenes are
reachable at all. This sweep runs the selector across a range of plausible
containment dates and reports how the recommendation moves, so a fire's
sensitivity to the operator's guess is measured instead of assumed.

Measured on South Fork (2026-07-28, stress-test F-2b): the window rule is
PROTECTIVE -- the honest (latest) containment date reached the cleanest post
scene, and earlier "more generous" dates reached materially worse ones.

History note: this module also carried the 2026-07-27 stress-test campaign's
one-shot instruments (the F-1/F-4 defect probes, the arm drivers, the
pre-registered verdict bands). Those were removed 2026-07-28 after the defects
were fixed and hermetically locked in tests/acquire/test_scene_select.py -- a
probe whose measured condition can no longer occur is a disarmed instrument,
not a record. The campaign record lives in the vault stress-test notes and in
git history.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.stress_fires import FIRES  # noqa: E402
from validation.stress_run import _jsonable, run_fire  # noqa: E402

_OUT_ROOT = _REPO_ROOT / "out" / "stress_test"


def _id_of(candidate):
    if not candidate:
        return None
    return candidate.get("id")


def _pair_summary(package):
    pair = package.get("pair")
    if not pair:
        return {"sensor": None, "pre": None, "post": None,
                "pre_date": None, "post_date": None, "rubric": None}
    return {
        "sensor": pair.get("sensor"),
        "pre": _id_of(pair.get("pre")),
        "post": _id_of(pair.get("post")),
        "pre_date": str(pair.get("pre", {}).get("date")),
        "post_date": str(pair.get("post", {}).get("date")),
        "rubric": pair.get("verdict"),
    }


def sweep_containment(fire_key, start, end, *, step_days=7, today=None, quiet=False):
    """Run the selector at step_days intervals over [start, end] inclusive.

    Each row records the recommendation at that containment date, plus (when the
    registry carries a frozen pair) whether the frozen post scene is reachable.
    Serialized to out/stress_test/<fire>/containment_sweep.json."""
    fire = FIRES[fire_key]
    frozen_post = (fire["frozen_pair"] or {}).get("post")

    rows, d = [], start
    while d <= end:
        package = run_fire(fire_key, containment=d, today=today)
        summary = _pair_summary(package)
        post_id = summary["post"]
        row = {
            "containment": d.isoformat(),
            "status": package["status"],
            **summary,
            "frozen_post_reachable": (
                None if frozen_post is None
                else bool(post_id and str(post_id).startswith(frozen_post))
            ),
        }
        rows.append(row)
        if not quiet:
            print(f"  {row['containment']}  {row['status']:<14s} "
                  f"post={row['post'] or '-'}  frozen_reachable={row['frozen_post_reachable']}")
        d += timedelta(days=step_days)

    out_dir = Path(_OUT_ROOT) / fire_key
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "containment_sweep.json").write_text(json.dumps(_jsonable(rows), indent=2))
    return rows


if __name__ == "__main__":
    from datetime import date

    print("=== South Fork containment sweep (2024-06-24 -> 2024-07-15, weekly) ===")
    sweep_containment("southfork", date(2024, 6, 24), date(2024, 7, 15))
