# 027: Old-pipeline baseline comparison - Evaluation

## Setup

- Prerequisites (user): the old bundle copied to `/home/admin1/raw/baseline/` (e.g. `scp <aws>:~/RAG/var/export/rag-snapshot-*.tar.gz /home/admin1/raw/baseline/`). Ticket 025 done; all 2026-01-01..2026-06-30 pages indexed in the new stores.
- Start: `uv sync && uv run newsrag stores up && uv run newsrag baseline up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Baseline stores up (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag baseline status; echo "exit=$?"`
- **Expected:** three `ok` lines and `exit=0`; `uv run newsrag stores status` still shows the new stores `ok`.

### E-2: Load the bundle (covers FR-2, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag baseline load $(ls /home/admin1/raw/baseline/rag-snapshot-*.tar.gz | tail -1)`
- **Expected:** exits 0; manifest counts equal loaded counts (28,355 chunks, 1,345 pages per handover; whatever the manifest says is the bar); real time < 30 min.

### E-3: Page map (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_baseline_pages.py -v`
  2. `python3 -c "import csv;r=list(csv.DictReader(open('/home/admin1/raw/reports/baseline-page-map.csv')));print(len(r), sum(x['found_in_new']=='True' for x in r))"`
- **Expected:** tests pass (including `ap-bgnoprsvaclt-2026-02-19#p01` → `2026-02-19_BGNOPRSVACLT_main_01`, `#fea02` → `_fea_02`, and a spread `#p08` → `..._main_08-09`); step 2 prints ≈ 1,345 and a found count ≥ 95% of it.

### E-4: Compare (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag baseline build-set && uv run newsrag baseline calibrate --n 50 --seed 9`
  2. `uv run newsrag baseline compare`
  3. `python3 -c "import json;d=json.load(open('/home/admin1/raw/reports/baseline-compare.json'));print(sorted(d), d['old']['n']==d['new']['n'], d['new']['n']>=100)"`
- **Expected:** step 1 prints `calibration=<pct>` with pct ≥ 80; step 2 prints the FR-4 line; step 3 prints `['calibration', 'new', 'new_beats_old', 'old', 'set']`, `True`, `True`. (Whether `new_beats_old` is true is the phase-1 bar checked in ticket 031.)

## Edge cases

### E-5: Bundle not extracted into the repo (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_baseline_load.py -k "streams_without_extracting or skips_crops or refuses_without_force or count_mismatch_exits_1"`
- **Expected:** 4 passed (a small synthetic bundle; the streaming test asserts mode `r|gz` and that no files are written outside the stores).

### E-6: Second-review clarifications (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_baseline_pages.py tests/test_baseline_load.py -k "compare_output_paths or calibration_file_required or spread_maps_both or short_set_fails or manifest_loaded_written"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-2, E-5 |
| FR-3 | E-3 |
| FR-4 | E-4 |
| NFR-1 | E-2 |
| FR-5 | E-6 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (`baseline-compare.json`, logs) saved in `evidence/` and listed in `results.md`
