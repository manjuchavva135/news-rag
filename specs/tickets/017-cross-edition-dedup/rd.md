# 017: Cross-edition story dedup (per date)

**Status:** draft
**Mode:** solo
**Runs:** waits for 016: needs stitched articles; waits for 015: standalone table/notice items

## Summary

The same story is printed in many district editions with small local edits. The old system indexed
every copy, so answers cited the wrong or duplicate references (grill F-6 item 7). This per-date stage
clusters near-identical printings into one canonical story that lists every printing (edition, page,
box), so search returns the story once while citations can show all printings (grill D-7, A-26, A-41).

## Functional requirements

- **FR-1** - DateStage `dedup` (`depends_on=("stitch", "transcribe")` — every page of the date must have `transcribe` done, and the date must have `stitch` done; `version="dedup-v1"`). Inputs: `newsrag.stitch.stitched_articles(date)` plus the date's ticket-015 items. The ticket-015 class overrides the grouping kind: an article with a `notice_tender` item is a `notice_tender` unit whose text is that item's markdown and which carries `department`, `notice_number`, `deadline`; an article with a `commercial` item is excluded. Units = articles of kind `news, editorial, brief_item, notice_tender, table_only` (a `table_only` article's text = its items' markdown joined by blank lines) plus standalone ticket-015 items (`article_id: null`; kind = item type, headline = `title` or `""`). `ad_commercial` (without a notice item), `masthead`, `other` and `commercial` are excluded (recorded as `excluded` counts).
- **FR-2** - Similarity: text = headline + "\n" + text; normalised with NFC, then only Unicode punctuation (categories P*) and whitespace removed (combining marks M* are kept); character 5-shingles; MinHash with 128 permutations and LSH to find candidate pairs; a pair is a duplicate when estimated Jaccard ≥ `dedup_jaccard` (0.85) **and** BGE-M3 cosine ≥ `dedup_cosine` (0.90). Clustering (transitive, grill A-41): duplicate pairs are processed in descending cosine order with union-find, and a union is skipped when the two clusters would share a page (a unit's pages = all its parts' pages), so units of the same page are never in one story. Stories of different kinds may merge; the story kind is the canonical unit's.
- **FR-3** - Story ids are stable: table `ledger.story_ids(unit_id text PK, story_id text, first_seen date)`. Within a date, clusters are processed in order of their smallest unit_id; each reuses the existing story_id held by most of its members (tie → oldest `first_seen`, then smallest id) unless an earlier cluster of this run already took that id; otherwise it gets a new id `s_<first 16 hex of sha1(smallest unit_id + pub_date + k)>` with the smallest k ≥ 0 not already used in `story_ids`. Rows of units that no longer exist for the date are deleted.
- **FR-4** - Canonical printing = the member with the longest text (tie → smallest unit_id) (grill A-26). Artifact per date: `{pub_date, stories:[{story_id, kind, canonical_unit_id, headline, text, department, notice_number, deadline, printings:[{unit_id, page_id, edition_code, section, page_num, parts:[{article_id, page_id, bbox_union}], flags}], flags}], excluded:{...}}` (notice fields `null` for non-notices). A story's `flags` = the union of its canonical unit's flags.
- **FR-5** - BGE-M3 runs through `newsrag.models.use("bge-m3")` (ticket 006: sentence-transformers under `gpu_lock`), opened in `open()` once per `stage run`, batch 64; `dedup_device=cpu` loads it on CPU without the lock. At the end of each date the stage logs `dedup <date>: <units> units, <stories> stories in <s> s`.
- **FR-6** - `uv run newsrag dedup stats [--date D]` prints units, stories, mean and max printings per story, and the count of stories with ≥ 5 printings.
- **FR-7** - Clarifications (second review):
  - Story-id reuse: clusters are processed by the number of members already holding a candidate id (most first), then by smallest unit_id; D-6 reads "reuse the id held by most members".
  - `dedup_device=cpu` loads sentence-transformers on CPU itself via `use("bge-m3", device="cpu")` (ticket 006); settings `dedup_jaccard`, `dedup_cosine`, `dedup_device` live in `config.py` and are part of the version's config hash.
  - `unit_id` = the stitched article id for articles, the `item_id` for standalone items. A standalone item's printing takes `edition_code/section/page_num` from `ledger.pages` and `bbox_union` = union of its blocks' `ocr_final` boxes.
  - A `notice_tender`/`ad_commercial` article without a usable ticket-015 item: notice → unit with its OCR text, null notice fields and flag `low_confidence:notice_untranscribed`; ad → excluded.
  - A `table_only` story's items are inlined (ticket 015 `inlined: true`); unit flags = union of article and item flags.

## Non-functional requirements

- **NFR-1** - Precision check: `uv run newsrag dedup verify --n 30 --seed 5` asks the local text LLM (`qwen3-30b-a3b`) whether 30 random merged pairs (canonical vs another printing) are "the same news story (local edits allowed)"; ≥ 28 of 30 must be `yes`.
- **NFR-2** - A date with ~150 pages dedups in < 2 min (the logged time per date, model already loaded).

## Affected areas

- `src/newsrag/stages/dedup.py`, `src/newsrag/dedup/{minhash,cluster,ids}.py` (new)
- `src/newsrag/ledger/sql/00N_story_ids.sql` (new migration)
- `pyproject.toml` - `datasketch`, `sentence-transformers` (changed)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_dedup.py` (new)

## Don't touch

- The stitch module's behaviour (import it, don't change it).

## Out of scope

- Merging across different dates (a next-day reprint is a separate story, grill A-26).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | One story or many | one canonical story with all printings | grill D-7 |
| D-2 | Similarity | MinHash ≥ 0.85, confirmed by embedding cosine | grill A-41 |
| D-3 | Embedding model for the confirm step | BGE-M3, fixed | independent of the retrieval bake-off (026), so dedup doesn't change when the search model changes |
| D-4 | Cosine threshold | 0.90 | a high bar for "same text"; MinHash already did the main filtering |
| D-5 | Canonical text | longest printing | grill A-26 |
| D-6 | Stable ids | reuse the oldest existing id in the cluster | grill A-41 |

## Open questions

None.
