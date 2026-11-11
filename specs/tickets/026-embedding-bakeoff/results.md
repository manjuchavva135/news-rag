# 026: Embedding model bake-off - Results

Built in the main checkout (no worktree), on `main`.

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| ruff check | PASS | evidence/automated-ruff.log |
| unit tests | PASS (902 passed) | evidence/automated-unit.log |
| E-1 bake-off | PASS: exit 0, `questions: 200`, one row per candidate, `winner: multilingual-e5-large-instruct` | evidence/E-1.log, E-1-stderr.log, embed-bakeoff.md, retrieval-json/ |
| E-2 apply | PASS: 0.9 s, `embed_model set to multilingual-e5-large-instruct; ...`; `chunk eligible=3` = done; search returns 3 hits on the old generation | evidence/E-2.log |
| E-3 tie rule, small corpus | PASS (16 passed, incl. the stores test) | evidence/E-3.log |
| E-4 cleanup | PASS: 3 `bakeoff__` collections deleted; `bge-m3__v1` still active with 432/432/432 (same as before) | evidence/E-4.log, E-1-collections-before.log |
| E-5 second-review clarifications | PASS (5 passed) | evidence/E-5.log |

## The result
| candidate | recall@10 | mrr | recall@10 te | recall@10 en | embed seconds |
|---|---|---|---|---|---|
| bge-m3 | 0.9950 | 0.8236 | 0.9929 | 1.0000 | 0.8 |
| qwen3-embedding-8b | 0.9850 | 0.8416 | 0.9929 | 0.9667 | 59.5 |
| multilingual-e5-large-instruct | 0.9850 | 0.8446 | 0.9929 | 0.9667 | 0.8 |

All three are within 0.01 of the best recall@10 (0.995 vs 0.985 is a tie); qwen3 and e5 are
within 0.01 of the best MRR; e5 has the smaller dim (1024): winner e5. `pipeline.yaml` now has
`embed_model: multilingual-e5-large-instruct`.

## Caveats
- The corpus is small: 432 chunks over 3 print dates, exactly 200 covered questions (the NFR-1
  floor). recall@10 differs by 2 questions; the decision rests on an MRR edge of 0.02. English
  recall is lower for e5 and qwen3 (0.967 vs 1.0, 30 English questions). Rerun the bake-off once
  more dates are indexed before the full run (031) if the choice should be firmer.
- qwen3-embedding-8b embeds ~7 chunks/s (59.5 s for 432). Not investigated, since it did not win.
- Restore the old choice with `newsrag.config.set_pipeline_key("embed_model", "bge-m3")` if wanted.

## Changes
- `models.yaml`: profiles `qwen3-embedding-8b`, `multilingual-e5-large-instruct`
- `src/newsrag/embed/bakeoff.py` (new): corpus, size check, payload copy, per-candidate embed +
  retrieval test, winner rule, report, `apply`, `cleanup`
- `src/newsrag/embed/cli.py`: `embed_app` with `newsrag embed bakeoff [--candidates] [--apply] [--cleanup]`;
  `src/newsrag/cli.py` registers it as `embed`
- `src/newsrag/embed/index.py`: `ensure_named_collection(name, dim)` split out of `ensure_collection`
- `src/newsrag/evals/retrieval.py`: `run_retrieval(questions=...)` takes a filtered question list
- `src/newsrag/config.py`: `# --- embed_bakeoff ---` block (candidates, report file, dir, min
  questions, tie, slab)
- `pyproject.toml` / `uv.lock`: `transformers>=4.51` (`sentence-transformers>=3.3` already)
- `tests/test_embed_bakeoff.py` (new, 16 tests, 1 `stores`); `tests/test_models_client.py` (profile
  list); `tests/conftest.py` pins `embed_model` to the code default like the other model choices
- An unknown or non-embed candidate exits 1 before anything is loaded (not reported `failed`).
