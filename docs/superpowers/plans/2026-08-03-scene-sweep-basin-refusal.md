# Scene Sweep + Per-Basin Cloud Refusal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **READ FIRST, in order:** (1) `.superpowers/sdd/progress.md` (the ledger — where are we), (2) the repo `CLAUDE.md` (Tier-1 rules), (3) the spec at `docs/superpowers/specs/2026-08-03-scene-sweep-basin-refusal-design.md`. The spec is authoritative over this plan on any conflict — HALT and reconcile.

**Goal:** When the recommended scene pair fails the per-basin cloud guard, the app/CLI automatically walk the vetted alternate scenes (then the other sensor) under one approval; if nothing is fully clean, rank the clean basins and refuse the clouded ones individually, loudly labeled.

**Architecture:** The fatal all-basins guard (creeks=None) becomes a partition at the frozen 0.20 bar, applied on scene-independent phase-1 geometry BEFORE `filter_burned_steep`. A new `autoacquire/sweep.py` orchestrates bounded retries (stage DEM once via a new `acquire.stage_fire` seam; per-attempt dNBR in `attempts/attempt_NN/`; winner promoted by copy). `GateAbort` gains a `scope` attr (default `"fire"` = stop loud).

**Tech Stack:** Python 3.11, conda env `wildfire-watershed` (`~/miniconda3/envs/wildfire-watershed/bin/python`), pytest, numpy, rasterio, Streamlit.

## Global Constraints

