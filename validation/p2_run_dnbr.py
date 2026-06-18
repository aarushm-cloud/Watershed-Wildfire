"""P2.2b -- run the dNBR arm (Arm A binning + Arm B continuous) through the REUSED pipeline and
produce a dNBR ranking PER ARM. The seam (src/ingest.ingest_dnbr_both_arms) turns the P2.0 native
dNBR into the same (wt, covered) handoff the SBS path produces; hydrology, delineation, the frozen
formula, and ranking are reused UNTOUCHED via validation/gate.py's stage functions.

⛔ HARD STOP -- NO COMPARISON TO SBS. This harness proves the dNBR arm *runs and ranks*. It does NOT
compute Spearman rho, rank-AUC-vs-control, tercile recovery, or any side-by-side -- that is P2.3, a
separate chunk. The SBS control is run here ONLY to (a) confirm the 7/7 lock-relevant pipeline is
live on HEAD and (b) supply the burn-independent delineation (same DEM -> same 36 basins) that the
A12-isolation golden assertion checks against. No control SCORE is compared to any dNBR score.

What this harness does:
  1. Run the SBS control fresh on HEAD (gate.run_pipeline) -- the basin set + flowed truth labels.
  2. Reproject native dNBR -> canonical (both arms) + normalize (src.ingest.ingest_dnbr_both_arms).
  3. NoData/cloud fail-loud guard on flowed basins (P2.1 §4 path 1; A8).
  4. Golden basin-set identity assertion: 36 basins, same IDs + (row,col) outlets as the control (A12).
  5. Score EACH arm with the reused stage_2e_score; attach the A23 diagnostic field + plain-language
     sentence; checksum that the A23 coverage labeling moves ONLY coverage fields, never score/rank.
  6. Write per-arm ranking.csv + provenance.json under validation/out/montecito_dnbr/{arm_a,arm_b}/.
  7. Print each arm's top-5 (reported as a fact, NOT vs SBS).

Run: python validation/p2_run_dnbr.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.grids import GateAbort
from src.config import DNBR_NODATA_FAILLOUD_FRAC
from src.ingest import ingest_dnbr_both_arms
from src.score import stage_2e_score
from src.outputs import SCREENING_STATEMENT   # A11 framing, reused read-only (no edit to the SBS path)

# --- load validation/gate.py as a module (cwd-independent), same mechanism as the behavior lock ---
_GATE_PATH = _REPO_ROOT / "validation" / "gate.py"
_spec = importlib.util.spec_from_file_location("gate", _GATE_PATH)
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate"] = gate
_spec.loader.exec_module(gate)

NATIVE_DNBR = _REPO_ROOT / "validation" / "out" / "montecito_dnbr" / "dnbr_native.tif"
NATIVE_PROV = _REPO_ROOT / "validation" / "out" / "montecito_dnbr" / "provenance.json"
OUT_BASE = _REPO_ROOT / "validation" / "out" / "montecito_dnbr"   # FM-9: namespaced, never clobbers out/

# A23.3 plain-language sentence carried on every operationally-low-coverage basin (A11 framing-travels).
LOW_COVERAGE_SENTENCE = ("scored low, but satellite imagery couldn't confirm whether it didn't burn "
                         "or couldn't be assessed -- treat as unscreened")
# A21 imagery-date stamp (post-scene 2018-06-19, fire ignition 2017-12-04 -> ~6 months; extended assessment).
IMAGERY_DATE_STATEMENT = ("burn severity from satellite imagery acquired ~6 months after the fire "
                          "(extended assessment; post-scene 2018-06-19) -- handled in the open as the "
                          "messy-real-fire condition the tool is built for (A21).")


def _basin_copies(basins):
    """Independent per-arm basin dicts (shallow copy; the read-only 'mask' ndarray is shared)."""
    return [dict(b) for b in basins]


def _attach_a23_diagnostic(basins, covered_interp):
    """A23: per-basin covered-INTERPRETATION fraction (below-floor counted as covered) -- a READ-ONLY
    diagnostic, never fed to low_coverage/score/rank/the P2.3 metric. Plus the plain-language sentence
    on operationally-low-coverage basins. score.py is untouched, so these are added here, post-scoring."""
    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        b["burn_coverage_frac_covered_interp"] = (
            float(np.asarray(covered_interp)[m].mean()) if ncells else 0.0)
        b["low_coverage_note"] = LOW_COVERAGE_SENTENCE if b["low_coverage"] else ""


def _nodata_fail_loud_guard(basins, nodata_mask):
    """P2.1 §4 path 1 (A8): if NoData/cloud covers > DNBR_NODATA_FAILLOUD_FRAC of any FLOWED basin,
    error loudly for that basin (a clouded scene is a bad scene, not a low-burn finding). Returns the
    per-flowed-basin NoData fraction (report-only) when the guard passes."""
    nd = np.asarray(nodata_mask)
    fracs = {}
    for b in basins:
        if not b.get("flowed"):
            continue
        m = b["mask"]
        ncells = int(m.sum())
        frac = float(nd[m].mean()) if ncells else 0.0
        fracs[b["basin_id"]] = frac
        if frac > DNBR_NODATA_FAILLOUD_FRAC:
            raise GateAbort(
                f"dNBR NoData covers {frac:.1%} of flowed basin {b['basin_id']} "
                f"(> {DNBR_NODATA_FAILLOUD_FRAC:.0%}) -- a clouded scene is a bad scene, not a "
                "low-burn finding. Refusing to rank it (P2.1 §4 path 1, A8).")
    return fracs


def _assert_basin_set_identity(control_basins, arm_basins):
    """A12 isolation (P2.1 §7 FROZEN ASSERTION): the dNBR run produces EXACTLY the control's 36
    basins -- same IDs, same (row,col) outlets. Golden (outlet-by-outlet), not just count. The
    delineation is burn-independent (same DEM), so these are identical by construction; the assertion
    is the boundary guard that the truth set {4,6,9,14,21,23} still aligns (A7, fail loud)."""
    ctrl = {(int(b["basin_id"]), (int(b["outlet"][0]), int(b["outlet"][1]))) for b in control_basins}
    arm = {(int(b["basin_id"]), (int(b["outlet"][0]), int(b["outlet"][1]))) for b in arm_basins}
    if ctrl != arm or len(arm) != 36:
        raise GateAbort(f"dNBR basin set differs from the SBS control delineation "
                        f"({len(arm)} basins) -- truth set would misalign, comparison meaningless (A12).")


def _checksum_a23_moves_only_coverage(control_basins, wt, slope, covered_operational, covered_interp):
    """P2.2b verification §6: prove the A23 coverage-labeling choice (operational `covered` vs the
    covered-interpretation) moves ONLY coverage fields, NEVER mean_burn/score/rank. Score the SAME
    arm twice with the two different `covered` masks and assert score/rank/mean_burn are bit-identical
    while burn_coverage_frac is allowed to differ (mean_burn never reads `covered`, A17)."""
    a = _basin_copies(control_basins)
    b = _basin_copies(control_basins)
    stage_2e_score(wt, covered_operational, slope, a)
    stage_2e_score(wt, covered_interp, slope, b)
    a = {x["basin_id"]: x for x in a}
    b = {x["basin_id"]: x for x in b}
    for bid in a:
        for k in ("score", "mean_burn", "mean_slope", "area_km2", "rank"):
            if a[bid][k] != b[bid][k]:
                raise GateAbort(f"A23 checksum FAILED: basin {bid} field {k} moved with the coverage "
                                f"labeling ({a[bid][k]} != {b[bid][k]}) -- coverage must not touch score.")
    # sanity: the two labelings DO differ somewhere in coverage (else the checksum is vacuous)
    differs = any(a[bid]["burn_coverage_frac"] != b[bid]["burn_coverage_frac"] for bid in a)
    return differs


def _write_arm_outputs(arm_name, burn_source_label, basins, base_prov, out_dir):
    """Write {out_dir}/ranking.csv + provenance.json for one dNBR arm. Self-contained (does NOT touch
    src/outputs.py / the SBS path); carries the A11 screening framing, the A21 imagery-date stamp, the
    A23 operational low_coverage + diagnostic field + sentence."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for b in sorted(basins, key=lambda x: x["rank"]):
        rows.append({
            "basin_id": b["basin_id"], "rank": b["rank"], "score": round(b["score"], 6),
            "mean_burn": round(b["mean_burn"], 4), "mean_slope": round(b["mean_slope"], 4),
            "area_km2": round(b["area_km2"], 4),
            "burn_coverage_frac": round(b["burn_coverage_frac"], 4),          # A23 operational
            "low_coverage": b["low_coverage"],                                # A23 operational gate
            "burn_coverage_frac_covered_interp": round(b["burn_coverage_frac_covered_interp"], 4),  # A23 diagnostic (non-gating)
            "flowed": b.get("flowed", False), "matched_creek": b.get("matched_creek", ""),
            "low_coverage_note": b["low_coverage_note"],
        })
    df = pd.DataFrame(rows)
    csv_path = out_dir / "ranking.csv"
    with open(csv_path, "w") as fh:
        fh.write(f"# {SCREENING_STATEMENT}\n")
        fh.write(f"# burn_source={burn_source_label}  normalization={arm_name}  "
                 f"validation_case=Thomas_Fire_2017/Montecito_2018\n")
        fh.write(f"# {IMAGERY_DATE_STATEMENT}\n")
        fh.write("# A23: 'low_coverage' is the OPERATIONAL flag (below-floor=non-covered); "
                 "'burn_coverage_frac_covered_interp' is a NON-GATING diagnostic only.\n")
        df.to_csv(fh, index=False)

    prov = dict(base_prov)
    prov.update({
        "phase": "P2.2b",
        "normalization_arm": arm_name,
        "burn_source": burn_source_label,
        "screening": SCREENING_STATEMENT,
        "imagery_date_statement": IMAGERY_DATE_STATEMENT,
        "a23_coverage": ("operational low_coverage = below-floor non-covered (class-15-equivalent); "
                         "burn_coverage_frac_covered_interp = non-gating diagnostic (below-floor "
                         "counted as covered). Identical floor across arms A and B."),
        "no_sbs_comparison": "P2.2b proves the dNBR arm runs+ranks; SBS comparison is P2.3.",
    })
    prov_path = out_dir / "provenance.json"
    prov_path.write_text(json.dumps(prov, indent=2))
    return csv_path, prov_path, df


