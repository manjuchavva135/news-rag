# 041: Answer list-type questions from a dated story listing - Evaluation

## Setup

- Prerequisite: tickets 035–040 are merged; `uv run newsrag answer init` was run; `uv run newsrag aliases stats` prints entity counts.
- Start: `export PATH=$HOME/.local/bin:$PATH && uv sync && uv run newsrag stores up && uv run newsrag models status` (prints `GPU free`)
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check . && uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

- Every `pytest -k` selection below must run at least one test (pytest exit code 5, "no tests ran", is a failure)
- `cat specs/tickets/041-list-question-routing/evidence/base-commit.txt` prints one commit hash (the base commit, see rd.md "Affected areas")

## Acceptance checks

### E-1: The route decision (covers FR-1, NFR-2, NFR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_answer_route.py -k "decide or route"`
  2. `grep -c "{question}" src/newsrag/prompts/answer_route_v1.md; grep -c "{today}" src/newsrag/prompts/answer_route_v1.md`
- **Expected:** step 1 passes with fake names and a fake LLM, including: no name → `search` and 0 calls; a question made only of name words → `latest` and 0 calls; a name plus other words → exactly 1 call with `thinking=False`, `max_tokens=128`; replies `{"kind":"list",…}`, `timeline`, `latest` give those kinds with parsed dates; a non-JSON reply, `{"kind":"other"}`, a bad date, `date_from > date_to` and an LLM exception each give `search`; `entity_ids` holds the first id of every name. Step 2 prints `1` and `1`.

### E-2: The listing query (covers FR-2, NFR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_answer_route.py`
- **Expected:** all pass, none skipped: in a throw-away warehouse schema, stories linked to the entity are returned newest `event_date` first (ties by `pub_date`, then `story_id`); a story naming the entity once in the text and not in the headline is left out and one naming it twice is kept; a `notice_tender` story is left out; a date range keeps only event dates inside it; with two entities only stories linked to both are returned; `total` counts all qualifying stories and `stories` has `limit` of them; on the real warehouse (read only) the listing for the entity `చంద్రబాబు` returns 30 stories with `total` > 30 in ≤ 2 s.

### E-3: A routed turn (covers FR-3, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_answer.py -k "route or listing"`
- **Expected:** all pass with fakes, including: a `latest` route calls `list_fn` with `answer_list_n` and runs no search; the user message contains `These are the 30 most recent of 676 stories about చంద్రబాబు, newest first.` (and `28 most recent` when the budget drops two sources) and the text of `answer_list_v1.md`; a date range adds ` with an event date from 2026-10-01 to 2026-10-31`; `timeline` gives the sources oldest first and says `oldest first`; each source text is at most 800 characters; `total == 0` falls back to search with `route == "search"`; `answer_route=False` never routes; no carried sources are added to a listing; the answer call of a listing turn has `thinking=False`; the Telugu form is not computed; `decide` receives the rewritten query; `Answer.route` and `Answer.total_stories` are set.

### E-4: Real questions (covers FR-3, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag answer sources "chandra babu" > /tmp/e4-041.txt; head -1 /tmp/e4-041.txt; grep -cE "^\[[0-9]+\]" /tmp/e4-041.txt`
  2. `grep -oE "event [0-9-]{10}" /tmp/e4-041.txt | awk '{print $2}' > /tmp/e4-dates.txt; sort -r /tmp/e4-dates.txt | diff - /tmp/e4-dates.txt && echo ORDERED`
  3. `uv run newsrag answer sources "chandra babu" --route timeline | grep -oE "event [0-9-]{10}" | awk '{print $2}' > /tmp/e4-tl.txt; sort /tmp/e4-tl.txt | diff - /tmp/e4-tl.txt && echo ORDERED`
  4. `uv run newsrag answer ask "chandra babu latest news" | tee /tmp/e4-ask.txt | grep -E "^route: "`
  5. `grep -cE "^\s*[-•*] " /tmp/e4-ask.txt`
- **Expected:** step 1 prints `route: latest (<n> of <total> stories about చంద్రబాబు)` with 1 ≤ n ≤ 30 and total > 30, then the same number n; step 2 prints `ORDERED` (event dates newest first); step 3 prints `ORDERED` (oldest first); step 4 prints a line starting `route: latest (` or `route: list (`; step 5 prints a number ≥ 5 (dated bullets). The tester copies the answer into `results.md`.

## Edge cases

### E-5: No name, forced route without a name, plain fact question (covers FR-1, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag answer sources "వర్షాలు" | grep -c "^route:"`
  2. `uv run newsrag answer sources "వర్షాలు" --route list; echo exit=$?`
  3. `uv run newsrag answer ask "చంద్రబాబు ఏ పార్టీ అధ్యక్షుడు?" | grep -c "^route:"`
- **Expected:** step 1 prints `0`; step 2 prints `no name found in the question` and `exit=1`; step 3 prints `0` (a fact question with a name stays a search); the live model decides this, so step 3 may be run a second time and passes when either run prints `0`.

### E-6: Nothing else changed (covers NFR-1, Don't touch)

- **Type:** CLI
- **Steps:**
  1. `git diff --stat $(cat specs/tickets/041-list-question-routing/evidence/base-commit.txt) -- src/newsrag/search src/newsrag/evals src/newsrag/answer/threads.py src/newsrag/answer/sql src/newsrag/prompts/answer_chat_v2.md src/newsrag/prompts/answer_rewrite_v1.md`
  2. `uv run pytest -q tests/test_answer.py tests/test_search.py tests/test_search_names.py`
- **Expected:** step 1 prints nothing; step 2 passes.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-5 |
| FR-2 | E-2 |
| FR-3 | E-3, E-4 |
| FR-4 | E-4, E-5 |
| FR-5 | E-3 |
| FR-6 | E-1, E-2, E-3 |
| NFR-1 | E-6 |
| NFR-2 | E-1 |
| NFR-3 | E-2 |
| NFR-4 | E-1, E-3 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (`e4-041.txt`, `e4-ask.txt`, command outputs) saved in `evidence/` and listed in `results.md`
