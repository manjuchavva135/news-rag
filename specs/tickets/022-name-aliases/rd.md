# 022: Name alias table

**Status:** done
**Mode:** solo
**Runs:** waits for 021: same enrich module, uses its entities

## Summary

The same person or party appears under many spellings: Telugu and English, initials, OCR variants
(the old evals show Chadalwada/Chadalavada). This corpus-level step proposes which names are the same
entity, applies only high-confidence merges automatically and lists the rest for review, so one person
gets one entity id across all stories (grill D-12, A-33, A-47).

## Functional requirements

- **FR-1** - `uv run newsrag aliases build` reads every `enrich_core` artifact and collects all distinct names per entity type — `person` (from `people`) or `org` (from `organisations`; the most frequent org sub-type is kept as `org_type`) — with counts and story ids. The lookup key of a name is `(type, normalised name)` with NFC, collapsed whitespace and lower-cased Latin. Every name becomes part of exactly one entity (a name that never merges is a one-alias entity).
- **FR-2** - Candidate pairs (same type only) come from three rules, recorded as the strongest one that matches (a > b > d): (a) identical keys after transliterating Telugu to Latin (ISO 15919 via `indic-transliteration`) and lower-casing with spaces and dots removed; (b) Jaro-Winkler ≥ 0.90 on the transliterated forms, compared only within blocks of names that share their last transliterated token or their first 3 transliterated characters; (d) a shared surname token plus co-occurrence in ≥ 2 common stories. At most `alias_max_llm_pairs` (default 50,000) new pairs go to the LLM per build, highest-count names first; the rest wait for the next build.
- **FR-3** - Each candidate pair goes to the local LLM (`enrich_model`, prompt `src/newsrag/prompts/alias_v1.md`, temperature 0) with up to 3 example sentences for each name (taken from the `dedup` artifacts' story texts); it returns `{"same": true|false, "confidence": 0-1}`; invalid JSON → 2 retries → the pair goes to the review CSV with empty confidence. Verdicts are cached in `<work_dir>/aliases/<version>/verdicts.jsonl` keyed by the sorted name pair, so a re-run asks only about new pairs.
- **FR-4** - Auto-apply only when `same` and `confidence ≥ 0.9` **and** the recorded rule is (a) or (b) (grill A-47). Everything else with `same: true` goes to `<reports_dir>/alias-review.csv` (`name_a, name_b, type, confidence, rule, example_a, example_b`; the file is always written with this header, even with 0 rows), not applied. Groups are built with union-find over applied pairs in descending confidence, skipping any union that would join two names an override `split` keeps apart.
- **FR-5** - Output is written to `<work_dir>/aliases/<version>/entities.json` (version = `<enrich_model>-alias-p1`): `{entities:[{entity_id, type, org_type, canonical, canonical_te, canonical_en, aliases:[...], story_count}], name_to_entity: {"<type>|<key>": entity_id}}`, entities sorted by id, aliases sorted, keys sorted, no timestamps (so equal input gives an identical file). `canonical` = most frequent spelling (tie → Telugu script, then smallest); `canonical_te` = most frequent Telugu-script alias or null; `canonical_en` = most frequent Latin alias, else the ISO 15919 transliteration of `canonical_te`. Ids are **persistent**: table `ledger.entity_ids(name_key PK, entity_id)`; a group whose names already have ids keeps the oldest id (smallest `created_at`, then smallest id); a group with none gets `e_<first 12 hex of sha1(type + canonical)>`; when two existing entities merge, the loser's names point to the winner's id. Ticket 023 puts the sha1 of `entities.json` into the chunk version, so a changed alias output re-chunks and re-indexes.
- **FR-6** - A curated override file `data/aliases/overrides.yaml` (committed, starts empty) with `merge: [[type, name_a, name_b], ...]` and `split: [[type, name_a, name_b], ...]`: `split` pairs are cannot-link constraints during grouping (FR-4); `merge` pairs are applied after grouping (joining the two groups). A merge that names an unknown name, or conflicts with a split, is ignored with a printed warning (split wins). Reviewed CSV rows are accepted by copying them here.
- **FR-7** - `uv run newsrag aliases stats` prints `names=<n> entities=<n> auto_pairs=<n> review_pairs=<n> largest_group=<n>`.
- **FR-8** - Clarifications (second review):
  - Keys for rules (a)/(b): both sides are folded before comparing — Telugu transliterated to ISO 15919, diacritics stripped (ā→a, ḍ→d, ṇ→n, ś/ṣ→s), English digraphs mapped (ch→c, th→t, dh→d, sh→s, aa→a, ee→i, oo→u, w→v), spaces and dots removed.
  - `ledger.entity_ids(name_key PK, entity_id, created_at)` with `name_key = "<type>|<normalised name>"` (migration `src/newsrag/ledger/sql/00N_entity_ids.sql`; tests use the throw-away schema and the `stores` marker). When one existing id ends up in two groups, the group holding the oldest name keeps it; the other gets a fresh `e_<sha1>` (adding `#k` to the hash input until unused).
  - `aliases build` loops until no candidate pairs are pending (the 50,000 cap is per LLM batch, not per build), so a build is complete when it writes `entities.json`.
  - Helpers `newsrag.aliases.key(type, name)` and `newsrag.aliases.lookup(type, name)` are the only way tickets 023 and the overrides match names (overrides match by normalised key). `canonical_te`/`canonical_en` tie → the lexicographically smallest.

## Non-functional requirements

- **NFR-1** - Auto-merge precision: `uv run newsrag aliases verify --n 40 --seed 17` asks the judge model (`gemma3-27b`) about 40 random pairs of names that ended up in the same auto-merged group (any two names of a group, so transitive merges are tested too); ≥ 38 must be judged the same entity.

## Affected areas

- `src/newsrag/aliases/` (new), `src/newsrag/prompts/alias_v1.md` (new), `data/aliases/overrides.yaml` (new, committed)
- `pyproject.toml` - transliteration + `rapidfuzz` (changed); `src/newsrag/cli.py` (changed)
- `tests/test_aliases.py` (new)
- `src/newsrag/ledger/sql/00N_entity_ids.sql` (new migration)

## Don't touch

- `enrich_core` artifacts (read only).

## Out of scope

- A UI for reviewing the CSV.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Who proposes merges | the local LLM, after rule-based candidate finding | grill A-33 |
| D-2 | Auto-apply | only `confidence ≥ 0.9` and a spelling rule (a/b) | grill A-47 "high confidence"; wrong merges corrupt entity data |
| D-3 | Others | listed for review, not applied | grill A-47 |
| D-4 | Overrides | a committed file that always wins | lets a person accept/reject without code |
| D-5 | Entity ids | persistent table; merges keep the oldest id | ids must not change when new dates arrive |
| D-6 | Scale | blocking + a per-build LLM cap | the full corpus has tens of thousands of names |

## Open questions

None.
