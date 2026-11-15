# 035: Search filters on the indexed fields, notices off by default

**Status:** draft
**Mode:** solo
**Runs:** alongside

## Summary

`newsrag.search.search()` accepts only a `page_ids` filter today, although every chunk in Qdrant and
OpenSearch already carries its kind, category, states, districts, editions, entities and dates. This
ticket lets a caller filter on those fields and makes a search without a kind filter leave out
government notices and tenders. Staff complained that a bare name returns notices and
advertisement-like items; in the short-query test 26% of the top-10 hits of real staff questions are
not news or editorials.

## Functional requirements

- **FR-1** - Filter keys. `hybrid.FILTER_KEYS` becomes
  `("page_ids", "kinds", "categories", "states", "districts", "editions", "entity_ids", "date_from", "date_to", "event_from", "event_to")`.
  `check_filters` validates and normalises them; an unknown key still raises
  `ValueError("unknown search filter(s): …")`.

  | key | value | chunk field | match |
  |---|---|---|---|
  | `page_ids` | list of str | `page_ids` | any of (unchanged) |
  | `kinds` | list of str, or `["all"]` | `kind` | any of |
  | `categories` | list of str | `category` | any of |
  | `states` | list of str | `states` | any of |
  | `districts` | list of str | `districts_current` | any of |
  | `editions` | list of str (edition codes) | `editions` | any of |
  | `entity_ids` | list of str | `entity_ids` | any of |
  | `date_from`, `date_to` | `YYYY-MM-DD` str or `date` | `pub_date_int` | inclusive range |
  | `event_from`, `event_to` | `YYYY-MM-DD` str or `date` | `event_date_int` | inclusive range |

  Different keys are combined with AND. A list key given as a plain string raises
  `ValueError("filter <key> must be a list")`. An empty list raises `ValueError("filter <key> is empty")`.
- **FR-2** - Validation messages (all `ValueError`):
  - kind not in `KNOWN_KINDS = ("news", "editorial", "brief_item", "notice_tender", "table_only", "table", "infographic")` and not `all`: `unknown kind <x> (known: news, editorial, brief_item, notice_tender, table_only, table, infographic, all)`;
  - category not in `newsrag.enrich.validate.CATEGORIES`: `unknown category <x> (known: …)`;
  - a date that `date.fromisoformat` rejects: `filter <key> must be YYYY-MM-DD`;
  - `date_from > date_to`: `filter date_from is after date_to`; same for `event_from`/`event_to`.
  States, districts, editions and entity ids are not checked against a list (an unknown value just matches nothing).
- **FR-3** - Default kinds. New setting `search_default_kinds: tuple[str, ...] = ("news", "editorial", "brief_item", "table_only", "table", "infographic")` in the `# --- search ---` block. In `search_new`, when the caller gives no `kinds`, the filter `kinds = search_default_kinds` is applied. `kinds=["all"]` applies no kind filter. An empty `search_default_kinds` means no default kind filter.
- **FR-4** - Both retrievers apply the same filters: Qdrant as `must` conditions (`MatchAny` for the list keys, `Range(gte=, lte=)` on the `*_int` fields), OpenSearch as `bool.filter` clauses (`terms`, `range` on the `*_int` fields). `bm25_body` and `dense_search` keep their signatures (`filters` is the checked dict).
- **FR-5** - `Hit` gets a field `kind: str = ""`, filled from the store payload (`kind` is added to the fields requested from Qdrant and OpenSearch).
- **FR-6** - Qdrant payload index. `"kind"` is added to `records.QDRANT_KEYWORD_INDEXES`. New command `uv run newsrag index ensure-payload-indexes` (in `src/newsrag/embed/cli.py`) calls `ensure_named_collection` for the active generation's collection (dimension read from the existing collection) and prints `<collection>: payload indexes ok`; with no active generation it prints `no active generation` to stderr and exits 1. Nothing is re-embedded.
- **FR-7** - CLI. `uv run newsrag search "<query>"` gets repeatable options `--kind`, `--category`, `--state`, `--district`, `--edition`, `--entity`, and `--from`, `--to`, `--event-from`, `--event-to` (dates). A `ValueError` from `check_filters` is printed to stderr with the option name in place of the key for dates (`--from must be YYYY-MM-DD`, `--from is after --to`) and exits 2. `hit_line` prints the kind between the page id and the headline: `rank  score  date  page  kind  headline`.
- **FR-8** - Evals. `uv run newsrag eval retrieval` gets `--kinds` (comma list or `all`, default `all`) and passes it as the `kinds` filter, because the retrieval sets contain notice questions. `uv run newsrag eval short` searches with the defaults (no option). `run_retrieval(..., kinds=…)` gets the matching keyword.
- **FR-9** - Before/after record. Before changing any code, at the ticket's base commit, the worker runs
  `uv run newsrag eval retrieval --set data/eval/retrieval-2026.jsonl --out specs/tickets/035-search-filters/evidence/before-retrieval.json` and
  `uv run newsrag eval short --out specs/tickets/035-search-filters/evidence/before-short.json`,
  and after the work the same two commands with `after-` names. `results.md` has a table with, before and after: `by_lang.te|en|te_latn` `recall_at_1` and `recall_at_10` (retrieval), and `names.te|latin|typo.other_kind`, `real.all.other_kind`, `real.person.other_kind`, `stories.all.recall_at_10` (short).
