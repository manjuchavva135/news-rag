# 029: Pipeline orchestrator and incremental ingest - Results

## What was built

- `src/newsrag/orchestrator.py` (new): `Orchestrator` / `RunOptions`, topological `stage_order`,
  corpus steps (`editions build` + `editions apply` after `folio`, `aliases build` after
  `enrich_core`) gated by fingerprints in `ledger.corpus_steps`, the register fingerprint
  (`.unpacked` markers + PDF counts per stem), `stale_dates()` → `chunk` reset per date after each
  corpus step, cached `version` properties dropped after each corpus step, the run file, and
  SIGINT/SIGTERM handling (`stop_on_signals`).
- `src/newsrag/orchestrator_cli.py` (new) + one `SUB_APPS` line in `src/newsrag/cli.py`:
  `newsrag run [--until S] [--only a,b] [--pages-file F] [--dates D1,D2] [--dry-run] [--tag T]`.
- `src/newsrag/ledger/sql/005_corpus_steps.sql` (new): `corpus_steps(step PK, fingerprint, finished_at)`.
- `src/newsrag/models/lock.py`: records `gpu_lock` holder names (kind `lock`) per process;
  `lock_holders()` exported from `newsrag.models`. `models_loaded` = `loaded_profiles()` + `lock_holders()`.
- `src/newsrag/config.py` `# --- orchestrator ---`: `runs_dir` (default `{reports_dir}/runs`),
  `run_stop_grace_s` (600), `run_requeue_older_than` (`2h`).
- `README.md`: "Run the pipeline" section.
- `tests/test_orchestrator.py` (new): 29 tests, fake stages, throw-away schema, temp dirs.

`runner.py` and all stage implementations are unchanged. The runner's `stop_event` from
ticket 003 was used as is.

## Decisions taken while building

- `--until S`: the topological prefix up to S. A corpus step runs only if some planned stage
  comes after its input stage, so `--until folio` does not run `editions build`.
- `--dates` is turned into a page-id filter (intersected with `--pages-file` when both are
  given). The runner then derives the dates for the date stages.
- Repeat guard: at most `max_attempts + 2` cycles. A cycle "has work" when a pass processed or
  failed an item, or a corpus step ran.
- `ingest unpack` returning non-zero does not stop the run, but the final exit code becomes 1.
- Run file name: `<YYYYmmddTHHMMSS.ffffffZ>.json` (microseconds, so names sort in time order).

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| `uv run ruff check .` / `ruff format --check .` | PASS | - |
| `pytest -q -m "not gpu and not data and not stores"` | PASS (922 passed) | - |
| `pytest -q -m stores tests/test_orchestrator.py` | PASS (29 passed) | - |
| E-1 dry run | PASS: FR-1 order, `eligible=<n>` per stage, `stage status` identical before/after, 5 s | - |
| E-2 fake-stage end to end | PASS: all 12 named tests plus extras | - |
| E-3 dev-sample `--until group_final` | PASS: exit 0, folio done=20, `interrupted: false`, `models_loaded: ["indic-ocr"]` | `evidence/e3-run.log`, `evidence/e3-run-file.json` |
| E-4 nothing to do (2024-12-02) | PASS: step 1 brought the date to `index` (24.6 min, exit 0); step 2 took 6.1 s; step 3 printed `[]` | `evidence/e4-step1.log`, `evidence/e4-step1-run-file.json`, `evidence/e4-step2.log`, `evidence/e4-step2-run-file.json`, `evidence/e4-step3.txt` |
| E-5 incremental archive | PASS (`test_incremental_archive`) | - |
| E-6 FR-7 clarifications | PASS (5 selected tests) | - |

## Notes

- The first real run (E-3) found two new archives in `archive_dir` (`October - 2026.zip`,
  `November-2026.zip`), unpacked them and registered **1,041 new pages**. They are in the ledger
  now, waiting for `render`; only the dev-sample pages were processed. `data/editions.yaml` was
  not changed by the `editions build` that ran.
- E-4 step 1 also ran `aliases build` inside the filtered run, because its fingerprint changed
  (FR-7).
- FR-7's unfiltered "everything done" timing after the *full* dev-sample set is indexed has not
  been run: only one date (2024-12-02) is indexed so far. The per-date check (E-4) is the timed
  evidence for now.