- ⛔ **STAGE ONLY — NEVER `git commit` / `git push`.** The owner commits. Every task ends with `git add`, nothing more.
- ⛔ **Frozen, verbatim, never tuned:** `DNBR_NODATA_FAILLOUD_FRAC = 0.20`; comparison is strictly `frac > 0.20`; `score = mean_burn × mean_slope × area_km2` (multiply order frozen); flowed-basin fatal guard behavior; `S2_BAD_SCL`; selector rubric. Changing any = Tier-1 HALT.
- ⛔ Oracle files never edited: `validation/gate.py`, `VALIDATION_REPORT.md`, `tests/core/test_behavior_lock.py`. `P2_PREREGISTRATION.md` gets ONLY the Task 9 append-only note, owner-gated.
- Suite must be green at the end of every task: `cd ~/Documents/Wildfire-Watershed-sweep && ~/miniconda3/envs/wildfire-watershed/bin/python -m pytest tests/ -q` (baseline: all pass, 0 fail).
- New sweep constants: `MAX_POST_SWAPS = 6` (owned value — stress harness's 2 was a harness budget). Sweep status vocabulary: `"clean" | "degraded" | "aborted"` — the string `"refused"` is reserved (terrain refusal, `pipeline.py:249`).
- Selection key (frozen score-blind, A41): fewest refused → lowest total nodata frac → earliest post date. It may never read scores/ranks/burn stats.
- Docstring/comment style: sparse, one-liners, booby-trap warnings at point of use with decision IDs (repo convention).
- Update the ledger (`.superpowers/sdd/progress.md`) after every task: mark done, note suite count, note any deviation.

---

### Task 1: Behavior locks that must pre-date the refactor

**Files:**
- Create: `tests/core/test_nodata_guard_locks.py`

**Interfaces:**
- Consumes: `src.pipeline._dnbr_nodata_guard`, `src.pipeline._dnbr_nodata_flags`, `src.grids.GateAbort` (all exist today).
- Produces: executable locks later tasks must keep green. **No prior lock exists on the fatal arm** (verified 2026-08-03; A41 adjudication note records this — there is no RED→GREEN retarget).

- [ ] **Step 1: Write the lock tests**

```python
"""Fatal nodata-guard locks (A41 prep). Pin the FROZEN >20% fatal semantics BEFORE the
partition refactor so the refactor provably preserves them (pre-reg P2 §4; A20/A21).
No prior test pinned the fatal arm -- these are the first locks, not a retarget."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import DNBR_NODATA_FAILLOUD_FRAC
from src.grids import GateAbort
from src.pipeline import _dnbr_nodata_guard


def _basin(mask):
    return {"basin_id": 7, "mask": np.asarray(mask, dtype=bool)}


def test_guard_raises_above_bar_with_frozen_message():
    """>20% nodata over a guarded basin -> GateAbort, message text pinned."""
    mask = np.ones((10, 10), dtype=bool)
    nd = np.zeros((10, 10), dtype=bool)
    nd[:3, :] = True                                  # 30% of the basin
    with pytest.raises(GateAbort, match="a clouded scene is a bad scene"):
        _dnbr_nodata_guard([_basin(mask)], nd)


def test_guard_passes_at_exactly_the_bar():
    """Boundary is strict '>': exactly 20% must NOT raise (frozen comparison)."""
    mask = np.ones((10, 10), dtype=bool)
    nd = np.zeros((10, 10), dtype=bool)
    nd[:2, :] = True                                  # exactly 20%
    _dnbr_nodata_guard([_basin(mask)], nd)            # must not raise
    assert DNBR_NODATA_FAILLOUD_FRAC == 0.20          # the frozen constant itself


def test_guard_ignores_nodata_outside_the_basin():
    """Cloud elsewhere in the box never counts against a basin."""
    mask = np.zeros((10, 10), dtype=bool)
    mask[5:, :] = True
    nd = np.zeros((10, 10), dtype=bool)
    nd[:5, :] = True                                  # all cloud OUTSIDE the basin
    _dnbr_nodata_guard([_basin(mask)], nd)            # must not raise
```

- [ ] **Step 2: Run them — all three must PASS against HEAD**

Run: `~/miniconda3/envs/wildfire-watershed/bin/python -m pytest tests/core/test_nodata_guard_locks.py -v`
Expected: 3 passed. (These lock CURRENT behavior; they are green now and must stay green through every later task.)

- [ ] **Step 3: Full suite green, then stage**

Run: `~/miniconda3/envs/wildfire-watershed/bin/python -m pytest tests/ -q` → all pass.
```bash
git add tests/core/test_nodata_guard_locks.py
```

---

### Task 2: `GateAbort.scope` + the partition in `run_pipeline`

**Files:**
- Modify: `src/grids.py` (GateAbort, ~line 12)
- Modify: `src/ingest.py` (the two footprint-hole raises, ~lines 151/153)
- Modify: `src/pipeline.py` (creeks=None dispatch ~414-427; incised filter block ~402-412; result dict ~440s)
- Create: `tests/core/test_partition_refused.py`

**Interfaces:**
- Consumes: Task 1 locks (must stay green untouched).
- Produces: `GateAbort(msg, scope="fire"|"attempt")` with `.scope`; `src.pipeline._partition_refused(basins, nodata_mask) -> (clean, refused)` attaching `b["nodata_frac"]` to every record; `run_pipeline` result gains `"refused_basins"` (list of basin records, possibly empty) on the dNBR path; zero-clean raises `GateAbort(..., scope="attempt")`; the `filter_burned_steep`-empty abort (~407) gains `scope="attempt"`.

- [ ] **Step 1: `GateAbort.scope` in `src/grids.py`**

Replace the class (keep docstring line):

```python
class GateAbort(RuntimeError):
    """Raised when a stage precondition is violated -- fail loud, never degrade (FM-10)."""

    def __init__(self, message, *, scope="fire"):
        # A41: "fire" = deterministic for this fire, a sweep must stop loud (the default,
        # so unclassified aborts are never silently retried past); "attempt" = scene-pair-
        # dependent, a sweep may record it and try the next vetted pair.
        super().__init__(message)
        self.scope = scope
```

- [ ] **Step 2: Tag the scene-dependent raises**

In `src/ingest.py`, the two raises inside `ingest_dnbr_both_arms` (non-finite/sentinel inside footprint, arm-B hole — ~lines 151/153): append `, scope="attempt"` to each `GateAbort(...)` call (pair-dependent: the next pair may be clean).

In `src/pipeline.py`, the incised `filter_burned_steep`-empty raise (~line 408, "no sub-basins are both sufficiently burned and steep"): append `scope="attempt"` (cloud→wt 0 can empty the set; scene-dependent — reviewer finding 3). All other raises keep the default `"fire"`.

- [ ] **Step 3: Write the failing partition tests**

```python
"""A41 partition tests: creeks=None dNBR runs refuse per-basin instead of aborting the run.
Flowed path (creeks present) is UNCHANGED -- Task 1 locks pin it."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import DNBR_NODATA_FAILLOUD_FRAC
from src.grids import GateAbort
from src.pipeline import _partition_refused


def _b(bid, mask):
    return {"basin_id": bid, "mask": np.asarray(mask, dtype=bool)}


def _grid(rows_cloudy):
    nd = np.zeros((10, 10), dtype=bool)
    nd[:rows_cloudy, :] = True
    return nd


def test_partition_splits_at_frozen_bar_and_attaches_frac():
    m_all = np.ones((10, 10), dtype=bool)
    m_low = np.zeros((10, 10), dtype=bool); m_low[8:, :] = True   # clean rows only
    clean, refused = _partition_refused([_b(0, m_all), _b(1, m_low)], _grid(3))
    assert [b["basin_id"] for b in refused] == [0]                # 30% > 0.20
    assert [b["basin_id"] for b in clean] == [1]
    assert refused[0]["nodata_frac"] == pytest.approx(0.30)
    assert clean[0]["nodata_frac"] == pytest.approx(0.0)


def test_partition_boundary_is_strictly_greater():
    m = np.ones((10, 10), dtype=bool)
    clean, refused = _partition_refused([_b(0, m)], _grid(2))     # exactly 20%
    assert refused == [] and len(clean) == 1                      # frozen '>' comparison


def test_partition_never_scores_refused():
    """Refused records must carry no rank/score keys -- they were never scored."""
    m = np.ones((10, 10), dtype=bool)
    _, refused = _partition_refused([_b(0, m)], _grid(5))
    assert "score" not in refused[0] and "rank" not in refused[0]


def test_gateabort_scope_default_and_attempt():
    assert GateAbort("x").scope == "fire"                         # unclassified = stop loud
    assert GateAbort("x", scope="attempt").scope == "attempt"
```

Run: `... -m pytest tests/core/test_partition_refused.py -v` → Expected: FAIL (`_partition_refused` not defined; scope kwarg missing).

- [ ] **Step 4: Implement in `src/pipeline.py`**

Add near `_dnbr_nodata_guard`:

```python
def _partition_refused(basins, nodata_mask):
    """A41: split basins at the FROZEN bar (DNBR_NODATA_FAILLOUD_FRAC, strictly '>');
    attaches b["nodata_frac"] to every record. Refused are never scored/ranked/renumbered."""
    nd = np.asarray(nodata_mask)
    clean, refused = [], []
    for b in basins:
        m = b["mask"]
        ncells = int(m.sum())
        b["nodata_frac"] = float(nd[m].mean()) if ncells else 0.0
        (refused if b["nodata_frac"] > DNBR_NODATA_FAILLOUD_FRAC else clean).append(b)
    return clean, refused
```

Rewire the dNBR path in `run_pipeline`. Today the order is: `D = ingest_dnbr_both_arms(...)` (~400) → incised `filter_burned_steep` block (~402-412) → guard dispatch (~414-427). New order — partition FIRST (closes the basin-erasure channel: `filter_burned_steep` counts cloud as unburned, subbasins.py:141), then filter clean only:

```python
    D = ingest_dnbr_both_arms(dnbr_path, dem_artifacts["profile"])

    # A41 dispatch: flowed basins (creeks present) keep the FROZEN fatal guard verbatim
    # (pre-reg P2 §4); creeks=None partitions per-basin instead of aborting the run.
    refused_basins = []
    if creek_nearest is not None:
        flowed_ids = {info["basin_id"] for info in creek_nearest.values() if info["dist_m"] <= TRUTH_MATCH_M}
        guard_basins = [b for b in basins if b["basin_id"] in flowed_ids]
        unguarded_basins = [b for b in basins if b["basin_id"] not in flowed_ids]
        _partition_refused(basins, D["nodata_mask"])   # attach nodata_frac only; discard split
        refused_basins = []                             # flowed path never refuses per-basin
        _dnbr_nodata_guard(guard_basins, D["nodata_mask"])
        nodata_warn = _dnbr_nodata_flags(unguarded_basins, D["nodata_mask"])
        if nodata_warn:
            _log.warning("dNBR NoData > %.0f%% on %d unguarded non-flowed basin(s) %s -- ranks may be "
                         "under-scored (cloud read as low burn); NOT aborted (flowed-only P2.3 parity). A P4 "
                         "truth fire must widen the guard or pre-screen the scene.",
                         DNBR_NODATA_FAILLOUD_FRAC * 100, len(nodata_warn), [bid for bid, _ in nodata_warn])
    else:
        # Partition on the scene-INDEPENDENT geometry, BEFORE the burn filter -- a clouded
        # burned basin must be REFUSED, not silently dropped by filter_burned_steep (A41).
        basins, refused_basins = _partition_refused(basins, D["nodata_mask"])
        if not basins:
            raise GateAbort(
                "FAIL: every basin exceeds the frozen dNBR NoData bar "
                f"({DNBR_NODATA_FAILLOUD_FRAC:.0%}) -- no clean basin to rank (B1/A41). "
                "Do NOT emit an empty ranking.", scope="attempt")

    if incised:
        from src.subbasins import filter_burned_steep
        basins = filter_burned_steep(basins, D["arm_a"]["wt"], slope)
        if not basins:
            raise GateAbort(
                "FAIL: no sub-basins are both sufficiently burned and steep on incised "
                "terrain (A39). The burn may not intersect mapped drainage. Do NOT emit an "
                "empty ranking.", scope="attempt")
        outlets = [b["outlet"] for b in basins]
```

(The old guard-dispatch block at ~414-427 is REPLACED by the above; the incised filter block MOVES below the partition. Delete the original copies.) In the returned dict (both the incised and range-front dNBR returns, ~440s), add `"refused_basins": refused_basins`. `outlets`, `n_ties`, arms, intensity ranks all compute from the post-partition `basins` — no other change.

**Silent-error note for the implementer:** on incised fires, refused records carry PHASE-1 ids while clean survivors are renumbered by `filter_burned_steep` — the id spaces collide by value. Never join them; Task 3 renders refused ids under the column name `phase1_basin_id`.

- [ ] **Step 5: Basin-erasure regression test (append to `tests/core/test_partition_refused.py`)**

```python
def test_erasure_channel_closed_partition_precedes_burn_filter():
    """A cloud-swamped BURNED basin must be refused, not vanish via filter_burned_steep
    (cloud counts as unburned there -- subbasins.py:141). Partition-first guarantees it."""
    from src.subbasins import filter_burned_steep
    m = np.ones((10, 10), dtype=bool)
    nd = _grid(5)                                     # 50% cloud
    wt = np.where(nd, 0.0, 1.0)                       # cloud -> weight 0 (frozen mapping)
    slope = np.full((10, 10), 0.3)
    clean, refused = _partition_refused([_b(0, m)], nd)
    assert len(refused) == 1                          # refused BEFORE the filter can hide it
    # negative control: filter alone would have kept it (50% burned >= 0.25) -- the old
    # order only dropped basins at heavier cloud; either way it was silent, never refused
    assert len(filter_burned_steep([_b(0, m)], wt, slope)) == 1
```

- [ ] **Step 6: Run partition tests → PASS; Task 1 locks → still PASS; full suite → green. Stage.**

```bash
git add src/grids.py src/ingest.py src/pipeline.py tests/core/test_partition_refused.py
```

---

### Task 3: Refusal artifacts in `src/outputs.py`

**Files:**
- Modify: `src/outputs.py` (`write_dnbr_outputs` signature; header writer ~195-199; geojson provenance ~234-245; `write_dual_rank_map` ~248+)
- Modify: `run.py` (basin-count print, ~59-65) and `app.py`/`stress_divergence.py` call sites only if signatures demand (param is keyword-with-default — they should not)
- Create: `tests/acquire/test_refusal_outputs.py`

**Interfaces:**
- Consumes: `result["refused_basins"]` records with `basin_id`, `mask`, `nodata_frac`, `area_km2`, `mean_slope` where present.
- Produces: `write_dnbr_outputs(..., refused=None, imagery=None)`. When `refused` non-empty: `refused_basins.geojson` + `refused_basins.csv` sidecars (columns `phase1_basin_id, nodata_frac, reason, area_km2, mean_slope`; NO score/rank/burn keys), banner line in every artifact header, `provenance.refused_count`/`n_basins_total` in `basins.geojson`, hatched layer on the dual-rank map. Always (all dNBR runs): `nodata_frac` column appended to ranking.csv rows; when `imagery={"sensor","pre_id","pre_date","post_id","post_date"}` is passed, one header line `# imagery: <sensor> pre <pre_id> (<pre_date>) -> post <post_id> (<post_date>)` (A21 obligation).
- Binding banner text (verbatim, spec §9): `"{N} of {M} basins could not be assessed (insufficient cloud-free imagery). Their hazard is UNKNOWN -- not low. Any refused basin could rank high if data existed; see refused_basins.csv."`

- [ ] **Step 1: Write failing tests** — build a tiny synthetic run through the EXISTING test fixture for `write_dnbr_outputs` (copy the fixture setup from `tests/acquire/test_dnbr_outputs.py`, which already constructs arm results + basins on a small grid; reuse its helpers verbatim). Assert:

```python
# (inside tests/acquire/test_refusal_outputs.py -- reuse test_dnbr_outputs.py's fixture helpers)
def test_zero_refused_is_projection_identical(tmp_path, small_run):
    """Permitted diffs vs pre-change output, exhaustively: the nodata_frac column, the
    imagery header (only when passed), refusal banner+sidecars (only when refused).
    With refused=None and imagery=None: identical except the appended nodata_frac column."""
    before = read_ranking_csv(small_run.out_dir)          # helper: header lines + rows
    out = write_dnbr_outputs(*small_run.args, refused=None)
    after = read_ranking_csv(small_run.out_dir)
    assert after.header_lines == before.header_lines       # no banner, no imagery line
    assert [r.drop("nodata_frac") for r in after.rows] == before.rows
    assert not (Path(small_run.out_dir) / "refused_basins.csv").exists()

def test_refused_sidecars_and_banner(tmp_path, small_run, refused_two):
    out = write_dnbr_outputs(*small_run.args, refused=refused_two)
    csv = (Path(small_run.out_dir) / "refused_basins.csv").read_text()
    assert "phase1_basin_id" in csv and "score" not in csv and "rank" not in csv
    header = ranking_header(small_run.out_dir)
    assert "hazard is UNKNOWN -- not low" in header
    gj = json.loads((Path(small_run.out_dir) / "basins.geojson").read_text())
    assert gj["provenance"]["refused_count"] == 2
    rj = json.loads((Path(small_run.out_dir) / "refused_basins.geojson").read_text())
    assert all("rank" not in f["properties"] and "score" not in f["properties"]
               for f in rj["features"])
```

- [ ] **Step 2: Implement.** Signature: `def write_dnbr_outputs(arm_a, arm_b, creek_nearest, out_dir, dem_path, *, validation_case, incised=False, subbasin_meta=None, refused=None, imagery=None):`. Refused geometry: reuse the exact mask→polygon code path `basins.geojson` features use (same rasterio.features shapes call, same CRS/transform) — factor it into a small `_mask_features(records, transform, crs, props)` helper used by both writers so geometry can never diverge. `nodata_frac` column: append to the ranking row dict from `b["nodata_frac"]` (present since Task 2; default `""` if absent for SBS-path safety — SBS path never calls this writer, but fail-safe). Banner/sidecars/provenance counts strictly under `if refused:`. Dual-rank map: `write_dual_rank_map(gj_path, ..., refused_gj_path=None)` draws refused features as gray cross-hatch with a legend entry `"refused -- insufficient data (hazard unknown)"` placed adjacent to the ramp.
- [ ] **Step 3: `run.py` print** — replace the bare basin count with: `print(f"{len(ranked)} ranked, {len(result.get('refused_basins', []))} refused (insufficient cloud-free data)")` at the existing print site (~run.py:59-65).
- [ ] **Step 4: Tests pass; full suite green (existing `test_dnbr_outputs.py` byte-expectations may need the nodata_frac column added to THEIR expected rows — that is the one sanctioned edit; anything else failing = your bug). Stage:**

```bash
git add src/outputs.py run.py tests/acquire/test_refusal_outputs.py tests/acquire/test_dnbr_outputs.py
```

---

### Task 4: `acquire.py` seam split (`stage_fire` / `attach_dnbr`)

**Files:**
- Modify: `acquire.py` (`build_fire_config`, ~272-315)
- Create: `tests/acquire/test_stage_fire_seam.py`

**Interfaces:**
- Produces: `stage_fire(bbox, out_dir, *, name="fire", buf_deg=None) -> fire_dict` (grid + DEM + buildings + manifest WITHOUT dnbr stats; `fire["dnbr"] = None`); `attach_dnbr(fire, dnbr_path) -> fire_dict` (runs `assert_raw_dnbr`, sets `fire["dnbr"]`, appends dnbr stats to the manifest). `build_fire_config(bbox, dnbr_path, out_dir, ...)` becomes `attach_dnbr(stage_fire(...), dnbr_path)` — byte-equivalent behavior and identical returned dict for existing callers.
- Consumes: nothing new. **Keep the CF-9 guard semantics: `attach_dnbr` validates before any dNBR use; `stage_fire` performs the A37 zone-coverage check before any fetch (move the existing check, don't duplicate).**

- [ ] **Step 1: Failing equivalence test** — monkeypatch the module-level fetchers exactly as `tests/acquire/test_acquire_fetch.py` does (`acquire.fetch_dem`, `acquire.fetch_buildings` are module-level per the CF-7 comment):

```python
def test_split_equals_wrapper(monkeypatch, tmp_path, tiny_raw_dnbr):
    calls = []
    monkeypatch.setattr(acquire, "fetch_dem", lambda bbox, grid, p: (calls.append("dem"), _touch(p))[1])
    monkeypatch.setattr(acquire, "fetch_buildings", lambda bbox, crs, p, buf_deg: (calls.append("bld"), (_touch(p), 0))[1])
    fire_a = acquire.build_fire_config(BBOX, tiny_raw_dnbr, tmp_path / "a", name="x")
    staged = acquire.stage_fire(BBOX, tmp_path / "b", name="x")
    assert staged["dnbr"] is None and calls.count("dem") == 2       # staged once per call
    fire_b = acquire.attach_dnbr(staged, tiny_raw_dnbr)
    keys = set(fire_a) | set(fire_b)
    assert {k: _norm(fire_a[k], "a") for k in keys} == {k: _norm(fire_b[k], "b") for k in keys}

def test_attach_dnbr_validates_first(tmp_path):
    staged = ...  # as above with monkeypatched fetchers
    with pytest.raises(GateAbort):                                  # CF-9 still bites
        acquire.attach_dnbr(staged, tmp_path / "not_a_dnbr.tif")
```

(`_norm` maps path values to their relative-to-out_dir form so the two out_dirs compare equal; `_touch` writes an empty placeholder file. `tiny_raw_dnbr`: reuse the raw-dNBR fixture already used by `test_acquire_fetch.py` / `test_dnbr_pipeline.py` for `assert_raw_dnbr`-passing input.)

- [ ] **Step 2: Implement the split.** Move lines: grid derivation + A37 coverage check + `fetch_dem` + `fetch_buildings` + manifest write (minus `dnbr_stats`) into `stage_fire`; `assert_raw_dnbr` + `fire["dnbr"] = dnbr_path` + manifest dnbr-stats append into `attach_dnbr` (manifest gains the stats by rewriting the same manifest file with the stats field filled — same final content as today). `build_fire_config` body becomes two calls; its docstring keeps the CF-9 line.
- [ ] **Step 3: Tests pass; full suite green (existing acquire tests unchanged). Stage:**

```bash
git add acquire.py tests/acquire/test_stage_fire_seam.py
```

---

### Task 5: `select(sensors=)` on the production selector

**Files:**
- Modify: `autoacquire/scene_select.py` (`select`, ~488-505)
- Create: `tests/acquire/test_select_sensors.py`

**Interfaces:**
- Produces: `select(bbox, *, ignition, containment, today=None, greenup_days=..., sensors=("S2", "Landsat"))`. Validation at entry: `sensors` must be a non-empty subset of `{"S2", "Landsat"}` else `GateAbort` (scene_select.py:303 branches `== "S2"` else-Landsat — a typo would silently query the wrong STAC). Loop becomes `for sensor in sensors:`. Default is byte-equivalent to today (S2-first, break at ~586 unchanged).

- [ ] **Step 1: Failing tests** — monkeypatch `scene_select._search_scenes` exactly as `tests/acquire/test_scene_select.py:246` does:

```python
def test_default_equals_explicit(monkeypatch, hermetic_pools):
    a = scene_select.select(BBOX, ignition=IGN, containment=CONT, today=TODAY)
    b = scene_select.select(BBOX, ignition=IGN, containment=CONT, today=TODAY,
                            sensors=("S2", "Landsat"))
    assert a == b

def test_single_sensor_restricts(monkeypatch, hermetic_pools):
    seen = []
    # wrap the patched _search_scenes to record the sensor argument
    ...
    scene_select.select(BBOX, ignition=IGN, containment=CONT, today=TODAY, sensors=("Landsat",))
    assert set(seen) == {"Landsat"}

@pytest.mark.parametrize("bad", [(), ("s2",), ("S2", "MODIS")])
def test_invalid_sensors_fail_loud(bad):
    with pytest.raises(GateAbort, match="sensors"):
        scene_select.select(BBOX, ignition=IGN, containment=CONT, today=TODAY, sensors=bad)
```

- [ ] **Step 2: Implement** (validation lines at function top; `("S2", "Landsat")` literal replaced by the param). Do NOT touch `stress_divergence.select_single_sensor` — its monkeypatch keeps working; note in the A41 draft that it can later be simplified (deferred, D0).
- [ ] **Step 3: Tests + full suite green. Stage:**

```bash
git add autoacquire/scene_select.py tests/acquire/test_select_sensors.py
```

---

### Task 6: `autoacquire/sweep.py` — the bounded sweep

**Files:**
- Create: `autoacquire/sweep.py`
- Create: `tests/acquire/test_sweep.py`

**Interfaces:**
- Consumes: `scene_select.select(..., sensors=)`, `dnbr_create.create_dnbr(pair, bbox, out_dir, name=)`, `acquire.stage_fire`/`attach_dnbr`, `pipeline.run_pipeline(fire, contour_m=)`, `outputs.write_dnbr_outputs(..., refused=, imagery=)`, `GateAbort.scope`.
- Produces:

```python
MAX_POST_SWAPS = 6   # owned A41 value; stress harness's 2 was a harness budget

def run_sweep(bbox, *, ignition, containment, out_dir, name="fire",
              greenup_days=None, max_post_swaps=MAX_POST_SWAPS,
              sensors=("S2", "Landsat"), contour_m=None, approve=False,
              select_fn=None, create_fn=None, stage_fn=None, attach_fn=None,
              pipeline_fn=None, write_fn=None) -> dict
```

Returns `{"status": "recommended"|"clean"|"degraded"|"aborted"|<selector passthrough>, "package", "attempts": [...], "chosen", "result_paths", "refused": [...], "message"?}`. The `*_fn` seams default to the real functions and exist for hermetic tests (repo monkeypatch convention).

- [ ] **Step 1: Write `autoacquire/sweep.py`**

```python
"""sweep.py -- bounded scene sweep (A41): recommended pair -> vetted alt_posts -> other
sensor, under ONE approval. First zero-refused attempt wins; else best by the frozen
score-blind key (fewest refused -> lowest total nodata -> earliest post). Winner's
artifacts are PROMOTED to out_dir; losers stay quarantined under attempts/."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

MAX_POST_SWAPS = 6   # A41 owned value


def _attempt_record(sensor, pre, post, outcome, refused=None, total=None, total_nodata=None):
    return {"sensor": sensor, "pre_id": pre.get("id"), "post_id": post.get("id"),
            "post_date": str(post.get("date")), "outcome": outcome,
            "refused_count": None if refused is None else len(refused),
            "n_basins_total": total, "total_nodata_frac": total_nodata}


def _selection_key(entry):
    # FROZEN score-blind (A41): coverage only, never ranking content.
    return (entry["record"]["refused_count"], entry["record"]["total_nodata_frac"],
            entry["record"]["post_date"])


def run_sweep(bbox, *, ignition, containment, out_dir, name="fire", greenup_days=None,
              max_post_swaps=MAX_POST_SWAPS, sensors=("S2", "Landsat"), contour_m=None,
              approve=False, select_fn=None, create_fn=None, stage_fn=None,
              attach_fn=None, pipeline_fn=None, write_fn=None):
    import acquire
    from autoacquire import dnbr_create, scene_select
    from src import outputs, pipeline as _pipeline
    from src.grids import GateAbort

    select_fn = select_fn or scene_select.select
    create_fn = create_fn or dnbr_create.create_dnbr
    stage_fn = stage_fn or acquire.stage_fire
    attach_fn = attach_fn or acquire.attach_dnbr
    pipeline_fn = pipeline_fn or _pipeline.run_pipeline
    write_fn = write_fn or outputs.write_dnbr_outputs

    kw = {} if greenup_days is None else {"greenup_days": greenup_days}
    first = select_fn(bbox, ignition=ignition, containment=containment,
                      sensors=(sensors[0],), **kw)
    if first["status"] != "recommended":
        return first                       # honest selector states pass through (B1)
    if not approve:
        return first                       # machine proposes, human disposes (B4)

    out_dir = Path(out_dir)
    fire = stage_fn(bbox, out_dir, name=name)          # ONCE; fire-scoped aborts surface here
    attempts, candidates, n_attempt = [], [], 0

    def _run_attempt(sensor, pair):
        nonlocal n_attempt
        adir = out_dir / "attempts" / f"attempt_{n_attempt:02d}"
        n_attempt += 1
        created = create_fn(pair, bbox, adir / "dnbr", name=name)
        afire = attach_fn({**fire, "out_dir": str(adir)}, created["dnbr_tif"])
        result = pipeline_fn(afire, contour_m=contour_m)
        if result.get("status") != "ranked":
            # terrain refusal etc.: DEM-deterministic -> fire-scoped, stop the sweep
            raise GateAbort(f"pipeline returned {result.get('status')!r} -- not scene-"
                            "recoverable; sweep stops (A41).", scope="fire")
        refused = result.get("refused_basins", [])
        total = len(refused) + len(result["arms"]["arm_a"]["basins"]) if "arms" in result \
            else len(refused) + len(result.get("basins", []))
        total_nodata = sum(b.get("nodata_frac", 0.0) for b in refused) + \
            sum(b.get("nodata_frac", 0.0) for b in result["arms"]["arm_a"]["basins"])
        paths = write_fn(result["arms"]["arm_a"], result["arms"]["arm_b"],
                         result["creek_nearest"], afire["out_dir"], afire["dem"],
                         validation_case=f"{name} (auto-acquire sweep, dNBR both-arms)",
                         incised=(result.get("terrain_mode") == "incised"),
                         subbasin_meta=result.get("subbasin_meta"),
                         refused=refused,
                         imagery={"sensor": pair["sensor"],
                                  "pre_id": pair["pre"].get("id"),
                                  "pre_date": str(pair["pre"].get("date")),
                                  "post_id": pair["post"].get("id"),
                                  "post_date": str(pair["post"].get("date"))})
        return {"dir": adir, "refused": refused, "paths": paths, "pair": pair,
                "record": _attempt_record(sensor, pair["pre"], pair["post"], "ranked",
                                          refused, total, round(total_nodata, 4))}

    def _sweep_sensor(sensor, package):
        base = package["pair"]
        posts = [base["post"]] + list(package["alternatives"]["post"])[:max_post_swaps]
        for post in posts:
            pair = {**base, "post": post}
            try:
                cand = _run_attempt(sensor, pair)
            except GateAbort as e:
                if getattr(e, "scope", "fire") == "fire":
                    raise
                attempts.append(_attempt_record(sensor, pair["pre"], post,
                                                f"abort: {str(e)[:140]}"))
                continue
            attempts.append(cand["record"])
            candidates.append(cand)
            if cand["record"]["refused_count"] == 0:
                return cand                # zero-refused wins outright
        return None

    winner = _sweep_sensor(sensors[0], first)
    if winner is None and len(sensors) > 1:
        try:
            second = select_fn(bbox, ignition=ignition, containment=containment,
                               sensors=(sensors[1],), **kw)
        except GateAbort as e:             # mid-sweep selector infra failure: sensor-scoped
            second = {"status": "aborted", "message": str(e)}
        if second.get("status") == "recommended":
            winner = _sweep_sensor(sensors[1], second)
        else:
            attempts.append({"sensor": sensors[1], "outcome":
                             f"selector: {second.get('status')} {second.get('message', '')[:100]}"})

    if winner is None:
        ranked = [c for c in candidates if c["record"]["refused_count"] is not None]
        if not ranked:
            return {"status": "aborted", "package": first, "attempts": attempts,
                    "message": "no attempt produced a ranking; see attempts."}
        winner = min(ranked, key=_selection_key)       # frozen score-blind key

    _promote(winner["dir"], out_dir)
    (out_dir / "sweep_attempts.json").write_text(json.dumps({
        "attempts": attempts,
        "chosen": {"sensor": winner["record"]["sensor"],
                   "pre_id": winner["record"]["pre_id"],
                   "post_id": winner["record"]["post_id"],
                   "post_date": winner["record"]["post_date"]},
        "selection": "chosen by coverage only (fewest refused -> lowest total nodata -> "
                     "earliest post); ranking content never consulted (A41)."}, indent=2))
    status = "clean" if winner["record"]["refused_count"] == 0 else "degraded"
    return {"status": status, "package": first, "attempts": attempts,
            "chosen": winner["record"],
            "refused": [{"phase1_basin_id": b["basin_id"],
                         "nodata_frac": b["nodata_frac"]} for b in winner["refused"]],
            "result_paths": {"out_dir": str(out_dir)}}


def _promote(attempt_dir, out_dir):
    """Winner's artifacts copied to the fire level; losers stay under attempts/ (the
    fire-level dir must hold exactly ONE coherent pair's artifacts -- A39/A40 purge rule)."""
    for p in Path(attempt_dir).iterdir():
        if p.is_file():
            shutil.copy2(p, Path(out_dir) / p.name)
        elif p.name == "dnbr":
            shutil.copytree(p, Path(out_dir) / "dnbr", dirs_exist_ok=True)
```

**Implementer note (Tier-2, flag if wrong):** the `total`/`total_nodata` computation assumes clean-basin records also carry `nodata_frac` (they do — Task 2 attaches it to every record) and that dNBR results carry `arms.arm_a.basins`. Verify against `run_pipeline`'s actual return keys at ~430-448 and adjust field access — NOT the selection-key semantics.

- [ ] **Step 2: Failing tests (`tests/acquire/test_sweep.py`)** — all hermetic via the `*_fn` seams; no network. Fake `select_fn` returns a canned package with 2 alt posts; fake `pipeline_fn` scripted per attempt:

```python
def _pkg(sensor, n_alts=2):
    mk = lambda i: {"id": f"{sensor}-{i}", "date": f"2026-07-{10+i:02d}"}
    return {"status": "recommended", "pair": {"sensor": sensor, "pre": mk(0), "post": mk(1),
            "metrics": {}}, "alternatives": {"pre": [], "post": [mk(2), mk(3)][:n_alts]}}

def _result(n_refused, n_clean=3):
    b = lambda i, f: {"basin_id": i, "nodata_frac": f, "mask": None}
    return {"status": "ranked", "terrain_mode": "range_front", "creek_nearest": None,
            "subbasin_meta": None,
            "refused_basins": [b(i, 0.5) for i in range(n_refused)],
            "arms": {"arm_a": {"basins": [b(100 + i, 0.0) for i in range(n_clean)]},
                     "arm_b": {"basins": []}}}

def test_zero_refused_wins_immediately(...):      # pipeline_fn -> _result(0); one attempt only
def test_walks_alternates_then_wins(...):         # scripted [2 refused, 0 refused]; 2 attempts
def test_sensor_fallback_engages(...):            # S2 attempts all refused, Landsat clean
def test_best_attempt_selection_key(...):         # all degraded: fewest refused wins; tie -> lower
                                                  # total nodata; tie -> earliest post_date
def test_selection_is_score_blind(...):           # mutate scores in results; chosen unchanged
def test_fire_scoped_abort_stops_sweep(...):      # pipeline_fn raises GateAbort(scope="fire")
                                                  # on attempt 1 -> raises, 1 attempt recorded
def test_attempt_scoped_abort_continues(...):     # create_fn raises GateAbort(scope="attempt")
                                                  # then clean -> status "clean", abort recorded
def test_zero_clean_everywhere_aborts(...):       # every attempt raises scope="attempt"
                                                  # zero-clean -> status "aborted" + attempts log
def test_gate_closed_without_approve(...):        # approve=False -> returns package untouched
def test_winner_promoted_and_attempts_json(...):  # write_fn writes marker files per attempt dir;
                                                  # assert fire-level dir holds winner's marker +
                                                  # sweep_attempts.json parses, scalars only
```

Write each with the fakes above (12–30 lines each; `write_fn` writes `(dir/"ranking.csv")` marker text naming the pair id so promotion is assertable).

- [ ] **Step 3: Implement fixes until green; full suite green. Stage:**

```bash
git add autoacquire/sweep.py tests/acquire/test_sweep.py
```

---

### Task 7: App wiring (`app.py`)

**Files:**
- Modify: `app.py` — `run_generated_screening` (~271-320), `_render_generate_panel` approve branch (~521-525), quicklook block (~500-519), map/banner rendering
- Modify: `tests/app/test_app_generate.py` (extend, existing patterns)

**Interfaces:**
- Consumes: `sweep.run_sweep` (Task 6), refused sidecars (Task 3).
- Produces: approve triggers the sweep (not the single pair); trail + degraded banner render from `st.session_state["screen"]`; a queued second Approve is a no-op; the pre-approval quicklook of a losing pair never renders above the winner.

- [ ] **Step 1: Replace `run_generated_screening`** with a sweep-backed version (same store-shape contract the panel already uses):

```python
def run_generated_screening(bbox_raw, sweep_inputs, *, name="frontend", contour_m=150.0):
    """Approve-gated sweep (A41): one approval covers the vetted family. NO st.* calls in
    here -- preemption safety (see SafeSessionState note below/main)."""
    from autoacquire.sweep import run_sweep
    out_dir = None
    try:
        bbox = validate_bbox(*bbox_raw)
        out_dir = Path(tempfile.mkdtemp(prefix="wws_sweep_"))
        sw = run_sweep(bbox, ignition=sweep_inputs["ignition"],
                       containment=sweep_inputs["containment"], out_dir=out_dir,
                       name=name, contour_m=contour_m, approve=True)
        if sw["status"] not in ("clean", "degraded"):
            return {"kind": "refusal", "message": sw.get("message", sw["status"]),
                    "attempts": sw.get("attempts", [])}
        # read EVERYTHING before rmtree (bytes, not paths -- the dir dies in finally)
        fire_dir = Path(sw["result_paths"]["out_dir"])
        payload = {"kind": "result", "sweep_status": sw["status"],
                   "attempts": sw["attempts"], "chosen": sw["chosen"],
                   "refused": sw["refused"],
                   "ranking_csv": (fire_dir / "ranking.csv").read_bytes(),
                   "basins_geojson": json.loads((fire_dir / "basins.geojson").read_text()),
                   "sweep_attempts_json": (fire_dir / "sweep_attempts.json").read_text()}
        rgj = fire_dir / "refused_basins.geojson"
        payload["refused_geojson"] = json.loads(rgj.read_text()) if rgj.exists() else None
        for extra in ("map_dual_rank.png",):
            p = fire_dir / extra
            if p.exists():
                payload[extra.replace(".", "_")] = p.read_bytes()
        ql = sorted(fire_dir.glob("dnbr_*_quicklook.png"))
        payload["winner_quicklook"] = ql[0].read_bytes() if ql else None   # re-READ, never re-render
        return payload
    except GateAbort as e:
        return {"kind": "refusal", "message": str(e)}
    except Exception as e:
        return {"kind": "error", "message": f"unexpected {type(e).__name__}: {e}"}
    finally:
        if out_dir is not None:
            shutil.rmtree(out_dir, ignore_errors=True)
```

(The panel currently passes `package["pair"]` — change the call site to pass `{"ignition": ..., "containment": ...}` from the same inputs the selector ran with; they are already in scope in the panel. Keep the existing store pattern verbatim: `screen["inputs"] = inputs_key; screen_box.clear(); screen_box.update(screen)`.)

- [ ] **Step 2: Panel changes** in `_render_generate_panel` approve branch (~521-525): (a) idempotence guard before the spinner: `if screen_box.get("inputs") == inputs_key and screen_box.get("kind") in ("result", "refusal", "error"): st.info("Already ran for these inputs -- change inputs to re-run."); else: <spinner + sweep>`; (b) after the sweep stores its result: `gen_box.pop("burnmap", None)` (the pre-approval quicklook may belong to a losing pair; render `payload["winner_quicklook"]` instead, captioned with `chosen` pair ids/dates); (c) render the trail as `st.dataframe(payload["attempts"])` inside an expander titled `f"Sweep: {len(attempts)} attempt(s), chose {chosen['sensor']} {chosen['post_id']}"`; (d) when `sweep_status == "degraded"`: `st.warning` with the binding banner text (Task 3 verbatim string, N/M filled from `refused` + basin counts); (e) refused hatching on the folium map: add `refused_geojson` features via `folium.GeoJson(style_function=lambda f: {"fillColor": "#888888", "color": "#555555", "dashArray": "4", "fillOpacity": 0.35})` with tooltip `"REFUSED -- insufficient cloud-free data (hazard UNKNOWN, not low)"`, added AFTER the ranked layer.
- [ ] **Step 3: Extend `tests/app/test_app_generate.py`** using its existing harness patterns: (a) approve path calls `run_sweep` once (monkeypatch `app.run_generated_screening`'s `run_sweep` import via `monkeypatch.setattr("autoacquire.sweep.run_sweep", fake)`) and stores `kind == "result"`; (b) second render with same `inputs_key` does NOT call the fake again (idempotence); (c) degraded payload renders banner text (assert the warning string appears in the captured markup per that file's existing assertion style); (d) `burnmap` key is popped after a completed sweep.
- [ ] **Step 4: Full suite green. Stage:**

```bash
git add app.py tests/app/test_app_generate.py
```

---

### Task 8: CLI (`autoacquire/autoacquire_run.py`)

**Files:**
- Modify: `autoacquire/autoacquire_run.py`
- Modify: `tests/acquire/test_autoacquire_run.py` (extend)

**Interfaces:**
- Produces: `--max-swaps N` (default 6; `0` = exactly today's single-attempt run INCLUDING no sensor fallback), `--contour-m M` (default 150.0), swept default path via `run_sweep`; `autoacquire_result.json` slim filter strips `"result"` in addition to `"pipeline"`/`"masks"`; attempt records serialize (scalars only — Task 6 shapes guarantee it).

- [ ] **Step 1: Failing tests** — extend the existing CLI tests' monkeypatch style: `--max-swaps 0` routes to the legacy `run_autoacquire` single-attempt path (assert the sweep fake NOT called); default routes to `run_sweep` with `max_post_swaps=6` and `contour_m` passed; result JSON of a fake degraded sweep round-trips through `json.loads` with no `"result"` key and no ndarray reprs (`"array(" not in text`).
- [ ] **Step 2: Implement:** add the two `argparse` args; in `__main__`, `if args.max_swaps == 0: out = run_autoacquire(...)` (unchanged legacy call, passing nothing new) `else: out = run_sweep(tuple(args.bbox), ignition=..., containment=..., out_dir=Path(args.out), name=args.name, greenup_days=args.greenup_days, max_post_swaps=args.max_swaps, contour_m=args.contour_m, approve=args.approve)`. Slim line becomes `slim = {k: v for k, v in out.items() if k not in ("pipeline", "masks", "result")}`. Print block: for `"clean"/"degraded"` print `chosen` pair + `f"{len(out.get('refused', []))} basin(s) refused"`; for `"aborted"` print the message + attempt count.
- [ ] **Step 3: Full suite green. Stage:**

```bash
git add autoacquire/autoacquire_run.py tests/acquire/test_autoacquire_run.py
```

---

### Task 9: Docs + governance drafts (owner-gated items clearly marked)

**Files:**
- Modify: `README.md` (~44-45, ~140, ~277), `docs/ALGORITHMS.md` (~82), `validation/stress_divergence.py` (comment only, `compare_rankings` ~94)
- Create: `docs/superpowers/A41_DRAFT.md` (owner transcribes to vault DECISIONS; repo mirror owner-owed)
- **OWNER-GATED, do not stage without explicit owner ok in the session:** append-only note at the END of `validation/reports/P2_PREREGISTRATION.md`

- [ ] **Step 1: Doc edits.** README ~44-45 and ~140: after the "within-fire ordinal ranking of detected basins" sentences, append: `Basins whose dNBR NoData/cloud exceeds the frozen 20% bar are refused individually ("insufficient data" -- hazard unknown, not low) and listed in refused_basins.csv; the ranking covers clean basins only (A41).` README ~277 (auto-acquire section): add one sentence: `On a per-basin cloud refusal the tool automatically retries the vetted alternate scenes, then the other sensor, under the one approval; the attempt trail is written to sweep_attempts.json.` `docs/ALGORITHMS.md` ~82: same refusal sentence after the ranking description. `compare_rankings` comment above ~94: `# A41 note: refused basins are absent from ranking rows, so only_a/only_b now conflate "not detected" with "refused for cloud" -- read sweep_attempts.json/refused sidecars before interpreting.`
- [ ] **Step 2: Write `docs/superpowers/A41_DRAFT.md`** — full A41 entry text per spec §11 clauses (1)-(7): Context (three refusals, stress evidence, reviewer passes), Decision clauses (partition semantics w/ flowed arm verbatim-unchanged; B4 gate scope pair→bounded vetted family; frozen score-blind selection key; MAX_POST_SWAPS=6 owned; GateAbort.scope; nodata_frac column; NOT-result-blind provenance block), Reasoning + honest cost (degraded maps are new surface area; mitigations = banner/hatching/sidecars), Status (`Accepted 2026-08-0X (owner-directed); tests: no prior fatal-arm lock existed -- new locks added (test_nodata_guard_locks.py, test_partition_refused.py)`), relation arrows (amends the creeks=None extension of A20/A21; extends B4; A39 renumbering preserved), and a Decision-log row line. Also draft the two-sentence pre-reg appendix note in this file for the owner: `> [A41 amendment note, 2026-08-0X -- appended, nothing above edited]: The flowed-basin sentence in §4 stands verbatim. Enforcement for creeks-absent (non-validation) fires is now specified by DECISIONS A41: per-basin refusal at the same frozen bar, never a whole-run abort. Not a correction; owner-directed, not result-blind.`
- [ ] **Step 3: Full suite green. Stage the non-gated files:**

```bash
git add README.md docs/ALGORITHMS.md validation/stress_divergence.py docs/superpowers/A41_DRAFT.md
```

- [ ] **Step 4: Ledger final update** — mark all tasks done, list owed owner actions: (1) review + commit staged work, (2) transcribe A41 to vault DECISIONS + log row, (3) approve + apply the pre-reg appendix note, (4) repo DECISIONS mirror, (5) optional live fire re-run (Laguna is the acceptance case: expect Landsat-clean or degraded-with-hatching, never a dead end).

---

## Self-review notes (done at write time)

- Spec coverage: §2 F1→T6, F2→T2; §3 seams→T2/T4/T5; §4 promotion→T6; §5 classification→T2 tags + T6 handling; §6 id spaces→T2 note + T3 column; §7 selection key→T6 `_selection_key` + score-blind test; §8 trail→T6 json; §9 wording→T3/T7 verbatim; §10 tests→T1-T8; §11 governance→T9. Upload-path partition (spec §2 F2 last lines) is covered by T2 alone — `run_screening` needs no change (it already passes result arms to the writer; extend its call with `refused=result.get("refused_basins")` — **add that one-line change to T7 Step 1's scope**, done in the code above via the same writer path? NO: upload path calls `write_dnbr_outputs` directly at app.py:179 — T7 Step 2 must also add `refused=result.get("refused_basins", [])` there. Folded into T7.
- Line numbers are anchors, not gospel — verified against HEAD `fb8b3ee`; re-grep if drifted.
- Type consistency: `refused` records = basin dicts with `basin_id`/`nodata_frac`/`mask`; JSON forms use `phase1_basin_id` naming at every serialization boundary (T3 csv/geojson, T6 return).
