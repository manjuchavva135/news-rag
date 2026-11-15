# 041: Answer list-type questions from a dated story listing

**Status:** draft
**Mode:** solo
**Runs:** waits for 038: edits the same `answer/service.py` (038 already waits for 035, 036, 037, 039 and 040, whose filters, name resolution, story sources and event dates this ticket uses)

## Summary

Staff ask for "all articles on X", a timeline, the latest on someone, or type just a name
("chandra babu"). A top-12 search cannot answer these: staff rated such answers "limited items",
missed timelines and got old figures for "latest". The warehouse knows which stories name each
person and organisation, with dates. This ticket recognises these questions and answers them from
the 30 most recent stories about the entity, newest first, telling the reader how many more exist.

## Functional requirements

- **FR-1** - Route. New `src/newsrag/answer/route.py: decide(question, names, llm, today) -> Route(kind, entity_ids, canonicals, date_from, date_to)`, `kind` ∈ `"search" | "list" | "timeline" | "latest"`. `names` is `newsrag.search.names.resolve(question)` (ticket 036).
  - No resolved name → `kind="search"`, no model call.
  - **Bare name:** every word of the question belongs to a resolved name span → `kind="latest"`, no model call, no dates.
  - Otherwise one call of the session LLM (`thinking=False`, `max_tokens = answer_route_max_tokens`, 128) with the new prompt `src/newsrag/prompts/answer_route_v1.md` (placeholders `{question}`, `{today}`), which asks for JSON only `{"kind": "search"|"list"|"timeline"|"latest", "date_from": "YYYY-MM-DD"|null, "date_to": "YYYY-MM-DD"|null}` and defines: `list` = the user wants all or many articles, news or coverage about the person or organisation; `timeline` = the user wants the events in order of time; `latest` = the user wants the newest news, update or status; `search` = any other question (a fact, a reason, a number, a comparison); dates only when the question names a period, resolved against `{today}`.
  - A reply that is not that JSON, an unknown `kind`, a date `date.fromisoformat` rejects, or `date_from > date_to` → `kind="search"` (one call, no retry). An LLM error → `kind="search"`.
  - `entity_ids` = for each resolved name its first entity id (the one with the most stories); `canonicals` = their canonical names.
- **FR-2** - Listing. New `src/newsrag/answer/listing.py: list_stories(entity_ids, date_from, date_to, limit) -> Listing(stories: list[StoryInfo], total: int)` over `warehouse`:
  - a story qualifies when it is linked in `story_entities` to **every** id of `entity_ids`, its `kind` is in `search_default_kinds` (ticket 035), its `event_date` is within `date_from..date_to` (inclusive, when given), and it is **about** the entity: for each entity, one of the link's `surface_forms` occurs in the headline, or the surface forms occur at least `answer_list_min_mentions` (2) times in the text (compared after NFC and removal of zero-width characters, as `evals.short.norm` does);
  - order: `event_date` descending, then `pub_date` descending, then `story_id`;
  - `stories` = the first `limit`; `total` = the number of qualifying stories.
- **FR-3** - In `service.ask`, when the setting `answer_route` is `True` (default) and the route kind is not `search`:
  - `list_stories(route.entity_ids, route.date_from, route.date_to, answer_list_n)` (`answer_list_n` = 30). When `total` is 0 the turn falls back to the normal search path (`route="search"`).
  - Each story becomes a story source (ticket 039) with its text cut to `answer_list_source_chars` (800); thread numbers, the `answer_sources_tokens` budget and the header (ticket 040) work as for search sources. No search runs and no carried sources are added. For `timeline` the sources are given oldest first.
  - The user message gets, between the question and `Sources:`, the block
    `These are the <n> most recent of <total> stories about <canonicals joined with ", ">[ with an event date from <date_from> to <date_to>], <newest first|oldest first>.` followed by the text of the new file `src/newsrag/prompts/answer_list_v1.md`, which tells the model: write one dated bullet per story, starting with the event date, in the order given, each with its citation; do not merge stories; when `<total>` is larger than `<n>`, end with one sentence, without a citation, saying that `<total − n>` older stories are not shown.
  - `Answer` gets `route: str` and `total_stories: int | None`; the turn stores the listing's sources like any others.
