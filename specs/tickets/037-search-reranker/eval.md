# 037: Rerank the fused candidates with a cross-encoder - Evaluation

## Setup

- Prerequisite: tickets 035 and 036 are merged. Network access to huggingface.co for the first download of `BAAI/bge-reranker-v2-m3` (about 2.3 GB, no licence to accept).
- Start: `export PATH=$HOME/.local/bin:$PATH && uv sync && uv run newsrag stores up && uv run newsrag models status` (prints `GPU free`)
- Base URL: n/a
- Test account: n/a
- Seed / reset: none. `T=specs/tickets/037-search-reranker/evidence`

## Automated checks

- `uv run ruff check . && uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

- Every `pytest -k` selection below must run at least one test (pytest exit code 5, "no tests ran", is a failure)
- `cat specs/tickets/037-search-reranker/evidence/base-commit.txt` prints one commit hash (the base commit, see rd.md "Affected areas")

## Acceptance checks

### E-1: Profile and loader (covers FR-1, FR-2, NFR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_models_client.py tests -k "rerank"`
  2. `uv run python -c "from newsrag.models import get_profile;print(get_profile('bge-reranker-v2-m3').kind)"`
- **Expected:** step 1 passes, including: a `rerank` profile loads without `dim`; `reranker("bge-m3", "cpu")` raises `bge-m3 is embed, not a rerank profile`; `reranker(name, "gpu")` raises `device must be 'cuda' or 'cpu', not 'gpu'`; `Reranker.score` returns one float per text from a fake cross-encoder. Step 2 prints `rerank`.

### E-2: Rerank order and fallback (covers FR-3, FR-4, FR-5, FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_search.py -k "rerank"`
- **Expected:** all pass, with a fake reranker, including: the first `search_rerank_top` fused chunks are reordered by score and the rest follow in RRF order; equal scores keep the RRF order; the fake receives the query as typed and `headline + "\n" + text`; `Hit.score` is the fake's score for reranked hits; `rerank=False` and `search_rerank=False` call no reranker and give `reranked == 0`; a reranker that raises gives the RRF order, `reranked == 0` and one `rerank failed:` line on stderr for two searches; `auto` gives `cuda` with the module's own embedder and `cpu` inside `use_embedder`; `close()` closes the reranker; `auto` on the CPU with `search_rerank_on_cpu=False` reranks nothing and with `True` reranks; after one failure the loader is not called again; `store="baseline"` receives no `rerank`; a chunk without a warehouse row is scored on its headline.

### E-3: The real model on the GPU (covers FR-2, FR-4, FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag search "Who won the Jubilee Hills by-election?" --k 5 | grep "^reranked:"`
  2. `uv run newsrag search "Who won the Jubilee Hills by-election?" --k 5 --no-rerank | grep -c "^reranked:"`
  3. `uv run newsrag models status`
- **Expected:** step 1 prints `reranked: 30`; step 2 prints `0`; step 3 prints `GPU free` (nothing left loaded).

### E-4: Before/after numbers, guard and time (covers FR-8, NFR-1, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `ls $T/before-retrieval.json $T/before-short.json`
  2. `uv run newsrag eval retrieval --set data/eval/retrieval-2026.jsonl --out $T/after-retrieval.json && uv run newsrag eval short --out $T/after-short.json`
  3. `python3 -c "import json;b=json.load(open('$T/before-retrieval.json'));a=json.load(open('$T/after-retrieval.json'));x=b['by_lang']['te']['recall_at_10'];y=a['by_lang']['te']['recall_at_10'];print(f'before={x:.4f} after={y:.4f}','GUARD OK' if y>=x-0.02 else 'GUARD FAIL');print('TIME OK' if a['median_search_ms']<=b['median_search_ms']+700 else 'TIME FAIL');[print(l,b['by_lang'][l]['recall_at_1'],a['by_lang'][l]['recall_at_1']) for l in ('te','en','te_latn')]"`
  4. `grep -ciE "cpu" specs/tickets/037-search-reranker/results.md`
- **Expected:** step 1 lists both files; step 3 prints `GUARD OK`, `TIME OK` and three lines of before and after recall@1 (any values); step 4 prints a number ≥ 1 (the CPU timing is recorded).

## Edge cases

### E-5: Fewer candidates than the rerank size (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag search "ముఖ్యమంత్రి" --k 3 --from 2026-11-10 --to 2026-11-10 --category cinema | grep "^reranked:"; echo exit=$?`
- **Expected:** prints `reranked: <n>` with 0 ≤ n ≤ 30 (n is the number of fused candidates) and `exit=0`.

### E-6: Nothing else changed (covers Don't touch)

- **Type:** CLI
- **Steps:**
  1. `git diff --stat $(cat specs/tickets/037-search-reranker/evidence/base-commit.txt) -- src/newsrag/baseline src/newsrag/answer data/eval`
  2. `uv run pytest -q tests/test_models_client.py -k "not rerank"`
  3. `uv run newsrag search "chandra babu" --k 3 | grep -c "^names:"`
- **Expected:** step 1 prints nothing; step 2 passes; step 3 prints `1`.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-1, E-3 |
| FR-3 | E-2 |
| FR-4 | E-2, E-3, E-5 |
| FR-5 | E-2 |
| FR-6 | E-3 |
| FR-7 | E-2 |
| FR-8 | E-4 |
| FR-9 | E-2, E-3, E-5 |
| NFR-1 | E-4 |
| NFR-2 | E-4 |
| NFR-3 | E-1, E-2 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (the `before-`/`after-` JSON files, the CPU timing run, command outputs) saved in `evidence/` and listed in `results.md`
