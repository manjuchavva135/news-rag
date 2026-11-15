# 036: Resolve names through the alias table before searching

**Status:** draft
**Mode:** solo
**Runs:** waits for 035: edits the same `search/hybrid.py` and uses its `entity_ids` filter

## Summary

Staff type names in Latin letters and with typos ("chandra babu", "revant reddy"). The stories are
in Telugu script, so BM25 cannot match them: in the short-query test only 46% of the hits of a
Latin-letter name mention the person, against 96% for the same name in Telugu script, and a Latin
query finds only 20% of the stories the Telugu query finds. The alias table (ticket 022) already
knows 18,790 people and organisations with their spellings, and every chunk carries `entity_ids`.
This ticket finds the names in a query, adds their Telugu spelling (when the table has one) to the search and adds a third
ranking of the chunks tagged with those entities.

## Functional requirements

- **FR-1** - Name index. New `src/newsrag/search/names.py` builds, from `aliases.load_entities()`, a map `aliases.fold(name) -> {entity_id}` over each entity's `canonical` and every string in `aliases`, and keeps each entity's `canonical`, `type` and `story_count`. Folded keys shorter than `search_name_min_chars` (3) or without a letter are left out. The index is cached per process and rebuilt when `entities.json` changes (same stamp rule as `aliases.load_entities`). When `aliases.entities_path()` does not exist, the index is empty and nothing is resolved (no error).
- **FR-2** - `names.resolve(query) -> list[NameMatch]`, `NameMatch(span: str, entity_ids: list[str], canonical: str, exact: bool, score: float)`:
  - The query is split into words on whitespace. Spans of 1 to `search_name_max_words` (4) consecutive words are tried, longest first, left to right; a word used by an accepted span is not used again.
  - A span's key is `aliases.fold(span)`. **Exact:** the key is in the index. **Fuzzy:** tried only when there is no exact match and the key has at least `search_name_fuzzy_min_chars` (6) characters: the index keys with `rapidfuzz.fuzz.ratio(key, candidate) >= search_name_fuzzy_min` (88); the best-scoring key wins (ties: the entity with the larger `story_count`).
  - A span whose key maps to more than `search_name_max_entities` (3) entities is too ambiguous and is skipped.
  - `entity_ids` are ordered by `story_count` descending; `canonical` is the first one's `canonical`; `span` is the words as typed; `score` is 100 for exact, else the ratio.
