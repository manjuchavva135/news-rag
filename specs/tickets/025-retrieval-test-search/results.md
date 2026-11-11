# 025: Retrieval test set and hybrid search script - Results

Built in the main checkout (no worktree), on `main`.

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| ruff check | PASS | evidence/automated-ruff.log |
| unit tests | PASS (887 passed) | evidence/automated-unit.log |
| E-1 fusion and hit rule | PASS (26 passed, incl. the stores tests) | evidence/E-1.log |
| E-2 search CLI | PASS (5 lines) | evidence/E-2.log |
| E-3 build the test set | PARTIAL: `200 0 0.7` (`short: window` printed) | evidence/E-3-build.log, E-3-check.log |
| E-4 run the test | PASS for the set that exists: `recall@10=0.9950 mrr=0.8236 median_search_ms=21.0 n=200` | evidence/E-4.log, evidence/retrieval-new.json |
| E-5 paraphrase guard | PASS | evidence/E-5.log |
| E-6 second-review clarifications | PASS (5 passed) | evidence/E-6.log |

## The setup data is not there (E-3)
The setup needs indexed dates 2026-04-01..07 and 2020-04-02. The stores hold only 2024-12-26,
2025-06-10 and 2025-08-02 (432 chunks, 240 news/editorial/notice_tender stories). The archives in
`/home/admin1/data` are now `2024.zip`, `2025.zip`, `October - 2026.zip` and `November-2026.zip`.
None of them holds Jan–Jun 2026 or 2020 pages, so the baseline window (grill A-20, A-45) has no
pages in this corpus. The builder printed `short: window`, giving 200 questions and 0 in the window.
Tickets 027 and 031 depend on that window.

## Notes
- recall@10 = 0.995 is near the ceiling: the corpus has only 432 chunks and the questions name
  specific people, numbers and dates. It is too easy to tell embedding models apart (026) until
  more dates are indexed. English questions find the story (recall 1.0) but rank it lower
  (MRR 0.59 vs 0.92 for Telugu).
- How strata fill: `n − window_n` is spread over the year-months by water-filling. A month with fewer
  stories than an even share gives all it has, prints `short: <month>`, and the other months take
  up the rest.
- Language: exactly round(0.7 × questions) are Telugu, assigned by the seed. A replacement story
  keeps its slot's language. Retries tell the model why its last question was rejected (copied the
  headline, wrong script, empty), because asking again at temperature 0 with the same prompt
  would give the same reply.
- `gold_text` = headline + "\n" + story text. Chunk text never includes the headline (it sits in
  the prefix).
- Settings added: `# --- search ---` (`search_candidates`, `search_rrf_c`, `search_bm25_fields`)
  and `# --- evals ---` (`retrieval_*`, including the window dates). No baseline settings (FR-5).
- `newsrag search` is a sub-app whose callback is the command. A small `TyperGroup` subclass lets
  options follow the query.
- The query embed model stays loaded across searches until `newsrag.search.close()`. Opening a
  different profile closes the previous one first (026).
- Searching while the text model still held the GPU lock: the first `eval retrieval` run right after
  the build printed nothing (stderr was discarded). The rerun was clean.

## Changes
- `src/newsrag/search/` (new): `hybrid.py` (Hit, RRF, Qdrant/OpenSearch retrievers, backend
  registry, `search`), `cli.py` (`newsrag search`)
- `src/newsrag/evals/` (new): `retrieval.py` (hit rule, metrics, builder, scoring), `cli.py`
  (`newsrag eval build-retrieval | retrieval`)
- `src/newsrag/config.py`: search and evals blocks, `retrieval_set_file` under repo_root
- `src/newsrag/cli.py`: registers `search` and `eval`
- `data/eval/retrieval-v1.jsonl` (new, 200 questions)
- `tests/test_search.py`, `tests/test_eval_retrieval.py` (new, 26 tests, 5 of them `stores`)
