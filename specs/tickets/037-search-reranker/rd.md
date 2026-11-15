# 037: Rerank the fused candidates with a cross-encoder

**Status:** draft
**Mode:** solo
**Runs:** waits for 036: edits the same `search/hybrid.py` (`search_detailed`)

## Summary

Search usually finds the right story but ranks it low: for English questions it is in the top 10 for
76% and at rank 1 for only 36%; for Telugu 95% and 73%. Reciprocal-rank fusion only merges two rank
lists; it never reads the text against the question. This ticket adds a multilingual cross-encoder
that scores the top fused candidates against the query and reorders them before the top `k` are
returned.

## Functional requirements

- **FR-1** - Model profile. `models.yaml` gets
  `- name: bge-reranker-v2-m3 / hf_id: BAAI/bge-reranker-v2-m3 / family: bge / kind: rerank / dtype: float16`.
  `Profile.kind` accepts `"rerank"`; a rerank profile needs neither `dim` nor the vLLM fields.
- **FR-2** - Loading. New in `src/newsrag/models/client.py` (exported from `newsrag.models`):
  `class Reranker` with `score(query: str, texts: Sequence[str], batch_size: int = 16) -> list[float]` (a `sentence_transformers.CrossEncoder`, `max_length = search_rerank_max_tokens`, one float per text, higher is better), and the context manager `reranker(name: str, device: str) -> Iterator[Reranker]`. Like `cpu_embedder`, it is loaded outside the one-model slot of `use()` and takes no GPU lock itself; weights come from `hub.download(profile, quiet=True)`. `device` is `"cpu"` or `"cuda"`; another value raises `ValueError("device must be 'cuda' or 'cpu', not <x>")`; a profile that is not `rerank` raises `ValueError("<name> is <kind>, not a rerank profile")`.
