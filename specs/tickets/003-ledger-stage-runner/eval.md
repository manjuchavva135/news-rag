# 003: Pipeline ledger and stage runner - Evaluation

## Setup

- Start: `uv sync && uv run newsrag stores up && uv run newsrag ledger init`
- Base URL: n/a
- Test account: n/a
- Seed / reset: the tests create their own schema; for E-2..E-6 use `NEWSRAG_LEDGER_SCHEMA=evalcheck` and drop it at the end with `uv run newsrag db sql "drop schema evalcheck cascade"`.

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0
- `uv run pytest -q -m stores tests/test_ledger.py tests/test_runner.py` exits 0

## Acceptance checks

### E-1: Migrations idempotent, tables exist (covers FR-1, FR-2)

- **Type:** CLI
- **Steps:**
  1. `NEWSRAG_LEDGER_SCHEMA=evalcheck uv run newsrag ledger init && NEWSRAG_LEDGER_SCHEMA=evalcheck uv run newsrag ledger init`
  2. `uv run newsrag db sql "select table_name from information_schema.tables where table_schema='evalcheck' order by 1"`
- **Expected:** step 1 exits 0 both times; step 2 prints `date_runs`, `pages`, `quarantine`, `schema_migrations`, `stage_runs`.

### E-2: Runner processes, resumes and versions (covers FR-3, FR-4, FR-5, FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_runner.py`
- **Expected:** all tests pass. The test file must contain at least these named tests (the tester checks with `grep -c "def test_" tests/test_runner.py` ≥ 21 and greps each name):
  - `test_eligibility_requires_dependencies_done`
  - `test_version_change_makes_stage_and_downstream_eligible`
  - `test_failure_recorded_and_retried_until_max_attempts`
  - `test_exception_does_not_stop_run`
  - `test_pages_file_and_page_filters`
  - `test_date_stage_waits_for_all_pages_of_date`
  - `test_date_stage_depending_on_date_stage`
  - `test_new_page_reruns_date_stage`
  - `test_running_row_never_eligible`
  - `test_attempt_reset_on_version_change`
  - `test_dependency_must_be_at_current_version`
  - `test_version_for_per_item`
  - `test_hooks_called_once_and_close_on_error`
  - `test_preflight_unavailable_exits_2_without_db`
  - `test_on_final_failure_called_at_max_attempts`
  - `test_force_and_skipped_count`
  - `test_all_quarantined_date_not_eligible`
  - `test_multiple_stage_names_run_in_order`
  - `test_change_propagates_three_stages` (A→B→C; B's version unchanged; A rerun makes C eligible)
  - `test_open_failure_restores_row_and_exits_2`
  - `test_stop_event_grace_marks_interrupted`

### E-3: No double processing (covers NFR-1, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_runner.py -k concurrent`
- **Expected:** passes; the test starts 2 OS processes on 50 dummy pages and asserts 50 `done` rows, each `attempt = 1`, and the dummy handler was called exactly 50 times.

### E-4: Status and failures output (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_runner.py -k status_output`
- **Expected:** passes; the test registers a dummy stage, runs it on 3 pages where 1 raises `ValueError("boom")`, and asserts `stage status` prints a line matching `dummy\s+done=2 failed=1 running=0 eligible=1` and `stage failures dummy` prints one line ending in `boom`.

### E-5: Stale requeue (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_runner.py -k stale`
- **Expected:** passes; a `running` row with `started_at` 3 h ago becomes `failed` after `requeue-stale --older-than 2h`, and a row started 10 min ago stays `running`.

### E-6: Artifacts and quarantine helpers (covers FR-8, FR-9)

- **Type:** CLI
- **Steps:**
  1. `uv run python -c "from newsrag.stages.base import artifact_path; print(artifact_path('ocr','indic-ocr@v1/p2','2026-04-01_FH_main_01'))"`
  2. `uv run pytest -q -m stores tests/test_ledger.py -k quarantine`
- **Expected:** step 1 prints `/home/admin1/raw/work/ocr/indic-ocr_v1_p2/2026-04-01_FH_main_01.json`; step 2 passes (a quarantined page is never eligible for any stage).

## Edge cases

### E-7: Non-TTY output (covers FR-10)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_runner.py -k non_tty`
- **Expected:** passes; captured output has lines `done <key>` and `FAILED <key>: <msg>` and no progress-bar control characters.

### E-8: Tests never touch the real ledger (covers NFR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag db sql "select count(*) from ledger.pages"` before and after `uv run pytest -q -m stores` (run `uv run newsrag ledger init` first so the table exists)
- **Expected:** the two counts are equal, and `uv run newsrag db sql "select count(*) from information_schema.schemata where schema_name like 'test_%'"` returns `0` after the run.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-1 |
| FR-3 | E-2 |
| FR-4 | E-2 |
| FR-5 | E-2, E-3 |
| FR-6 | E-2, E-4 |
| FR-7 | E-5 |
| FR-8 | E-6 |
| FR-9 | E-6 |
| FR-10 | E-7 |
| NFR-1 | E-3 |
| NFR-2 | E-8 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-6 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (command logs) saved in `evidence/` and listed in `results.md`
