# 028: Customer-question answer check

**Status:** done
**Mode:** solo
**Runs:** waits for 025: needs the search script

## Summary

A thin test script that answers the customer's 30 real Telugu questions from the new stores with a
local LLM, then has a second local model check every claim against the cited sources, and writes the
answers next to the old system's answers for the user to read (grill D-14, D-16, D-21). This is not the
phase-2 answer service; it only proves the new data supports good answers.

## Functional requirements

- **FR-1** - `data/eval/customer-questions.json` is a copy of `/home/admin1/RAG/evals/customer-questions.json` (30 questions, ids Q01–Q30).
- **FR-2** - `uv run newsrag eval customer [--k 12]` runs in three passes so only one model is loaded at a time: (1) `search(k=12)` for all 30 questions on the new store (active generation); (2) with `newsrag.models.use(answer_model)` (setting `answer_model` in `config.py`, default `qwen3-30b-a3b`; temperature 0, max_tokens 2048) a Telugu answer per question using only the numbered sources, citing them as `[n]`, saying clearly when the sources don't cover something, and never inventing dates or numbers (prompt `src/newsrag/prompts/answer_check_v1.md`; each source as `[n] <headline> | <pub_date> | <edition names> | page <canonical_page_id>` + its text cut to 1,500 chars; edition names and canonical page from `warehouse.stories`/`warehouse.pages`); (3) the claim check (FR-3). An answer request that fails is retried 2 times; then the question is recorded `failed`. A question with 0 hits gets the answer `మూలాలు దొరకలేదు` (no sources) and 0 claims.
- **FR-3** - Claim check (judge = setting `answer_judge_model` from ticket 032 when set, else `judge_model`): code splits each answer into units — sentences (ends `?`, `!`, `।`, newline, and `.` not between digits) with each bullet item and table row being one unit; a unit containing `[n]` is a *cited claim*. The judge model (`judge_model`, text only, a different family from `answer_model`, temperature 0) labels each cited claim `supported` | `not_supported` against the text of the sources it cites (supported if any cited source supports it; a citation outside 1..k counts as not supported; an unparseable judge reply after 2 retries counts as not supported), and labels each uncited unit `factual` | `not_factual` (meta sentences such as "the sources do not cover this"). `supported_pct` = supported / all cited claims × 100 over answered questions (0 when there are no cited claims); `uncited` = uncited factual units; `n_failed` = failed questions.
- **FR-4** - Output `<reports_dir>/customer-answers.md`: per question — question, new answer, sources list (headline, date, editions, page_id), per-claim verdicts, and the old system's answer for the same question extracted from `/home/admin1/RAG/evals/results-2026-07-11.md`. Plus `<reports_dir>/customer-answers.json` with `{run_id (UTC timestamp), supported_pct, n_claims, uncited, n_failed, per_question:[...]}`. Prints `supported_pct=<x> n_claims=<n> uncited=<n> n_failed=<n>`. Starting a new run renames an existing sign-off file to `customer-answers-signoff.json.old` (the new answers need a new reading).
- **FR-5** - `uv run newsrag eval customer-signoff --name "<reader>" [--note "..."]` writes `<reports_dir>/customer-answers-signoff.json` `{name, note, signed_at, supported_pct, answers_sha1, run_id}` (sha1 of the current `customer-answers.json`) — the record that the user read these answers (used by tickets 030/031). Without `customer-answers.json` it exits 1 with `no customer answers to sign`.
- **FR-6** - Clarifications (second review):
  - If `judge_model` (ticket 013's setting, default `gemma3-27b`, used here as a text model) and `answer_model` share a `family`, the command exits 2 with `judge_model and answer_model must be different families`.
  - Pass 1 runs all 30 searches inside one `use(embed_profile)` scope; ticket 006's re-entrant `use()` lets `search()` reuse it.
  - Source header edition names = union over all printings' pages (`warehouse.story_printings` → `warehouse.pages.edition_names`); the page shown is `canonical_page_id`.
  - The answer prompt receives `{today}` (the run date) and `{covered_years}` (distinct years of `pub_date` in the active generation) and must say which asked-for years are not covered; such coverage sentences are uncited meta units (`not_factual`).

## Non-functional requirements

- **NFR-1** - The whole run (30 questions) finishes in < 45 min including three model starts (downloads excluded).

## Affected areas

- `data/eval/customer-questions.json` (new); `src/newsrag/evals/customer.py`, `src/newsrag/prompts/answer_check_v1.md`, `claim_check_v1.md` (new); `src/newsrag/cli.py`, `models.yaml` (if a profile is added) (changed)
- `tests/test_eval_customer.py` (new, fake models)
- `src/newsrag/config.py` - `answer_model` (changed)

## Don't touch

- `/home/admin1/RAG/**` (read only).

## Out of scope

- An answer API or UI, query rewriting, conversation history (phase 2).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Answer model | local LLM only | grill D-16 |
| D-2 | Judge | local model of another family | same rule as grouping (A-9) |
| D-3 | Bar | ≥ 90% of cited claims supported, then the user reads them | grill D-21 |

## Open questions

None.
