# 024: Embed and index into stores - Results

Branch: ticket/024-embed-index
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-a17f2397286238e63
Port: 8701 (not used, no web page)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| setup (uv sync, db init) | PASS | evidence/setup.log |
| ruff check | PASS | evidence/automated-ruff.log |
| unit tests | PASS (865 passed) | evidence/automated-unit.log |
| stores tests (tests/test_index.py) | PASS (16 passed) | evidence/automated-stores.log |
| E-1 | PASS | evidence/E-1.log, E-1-run.log, E-1-validate.log |
| E-2 | PASS (286 414 1322 1000 417) | evidence/E-2.log |
| E-3 | PASS | evidence/E-3.log |
| E-4 | PASS | evidence/E-4.log |
| E-5 | PASS | evidence/E-5.log |
| E-6 | PASS | evidence/E-6.log |

E-1 logged rates (NFR-1, >= 200): 2024-12-26 175 chunks 261 chunks/s; 2025-06-10 125 chunks 305
chunks/s; 2025-08-02 132 chunks 258 chunks/s. Author's own runs: 288-307 chunks/s.
`index validate`: three `ok` lines, exit=0; `index collections`: `bge-m3__v1 qdrant=432 opensearch=432 postgres=432 active`.

The evidence logs were produced before the post-review fixes below (a test-only change to two
tests, a lock and a docstring); lint, the unit tests and `pytest -m stores tests/test_index.py`
(16 passed) were re-run afterwards.

## Attempts
- Attempt 1: all checks pass. Reviewer findings fixed: `test_db_init_runs_both_migrations` now
  uses throw-away schema names (it touched the real schemas); `activate_if_first` takes a lock
  (`stage run --workers N`); `activate` documents its three-step order; the real-index district
  test only compares dates that are indexed.

## Changes
- `src/newsrag/config.py` - `warehouse_schema`, `store_alias_prefix` (stores block); `# --- index ---` block: `embed_batch_size`, `index_generation_tag`, `index_write_batch`, `index_script_share`
- `src/newsrag/stages/index.py` - `IndexStage` (embed, then write Postgres / Qdrant / OpenSearch in parallel; timings in detail)
- `src/newsrag/stages/registry.py` - registers `index`
- `src/newsrag/embed/` - naming, record builders (payload, OpenSearch docs, script routing), store operations, generations (activate / collections / validate), `newsrag index` CLI
- `src/newsrag/warehouse/` - schema migration `001_init.sql`, db helpers, pure row builders, transactional date replace, entity sync, `newsrag warehouse init`
- `src/newsrag/stores/cli.py` - `newsrag db init` (ledger init + warehouse init)
- `src/newsrag/cli.py` - registers `warehouse` and `index`
- `tests/test_index.py` - 28 tests (12 unit, 16 stores, 2 of those also `data`)

## Notes for the router
- eval.md names the dates 2026-04-03 / 2025-06-10 / 2020-04-02, but `chunk` is only done for 2024-12-26, 2025-06-10 and 2025-08-02: E-1 used those. The real index now holds those three dates (generation `bge-m3__v1`, active).
- `models.yaml` already had `dtype: float16` for bge-m3; no change needed.
- Deviation: `warehouse.chunks` has an extra `pub_date` column (re-index replaces a date's chunks even when their story has gone; validate counts by it).
- Entity sync (FR-6) runs once per `stage run` per changed `entities.json` (mtime/size), not once per date, to avoid upserting ~100k rows 3000 times; it still upserts and prunes.
- Throughput note: the timed section is embedding plus the three writes of the date (collection bootstrap, entity sync, activation and model load/warm-up excluded). The three writes run in parallel threads. Dates have only ~125-175 chunks, so per-store fixed costs (~0.15 s) matter; margin over 200/s is about 30-50%.
- Not done (reviewer minor notes): the stage version hashes settings but not the `models.yaml` profile (same gap as `chunk`); `index validate` cannot detect an unknown `--generation` or chunks lost from all three stores at once (it follows the spec: `ok <date> 0`).
