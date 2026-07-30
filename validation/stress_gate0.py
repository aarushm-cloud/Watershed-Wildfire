"""Gate 0 -- validate the dNBR creator before trusting any measurement from it.

Rebuilds the frozen South Fork scene pair BY ID using the production candidate
builder (scene_select._search_scenes -> group_candidates), so the candidate dicts
are never hand-assembled, then pushes the pair through dnbr_create.create_dnbr and
compares the result to the committed reference raster.

Only the selector's JUDGEMENT is bypassed, not its plumbing. That matters, because
F-2 says the frozen post scene is outside the selector's window: Gate 0 must be
able to validate the creator even when the selector could never propose this pair.

Outcomes, all informative:

  pass            instrument validated; `stats` is the ceiling anchor.
  lattice_abort   the frozen pair crosses WRS paths (033/037 -> 032/037) and
                  create_dnbr refuses to pair across native lattices. Finding F-3:
                  the validated artifact and the pathway that replaced it are
                  structurally incompatible. NOT a harness bug -- fall back to the
                  Putah ceiling so the rest of the test still has an instrument.
  zone_abort      the pair spans two UTM zones.
  other_abort     anything else; read `detail` before interpreting downstream runs.
  scene_missing   a frozen scene is not returned by a STAC search over the bbox.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from autoacquire import dnbr_create, scene_select  # noqa: E402
from src.grids import GateAbort  # noqa: E402
from validation.stress_compare import compare_aligned, compare_same_grid  # noqa: E402
from validation.stress_fires import FIRES  # noqa: E402

_OUT_ROOT = _REPO_ROOT / "out" / "stress_test"


def _members(candidate):
    return candidate.get("items") or [candidate]


def _id_matches(actual, wanted):
    """Frozen IDs are recorded WITHOUT the Collection-2 tier suffix.

    provenance.json stores 'LC09_L2SP_033037_20240612'; the STAC id is
    'LC09_L2SP_033037_20240612_02_T1'. Prefix matching is unambiguous for Landsat
    (one scene per path/row/date), so it cannot over-match.
    """
    return actual is not None and (actual == wanted or actual.startswith(wanted))


def _matches(candidate, wanted_id):
    """True if this candidate IS the wanted scene or contains it as a member.

    group_candidates merges same-day adjacent tiles into one candidate, so an
    exact top-level id match is not sufficient.
    """
    if _id_matches(candidate.get("id"), wanted_id):
        return True
    return any(_id_matches(m.get("id"), wanted_id) for m in _members(candidate))


def _date_from_landsat_id(scene_id):
    """LC09_L2SP_033037_20240612_02_T1 -> date(2024, 6, 12)."""
    stamp = scene_id.split("_")[3]
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))


def rebuild_frozen_pair(fire_key="southfork"):
    """Frozen scene IDs -> a create_dnbr-shaped pair, built by production code."""
    fire = FIRES[fire_key]
    frozen = fire["frozen_pair"]
    if frozen is None:
        raise ValueError(f"{fire_key} has no frozen pair to rebuild")

    sensor, bbox = frozen["sensor"], fire["bbox"]
    found, audit = {}, {}
    for role in ("pre", "post"):
        wanted = frozen[role]
        d = _date_from_landsat_id(wanted)
        pool = scene_select._search_scenes(sensor, bbox, d, d + timedelta(days=1))
        audit[role] = {"searched": d.isoformat(),
                       "returned": [c.get("id") for c in pool]}
        hits = [c for c in pool if _matches(c, wanted)]
        if not hits:
            raise GateAbort(
                f"Gate 0: frozen {role} scene {wanted} not returned by a STAC search "
                f"over {bbox} on {d}. Returned instead: {audit[role]['returned']}. "
                "Either the bbox is wrong or the archive changed."
            )
        found[role] = hits[0]

    return {"sensor": sensor, "pre": found["pre"], "post": found["post"]}, audit


def _compare(reference, built):
    """Same-grid first; fall back to whole-pixel alignment before giving up.

    A window offset is expected (the reference was cut to its own box) and is not
    drift. A sub-pixel offset IS drift and is left to raise.
    """
    try:
        stats = compare_same_grid(reference, built)
        stats["comparison"] = "same_grid"
        return stats
    except ValueError:
        stats = compare_aligned(reference, built)
        stats["comparison"] = "aligned_intersection"
        return stats


def run_gate0(fire_key="southfork", out_dir=None):
    out_dir = Path(out_dir or _OUT_ROOT / "gate0")
    out_dir.mkdir(parents=True, exist_ok=True)
    fire = FIRES[fire_key]

    def _save(result):
        (out_dir / f"gate0_{fire_key}.json").write_text(
            json.dumps(result, indent=2, default=str)
        )
        return result

    try:
        pair, audit = rebuild_frozen_pair(fire_key)
    except GateAbort as e:
        return _save({"outcome": "scene_missing", "stats": None, "detail": str(e)})

    scenes = {
        "pre": [m.get("id") for m in _members(pair["pre"])],
        "post": [m.get("id") for m in _members(pair["post"])],
        "pre_epsg": sorted({m.get("epsg") for m in _members(pair["pre"])}),
        "post_epsg": sorted({m.get("epsg") for m in _members(pair["post"])}),
    }

    try:
        created = dnbr_create.create_dnbr(pair, fire["bbox"], out_dir,
                                          name=f"gate0_{fire_key}")
    except GateAbort as e:
        msg = str(e).lower()
        if "utm" in msg or "zone" in msg:
            outcome = "zone_abort"
        elif "lattice" in msg or "resampl" in msg or "grid" in msg:
            outcome = "lattice_abort"
        else:
            outcome = "other_abort"
        return _save({
            "outcome": outcome, "stats": None, "detail": str(e),
            "scenes": scenes, "search_audit": audit,
            "finding": (
                "F-3: the frozen South Fork pair crosses WRS paths 033->032 and cannot "
                "be rebuilt by the current creator. The validated artifact and the "
                "pathway that replaced it are structurally incompatible. Fall back to "
                "the Putah ceiling for instrument validation."
            ) if outcome == "lattice_abort" else None,
        })

    stats = _compare(fire["reference"], created["dnbr_tif"])
    return _save({
        "outcome": "pass", "stats": stats,
        "detail": "instrument validated -- same scenes, same math, compared to the "
                  "committed reference",
        "scenes": scenes, "search_audit": audit,
        "created": {k: str(v) for k, v in created.items()},
    })


if __name__ == "__main__":
    import pprint
    key = sys.argv[1] if len(sys.argv) > 1 else "southfork"
    pprint.pprint(run_gate0(key))
