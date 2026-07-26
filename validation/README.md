# validation/

The behavior oracles and the one-off phase drivers that produced them. Nothing here is imported
by `src/` — these are run by hand, and their outputs are frozen into `reports/` and into the
behavior locks in `tests/`.

## Layout

```
gate.py                        # the reconstructed Week-0 Montecito oracle (AUC 0.9722 / 36 basins / 44.7273 km²)
reports/                       # frozen write-ups — read-only behavior anchors, never edited to make a run pass (A16)
data/  out/  p3_southfork/     # inputs and regenerated artifacts (gitignored; the locks are the oracle, not these files)
```

Phase drivers, grouped by their filename prefix:

| Prefix | Phase | Scripts |
|---|---|---|
| `p2_` | P2 — dNBR input path + swap test | `p2_acquire_dnbr` · `p2_run_dnbr` · `p2_3_swap_test` |
| `p3_` | P3 — generalization to South Fork 2024 | `p3_acquire_dem` · `p3_acquire_assets` · `p3_acquire_dnbr` · `p3_ingest_dnbr` · `p3_manifest_and_checks` |
| `cf11_` | CF-11 — independent flow-model cross-check | `cf11_pyflwdir_crosscheck` |
| `a39_` | A39 — incised sub-basin concordance | `a39_southfork_concordance` |

## Why the scripts are flat

They derive the repo root from `__file__` and one crosses phases (`p3_acquire_dnbr` imports from
`p2_acquire_dnbr`), so nesting them costs real path edits. The test suite does not cover this
directory, so those edits would be unverifiable — and a silently-broken reproduction script is
exactly the failure mode this project designs against. The prefixes carry the grouping instead.

## reports/

| File | What it is |
|---|---|
| `VALIDATION_REPORT.md` | the SBS validation write-up — the Montecito behavior oracle |
| `DNBR_VALIDATION_FINDING.md` | the dNBR input-swap finding (P2.3) |
| `P2_PREREGISTRATION.md` | P2.1/A20 firewall — the frozen dNBR constants, fused to code by `tests/acquire/test_dnbr_frozen_constants.py` |
| `P3.1_PREREGISTRATION.md` | P3.1 pre-registration for the South Fork generalization run |
| `P3.2_BUILD_REPORT.md` | P3.2 build report + acquisition checks |
