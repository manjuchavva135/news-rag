# 003: Pipeline ledger and stage runner - Results

Built in the main checkout on branch `ticket-003` (from main after 001 + 002). The stores ran
under `sg docker` (this session predates the `docker` group change). The real `ledger` schema is
now created by `newsrag ledger init` and is still empty.

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| `uv run ruff check .` / `ruff format --check .` | PASS | `evidence/auto-ruff-*.log` |
| unit tests | PASS, 30 passed | `evidence/auto-pytest-unit.log` |
| `-m stores tests/test_ledger.py tests/test_runner.py` | PASS, 30 passed | `evidence/auto-pytest-stores.log` |
| E-1 migrations idempotent, 5 tables | PASS (1 then 0 new migrations) | `evidence/E-1.log` |
| E-2 runner tests: 26 `def test_`, all 21 named tests present | PASS, 26 passed | `evidence/E-2.log` |
| E-3 2 processes × 50 pages | PASS: 50 done, attempt 1, 50 handler calls, split 24/26 | `evidence/E-3.log` |
| E-4 status / failures output | PASS | `evidence/E-4.log` |
| E-5 requeue-stale | PASS | `evidence/E-5.log` |
| E-6 artifact path + quarantine | PASS: `/home/admin1/raw/work/ocr/indic-ocr_v1_p2/2026-04-01_FH_main_01.json` | `evidence/E-6.log` |
| E-7 non-TTY output | PASS | `evidence/E-7.log` |
| E-8 real ledger untouched, no `test_%` schemas left | PASS: 0 → 0, 0 schemas | `evidence/E-8.log` |

The suite was also run 3 more times in a row (33/33 store tests each time). The TTY progress bar
(count, %, rate, elapsed, ETA) was checked once under a pseudo-terminal with `script`.

## Where I read the spec a particular way

- **Claim lock:** the claim takes `pg_try_advisory_xact_lock(hashtext(<schema>:<stage>),
  hashtext(<page_id or date>))` instead of `SELECT ... FOR UPDATE SKIP LOCKED` on `pages`.
  - It gives the same try-lock / skip semantics per item.
  - A row lock on `pages` would also make a runner of a *different* stage silently skip a page
    whose claim happens to overlap.
  - Eligibility is checked again under the lock, as FR-5 requires.
- **`--force`** makes matching items eligible once whatever their own status, even `running`. It
  does not bypass quarantine or unmet dependencies (there would be no input to work on).
  Attempts keep counting when the version and inputs are unchanged.
- **`open()` / `close()`:** `close()` runs once at the end of every run in which `open()` was
  called, including after item errors, a stop or a failed `open()`. A run with nothing to claim
  calls neither.
- **Date stages:** for a page-stage dependency, `input_versions` records that stage's `version`.
  The per-page versions and `finished_at` go into `pages_fingerprint`, which also covers every
  active page's `(page_id, revision)`. So adding or re-registering a page reruns the date stage
  even when it has no page dependency.
- **A page stage may only depend on page stages.** The runner raises `TypeError` otherwise.
- **`stage run` exit codes:** it exits 0 when items fail (they are reported in the summary
  line), 2 on `StageUnavailable` or an unknown stage name.
- **Skipped counts:** an item that another runner claims first counts as `skipped` only under a
  filter, as do unknown page ids (each also gets a stderr line).

## Changes outside the listed areas

- **`src/newsrag/stores/pg_store.py`:** the pool now has a `reset` hook (`RESET ALL`) so a
  ledger `search_path` never leaks to other pool users. `test_conn_search_path_and_no_leak`
  covers it.
- **New modules:**
  - `src/newsrag/ledger/cli.py`, `src/newsrag/stages/cli.py` (the per-module CLI convention)
  - `src/newsrag/stages/eligibility.py` (the pure FR-4 rules)
  - `tests/_dummy_stages.py` (dummy stages, which also serve as the subprocess script for E-3)
- **Helpers for later tickets:**
  - `db.upsert_page(...)` and `db.fetch_all/fetch_one`
  - `base.versioned(prefix, settings, files)` and `base.write_json_atomic(path, data)`
  - `runner.list_eligible(stage, filters)`
  - fixtures `ledger` (throw-away schema) and `registry` (empty REGISTRY)

## Performance note

Listing eligible items loads every page plus every stage row of the stage and its ancestors
into memory once per `stage run` / `stage status`. That's fine for tens of thousands of pages.
If it gets slow at full-corpus size (031), prefilter in SQL.

## Out of scope / don't touch

No real stage was registered (REGISTRY is empty) and no orchestrator was built. `/home/admin1/RAG`
and the store containers' config are unchanged.
