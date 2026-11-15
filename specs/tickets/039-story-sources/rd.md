# 039: Answer from whole stories, one source per story

**Status:** draft
**Mode:** solo
**Runs:** alongside

## Summary

The answer service gives the model the top 12 chunks, each cut to 1,500 characters. Two chunks of
one story show up as two sources (staff complained about duplicate sources), and a long story is
seen only in pieces. This ticket makes a source a story: hits are collapsed by story and the model
gets the story's full text from the warehouse, within a size budget. It also adds a command that
prints the sources of a question without calling the answer model, so the result can be checked.

## Functional requirements

- **FR-1** - Story loader. New `src/newsrag/answer/stories.py: load_stories(story_ids) -> dict[story_id, StoryInfo]` reading `warehouse`: `StoryInfo(story_id, kind, headline, text, pub_date: str, event_date: str, page_id: str, editions: list[str])`. `pub_date`/`event_date` are ISO strings; a NULL `event_date` (the column allows it; no row has one today) becomes the `pub_date`; `page_id` is `stories.canonical_page_id` (or `""`); `editions` is the sorted union of `pages.edition_names` over the story's printings (same rule as `evals.customer.story_meta_from_rows`). A story id not in the warehouse is absent from the result.
- **FR-2** - One source per story. `service.ask` searches with `k = answer_search_k` (new setting, 30) and `context.sources_for` collapses the hits by `story_id` in rank order (a story takes the rank of its best hit) and keeps the first `answer_k` (12) stories. A hit with an empty `story_id`, or whose story `load_stories` did not return, stays a chunk source as today (text = the hit's text cut to `answer_source_chars`).
- **FR-3** - Source text. For a story source:
  - the story's `text` when it has at most `answer_story_chars` (new setting, 6000) characters;
  - otherwise the texts of that story's hit chunks in `seq` order (`seq` = the number after `#c` in the `chunk_id`), joined with `"\n[…]\n"`, cut to `answer_story_chars`;
  - then, in both cases, for each hit chunk of the story whose chunk `kind` (from `warehouse.chunks`) is `table` or `infographic`: a blank line and the chunk's text, unless that text is already contained in the source text. The appended parts do not count against `answer_story_chars`.
- **FR-4** - Budget. Sources are added in rank order while the sum of `est_tokens(source.block())` stays within `answer_sources_tokens` (new setting, 16000); the first source is always kept, cut to fit when it alone is over the budget. Sources that do not fit are dropped (not cut). Carried sources (FR-6) are added after the fresh ones under the same budget.
- **FR-5** - `Source` keeps its fields; `chunk_id` is the story's best hit chunk; `headline` is the story's headline (whitespace collapsed); `pub_date` the story's; new field `kind: str` (the story kind; `""` for a chunk source). `record()` stores `kind` too.
- **FR-6** - Numbers in a thread. A source's number is kept per **story** for the whole thread: the key is `story_id` when it is not empty, else `chunk_id`. Records of earlier turns (which were chunk sources) are keyed the same way, so a story cited earlier as `[3]` is `[3]` again; when earlier turns hold several records of one story, the lowest number wins. A carried source is not added when its story is among the fresh sources.
- **FR-7** - New command `uv run newsrag answer sources "<question>" [--thread ID]` prints, without calling the answer model: for each source one line `<header>  (<len(text)> chars[, carried])` (the header of `Source.header()`), then `sources: <n>  est_tokens: <sum>`. With `--thread` the thread's turns are used for numbers and carried sources; the question is searched as typed (no rewrite). How it loads the embedder: see FR-9. An unknown thread prints `no thread <id>` and exits 1.
- **FR-8** - Settings in the `# --- answer ---` block: `answer_search_k: int = 30`, `answer_story_chars: int = 6000`, `answer_sources_tokens: int = 16000`. `answer_k` now means stories; its comment is updated. `answer_source_chars` stays for chunk sources.
- **FR-9** - Clarifications (gap check):
  - **Hits whose story is not loaded.** They are collapsed by `story_id` like the others: one source per story, text = the best hit's text cut to `answer_source_chars`, numbered by `story_id`. Only a hit with an empty `story_id` is its own source, numbered by `chunk_id`. Either kind takes one of the `answer_k` places. Existing tests that expect one source per chunk of the same story are changed to this rule.
  - **Chunk kinds.** `stories.load_chunk_kinds(chunk_ids, generation=None) -> dict[chunk_id, kind]` reads `warehouse.chunks` of the active generation in one SQL statement (separate from NFR-2, which limits `load_stories`). `service.ask` passes the result to `sources_for(..., chunk_kinds=...)`; a chunk missing from it is treated as not a table.
  - **Budget.** Sources are taken in rank order and the list ends at the first one that does not fit (later, smaller ones are not tried). New numbers are given after the budget cut, so a dropped source uses no number.
  - **Several earlier records of one story.** The lowest number and its record are used (also for the carried text). The other numbers of that story are never given to another story.
  - **`answer sources`.** It does not go through `service.session` and creates no LLM: it searches like `newsrag search` (the query embedder through `hybrid`, on the GPU, waiting for the GPU lock like any other command) and closes it at the end. This replaces the device sentence of FR-7.

## Non-functional requirements

- **NFR-1** - Search is untouched: `git diff` of `src/newsrag/search/` against the base commit is empty (this is the no-regression guard of this ticket: retrieval numbers cannot change).
- **NFR-2** - `load_stories` uses at most 2 SQL statements per call, whatever the number of ids.
- **NFR-3** - `sources_for` stays a pure function: the story loader's result is passed in (`stories=`), so unit tests use dicts.

## Affected areas

- `src/newsrag/answer/stories.py` - `StoryInfo`, `load_stories` (new)
- `src/newsrag/answer/context.py` - collapse by story, text rule, budget, story-keyed numbers (changed)
- `src/newsrag/answer/service.py` - `answer_search_k`, passes the stories to `sources_for` in place of `story_meta` (changed)
- `src/newsrag/answer/cli.py` - `sources` command (changed)
- `src/newsrag/config.py` - `# --- answer ---` block (changed)
- `tests/test_answer.py` (changed), `tests/test_answer_stories.py` (new, `stores`-marked part uses a throw-away warehouse schema)
- Base commit: before the first change the worker writes `git rev-parse HEAD` to `specs/tickets/039-story-sources/evidence/base-commit.txt`. Every "against the base commit" comparison and every `before-*.json` file refers to that commit; the diffs in eval.md compare it with the working tree.
- Test names: new tests are named so that every `pytest -k` selection in eval.md matches at least one of them.

## Don't touch

- `src/newsrag/search/**` (tickets 035–037 work there).
- `src/newsrag/evals/**`: `eval customer` and `eval answers` keep their own chunk sources (`customer.make_sources`).
- `src/newsrag/answer/threads.py` and `src/newsrag/answer/sql/`: no schema change; old turns must still load.
- `src/newsrag/prompts/answer_chat_v1.md` (ticket 040 changes the prompt).

## Out of scope

- Showing the event date in the source header (ticket 040).
- Listing stories by entity and date (ticket 041).
- Measuring answer quality with the answer service in `eval answers` (the eval keeps the ticket 028 path).
- Merging the same story printed on different dates.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | What a source is | a story (one per `story_id`) | removes duplicate sources; the retrieval unit is the story (grill A-11) |
| D-2 | How much text | the whole story up to 6,000 characters; above that the hit chunks | 95% of news stories are under 3,900 characters, the longest is 27,000 |
| D-3 | Tables and infographics | hit chunks of kind `table`/`infographic` are appended | 257 such chunks belong to news stories whose `text` may not hold the table |
| D-4 | Total size | 16,000 estimated tokens of sources | `qwen3-30b-a3b` has a 32,768-token window that also holds history, reasoning and the answer |
| D-5 | Chunks fetched | 30, collapsed to 12 stories | 12 chunks often collapse to fewer than 12 stories |
| D-6 | Done bar | unit and store tests plus NFR-1 | this ticket does not change search, so the retrieval guard is "search code unchanged" |

## Open questions

None.
