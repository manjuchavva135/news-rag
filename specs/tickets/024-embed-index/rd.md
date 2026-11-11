# 024: Embed and index into stores

**Status:** done
**Mode:** solo
**Runs:** waits for 023: needs chunks

## Summary

Writes each date's chunks into the three stores: dense vectors in Qdrant, BM25 text in OpenSearch (ICU
analyzer for Telugu) and the structured warehouse in Postgres (stories, printings, entities, places,
facts). Writes are upserts, a re-indexed date replaces its old rows, flagged items are stored with
their flags, and each embedding model gets its own Qdrant collection so models can be swapped
blue-green (grill A-12, A-30, D-20).

## Functional requirements

- **FR-1** - Embedding uses the `embed_model` profile from `models.yaml` (ticket 006 defines `bge-m3`) through `newsrag.models.use(embed_model)` (sentence-transformers under `gpu_lock`), opened in `open()` once per `stage run`; `models_needed=(embed_model,)`; batch `embed_batch_size` (default 64), L2-normalised vectors. A **generation** = `<embed_model>__v1`; everything that depends on the embed model is stored per generation.
- **FR-2** - DateStage `index` (`depends_on=("chunk",)`, `version="index-v1-<embed_model>"`) embeds `doc_prefix + prefix + "\n" + text` for each chunk and writes to the current generation G:
  - **Qdrant** collection `chunks__G` (cosine, dim from profile), point id = UUID5 of `chunk_id`, payload = every chunk record field except `text` and `prefix`, with `pub_date_int` (YYYYMMDD) and `event_date_int`. Payload indexes on `pub_date_int`, `event_date_int`, `category`, `editions`, `edition_names`, `states`, `districts_current`, `districts_at_print`, `edition_districts`, `entity_ids`, `story_id`, `page_ids`, `low_confidence`.
  - **OpenSearch** index `chunks__G` with fields `text_te`/`headline_te` (ICU tokenizer + ICU folding), `text_en`/`headline_en` (english analyzer) — a text whose letters are ≥ 20% Telugu script goes to `_te`, ≥ 20% Latin to `_en` (both when both) — `headline` from the chunk record, plus the same filter fields (including `page_ids`) as keywords/dates.
  - **Postgres** schema `warehouse` (migrations in `src/newsrag/warehouse/sql/`, applied by `uv run newsrag warehouse init`; `uv run newsrag db init` runs `ledger init` + `warehouse init`). Sources: stories and printings from the `dedup` artifact (only stories that produced chunks), enrich fields from `newsrag.enrich.load()`, entities from the alias output, page data from `ledger.pages` + `ledger.page_editions`. Tables: `pages(page_id PK, pub_date, edition_code, section, page_num, page_end, edition_names text[], districts_current text[], states text[])`, `stories(story_id PK, pub_date, event_date, kind, category, headline, text, canonical_page_id, n_printings, department, notice_number, deadline, low_confidence, low_confidence_reasons text[])`, `story_printings(story_id, unit_id, page_id, edition_code, section, page_num, parts jsonb, PK(story_id, unit_id))`, `chunks(generation, chunk_id, story_id, seq, kind, headline, prefix, text, n_tokens, PK(generation, chunk_id))`, `entities(entity_id PK, type, org_type, canonical, canonical_te, canonical_en, aliases jsonb)`, `story_entities(story_id, entity_id, surface_forms jsonb, PK(story_id, entity_id))`, `story_places(story_id, seq, name, llm_type, kind, state, districts_current text[], districts_at_print text[], PK(story_id, seq))`, `facts(fact_id PK, story_id, value_text, value, scale, normalized_value, unit, refers_to, period_text, period_start, period_end, place, districts_current text[], districts_at_print text[])`, `active_generation(singleton bool PK default true, generation)`.
- **FR-3** - Re-indexing a date is idempotent: in one Postgres transaction it deletes every warehouse row of that date's stories (stories, printings, story_entities, story_places, facts, and `chunks` of generation G) and of stories of that date no longer present, then inserts the new rows; `entities` are upserted and never deleted. Qdrant points and OpenSearch docs of that date in generation G are deleted by `pub_date_int` filter and re-written.
- **FR-4** - `uv run newsrag index activate <generation>` switches, together, the Qdrant alias `chunks`, the OpenSearch alias `chunks` and `warehouse.active_generation` to that generation; the first generation ever written is activated automatically. `uv run newsrag index collections` lists generations with Qdrant point counts, OpenSearch doc counts and Postgres chunk rows, and marks the active one. Search (ticket 025) reads only the active generation.
- **FR-5** - `uv run newsrag index validate [--date D] [--generation G]` (default: the active generation) checks per date that Qdrant point count = OpenSearch doc count = Postgres `chunks` rows of that generation, prints `ok <date> <n>` or `MISMATCH <date> q=<n> os=<n> pg=<n>`, and exits 1 on any mismatch. After each date the stage logs `index <date>: <n> chunks in <s>s (<r> chunks/s)` (embedding + writes, model load excluded) and stores `chunks_per_s` in detail.
- **FR-6** - Clarifications (second review):
  - Test isolation: settings `warehouse_schema` (default `warehouse`) and `store_alias_prefix` (default `""`, so aliases are `chunks`); tests set both to throw-away values in a fixture and drop them afterwards, and never touch the real `warehouse` or `chunks` aliases.
  - The embedder is injectable (`IndexStage(embedder=...)`); `stores`-marked tests use a fake embedder (no GPU); E-3 reads the real index built in E-1.
  - `preflight()` raises `StageUnavailable("run 'newsrag db init' first")` if the warehouse schema is missing.
  - Embedding passes `prefix + "\n" + text` with `kind="doc"` (the profile's `doc_prefix` is added once by ticket 006's embedder). The `bge-m3` profile gets `dtype: float16`.
  - `index validate` checks the union of dates with `index` done in the ledger and dates present in any of the three stores; a date with 0 chunks everywhere prints `ok <date> 0`.
  - Each index run upserts all entities of the current `entities.json` and deletes `warehouse.entities` rows whose id is no longer in it. `facts` gets a `state` column (ticket 021).

## Non-functional requirements

- **NFR-1** - Indexing throughput ≥ 200 chunks/s with BGE-M3 on this GPU (the logged `chunks/s`).
- **NFR-2** - Telugu BM25 works: an OpenSearch `match` query for a Telugu word taken from a chunk returns that chunk in the top 10.

## Affected areas

- `models.yaml` (changed); `src/newsrag/stages/index.py`, `src/newsrag/embed/`, `src/newsrag/warehouse/` (new)
- `src/newsrag/stores/*` - collection/index bootstrap (changed)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_index.py` (new, `stores`; small fake chunks, test collection/index names)

## Don't touch

- Store container config (ticket 002); ledger schema.

## Out of scope

- Search and evaluation (025); the embedding bake-off (026).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Stores | Qdrant + OpenSearch (ICU) + Postgres | grill A-12 |
| D-2 | Model swap | one collection per embed model, alias `chunks` = active | grill A-30 blue-green |
| D-3 | Flagged items | indexed with `low_confidence` + reasons | grill D-20 |
| D-4 | Embedding runtime | sentence-transformers in the main venv under the GPU lock | simpler than a vLLM server for batch embedding |
| D-5 | What blue-green covers | Qdrant collection, OpenSearch index and `warehouse.chunks` per generation, switched together | chunks depend on the embed model's tokenizer (ticket 023), so all three must change at once |

## Open questions

None.