- **FR-4** - CLI. `uv run newsrag answer ask|chat` print `route: <kind> (<n> of <total> stories about <canonicals>)` above the answer when the route is not `search`. `uv run newsrag answer sources "<question>"` (ticket 039) gets `--route auto|search|list|timeline|latest` (default `auto`): `auto` applies FR-1 without a model (bare names route to `latest`, everything else to `search`); a forced kind skips the decision and needs at least one resolved name, else it prints `no name found in the question` and exits 1. It prints the same `route:` line first.
- **FR-5** - Settings in the `# --- answer ---` block: `answer_route: bool = True`, `answer_route_max_tokens: int = 128`, `answer_list_n: int = 30`, `answer_list_source_chars: int = 800`, `answer_list_min_mentions: int = 2`.
- **FR-6** - Clarifications (gap check):
  - **"About" test.** Surface forms are compared as stored (90% of them occur word for word in their story) and also with their spaces removed, as `evals.short.surface_forms` does. Occurrences are counted over headline and text together; one occurrence in the headline is enough.
  - **Missing event date.** A NULL `event_date` counts as the `pub_date` for the order and the date range (no row has one today).
  - **Order inside a turn.** The follow-up rewrite runs first; `names.resolve` and `decide` work on the rewritten `search_query`. On a listing turn ticket 038's Telugu form is not computed: `search_also = []`, `telugu_form_how = "none"`.
  - **`<n>`.** In the route line, the message block and the "older stories" sentence, `<n>` is the number of sources actually sent after the `answer_sources_tokens` budget (at most `answer_list_n`).
  - **Listing sources.** Their `chunk_id` is `""`; they are numbered by `story_id`.
  - **Route reply.** Parsed with `newsrag.models.client.parse_json_reply` (a fenced JSON reply is accepted). The system string of the call is `You classify questions.`
  - **Answer call of a listing turn.** `thinking=False` and `max_tokens = answer_max_tokens`, so 30 bullets fit.

## Non-functional requirements

- **NFR-1** - Search is untouched: `git diff` of `src/newsrag/search/` against the base commit is empty (retrieval numbers cannot change).
- **NFR-2** - A question with no resolved name makes no extra model call (unit test counts the calls of a fake LLM).
- **NFR-3** - `list_stories` for an entity with more than 500 linked stories returns in ≤ 2 s (`stores`-marked test on the real warehouse with the entity whose canonical is `చంద్రబాబు`, read only).
- **NFR-4** - `decide` takes `names` and `llm` as arguments and `service.ask` takes `list_fn=`, so unit tests use fakes.

## Affected areas

- `src/newsrag/answer/route.py`, `src/newsrag/answer/listing.py` (new)
- `src/newsrag/prompts/answer_route_v1.md`, `src/newsrag/prompts/answer_list_v1.md` (new)
- `src/newsrag/answer/service.py` - the route in a turn, the listing message (changed)
- `src/newsrag/answer/context.py` - sources from a listing (changed)
- `src/newsrag/answer/cli.py` - `route:` line, `--route` (changed)
- `src/newsrag/config.py` - `# --- answer ---` block (changed)
- `tests/test_answer_route.py` (new), `tests/test_answer.py` (changed)
- Base commit: before the first change the worker writes `git rev-parse HEAD` to `specs/tickets/041-list-question-routing/evidence/base-commit.txt`. Every "against the base commit" comparison and every `before-*.json` file refers to that commit; the diffs in eval.md compare it with the working tree.
- Test names: new tests are named so that every `pytest -k` selection in eval.md matches at least one of them.

## Don't touch

- `src/newsrag/search/**`: names are read through `names.resolve`, nothing changes there.
- `src/newsrag/answer/threads.py`, `src/newsrag/answer/sql/`: no schema change.
- `src/newsrag/prompts/answer_chat_v2.md`, `answer_rewrite_v1.md`; `src/newsrag/evals/**`; the warehouse tables (read only).

## Out of scope

- List questions about a topic or a place that is not a person or organisation in the alias table ("all articles on aqua feed"): they stay normal searches.
- More than 30 stories in one answer, paging, or "show the next 30".
- "As of June" figures from the `facts` table.
- Follow-up turns that refer to a listing ("and before that?") beyond what the rewrite step already does.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | What a list answer is built from | the 30 most recent stories about the entity, newest first, with the count of the rest | user chose "Latest 30 stories, newest first" |
| D-2 | A bare name ("chandra babu") | routed as `latest`, without a model call | user chose it; staff rated the search answer to a bare name "limited items" |
| D-3 | How list questions are recognised | one small LLM call, only when a name was resolved | keyword rules cannot cover Telugu, English and Romanized phrasing; the call is skipped for most questions |
| D-4 | "About" the entity | named in the headline or at least twice in the text | a passing mention must not fill the listing (same idea as the short-query test's `about`) |
| D-5 | Several entities | stories linked to all of them | "X and Y" asks for stories with both |
| D-6 | Timeline | the same 30 most recent stories, shown oldest first | keeps the cap the user chose; the answer says how many older ones are not shown |
| D-7 | Notices | left out, as in search (`search_default_kinds`) | user chose "all except notices" in ticket 035 |
| D-8 | Done bar | tests, a real routed answer (E-4) and NFR-1 | this ticket does not change search; the guard is "search code unchanged" |

## Open questions

None.
