# 026: Embedding model bake-off

**Status:** draft
**Mode:** solo
**Runs:** waits for 025: needs the retrieval test

## Summary

Picks the embedding model by evidence: 2-3 local models are compared on our own Telugu/English
retrieval test and the winner becomes the model for the full run (grill D-13).

## Functional requirements

- **FR-1** - `models.yaml` gains embed profiles `qwen3-embedding-8b` (`Qwen/Qwen3-Embedding-8B`, family `qwen`, dim 4096, `query_prefix` = `Instruct: Given a news question, retrieve the news article that answers it\nQuery: `, `doc_prefix` = `""`) and `multilingual-e5-large-instruct` (`intfloat/multilingual-e5-large-instruct`, family `e5`, dim 1024, the same `query_prefix`, `doc_prefix` = `""`), next to `bge-m3`.
- **FR-2** - `uv run newsrag embed bakeoff [--candidates bge-m3,qwen3-embedding-8b,multilingual-e5-large-instruct]`: the bake-off corpus = all rows of `warehouse.chunks` of the active generation. For each candidate, one at a time through `newsrag.models.use(candidate)` (ticket 006), it embeds `doc_prefix + prefix + "\n" + text` of every corpus chunk with ticket 024's embedding code into Qdrant collection `bakeoff__<candidate>` (same point ids and payload as ticket 024; an existing collection of that name is dropped and recreated), then runs `eval retrieval --set data/eval/retrieval-v1.jsonl --qdrant-collection bakeoff__<candidate>` (ticket 025; BM25 and texts come from the active generation, the query is embedded with the candidate) on the questions whose `story_id` appears among the corpus chunks. Embedding seconds exclude download and model load.
- **FR-3** - Writes `<reports_dir>/embed-bakeoff.md` with a line `questions: <n>` and a table with columns `candidate | recall@10 | mrr | recall@10 te | recall@10 en | embed seconds`, then `winner: <profile>`. Winner: tie set = candidates within 0.01 of the best recall@10; within it, those within 0.01 of the best MRR; then the smaller dim; then the order in `--candidates`.
- **FR-4** - `uv run newsrag embed bakeoff --apply` reads the winner from the existing report (no rerun), writes `embed_model: <winner>` into `pipeline.yaml` and prints `embed_model set to <winner>; chunk and index stages will rerun` — or `embed_model unchanged (<winner>)` when it already was. It deletes nothing. The active generation stays as it is (search keeps working on it) until ticket 031 re-indexes and runs `index activate`.
- **FR-5** - `uv run newsrag embed bakeoff --cleanup` deletes `bakeoff__*` collections.
- **FR-6** - Clarifications (second review):
  - Each candidate is evaluated by calling the Python eval directly with `embed_profile=<candidate>` and `qdrant_collection=bakeoff__<candidate>` on the filtered question list, inside the same `use(candidate)` scope (ticket 006's `use()` is re-entrant for the same profile, so `search()` reuses the open embedder).
  - Point payloads are copied by id from the active generation's Qdrant points (read-only scroll); only the vectors are new.
  - The corpus-size check (NFR-1) runs before any embedding. A candidate that fails (download, OOM, unsupported) is reported `failed` and excluded; if none finish, the report says `winner: none` and the command exits 1.
  - `--apply` with no report or `winner: none` exits 1 and changes nothing; it writes through `newsrag.config.set_pipeline_key`.
  - Profiles include `kind: embed` and `dtype: bfloat16` (`qwen3-embedding-8b`) / `float16` (`multilingual-e5-large-instruct`); `pyproject.toml` requires `transformers>=4.51` and `sentence-transformers>=3.0`.
  - E-1 records `uv run newsrag index collections` before and E-4 compares after: the active marker and the counts are unchanged.

## Non-functional requirements

- **NFR-1** - At least 200 test questions have their gold story in the bake-off corpus (otherwise the command exits 1 with `bake-off corpus too small: <n> questions`).

## Affected areas

- `models.yaml` (changed); `src/newsrag/embed/bakeoff.py` (new); `src/newsrag/cli.py` (changed); `pipeline.yaml` (changed by `--apply`)
- `tests/test_embed_bakeoff.py` (new, fake embedder)
- `pyproject.toml` - version floors (changed)

## Don't touch

- The active collection and alias.

## Out of scope

- Fine-tuning embeddings; rerankers.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Choose by test | yes, 2-3 local models | grill D-13 |
| D-2 | Candidates | BGE-M3, Qwen3-Embedding-8B, multilingual-e5-large-instruct | strong multilingual open models covering Telugu |
| D-3 | Chunks for the comparison | the existing 512-token BGE-M3 chunks for all | same text for every candidate; the winner re-chunks with its own tokenizer in the full run |
| D-4 | Tie rule | recall@10, then MRR, then smaller | quality first (D-11) |

## Open questions

None.