- **FR-3** - Search with names. New `hybrid.search_detailed(query, k, filters, generation, qdrant_collection, embed_profile, resolve_names=None) -> SearchResult(hits: list[Hit], query_text: str, names: list[NameMatch])` for store `new`; `search(...)` for store `new` returns `search_detailed(...).hits` and accepts the same `resolve_names` keyword. `resolve_names=None` means the setting `search_resolve_names` (default `True`). When names are resolved:
  - `query_text` = the query as typed, followed by a space and the `canonical` of each match whose `canonical` is not already a substring of the query (in match order). `query_text` is what is embedded and what goes to BM25.
  - A third ranking is added to the RRF: a dense search with `query_text`'s vector and the caller's filters plus `entity_ids` = all matched ids (if the caller also gave `entity_ids`, the dense entity search uses the caller's list only). Its RRF contributions are multiplied by `search_name_rrf_weight` (1.0). `rrf_fuse` gets an optional `weights` argument (default: all 1.0, same results as today).
  - With no match, `query_text` is the query and the search is exactly the two-ranking search of ticket 035.
- **FR-4** - CLI. `uv run newsrag search "<query>"` prints, before the hits, one line per match: `names: <span> -> <canonical> (<first entity id>[, +<n> more])`, with `~` in place of `->` for a fuzzy match. New option `--no-names` searches with `resolve_names=False`.
- **FR-5** - Settings in the `# --- search ---` block: `search_resolve_names: bool = True`, `search_name_min_chars: int = 3`, `search_name_max_words: int = 4`, `search_name_fuzzy_min: float = 88.0`, `search_name_fuzzy_min_chars: int = 6`, `search_name_max_entities: int = 3`, `search_name_rrf_weight: float = 1.0`.
- **FR-6** - Before/after record, as in ticket 035 FR-9 with folder `specs/tickets/036-name-alias-resolution/evidence`: `before-retrieval.json`, `before-short.json` at the base commit, `after-*.json` when done. `results.md` has a table, before and after, of `names.te|latin|typo`: `mention`, `about`, `other_kind`, `same_as_te`; `stories.te|latin.recall_at_10`; and retrieval `by_lang.te|en|te_latn` `recall_at_1`, `recall_at_10`.
- **FR-7** - If NFR-1 fails with `search_name_rrf_weight` 1.0, the worker reruns the two evals with 0.5 and then 0.25 (set with `NEWSRAG_SEARCH_NAME_RRF_WEIGHT`), records every run in `results.md`, and sets the code default to the largest of the three values that passes. If 0.25 also fails, the ticket fails and `results.md` says so.
- **FR-8** - Clarifications (gap check):
  - **Text appended and shown.** `NameMatch.canonical` is the first entity's `canonical_te` when it is not null, else its `canonical` (665 of 18,790 entities have no Telugu form). That string is what FR-3 appends to `query_text` and what the CLI prints.
  - **Span scan.** At each word position from the left the longest span is tried first (4 words, then 3, 2, 1). An accepted match moves the position past the span; no match moves it one word.
  - **Fuzzy and ambiguity.** Only keys that map to at most `search_name_max_entities` entities are fuzzy candidates. The best ratio wins; on equal ratios the key whose most-covered entity has the larger `story_count`, then the smaller key in string order.
  - **Caller gave `entity_ids`.** No third ranking is added (it would repeat the dense ranking); the name is still appended to `query_text`. Otherwise the third ranking uses the caller's other filters, the default kinds of ticket 035 and the limit `max(k, search_candidates)`.
  - **Other stores.** `search(store=...)` passes `resolve_names` only to the `new` backend; for `baseline` it is ignored. `newsrag search --store baseline` calls `search()` and prints no `names:` line.
  - **Index input.** The index and `resolve` take `path: Path | None` (None = `aliases.entities_path()`); unit tests write a small `entities.json` to a temporary path.
  - **Evidence names.** Runs at a weight other than the final one are saved as `after-retrieval-w<weight>.json` and `after-short-w<weight>.json`. `after-retrieval.json` and `after-short.json` are produced last, with the code default the ticket ends with. `same_as_te` exists only for `latin` and `typo`.

## Non-functional requirements

- **NFR-1** - No-regression guard: `by_lang.te.recall_at_10` in `after-retrieval.json` ≥ the value in `before-retrieval.json` − 0.02.
- **NFR-2** - Building the name index takes ≤ 10 s and `resolve()` of a 12-word query takes ≤ 50 ms after the index is built, on the real `entities.json` (measured by the `data`-marked test of E-2).
- **NFR-3** - `names.resolve` and the index take the entity table as an argument (default: the real one), so unit tests run on a small fake table.

## Affected areas

- `src/newsrag/search/names.py` - index, `resolve`, `NameMatch` (new)
- `src/newsrag/search/hybrid.py` - `search_detailed`, `SearchResult`, the entity ranking, `rrf_fuse(weights=)` (changed)
- `src/newsrag/search/__init__.py` - exports (changed)
- `src/newsrag/search/cli.py` - `names:` lines, `--no-names` (changed)
- `src/newsrag/config.py` - `# --- search ---` block (changed)
- `tests/test_search_names.py` (new), `tests/test_search.py` (changed)
- Base commit: before the first change the worker writes `git rev-parse HEAD` to `specs/tickets/036-name-alias-resolution/evidence/base-commit.txt`. Every "against the base commit" comparison and every `before-*.json` file refers to that commit; the diffs in eval.md compare it with the working tree.
- Test names: new tests are named so that every `pytest -k` selection in eval.md matches at least one of them.

## Don't touch

- `src/newsrag/aliases/**` and `entities.json`: read only. Wrong aliases in the table (e.g. `చాంద్ బాషా` listed under `చంద్రబాబు`) are not fixed here.
- `src/newsrag/baseline/search.py`: the baseline store gets no name resolution.
- `src/newsrag/answer/**`, `data/eval/**`, store contents.
- The filters and default kinds of ticket 035.

## Out of scope

- Places and subjects (the alias table holds people and organisations only).
- Listing every story of an entity by date (ticket 041).
- Transliterating or translating the rest of the question (ticket 038).
- Learning new aliases from queries.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | How a Latin-letter name is compared with Telugu aliases | `aliases.fold()` on both sides | it already maps Telugu script and Latin spellings to one form (ticket 022 rule a) |
| D-2 | Typos | `rapidfuzz` ratio ≥ 88 on the folded key, only for keys of 6+ characters | `rapidfuzz` is already a dependency; short keys give false matches |
| D-3 | How a match changes the search | Telugu spelling appended to the query text, plus a third RRF ranking restricted to the entity's chunks | BM25 gets a Telugu term to match; the tag ranking does not depend on spelling |
| D-4 | A name shared by many entities | skipped above 3 entities | a surname alone ("reddy") must not pull in every Reddy |
| D-5 | Telugu-script queries | resolved too | one code path; the guard (NFR-1) protects the Telugu numbers |
| D-6 | Done bar | before/after report plus NFR-1; the weight may be lowered as in FR-7 | user chose "report, with a no-regression guard" |

## Open questions

None.
