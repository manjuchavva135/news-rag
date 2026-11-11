# 023: Chunking

**Status:** done
**Mode:** solo
**Runs:** waits for 022: needs enriched stories and entity ids; waits for 015: table/notice items

## Summary

Turns each canonical story (and each table and notice) into retrieval chunks carrying all the
metadata filters need. The story is the retrieval unit: short stories are one chunk, long ones are split
on paragraphs, every chunk is prefixed with headline + print date + number of printings, and each table
is its own chunk linked to its story (grill A-11, A-32, A-42, A-48).

## Functional requirements

- **FR-1** - DateStage `chunk` (`depends_on=("enrich_facts",)`, `version="chunk-v1-<embed_model>"` (+ ticket 003 config hash); `embed_model` setting, default `bge-m3`, read from `pipeline.yaml` if present; the tokenizer is `AutoTokenizer.from_pretrained(<hf_id of that embed profile in models.yaml>)`). It requires the alias output for the current alias version (ticket 022) to exist; `preflight()` raises `StageUnavailable("run 'newsrag aliases build' first")` otherwise. A changed alias output or edition map re-chunks only the dates whose lookups changed (FR-7).
- **FR-2** - Units: each story from the date's `dedup` artifact with its fields from `newsrag.enrich.load()` (ticket 019 FR-6), excluding kinds `ad_commercial`, `masthead`, `other`. Tables/infographics attached to a story = ticket-015 items whose `article_id` is any part's `article_id` of any of the story's printings (de-duplicated by markdown text); they become separate chunks of kind `table` / `infographic` with the parent's `story_id`; standalone items are their own stories already (from dedup).
- **FR-3** - Prefix = `<headline> | <print date DD-MM-YYYY> | <n> printings` (for 1 printing: `1 printing`); headline = the story's headline, for a table chunk the parent's headline, and when empty the table's `title`, else `(శీర్షిక లేదు)`. `n_tokens` = `len(tokenizer(doc_prefix + prefix + "\n" + text, add_special_tokens=True).input_ids)` — exactly the string ticket 024 embeds (grill A-42).
- **FR-4** - Size: if `n_tokens` ≤ `chunk_max_tokens` (512) → one chunk. Else split the text on paragraph breaks (blank lines; a paragraph alone over the limit is split on sentence ends `?`, `!`, `।`, and `.` not between digits, then hard-split by tokens) into consecutive windows ≤ 512 tokens, where each new window starts with the last paragraph of the previous window (one-paragraph overlap, grill A-32); if that overlap plus the next paragraph does not fit, the overlap shrinks to the previous window's last sentence, and if that does not fit either, there is no overlap. Every window contains at least one paragraph not in the previous window. Table/infographic markdown is split by rows instead, repeating the header row and separator row in every chunk.
- **FR-5** - Chunk record: `{chunk_id: "<story_id>#cNN" (story chunks first, then table chunks, numbered on), story_id, kind, seq, headline, prefix, text, n_tokens, pub_date, event_date, category, editions:[codes], edition_names, edition_districts, edition_states, states, districts_current, districts_at_print, entity_ids, facts_ids, department, notice_number, deadline, page_ids:[all printings' pages], canonical_page_id, low_confidence: bool, low_confidence_reasons:[...]}`. Edition fields are the union of `ledger.page_editions` (ticket 011) over the printings' pages (`edition_districts` = their `districts_current`). `entity_ids` map story names through the alias output's `name_to_entity`; names not found are skipped and counted (`unmapped_names` in detail). `low_confidence` is true when any flag starting with `low_confidence:` is on the story, any printing, any part, or the canonical page; `low_confidence_reasons` lists those flags without the prefix (grill D-20).
- **FR-6** - Artifact per date: `{pub_date, chunks:[...]}`. `uv run newsrag chunk stats [--date D]` prints chunks, stories, chunks per story (mean, max), token p50/p95/max, low_confidence share.
- **FR-7** - Clarifications (second review):
  - Entity and edition lookups do not go into the version (the version is `chunk-v1-<embed_model>` + ticket 003's config hash). Instead each date's detail stores `lookup_hash` = sha1 of the `name_to_entity` entries and `editions.yaml` entries that date's chunks used. `newsrag.chunking.stale_dates() -> list[date]` recomputes it per date and returns dates whose hash changed; ticket 029 runs it after `aliases build`/`editions apply` and resets `chunk` for only those dates.
  - Items with `inlined: true` (ticket 015) are not chunked again. Stories of kind `table`, `infographic` and `table_only` are split by rows like table chunks; every chunk's `kind` = its story's kind (attached tables: `table`/`infographic`).
  - A story whose text is empty after trimming is not chunked and is counted as `empty_stories` in detail.
  - `low_confidence` sources: the dedup story's `flags` (which include printing and part flags), each printing's `flags`, `group_final.page_flags` of the canonical page, `transcribe.page_flags` of the canonical page, and `ledger.page_editions.flags` of all printings' pages.
  - Unit tests inject a small whitespace tokenizer; tests with the real tokenizer are marked `data`. Tokenizers load with `cache_dir=settings.hf_home`.
  - `preflight()` raises `StageUnavailable("embed_model <x> not in models.yaml")` for an unknown profile; E-4 uses a temporary test profile added in the pytest fixture.

## Non-functional requirements

- **NFR-1** - No chunk exceeds 512 tokens by the embed tokenizer (checked over all chunks of the test dates).

## Affected areas

- `src/newsrag/stages/chunk.py`, `src/newsrag/chunking/` (new)
- `pipeline.yaml` (add `embed_model: bge-m3`) (changed)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_chunking.py` (new)

## Don't touch

- Enrich and dedup artifacts.

## Out of scope

- Embedding and writing to stores (024); choosing the embedding model (026).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Retrieval unit | the canonical story | grill A-11 |
| D-2 | Size and overlap | ≤ 512 tokens, one-paragraph overlap | grill A-32 |
| D-3 | Prefix | headline + print date + printings count, counted in the 512 | grill A-42 |
| D-4 | Tokenizer | the configured embed model's; the version includes it so a model change re-chunks | grill A-42, A-30 |
| D-5 | Captions/ads | not chunked | grill A-8, D-9 |

## Open questions

None.
