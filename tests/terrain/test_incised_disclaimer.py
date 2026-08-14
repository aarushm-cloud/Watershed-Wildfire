"""A39 -- the disclaimer must reach the artifact from EVERY entrypoint."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _writer_call_sites():
    import app, run
    from autoacquire import autoacquire_run, sweep
    return {"app.run_screening": inspect.getsource(app.run_screening),
            "autoacquire_run": inspect.getsource(autoacquire_run),
            "run.py": inspect.getsource(run),
            # A41 (Task 7 review F2): the Generate path's REAL writer call moved here
            # (sweep.py's nested _run_attempt) once app.run_generated_screening became a thin
            # sweep-backed wrapper -- inspect.getsource(run_sweep) still walks the nested
            # closure's source text, so this call site is not silently unguarded.
            "autoacquire.sweep.run_sweep": inspect.getsource(sweep.run_sweep)}


@pytest.mark.parametrize("name", ["app.run_screening", "autoacquire_run", "run.py",
                                  "autoacquire.sweep.run_sweep"])
def test_every_writer_call_site_passes_incised(name):
    """Each real writer call site must pass incised=. The Generate path's write lives in
    sweep.run_sweep's nested _run_attempt, so app.run_generated_screening -- now a thin
    sweep-backed wrapper -- is intentionally not listed (its writer is covered via run_sweep)."""
    src = _writer_call_sites()[name]
    assert "write_dnbr_outputs" in src, (
        f"{name} is listed as a writer call site but no longer calls write_dnbr_outputs -- "
        "update the registry or fix the wiring (no silent skip)")
    assert "incised=" in src, (
        f"{name} calls write_dnbr_outputs without incised= -- an incised fire would ship "
        f"an UNDISCLAIMED ranking")


def test_end_to_end_incised_artifact_carries_the_disclaimer(incised_fire, tmp_path):
    """The real proof: run the pipeline, write via the real driver path, read the file."""
    from src.pipeline import run_pipeline
    from src.outputs import write_dnbr_outputs, INCISED_FRAMING
    result = run_pipeline(incised_fire)
    csv_path, gj_path = write_dnbr_outputs(
        result["arms"]["arm_a"], result["arms"]["arm_b"], None,
        incised_fire["out_dir"], incised_fire["dem"], incised_fire["validation_case"],
        incised=(result["terrain_mode"] == "incised"),
        subbasin_meta=result.get("subbasin_meta"))
    assert INCISED_FRAMING in csv_path.read_text()
    import json
    assert "incised_framing" in json.loads(gj_path.read_text())["provenance"]