def main():
    print("=" * 78)
    print("P2.2b -- dNBR arm run (Arm A binning + Arm B continuous). NO SBS COMPARISON (that is P2.3).")
    print("=" * 78)

    # 1. SBS control fresh on HEAD -> basin set + flowed truth labels (NOT scores; no comparison).
    control = gate.run_pipeline()
    cbasins = control["basins"]
    flowed_ids = sorted(b["basin_id"] for b in cbasins if b["flowed"])
    print(f"\n[control] SBS pipeline live on HEAD: {len(cbasins)} basins; flowed truth set = {flowed_ids}")

    # 2. dem profile (reproject destination) + dNBR arms via the seam.
    with rasterio.open(gate.DEM_TIF) as d:
        dem_profile = d.profile
        dem_shape = (d.height, d.width)
    print(f"[reproject] native 30 m -> canonical {dem_shape[1]}x{dem_shape[0]} @ 10 m (3x UPSAMPLE), "
          "snapped to DEM grid; assert_aligned passed for both arms (Arm A nearest / Arm B bilinear).")
    D = ingest_dnbr_both_arms(NATIVE_DNBR, dem_profile)
    valid = D["valid"]
    n_noncov_floor = int((valid & ~D["arm_a"]["covered"]).sum())
    print(f"[reproject] valid footprint: {int(valid.sum())}/{valid.size} cells "
          f"({int((~valid).sum())} NoData cells); below-floor non-covered (Arm A) = {n_noncov_floor} cells.")

    # 3. NoData fail-loud guard on flowed basins (likely no-op: P2.0 had 0% flowed cloud).
    nd_fracs = _nodata_fail_loud_guard(cbasins, D["nodata_mask"])
    print("[guard] flowed-basin NoData fraction (P2.1 §4 path 1; <= "
          f"{DNBR_NODATA_FAILLOUD_FRAC:.0%} required): "
          + ", ".join(f"b{k}={v:.1%}" for k, v in sorted(nd_fracs.items())))

    slope = gate.mean_slope_tan(control["hydro"]["dem_raw"])    # reused 2d slope raster (DEM-derived)

    results = {}
    for arm_name, arm_key, label in [("ArmA_binning", "arm_a", "dNBR-A"),
                                     ("ArmB_continuous", "arm_b", "dNBR-B")]:
        wt = D[arm_key]["wt"]
        covered = D[arm_key]["covered"]

        # 4. Golden basin-set identity (A12) -- assert before scoring.
        basins = _basin_copies(cbasins)
        _assert_basin_set_identity(cbasins, basins)

        # 5. score with the REUSED stage_2e_score (frozen formula + ranking, untouched).
        ranked, n_ties = stage_2e_score(wt, covered, slope, basins)
        _attach_a23_diagnostic(basins, D["covered_interp"])

        # 6. A23 checksum: coverage labeling moves only coverage fields, never score/rank.
        differs = _checksum_a23_moves_only_coverage(cbasins, wt, slope, covered, D["covered_interp"])

        n_low = sum(1 for b in basins if b["low_coverage"])
        out_dir = OUT_BASE / ("arm_a" if arm_key == "arm_a" else "arm_b")
        csv_path, prov_path, _df = _write_arm_outputs(arm_name, label, basins, _load_base_prov(), out_dir)
        results[arm_key] = {"ranked": ranked, "basins": basins, "n_low": n_low,
                            "checksum_differs": differs, "csv": csv_path}

        print(f"\n[{label}] complete ranking: 36 basins scored+ranked; exact score ties: {n_ties}.")
        print(f"[{label}] A23 checksum: score/rank/mean_burn identical under both coverage labelings "
              f"= PASS; coverage fields differ = {differs}.")
        print(f"[{label}] basins flagged low_coverage (operational): {n_low}/36.")
        print(f"[{label}] wrote {csv_path.relative_to(_REPO_ROOT)} + {prov_path.relative_to(_REPO_ROOT)}")
        print(f"[{label}] TOP 5 (reported as a fact, NOT vs SBS):")
        print(f"     {'rk':>2} {'id':>3} {'score':>8} {'burn':>5} {'slope':>6} {'area':>7} "
              f"{'cov':>5} {'covI':>5} {'flowed':>6}")
        for b in ranked[:5]:
            print(f"     {b['rank']:2d} {b['basin_id']:3d} {b['score']:8.4f} {b['mean_burn']:5.3f} "
                  f"{b['mean_slope']:6.3f} {b['area_km2']:7.3f} {b['burn_coverage_frac']:5.2f} "
                  f"{b['burn_coverage_frac_covered_interp']:5.2f} {str(b.get('flowed', False)):>6}")

    # flowed-basin coverage fractions per arm (P2.1 §7 secondary metric; report-only, NOT vs SBS).
    print("\n[coverage] flowed-basin coverage fraction per arm (operational | covered-interp diagnostic):")
    for arm_key, label in [("arm_a", "dNBR-A"), ("arm_b", "dNBR-B")]:
        by_id = {b["basin_id"]: b for b in results[arm_key]["basins"]}
        cells = []
        for fid in flowed_ids:
            b = by_id[fid]
            cells.append(f"b{fid}={b['burn_coverage_frac']:.2f}|{b['burn_coverage_frac_covered_interp']:.2f}")
        print(f"     {label}: " + "  ".join(cells))

    print("\n" + "=" * 78)
    print("P2.2b dNBR arm RUNS and RANKS (both arms). NO SBS comparison computed. STOP -> P2.3.")
    print("=" * 78)


def _load_base_prov():
    """The P2.0 native-raster provenance, carried forward verbatim under each arm's stamp."""
    return json.loads(NATIVE_PROV.read_text())


if __name__ == "__main__":
    try:
        main()
    except GateAbort as exc:
        print(f"\nGATE ABORT (fail-loud, A8/FM-10): {exc}", file=sys.stderr)
        sys.exit(2)
