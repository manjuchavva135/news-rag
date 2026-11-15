# 040: Show the event date in every source - Evaluation

## Setup

- Prerequisite: ticket 039 is merged; `uv run newsrag answer init` was run.
- Start: `export PATH=$HOME/.local/bin:$PATH && uv sync && uv run newsrag stores up && uv run newsrag models status` (prints `GPU free`)
- Base URL: n/a
- Test account: n/a
- Seed / reset: none
- Test story: `s_025007109c2dd9bb` (headline `‘సమ్మక్క’ డీపీఆర్ వెనక్కి`, printed 2026-11-10, event 2026-10-15). Confirm with `uv run newsrag db sql "select pub_date, event_date from warehouse.stories where story_id = 's_025007109c2dd9bb'"`; if the row is gone, pick another with `uv run newsrag db sql "select story_id, headline, pub_date, event_date from warehouse.stories where kind = 'news' and event_date < pub_date - 7 order by story_id limit 1"` and use its headline and dates below.

## Automated checks

- `uv run ruff check . && uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

- Every `pytest -k` selection below must run at least one test (pytest exit code 5, "no tests ran", is a failure)
- `cat specs/tickets/040-event-date-sources/evidence/base-commit.txt` prints one commit hash (the base commit, see rd.md "Affected areas")

## Acceptance checks

### E-1: Header, record and old turns (covers FR-1, FR-2, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_answer.py -k "event or header or record"`
- **Expected:** all pass, including: a story source with print date 2026-11-10 and event date 2026-10-15 has the header `[1] <headline> | printed 2026-11-10 | event 2026-10-15 | <editions> | page <page>`; equal dates are both shown; a chunk source and an old record without `event_date` get `event_date == pub_date`; `record()` has the key `event_date`.

### E-2: The prompt (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `diff <(grep -v '"printed" is the day' src/newsrag/prompts/answer_chat_v2.md) src/newsrag/prompts/answer_chat_v1.md && echo SAME`
  2. `grep -c '"event" is the day the event happened' src/newsrag/prompts/answer_chat_v2.md`
  3. `uv run python -c "from newsrag.answer import service;print(service.SYSTEM_PROMPT.name)"`
- **Expected:** step 1 prints `SAME`; step 2 prints `1`; step 3 prints `answer_chat_v2.md`.

### E-3: A real source and a real answer (covers FR-2, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag answer sources "సమ్మక్క డీపీఆర్ వెనక్కి" | grep "సమ్మక్క" | head -1`
  2. `uv run newsrag answer ask "సమ్మక్క ప్రాజెక్టు డీపీఆర్‌ను ఎప్పుడు వెనక్కి పంపారు?" | tee /tmp/e3-040.txt | grep -c "printed 2026"`
- **Expected:** step 1 prints a header containing `| printed 2026-11-10 | event 2026-10-15 |`; step 2 prints a number ≥ 1 (the cited-source lines use the new header). The tester copies the answer text from `/tmp/e3-040.txt` into `results.md`; the check passes when the answer does not state 10 November 2026 (`2026-11-10`, `నవంబర్ 10`, `10 నవంబర్`) as the day the DPR was returned.

## Edge cases

### E-4: Nothing else changed (covers NFR-1, Don't touch)

- **Type:** CLI
- **Steps:**
  1. `git diff --stat $(cat specs/tickets/040-event-date-sources/evidence/base-commit.txt) -- src/newsrag/search src/newsrag/evals src/newsrag/prompts/answer_chat_v1.md src/newsrag/answer/threads.py src/newsrag/answer/sql`
  2. `uv run pytest -q tests/test_answer.py tests/test_eval_answers.py`
- **Expected:** step 1 prints nothing; step 2 passes.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-1, E-3 |
| FR-3 | E-2 |
| FR-4 | E-3 |
| NFR-1 | E-4 |
| NFR-2 | E-1 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (`e3-040.txt`, command outputs) saved in `evidence/` and listed in `results.md`
