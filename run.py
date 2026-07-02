"""run.py -- the single entrypoint (python run.py --fire <name>). Wires the
seven stages (ingest -> hydrology -> delineate -> score -> outputs) in order
and writes to out/<fire>/. The only place stage order is hardcoded; a thin
script, not a module (no orchestrator -- DECISIONS A7). See ARCHITECTURE.md.

A30: run.py is the production driver over the per-fire I/O config. The pipeline itself
(run_pipeline + the stage order + the A27 refusal dispatch) lives in validation/gate.py and is
NOT moved here (A7: run.py holds no inter-stage state and makes no analytical decision -- it only
resolves a fire name to its I/O dict, calls the pipeline, dispatches the polymorphic result, and
writes outputs on the ranked path). argparse + execution live inside main()/run_fire so `import run`
has NO side effects (a bare import must never parse args or run the pipeline).
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

# Load validation/gate.py as an importable module via its file path -- the same cwd-independent
# pattern the tests use (validation/ is not a package; gate.py anchors its own DATA/OUT paths to
# __file__). run_pipeline / dispatch_result / MONTECITO_FIRE come from this single module.
_REPO_ROOT = Path(__file__).resolve().parent
_GATE_PATH = _REPO_ROOT / "validation" / "gate.py"
_spec = importlib.util.spec_from_file_location("gate", _GATE_PATH)
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate"] = gate
_spec.loader.exec_module(gate)

run_pipeline = gate.run_pipeline
dispatch_result = gate.dispatch_result
MONTECITO_FIRE = gate.MONTECITO_FIRE
SOUTHFORK_FIRE = gate.SOUTHFORK_FIRE

from src.outputs import write_outputs

# Registry of runnable fires. Add production fires here (data/<fire>/ paths + expected_crs +
# validation_case) as they land; MONTECITO_FIRE is the validated reconstruction case. SOUTHFORK_FIRE
# (A31) is the incised-terrain refusal demonstration -- its data is gitignored, so it is registered but
# data-absent on a clean checkout (see _assert_inputs_present), NOT a CI dependency.
FIRES = {"montecito": MONTECITO_FIRE, "southfork": SOUTHFORK_FIRE}

# The on-disk INPUT paths a fire dict may carry (out_dir is an OUTPUT, name/expected_crs/validation_case
# are not paths). A None value is "absent by design" (e.g. sbs=None for a dNBR-only fire), never a
# missing-file error -- only a non-None path to a nonexistent file is a data-absence exit.
_INPUT_PATH_KEYS = ("dem", "sbs", "assets", "creeks")


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
