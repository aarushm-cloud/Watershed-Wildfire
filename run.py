"""run.py -- the single entrypoint (python run.py --fire <name>). Wires the
seven stages (ingest -> hydrology -> delineate -> score -> outputs) in order
and writes to out/<fire>/. The only place stage order is hardcoded; a thin
script, not a module (no orchestrator -- DECISIONS A7). See ARCHITECTURE.md.

A30: run.py is the production driver over the per-fire I/O config. The pipeline itself
(run_pipeline + the stage order + the A27 refusal dispatch) lives in src/pipeline.py and is
NOT moved here (A7: run.py holds no inter-stage state and makes no analytical decision -- it only
resolves a fire name to its I/O dict, calls the pipeline, dispatches the polymorphic result, and
writes outputs on the ranked path). argparse + execution live inside main()/run_fire so `import run`
has NO side effects (a bare import must never parse args or run the pipeline).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the project root importable so `from src...` resolves whether run.py is executed as a script
# (python run.py) or imported by a test. run.py lives at <root>/run.py.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The pipeline (run_pipeline + the stage order + the A27 refusal dispatch + the per-fire configs) was
# promoted verbatim into src/pipeline.py (behavior-neutral); run.py imports it directly from there now
# instead of loading validation/gate.py by file path. write_outputs is the DAG sink (src/outputs.py).
from src.pipeline import run_pipeline, dispatch_result, MONTECITO_FIRE, SOUTHFORK_FIRE, MONTECITO_DNBR_FIRE
from src.outputs import write_outputs, write_dnbr_outputs

# Registry of runnable fires. Add production fires here (data/<fire>/ paths + expected_crs +
# validation_case) as they land; MONTECITO_FIRE is the validated reconstruction case. SOUTHFORK_FIRE
# (A31) is the incised-terrain refusal demonstration -- its data is gitignored, so it is registered but
# data-absent on a clean checkout (see _assert_inputs_present), NOT a CI dependency.
FIRES = {"montecito": MONTECITO_FIRE, "southfork": SOUTHFORK_FIRE, "montecito_dnbr": MONTECITO_DNBR_FIRE}

# The on-disk INPUT paths a fire dict may carry (out_dir is an OUTPUT, name/expected_crs/validation_case
# are not paths). A None value is "absent by design" (e.g. sbs=None for a dNBR-only fire), never a
# missing-file error -- only a non-None path to a nonexistent file is a data-absence exit.
_INPUT_PATH_KEYS = ("dem", "sbs", "dnbr", "assets", "creeks")


def resolve_fire(name):
    """Look up a fire's I/O + provenance dict by name; SystemExit with the available list on miss."""
    if name not in FIRES:
        raise SystemExit(f"unknown fire {name!r}; available: {sorted(FIRES)}")
    return FIRES[name]


def _assert_inputs_present(fire):
    """A31 driver-layer data-absence guard: every NON-None input path must exist before the pipeline runs.

    A registered fire whose (gitignored) data is absent on a clean checkout must exit CLEANLY with an
    acquisition pointer instead of crashing deep in rasterio.open/load_dem. `sbs=None` (no SBS by design,
    e.g. South Fork's dNBR-only burn) is a legitimate value and is SKIPPED -- only a non-None path to a
    missing file triggers the exit. Generic: applies to ANY registered fire, not South-Fork-special-cased.
    This lives in the DRIVER (not run_pipeline): run_pipeline stays a library function that RAISES on bad
    input; the driver is where a data-absent fire becomes a clean SystemExit.
    """
    for key in _INPUT_PATH_KEYS:
        path = fire.get(key)
        if path is None:
            continue                                  # absent by design (e.g. sbs=None); never a missing-file error
        if not Path(path).exists():
            raise SystemExit(
                f"{fire['name']} data not present (gitignored): missing {key} at {path}; "
                "see acquisition_manifest.json")


def run_fire(fire):
    """Run the pipeline for one fire, dispatch the polymorphic result, and write outputs if ranked.

    Returns the process exit code. On an A27 terrain-applicability REFUSE, dispatch_result prints the
    refusal message and returns 0 and NO ranking is written (refusal.json was already written by the
    pipeline). On a ranked result, writes ranking.csv + basins.geojson stamped with this fire's
    validation_case. Does not run the pipeline twice or print the gate's validation probes (A7: thin).

    A31: a data-absent registered fire exits cleanly here (before the pipeline) via _assert_inputs_present.
    """
    _assert_inputs_present(fire)                          # clean SystemExit if any non-None input path is missing
    result = run_pipeline(fire)
    code = dispatch_result(result)                       # refusal -> prints + exit 0; ranked -> 0
    if result["status"] == "ranked":
        if result["provenance"]["burn_source"] == "dNBR":
            # A34 dNBR both-arms: Arm A headline + Arm B companion + rank_delta (src/outputs.write_dnbr_outputs)
            csv_path, gj_path = write_dnbr_outputs(
                result["arms"]["arm_a"], result["arms"]["arm_b"], result["creek_nearest"],
                fire["out_dir"], fire["dem"], validation_case=fire["validation_case"],
                incised=(result.get("terrain_mode") == "incised"),
                subbasin_meta=result.get("subbasin_meta"))
            n = len(result["arms"]["arm_a"]["basins"])
            print(f"[{fire['name']}] ranked (dNBR both-arms): {n} basins; wrote {csv_path} , {gj_path}")
        else:
            csv_path, gj_path, _ = write_outputs(
                result["basins"], result["creek_nearest"], fire["out_dir"], fire["dem"],
                result["provenance"]["burn_source"], validation_case=fire["validation_case"])
            print(f"[{fire['name']}] ranked: {len(result['basins'])} basins; wrote {csv_path} , {gj_path}")
    return code


def main():
    ap = argparse.ArgumentParser(description="Post-fire debris-flow watershed screening (per-fire).")
    ap.add_argument("--fire", required=True, help=f"fire to run; available: {sorted(FIRES)}")
    raise SystemExit(run_fire(resolve_fire(ap.parse_args().fire)))


if __name__ == "__main__":
    main()
