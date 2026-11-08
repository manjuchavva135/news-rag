# 027: Old-pipeline baseline comparison

**Status:** draft
**Mode:** solo
**Runs:** waits for 025: needs the retrieval test and search script

## Summary

Proves the new pipeline beats the old one on the same pages. The old system's export bundle (from
AWS) is loaded into separate baseline stores, and the same retrieval test runs on both, restricted to
the Jan–Jun 2026 pages both systems have, using the same plain hybrid search (grill A-20, A-45, D-21).

## Functional requirements

- **FR-1** - `docker/docker-compose.baseline.yml` (project `newsrag-baseline`, host network) runs a second Postgres (port 5452), Qdrant (6353/6354) and OpenSearch with ICU (9220/9320), named volumes `newsrag-baseline-*`. `uv run newsrag baseline up|down|status` like ticket 002. Settings `baseline_postgres_dsn` (default `postgresql://newsrag:newsrag@localhost:5452/newsrag_baseline`), `baseline_qdrant_url` (`http://localhost:6353`), `baseline_opensearch_url` (`http://localhost:9220`) default to these, and `newsrag db sql` gains `--dsn baseline`.
- **FR-2** - `uv run newsrag baseline load <bundle.tar.gz> [--force]` reads the old stage-9 bundle format (`manifest.json`, `postgres/<table>.jsonl`, `qdrant/points.jsonl` with vectors; reference `/home/admin1/RAG/pipeline/stage09_validate/run.py` `export_snapshot` — its `restore_snapshot` OpenSearch call is broken and is not copied) by streaming the tar (`tarfile` mode `r|gz`, members read one by one, nothing extracted to disk; `crops/` members are skipped). It creates schema `old` in the baseline Postgres with the DDL of `issues`, `pages`, `articles`, `chunks` copied from `/home/admin1/RAG/docker/postgres/init/01_warehouse.sql` (other tables are skipped), loads those rows, loads Qdrant points into baseline collection `chunks_old` (vectors as stored, 1024-dim BGE-M3), and builds baseline OpenSearch index `chunks_old` from `old.chunks` text plus `old.articles.headline` using ticket 024's mapping and script rule. If any of these already exist it refuses unless `--force` (which drops and reloads them). It prints the manifest counts and the loaded counts; on any mismatch it exits 1.
- **FR-3** - Page mapping: `src/newsrag/baseline/pages.py` parses every old `page_id` (`ap-<edition>-<YYYY-MM-DD>#p<NN>` or `#<section><NN>`) into (date, EDITION upper-cased, section, NN) and looks up `ledger.pages` for that date, edition and section with `page_num = NN` (any `page_end`, so old `#p08` of a spread maps to `..._main_08-09`). It writes `<reports_dir>/baseline-page-map.csv` (`old_page_id,new_page_id,found_in_new`). `search(store="baseline")` (ticket 025 FR-1) is implemented here: dense search on `chunks_old` with BGE-M3 and BM25 on `chunks_old`, RRF as for `new`; each `Hit` gets `text` from `old.chunks`, `headline` and `pub_date` from `old.articles`, and `page_ids` = `[mapped new page_id]` (empty if unmapped). `--restrict-pages` never applies to the baseline store.
- **FR-4** - `uv run newsrag baseline build-set` builds the committed comparison set `data/eval/retrieval-baseline-v1.jsonl` with ticket 025's builder (`--n 100`, seed 7), sampling only new stories that have a printing on a mapped page (`found_in_new`). `uv run newsrag baseline calibrate --n 50 --seed 9` takes 50 of those stories and, for each, the old chunks on its mapped page; it reports `calibration=<pct>` = share of stories for which at least one old chunk passes the ticket-025 hit rule against the story (proves the rule can credit old Sarvam text). `uv run newsrag baseline compare` refuses (exit 1, `calibration below 80%: <pct>`) if calibration < 80%; otherwise it runs `eval retrieval --set data/eval/retrieval-baseline-v1.jsonl` on `baseline` and on `new` with `--restrict-pages` = all mapped new page ids, and writes `<reports_dir>/baseline-compare.json` `{set, calibration, old:{recall_at_10, mrr, n}, new:{recall_at_10, mrr, n}, new_beats_old: bool}` (same n for both; `new_beats_old` = new recall@10 > old recall@10) and prints `old recall@10=<x> new recall@10=<y> new_beats_old=<bool>`.
- **FR-5** - Clarifications (second review):
  - `baseline compare` writes its two runs to `<reports_dir>/baseline-compare-old.json` and `baseline-compare-new.json` (never the default `retrieval-<store>.json`).
  - `baseline calibrate` writes `<reports_dir>/baseline-calibration.json` `{calibration, n, sampled_from, at}`; `compare` reads it and refuses with `run 'newsrag baseline calibrate' first` if it is missing. Calibration samples from the stories of `data/eval/retrieval-baseline-v1.jsonl`.
  - Page map: when both `..._NN` and `..._NN-MM` exist, an old page maps to both (`new_page_ids` list in the CSV; `Hit.page_ids` gets both).
  - `build-set` calls ticket 025's `build_retrieval_set(..., story_filter=...)`; a short set (< 100) fails `build-set` with exit 1 and the count.
  - `baseline load` also writes `<raw_root>/baseline/manifest-loaded.json` with the manifest counts and the bundle path (used by ticket 031's preflight). The baseline backend is registered through `newsrag.search.register_backend("baseline", ...)` (ticket 025).

## Non-functional requirements

- **NFR-1** - Loading the 6.3 GB bundle takes < 30 min.

## Affected areas

- `docker/docker-compose.baseline.yml` (new); `src/newsrag/baseline/` (new); `src/newsrag/cli.py`, `src/newsrag/config.py`, `src/newsrag/search/hybrid.py` (baseline store wiring) (changed)
- `tests/test_baseline_pages.py` (new)
- `src/newsrag/evals/retrieval.py` (uses the story filter), `tests/test_baseline_load.py` (new)

## Don't touch

- The new stores and their volumes; `/home/admin1/RAG/**`; the AWS system.

## Out of scope

- Re-running the old pipeline; comparing answers (028 shows old answers side by side).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Baseline source | the old stage-9 export bundle copied from AWS by the user | grill A-20 |
| D-2 | Fairness | same plain hybrid search, new store restricted to the same pages, window questions only | grill A-45 |
| D-3 | Pass rule | new recall@10 > old recall@10 | grill D-21 |
| D-4 | Question set | a dedicated 100-question set from stories on pages both systems have | the old corpus is only 1,345 AP pages; window questions from other pages can't be compared |
| D-5 | Cross-OCR fairness | calibration ≥ 80% before comparing | the same article read by two OCR engines must be creditable for the comparison to mean anything |

## Open questions

None.
