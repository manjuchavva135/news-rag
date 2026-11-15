# 040: Show the event date in every source

**Status:** draft
**Mode:** solo
**Runs:** waits for 039: edits the same `answer/context.py` and reads `event_date` from its story loader

## Summary

A newspaper prints an event the day after it happens, and sometimes weeks later. The answer model
sees only the print date of each source, so it reports the print date as the day of the event; staff
flagged this. The warehouse stores an `event_date` for every story (it differs from the print date
for 4,892 of 9,525 stories). This ticket shows both dates in each source header and tells the model
which one to use.

## Functional requirements

- **FR-1** - `Source` gets `event_date: str = ""`. For a story source it is `StoryInfo.event_date` (ticket 039). For a chunk source and for a record of an earlier turn that has no `event_date`, it is the source's `pub_date`. `record()` stores `event_date`.
- **FR-2** - Header. `Source.header()` returns
  `[<n>] <headline> | printed <pub_date> | event <event_date> | <editions or -> | page <page_id>`.
  Both dates are always shown, also when they are equal.
- **FR-3** - Prompt. New file `src/newsrag/prompts/answer_chat_v2.md` = `answer_chat_v1.md` plus this rule, placed after the rule that starts "Never invent or guess dates":
  `- Each source shows two dates: "printed" is the day the newspaper printed the story, "event" is the day the event happened. When you say when something happened, give the event date. Give the print date only when the question asks when it was reported or published, and then call it the print date. "Latest" and "most recent" mean the newest event date. When sources disagree, give each one's event date.`
  The rule is one line of the file.
  `service.SYSTEM_PROMPT` points to `answer_chat_v2.md`. `answer_chat_v1.md` stays in the repo unchanged.
- **FR-4** - `uv run newsrag answer sources` (ticket 039) and the cited-source lines of `answer ask` print the new header (they already use `Source.header()`).

## Non-functional requirements

- **NFR-1** - Search is untouched: `git diff` of `src/newsrag/search/` against the base commit is empty.
- **NFR-2** - Turns stored before this ticket still load, and their sources get `event_date = pub_date` (unit test with a record lacking the key).

## Affected areas

- `src/newsrag/answer/context.py` - `Source.event_date`, header, `record()`, `_from_record` (changed)
- `src/newsrag/answer/service.py` - `SYSTEM_PROMPT` (changed)
- `src/newsrag/prompts/answer_chat_v2.md` (new)
- `tests/test_answer.py` (changed)
- Base commit: before the first change the worker writes `git rev-parse HEAD` to `specs/tickets/040-event-date-sources/evidence/base-commit.txt`. Every "against the base commit" comparison and every `before-*.json` file refers to that commit; the diffs in eval.md compare it with the working tree.
- Test names: new tests are named so that every `pytest -k` selection in eval.md matches at least one of them.

## Don't touch

- `src/newsrag/prompts/answer_chat_v1.md`, `src/newsrag/evals/**` (the evals keep the ticket 028 header and prompt).
- `src/newsrag/search/**`, `src/newsrag/answer/threads.py`, `src/newsrag/answer/sql/`.
- How `event_date` is computed (ticket 019).

## Out of scope

- Sorting or filtering sources by event date (ticket 041 orders its listings by it).
- Correcting wrong event dates in the warehouse.
- Showing dates in another format than `YYYY-MM-DD`.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Show the event date only when it differs? | always show both, labelled | one fixed header shape; the model never has to infer which date it sees |
| D-2 | Edit the prompt in place or add a revision | new `answer_chat_v2.md` | prompts are versioned by file in this repo |
| D-3 | Old thread records | `event_date = pub_date` | no schema change; nothing better is stored for them |
| D-4 | Done bar | tests plus one real answer check (E-3) | this ticket does not change search; the guard is "search code unchanged" |

## Open questions

None.
