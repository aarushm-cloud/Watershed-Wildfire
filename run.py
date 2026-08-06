"""run.py -- the CLI driver (python run.py --fire <name>): resolve the fire, run the pipeline,
write outputs -> out/<fire>/. Thin; the stage order lives in src/pipeline.py.
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

from src.pipeline import run_pipeline, dispatch_result, MONTECITO_FIRE, SOUTHFORK_FIRE, MONTECITO_DNBR_FIRE
from src.outputs import write_outputs, write_dnbr_outputs

# Registry of runnable fires; gitignored data means registered-but-absent on a clean checkout.
FIRES = {"montecito": MONTECITO_FIRE, "southfork": SOUTHFORK_FIRE, "montecito_dnbr": MONTECITO_DNBR_FIRE}

# Input-path keys; None = absent by design (e.g. sbs=None), never a missing-file error.
_INPUT_PATH_KEYS = ("dem", "sbs", "dnbr", "assets", "creeks")


def resolve_fire(name):
    """Look up a fire's I/O + provenance dict by name; SystemExit with the available list on miss."""
    if name not in FIRES:
        raise SystemExit(f"unknown fire {name!r}; available: {sorted(FIRES)}")
    return FIRES[name]


def _assert_inputs_present(fire):
    """Every non-None input path must exist before the pipeline runs; a data-absent registered
    fire exits cleanly here instead of crashing deep in rasterio (A31)."""
    for key in _INPUT_PATH_KEYS:
        path = fire.get(key)
        if path is None:
            continue                                  # absent by design (e.g. sbs=None); never a missing-file error
        if not Path(path).exists():
            raise SystemExit(
                f"{fire['name']} data not present (gitignored): missing {key} at {path}; "
                "see acquisition_manifest.json")


def run_fire(fire):
    """Run the pipeline for one fire, dispatch the result, write outputs if ranked. Returns the
    process exit code."""
    _assert_inputs_present(fire)
    result = run_pipeline(fire)
    code = dispatch_result(result)
    if result["status"] == "ranked":
        if result["provenance"]["burn_source"] == "dNBR":
            csv_path, gj_path = write_dnbr_outputs(
                result["arms"]["arm_a"], result["arms"]["arm_b"], result["creek_nearest"],
                fire["out_dir"], fire["dem"], validation_case=fire["validation_case"],
                incised=(result.get("terrain_mode") == "incised"),
                subbasin_meta=result.get("subbasin_meta"),
                refused=result.get("refused_basins"))
            ranked = result["arms"]["arm_a"]["basins"]
            print(f"[{fire['name']}] {len(ranked)} ranked, "
                 f"{len(result.get('refused_basins', []))} refused (insufficient cloud-free "
                 f"data); wrote {csv_path} , {gj_path}")
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