- **FR-3** - Device. Setting `search_rerank_device: str = "auto"` (`auto | cpu | cuda`). `auto` uses the device of the query embedder that `hybrid` has open: `cuda` when it was opened by `hybrid._embedder` through `models.use` (this process then holds the GPU lock), `cpu` when the caller supplied it with `use_embedder` (the answer path next to a local LLM). The reranker is loaded on the first reranked search, kept until `hybrid.close()` and closed with the embedder; a change of device closes and reloads it.
- **FR-4** - Reranking in `search_detailed` (store `new`). `rerank: bool | None = None` (None → setting `search_rerank`, default `True`). After the RRF fusion, the first `search_rerank_top` (30) fused chunks are scored against the query **as typed** (not `query_text`) on the passage `headline + "\n" + text` from `warehouse.chunks`, and ordered by score, best first (ties keep the RRF order). Fused chunks after the first `search_rerank_top` follow in RRF order. The top `k` of that list are returned. A reranked hit's `Hit.score` is the cross-encoder score; the others keep the RRF score. `SearchResult` gets `reranked: int` (how many were scored; 0 when off). `search(..., rerank=…)` passes it through.
- **FR-5** - Failure. If loading or scoring raises, the search returns the RRF order, `reranked = 0`, and one line `rerank failed: <ExceptionName>: <message>` is written to stderr once per process.
- **FR-6** - CLI. `uv run newsrag search "<query>" --no-rerank` searches with `rerank=False`. With reranking, the line `reranked: <n>` is printed before the hits.
- **FR-7** - Settings in the `# --- search ---` block: `search_rerank: bool = True`, `search_rerank_model: str = "bge-reranker-v2-m3"`, `search_rerank_top: int = 30`, `search_rerank_max_tokens: int = 512`, `search_rerank_batch: int = 16`, `search_rerank_device: str = "auto"`, `search_rerank_on_cpu: bool = False`.
- **FR-8** - Before/after record, as in ticket 035 FR-9 with folder `specs/tickets/037-search-reranker/evidence`. `results.md` has a table, before and after, of retrieval `by_lang.te|en|te_latn`: `recall_at_1`, `recall_at_5`, `recall_at_10`, `mrr`; `median_search_ms`; and short `stories.te|latin`: `recall_at_1`, `recall_at_10`, plus `names.latin.mention`. It also records the median search time of one extra run with `NEWSRAG_SEARCH_RERANK_DEVICE=cpu` over the first 20 questions of the set (`head -20` into a temporary set file).
- **FR-9** - Clarifications (gap check):
  - **CPU.** New setting `search_rerank_on_cpu: bool = False`. When `search_rerank_device` is `auto` and the device resolves to `cpu` (the answer path next to a local LLM), nothing is reranked unless this is `True` (`reranked = 0`, no stderr line). `search_rerank_device = cpu` always reranks (FR-8's timing run). `auto` with no embedder open resolves to `cpu`.
  - **Explicit device.** `cuda` or `cpu` is used as given. Entering or leaving `use_embedder`, and `close()`, close a loaded reranker.
  - **Tests.** `hybrid.use_reranker(reranker)` (a context manager like `use_embedder`) makes searches in its scope use the given object whatever the device rule says.
  - **After a failure.** Reranking stays off for the rest of the process (no second load) until `hybrid.close()`.
  - **Other stores.** `rerank` is passed only to the `new` backend; `--store baseline` prints no `reranked:` line.
  - **Output order.** `translit:` line, `names:` lines, `reranked: <n>` (printed whenever reranking is on for store `new`, also `reranked: 0`), then the hits.
  - **dtype.** `float16` on `cuda`, `float32` on `cpu` (the rule of `_load_sentence_transformer`).
  - **A chunk without a `warehouse.chunks` row.** It is scored on its headline alone and counts in `reranked`.

## Non-functional requirements

- **NFR-1** - No-regression guard: `by_lang.te.recall_at_10` in `after-retrieval.json` ≥ the value in `before-retrieval.json` − 0.02.
- **NFR-2** - On `cuda`, `median_search_ms` in `after-retrieval.json` ≤ the value in `before-retrieval.json` + 700.
- **NFR-3** - `Reranker` is injected in tests (a fake with `score`), so the unit tests need no GPU and no download.

## Affected areas

- `models.yaml` - the rerank profile (changed)
- `src/newsrag/models/profiles.py`, `src/newsrag/models/client.py`, `src/newsrag/models/__init__.py` - kind `rerank`, `Reranker`, `reranker()` (changed)
- `src/newsrag/search/hybrid.py` - rerank step, device rule, `close()` (changed)
- `src/newsrag/search/cli.py` - `--no-rerank`, `reranked:` line (changed)
- `src/newsrag/config.py` - `# --- search ---` block (changed)
- `tests/test_search.py`, `tests/test_models_client.py` (changed)
- Base commit: before the first change the worker writes `git rev-parse HEAD` to `specs/tickets/037-search-reranker/evidence/base-commit.txt`. Every "against the base commit" comparison and every `before-*.json` file refers to that commit; the diffs in eval.md compare it with the working tree.
- Test names: new tests are named so that every `pytest -k` selection in eval.md matches at least one of them.

## Don't touch

- `models.use()` and the GPU lock: one vLLM or embed profile per process, as today.
- The embed profiles and `embed_model`; the stores; `src/newsrag/baseline/**`; `src/newsrag/answer/**`; `data/eval/**`.
- Ticket 035's filters and ticket 036's name resolution: both still run before the rerank.

## Out of scope

- A bake-off between rerankers, or fine-tuning one.
- Reranking in the baseline store.
- Scoring whole stories instead of chunks.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Which reranker | `BAAI/bge-reranker-v2-m3` | chosen by the ticket writer, not by the user: multilingual (built on the same base as bge-m3, which covers Telugu), small (568M parameters), runs through `sentence-transformers`, which is already installed. The before/after table shows whether it should stay on |
| D-2 | How many candidates | the first 30 fused chunks | keeps the added time small; the right story is in the top 10 for 76–95% already |
| D-3 | Text scored | the query as typed against `headline + text` | the name-expanded `query_text` of 036 is a search aid, not the question |
| D-4 | Where it runs | on the GPU with the GPU query embedder; off by default when the embedder is on the CPU (`search_rerank_on_cpu`) | next to a local LLM the GPU is taken; 30 passages on the CPU would add seconds to every answer, and staff already called 45 s too slow. Chosen by the ticket writer as the safest option: the user can turn it on after seeing FR-8's CPU timing |
| D-5 | A reranker error | fall back to the RRF order with one stderr line | a search must not fail because of an optional step |
| D-6 | Done bar | before/after report plus NFR-1 and NFR-2 | user chose "report, with a no-regression guard" |

## Open questions

None.
