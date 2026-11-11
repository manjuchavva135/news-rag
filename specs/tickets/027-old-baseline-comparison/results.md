# 027: Old-pipeline baseline comparison - Results

Built in the main checkout (no worktree), on `main`. **Not done:** E-2, E-3 (step 2) and E-4 need
inputs this machine doesn't have yet (see Blocked).

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| ruff check | PASS | evidence/automated-ruff.log |
| unit tests | PASS (922 passed) | evidence/automated-unit.log |
| E-1 baseline stores up | PASS: three `ok`, `exit=0`; new stores still `ok` | evidence/E-1.log |
| E-2 load the bundle | BLOCKED: no bundle in `/home/admin1/raw/baseline/` | - |
| E-3 page map | step 1 PASS (12 passed); step 2 BLOCKED (needs the loaded bundle and 2026 pages in the ledger) | evidence/E-3-tests.log |
| E-4 compare | BLOCKED (needs E-2 and indexed Jan–Jun 2026 stories) | - |
| E-5 streaming, crops, --force, count mismatch | PASS (4 passed) | evidence/E-5.log |
| E-6 second-review clarifications | PASS (5 passed) | evidence/E-6.log |
| stores-marked tests (real baseline stores, throw-away schema/collection) | PASS (8 passed, none skipped) | evidence/stores-tests.log |

## Blocked
1. **The old bundle.** Copy it from AWS:
   `scp <aws>:~/RAG/var/export/rag-snapshot-*.tar.gz /home/admin1/raw/baseline/`
2. **New-side pages.** `ledger.pages` covers 2024-12-01..2025-12-31 only (3,497 pages), and the
   new stores hold 3 print dates. The page map can't reach ≥ 95% found, and `build-set` can't get
   100 questions, until the 2026-01-01..2026-06-30 pages are registered and processed through
   `index` (ticket 029's orchestrator, or stage by stage).

Once both are in place:
```
uv run newsrag baseline load /home/admin1/raw/baseline/rag-snapshot-<stamp>.tar.gz
uv run newsrag baseline build-set && uv run newsrag baseline calibrate --n 50 --seed 9
uv run newsrag baseline compare
```

## Changes
- `docker/docker-compose.baseline.yml` (new): project `newsrag-baseline`, host network, Postgres
  5452, Qdrant 6353/6354, OpenSearch ICU 9220/9320, volumes `newsrag-baseline-*`.
- `src/newsrag/baseline/` (new):
  - `stores.py`: baseline clients and pings.
  - `load.py`: streams the bundle (`tarfile` mode `r|gz`, nothing extracted, `crops/` and other
    tables skipped); schema `old` with the old DDL (`sql/old_warehouse.sql`, issues/pages/
    articles/chunks copied from `RAG/docker/postgres/init/01_warehouse.sql`); rows by `COPY`;
    Qdrant `chunks_old` with vectors as stored; OpenSearch `chunks_old` from `old.chunks` +
    `old.articles.headline` with ticket 024's mapping and `os_document` script rule; refuses
    unless `--force`; compares manifest vs loaded counts (exit 1 on any mismatch); writes
    `<raw_root>/baseline/manifest-loaded.json` on success. Writes go through a `Sink` (tests use
    a fake one).
  - `pages.py`: old page id parsing and the map (`baseline-page-map.csv` with
    `old_page_id,new_page_id,found_in_new,new_page_ids`; a page maps to both `_NN` and `_NN-MM`
    when both exist).
  - `search.py`: the `baseline` search backend (BGE-M3 query vectors, BM25, RRF; text, headline,
    pub_date from `old.*`; `page_ids` = mapped new page ids; a page filter is refused).
  - `compare.py`: `build_set` (025's builder with `story_filter`, short set → exit 1),
    `calibrate` (`baseline-calibration.json`), `compare` (refuses without / below 80%
    calibration; runs to `baseline-compare-old.json` / `-new.json`; new restricted to every
    mapped new page; `baseline-compare.json`).
  - `cli.py`: `newsrag baseline up|down|status|load|map-pages|build-set|calibrate|compare`.
- `src/newsrag/search/hybrid.py`: registers `baseline` (lazy import of `newsrag.baseline.search`).
- `src/newsrag/stores/cli.py`: `compose_up` / `compose_stop` / `report_status` shared by
  `stores` and `baseline`; `db sql --dsn new|baseline|<dsn>`.
- `src/newsrag/stores/{pg,qdrant,os}_store.py`: `ping()` takes an optional address.
- `src/newsrag/config.py`: `# --- baseline ---` block.
- `src/newsrag/cli.py`: registers `baseline`.
- Tests: `tests/test_baseline_load.py`, `tests/test_baseline_pages.py` (new);
  `tests/test_search.py` restores the `baseline` backend after the tests that replace it.

## Notes
- `calibration` is stored and printed as a percent (`calibration=86.0`); `compare` refuses with
  `calibration below 80%: <pct>`.
- `newsrag baseline map-pages` (extra) rebuilds the page map on its own; `load` and `build-set`
  also write it.
- Docker needs the `docker` group: in a shell without it, run `sg docker -c "uv run newsrag baseline up"`.