- **FR-10** - Clarifications (gap check):
  - **Who gets the default kinds.** Every caller that sets no `kinds`, including the answer service (`answer/service.py` is not edited; it simply inherits the default). The evals that must stay comparable ask for every kind: `run_retrieval(kinds=None)` means `["all"]` (so `embed/bakeoff.py` and `baseline/compare.py` are unchanged), and `eval customer` and `eval answers` call search with `filters={"kinds": ["all"]}`. `eval short` uses the default.
  - **Other stores.** `hybrid.search(store != "new")` raises `ValueError("<store> store takes only the page_ids filter")` when the filters hold any other key, except that `kinds == ["all"]` is dropped before the backend is called. The CLI prints it and exits 2.
  - **`kinds` values.** `all` together with other kinds raises `ValueError("filter kinds: all cannot be combined with other kinds")`. A key whose value is `None` is treated as not given.
  - **Empty kind.** `hit_line` prints `-` when `Hit.kind` is empty.
  - **Reading `other_kind`.** The short test counts every story kind other than news and editorial, so briefs, tables and infographics still count after this ticket; the value is expected to fall, not to reach 0.

## Non-functional requirements

- **NFR-1** - No-regression guard: `by_lang.te.recall_at_10` in `after-retrieval.json` ≥ the value in `before-retrieval.json` − 0.02.
- **NFR-2** - With `kinds=["all"]` and no other filter, the requests sent to Qdrant and OpenSearch are the same as before this ticket apart from the added `kind` payload field (unit test on `bm25_body` and on the Qdrant filter being `None`).
- **NFR-3** - `median_search_ms` in `after-retrieval.json` ≤ 2 × the value in `before-retrieval.json`.

## Affected areas

- `src/newsrag/search/hybrid.py` - filter keys, validation, default kinds, both retrievers, `Hit.kind` (changed)
- `src/newsrag/search/cli.py` - filter options, kind column (changed)
- `src/newsrag/embed/records.py` - `kind` in `QDRANT_KEYWORD_INDEXES` (changed)
- `src/newsrag/embed/cli.py` - `ensure-payload-indexes` (changed)
- `src/newsrag/evals/retrieval.py`, `src/newsrag/evals/cli.py` - `--kinds` (changed)
- `src/newsrag/evals/customer.py`, `src/newsrag/evals/answers.py` - search with `kinds=["all"]` (changed)
- `src/newsrag/config.py` - `# --- search ---` block: `search_default_kinds` (changed)
- `tests/test_search.py`, `tests/test_eval_retrieval.py`, `tests/test_index.py` (changed)
- Base commit: before the first change the worker writes `git rev-parse HEAD` to `specs/tickets/035-search-filters/evidence/base-commit.txt`. Every "against the base commit" comparison and every `before-*.json` file refers to that commit; the diffs in eval.md compare it with the working tree.
- Test names: new tests are named so that every `pytest -k` selection in eval.md matches at least one of them.

## Don't touch

- `src/newsrag/baseline/search.py`: the baseline backend keeps refusing a page filter and gets no default kinds.
- `src/newsrag/stages/index.py` and its `VERSION_SETTINGS`: no date may be re-indexed by this ticket.
- `src/newsrag/answer/**` (ticket 039 works there at the same time); the answer service gets the default kinds without being edited.
- Store contents other than the added Qdrant payload index; `data/eval/**`.

## Out of scope

- Turning words of a question ("in October", "Guntur") into filters (ticket 041 does it for list questions only).
- Filters on `edition_states`, `department`, `low_confidence` or facts.
- Any UI.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Kinds returned when no kind filter is set | all except `notice_tender` | user chose |
| D-2 | How a caller asks for every kind | `kinds=["all"]` / `--kind all` | one explicit value; `None` already means "use the default" |
| D-3 | Kind used for the filter | the chunk's `kind` (a table inside a news story is `table`) | it is the field both stores hold |
| D-4 | `eval retrieval` default | `--kinds all` | the committed sets contain notice questions; numbers stay comparable with earlier runs |
| D-5 | Done bar | before/after report plus NFR-1 | user chose "report, with a no-regression guard" |
| D-6 | Qdrant index on `kind` | added in place by a command, no re-index | `create_payload_index` works on an existing collection |

## Open questions

None.
