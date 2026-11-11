# 025: Retrieval test set and hybrid search script

**Status:** done
**Mode:** solo
**Runs:** waits for 024: searches the stores

## Summary

A thin, plain hybrid search over the stores (dense + BM25 + reciprocal-rank fusion) and a ~300-question
Telugu/English retrieval test with recall@10 and MRR. It is the yardstick for the embedding bake-off
(026), the old-vs-new comparison (027) and the customer-question check (028). The correct answer is
matched by page plus text overlap, so the old store (different ids) can be scored fairly (grill A-31).

## Functional requirements

- **FR-1** - `src/newsrag/search/hybrid.py: search(query, k=10, store="new", filters=None, generation=None, qdrant_collection=None, embed_profile=None) -> list[Hit]`. For store `new`, `generation` defaults to the active generation (`warehouse.active_generation`, ticket 024) and `embed_profile` to the model named in it; `qdrant_collection` overrides only the dense side (used by ticket 026). It embeds the query with that profile's `query_prefix` (via `newsrag.models.use`), takes the top 50 from Qdrant and the top 50 BM25 hits from OpenSearch (`multi_match` over `headline_te^2, text_te, headline_en^2, text_en`), fuses with RRF (c = 60), and returns the top k as `Hit(chunk_id, story_id, score, page_ids, text, headline, pub_date)`, with `text`/`headline` read from `warehouse.chunks` of that generation. Store `baseline` (ticket 027 implements it; until then it raises `baseline stores not configured`) uses BGE-M3 and the `old` schema. Optional filter `page_ids` restricts both retrievers to chunks whose `page_ids` intersect the list (the `page_ids` field is indexed by ticket 024).
- **FR-2** - `uv run newsrag search "<query>" [--k 10] [--store new|baseline]` prints one line per hit: rank, score, pub_date, first page_id, headline (60 chars).
- **FR-3** - `uv run newsrag eval build-retrieval [--n 300] [--seed 7] [--out data/eval/retrieval-v1.jsonl]` builds a committed test set from the stories in the active generation at build time (kinds news/editorial/notice_tender): exactly 100 from print dates 2026-01-01..2026-06-30 (`in_baseline_window: true`) and n − 100 spread evenly over the other year-months present (if a stratum is short, all its stories are used and `short: <stratum>` is printed). Order: stories shuffled with the seed within each stratum. For each, the local LLM (`enrich_model`, temperature 0) writes one question answerable only from that story — 70% in Telugu, 30% in English (assigned by seed) — with the instruction to paraphrase. A question sharing a run of ≥ 4 consecutive words with the headline, or an empty/invalid reply, is regenerated (up to 3 tries); then the story is replaced by the next story of the same stratum. Each line: `{qid, lang, question, story_id, gold_page_ids (all printings' pages), gold_text (the full story text), pub_date, in_baseline_window}`. Ticket 026 uses v1 (built before the full run); ticket 031 builds `retrieval-v2.jsonl` on the full corpus with the same command for the final numbers.
- **FR-4** - `uv run newsrag eval retrieval [--set FILE] [--store new|baseline] [--only-window] [--restrict-pages FILE] [--qdrant-collection C] [--out F]`: for each question, `search(k=10)`; a hit is correct when its `page_ids` intersect `gold_page_ids` **and** ≥ `hit_overlap` (0.3) of the hit text's character 5-grams (NFC, whitespace removed) occur in `gold_text` (the same rule for both stores, so different chunking and ids don't matter). `--restrict-pages` = a file with one new page_id per line, applied as the `page_ids` filter (store `new` only). Prints `recall@10=<x> mrr=<x> median_search_ms=<x> n=<n>` and writes JSON `{store, set, n, recall_at_10, mrr, median_search_ms, by_lang:{te:{n, recall_at_10, mrr}, en:{...}}}` (default out `<reports_dir>/retrieval-<store>.json`). `median_search_ms` is measured warm (from the second query on), including the Postgres text lookup.
- **FR-5** - Clarifications (second review):
  - Setup data: the stores must hold every date from 2026-04-01 to 2026-04-07 (in-window dates) plus 2025-06-10 and 2020-04-02. The 100 window stories come from those April 2026 dates; the rest from the other indexed year-months, shortfalls allowed. E-4 expects `n` = the number of lines in `retrieval-v1.jsonl`.
  - `eval retrieval` takes `--embed-profile P` (passed to `search`); ticket 026 passes the candidate.
  - Builder function `newsrag.evals.retrieval.build_retrieval_set(n, seed, out, window_n=100, story_filter=None)` backs the CLI; ticket 027 calls it with a filter.
  - This ticket adds no baseline settings: `search(store="baseline")` raises `baseline stores not configured` until ticket 027 registers a baseline backend with `newsrag.search.register_backend("baseline", ...)`.
  - `--only-window` keeps only questions with `in_baseline_window: true`; `n` and `by_lang` then count only those.

## Non-functional requirements

- **NFR-1** - One search takes < 500 ms after the model is loaded (median over the test set).

## Affected areas

- `src/newsrag/search/` (new), `src/newsrag/evals/retrieval.py` (new), `data/eval/retrieval-v1.jsonl` (new, committed)
- `src/newsrag/config.py` (baseline_* settings), `src/newsrag/cli.py` (changed)
- `tests/test_search.py`, `tests/test_eval_retrieval.py` (new)

## Don't touch

- Store contents (read only).

## Out of scope

- Reranking, query rewriting, answer generation (phase 2; a thin answer script is ticket 028).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Questions | ~300, 70% Telugu / 30% English, written by a local LLM in its own words | grill A-31 |
| D-2 | Correct hit | page overlap + text overlap | grill A-31: comparable across old/new ids |
| D-3 | Metrics | recall@10 and MRR on plain hybrid | grill A-31, A-45 |
| D-4 | Window | ≥ 100 questions from Jan–Jun 2026 for the old-vs-new comparison | grill A-20, A-45 |
| D-5 | Hit rule | page overlap + the hit's text contained in the gold story (≥ 0.3) | a hit on any chunk of the story counts; works for old chunks too |
| D-6 | Test-set timing | v1 now (for the bake-off), v2 on the full corpus in ticket 031 | the corpus grows from a few dates to all 21k pages |

## Open questions

None.
