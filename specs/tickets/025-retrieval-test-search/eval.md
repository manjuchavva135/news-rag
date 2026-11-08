# 025: Retrieval test set and hybrid search script - Evaluation

## Setup

- Prerequisites: ticket 024 done; the stores hold indexed data for 2026-04-01..2026-04-07, 2025-06-10 and 2020-04-02 (see FR-6); GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Fusion and hit rule (covers FR-1, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_search.py tests/test_eval_retrieval.py -v`
- **Expected:** all pass, including `test_rrf_fusion_order`, `test_page_ids_filter`, `test_hit_needs_page_and_text_overlap`, `test_hit_on_later_chunk_counts`, `test_baseline_not_configured_error`, `test_generation_and_collection_override`.

### E-2: Search CLI (covers FR-2, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag search "ఆంధ్రప్రదేశ్ అప్పులు" --k 5`
- **Expected:** 5 lines with rank, score, date, page id, headline.

### E-3: Build the test set (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag eval build-retrieval --n 300 --seed 7`
  2. `uv run python -c "import json;r=[json.loads(l) for l in open('data/eval/retrieval-v1.jsonl')];print(len(r), sum(x['in_baseline_window'] for x in r), round(sum(x['lang']=='te' for x in r)/len(r),2))"`
- **Expected:** `300` (or fewer with `short:` lines printed), `100`, and a value between 0.65 and 0.75.

### E-4: Run the test (covers FR-4, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag eval retrieval --store new`
- **Expected:** prints `recall@10=... mrr=... median_search_ms=... n=300`; `retrieval-new.json` has those keys and `by_lang.te`/`by_lang.en`; `median_search_ms` < 500.

## Edge cases

### E-5: Paraphrase guard (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_eval_retrieval.py -k headline_copy_regenerated`
- **Expected:** passes.

### E-6: Second-review clarifications (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_eval_retrieval.py tests/test_search.py -k "embed_profile_flag or builder_story_filter or baseline_backend_registry or only_window"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-2 |
| FR-2 | E-2 |
| FR-3 | E-3, E-5 |
| FR-4 | E-1, E-4 |
| NFR-1 | E-4 |
| FR-5 | E-6 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (`retrieval-new.json`, logs) saved in `evidence/` and listed in `results.md`
