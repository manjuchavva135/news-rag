# 029: Pipeline orchestrator and incremental ingest - Evaluation

## Setup

- Prerequisites: ticket 024 done (all stages exist).
- Start: `uv sync && uv run newsrag stores up && uv run newsrag ledger init`
- Base URL: n/a
- Test account: n/a
- Seed / reset: the tests use their own ledger schema and temp dirs.

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0
- `uv run pytest -q -m stores tests/test_orchestrator.py` exits 0

## Acceptance checks

### E-1: Order and dry run (covers FR-1, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag run --dry-run`
- **Expected:** lists the stages in the FR-1 order (each stage after all stages it depends on), each with `eligible=<n>`, and processes nothing (`stage status` unchanged before/after).

### E-2: Fake-stage end to end (covers FR-1, FR-2, FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_orchestrator.py -v`
- **Expected:** all pass, including `test_runs_until_no_work`, `test_model_loaded_once_per_pass`, `test_corpus_steps_after_inputs_done`, `test_run_file_written`, `test_interrupt_writes_run_file_and_exits_130`, `test_requeue_stale_first`, `test_zero_eligible_stage_not_opened`, `test_corpus_step_skipped_when_fingerprint_same`, `test_register_skipped_when_unchanged`, `test_stage_unavailable_stops_run_exit_1`, `test_only_and_until_semantics`, `test_dry_run_changes_nothing`.

### E-3: Real dev-sample run (covers FR-1, FR-2, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag run --pages-file data/dev-sample.txt --until group_final`
  2. `ls -t /home/admin1/raw/reports/runs/ | head -1`
- **Expected:** exits 0; the newest run file has per-stage counts and `interrupted: false`.

## Edge cases

### E-4: Nothing to do (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag run --dates $(grep -v '^#' data/dev-sample.txt | cut -c1-10 | sort -u | head -1) --until index` (bring one date fully to index)
  2. `time uv run newsrag run --dates <same date> --until index` (second time)
  3. `python3 -c "import json,glob;f=sorted(glob.glob('/home/admin1/raw/reports/runs/*.json'))[-1];print(json.load(open(f))['models_loaded'])"`
- **Expected:** step 2 real time < 2 min; step 3 prints `[]`.

### E-5: Incremental archive (covers NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_orchestrator.py -k incremental_archive`
- **Expected:** passes (N new pages → N new render rows, old pages untouched, affected dates' date stages eligible).

### E-6: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_orchestrator.py -k "filtered_run_runs_changed_corpus_steps or stale_dates_reset_only or versions_reevaluated or overrides_in_fingerprint or fingerprint_after_success"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-2, E-3 |
| FR-2 | E-2, E-3 |
| FR-3 | E-1 |
| FR-4 | E-2, E-3 |
| FR-5 | E-2 |
| FR-6 | E-4 |
| NFR-1 | E-5 |
| FR-7 | E-6 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (run files, logs) saved in `evidence/` and listed in `results.md`
