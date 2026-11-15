# 038: Search Latin-letter questions in Telugu too (transliterate or translate)

**Status:** draft
**Mode:** solo
**Runs:** waits for 037: edits the same `search/hybrid.py`; waits for 040: edits the same `answer/service.py`

## Summary

47% of the questions staff type are in Latin letters: English, or Telugu typed in English letters.
The stories are in Telugu script, so these questions search badly: the right story is at rank 1 for
36% of English and 48% of Romanized Telugu questions, against 73% for Telugu script. Ticket 034
showed that rewriting a Romanized question in Telugu script lifts rank 1 from 48% to 63%, but that
step is used only by the evals and `newsrag search --translit`. This ticket puts it into the answer
path, adds translation of English questions into Telugu, and searches with the typed question and
its Telugu form together.

## Functional requirements

- **FR-1** - Telugu form. New `src/newsrag/search/telugu_form.py`:
  `telugu_form(query: str, ask: Ask) -> TeluguForm(query: str, text: str | None, how: str, failed: bool)`, `how` ∈ `"none" | "translit" | "translate"`.
  - A query is **Latin-letter** when `script_shares(query)` gives a Latin share ≥ `retrieval_lang_share` (0.5) and it has no Telugu letter. Any other query: `how="none"`, `text=None`, no model call.
  - A Latin-letter query that `newsrag.evals.retrieval.is_romanized_telugu` accepts: `how="translit"`, `text` from the existing `translit.to_telugu(query, ask)` (prompt `translit_v1.md`, unchanged).
  - Any other Latin-letter query (English, or names only): `how="translate"`, with the new prompt `src/newsrag/prompts/translate_query_v1.md`. The prompt tells the model: translate the search query or question into Telugu as a Telugu newspaper would write it; write names of people, places, parties and organisations in Telugu script the way Telugu newspapers print them; keep numbers and dates; add nothing, answer nothing; reply with JSON only `{"telugu": "..."}`. It contains the placeholder `{query}` and these three examples: `chandra babu` → `చంద్రబాబు`; `Who won the Jubilee Hills by-election?` → `జూబ్లీహిల్స్ ఉప ఎన్నికలో ఎవరు గెలిచారు?`; `aqua feed prices` → `ఆక్వా మేత ధరలు`.
  - Replies are parsed and checked exactly like `translit.parse` (JSON with a non-empty `telugu` whose Telugu-letter share is ≥ `retrieval_lang_share`), with at most `retrieval_max_tries` (3) calls in total. When every try fails: `text=None`, `failed=True`.
- **FR-2** - Search with several query texts. `hybrid.search_detailed` and `search` (store `new`) get `also: Sequence[str] = ()`: extra query texts. Each text (the query and each `also` text) is embedded and sent to BM25 with the same filters; all their rankings (two per text) plus ticket 036's entity ranking are fused in one RRF. Names are resolved on the typed query only; each `also` text is searched as given (no name expansion). The reranker (ticket 037) scores against the **last** `also` text when there is one, else the typed query. `SearchResult` gets `also: list[str]`. `also=()` gives exactly the ticket 037 behaviour.
- **FR-3** - Answer path. In `service.ask`, after the follow-up rewrite, `telugu_form(search_query, ask)` is called when the setting `search_telugu_form` is `True` (default), with an `Ask` built on the session's answer LLM: `llm.complete("You rewrite search queries.", [{"role": "user", "content": <prompt>}], max_tokens=search_telugu_form_max_tokens, thinking=False).text`. When `text` is not None the search is `search_fn(search_query, k=…, also=[text])`. A failed form searches as typed. `Answer` gets `search_also: list[str]` and `telugu_form_how: str`; neither is stored in the thread tables.
- **FR-4** - CLI.
  - `uv run newsrag answer ask|chat` print `search (te, <how>): <text>` under the `search:` line when a Telugu form was used.
  - `uv run newsrag search "<query>" --telugu-form` computes the form with `enrich_model` (released before the search, as `--translit` does), prints `telugu (<how>): <text>` (or `telugu: failed, searching as typed`, or nothing when `how` is `none`) and searches with `also=[text]`. `--translit` keeps its present behaviour.
- **FR-5** - Eval. `uv run newsrag eval retrieval --telugu-form`: every question whose `lang` is `en` or `te_latn` is searched twice, as typed (`by_lang.en`, `by_lang.te_latn`, as today) and as typed plus its Telugu form (`by_lang.en_both`, `by_lang.te_latn_both`). All forms are computed first under `use(enrich_model)`, then all searches run (never two models at once). `per_question` entries get `telugu_form`, `telugu_form_how`, `telugu_form_failed`, `rank_both`. Overall `recall_at_10`/`mrr` keep using the as-typed run. `--telugu-form` and `--translit` may be given together.
- **FR-6** - Settings in the `# --- search ---` block: `search_telugu_form: bool = True`, `search_telugu_form_max_tokens: int = 256`.
- **FR-7** - Before/after record in `specs/tickets/038-telugu-query-form/evidence`: at the base commit `before-retrieval.json` from `uv run newsrag eval retrieval --set data/eval/retrieval-2026.jsonl --translit --out …`; when done `after-retrieval.json` from the same command plus `--telugu-form`. `results.md` has a table of `recall_at_1`, `recall_at_5`, `recall_at_10`, `mrr` for `te`, `en`, `en_both`, `te_latn`, `te_latn_translit`, `te_latn_both`, the count of failed forms, and five example questions per language with their Telugu form.
- **FR-8** - Clarifications (gap check):
  - **Other stores.** `also` is passed only to the `new` backend. `newsrag search --store baseline --telugu-form` and `eval retrieval --store baseline --telugu-form` print `--telugu-form needs --store new` to stderr and exit 2.
  - **A failed form.** In the eval the question is searched as typed: `rank_both = rank`, it is counted in `en_both`/`te_latn_both`, and `telugu_form_failed` is `true`. An `ask` that raises (also `AnswerUnavailable` from the answer LLM) counts as a try and never ends the turn.
  - **Which model in the eval and CLI.** `translit.model_ask` on `enrich_model` (temperature 0, JSON schema, `retrieval_max_tokens`). The answer path uses the session LLM without a JSON schema, and the Claude provider is not measured; `results.md` says so.
  - **Both options.** `newsrag search --translit --telugu-form` behaves as `--telugu-form` alone. In `eval retrieval` with both, a `te_latn` question that is Romanized Telugu is transliterated once and that text is used for both `te_latn_translit` and `te_latn_both`.
  - **`answer sources`** (ticket 039) computes no form (it loads no LLM); for a Latin-letter question it prints the line `note: searched as typed (answer ask also searches a Telugu form)` first.
  - **Time.** No limit is set. `results.md` records the median seconds of one form call over the eval's `en` and `te_latn` questions and `median_search_ms` with and without `also`.
  - **Name.** "`search_query`" in FR-3 is the variable `query` of `service.ask`.

## Non-functional requirements

- **NFR-1** - No-regression guard: `by_lang.te.recall_at_10` in `after-retrieval.json` ≥ the value in `before-retrieval.json` − 0.02 (Telugu-script questions get no Telugu form, so the value is expected to be equal).
- **NFR-2** - A Telugu-script question causes no extra model call in `service.ask` (unit test counts the calls of a fake LLM).
- **NFR-3** - `telugu_form` takes `ask` as an argument and `service.ask` its `llm`, so unit tests use fakes (no GPU).

## Affected areas

- `src/newsrag/search/telugu_form.py` (new), `src/newsrag/prompts/translate_query_v1.md` (new)
- `src/newsrag/search/hybrid.py` - `also`, fusion over several texts, rerank query (changed)
- `src/newsrag/search/cli.py` - `--telugu-form` (changed); `src/newsrag/search/__init__.py` (changed)
- `src/newsrag/answer/service.py`, `src/newsrag/answer/cli.py` - the form in a turn, the printed line (changed)
- `src/newsrag/evals/retrieval.py`, `src/newsrag/evals/cli.py` - `--telugu-form` (changed)
- `src/newsrag/config.py` - `# --- search ---` block (changed)
- `tests/test_telugu_form.py` (new), `tests/test_search.py`, `tests/test_answer.py`, `tests/test_eval_retrieval.py` (changed)
- Base commit: before the first change the worker writes `git rev-parse HEAD` to `specs/tickets/038-telugu-query-form/evidence/base-commit.txt`. Every "against the base commit" comparison and every `before-*.json` file refers to that commit; the diffs in eval.md compare it with the working tree.
- Test names: new tests are named so that every `pytest -k` selection in eval.md matches at least one of them.

## Don't touch

- `src/newsrag/search/translit.py` and `src/newsrag/prompts/translit_v1.md` (measured in ticket 034): used as they are.
- `src/newsrag/prompts/answer_rewrite_v1.md`: the follow-up rewrite stays as it is.
- `src/newsrag/answer/threads.py`, `src/newsrag/answer/sql/`: no schema change.
- `data/eval/**`; `eval answers`, `eval customer`, `eval short`; the baseline store.

## Out of scope

- Translating Telugu questions into English, or answers into another language.
- A dictionary of Telugu/English term pairs (e.g. `k k line`).
- Caching forms between runs.
- Storing the Telugu form with the turn.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Which Latin-letter questions get a Telugu form | Romanized Telugu is transliterated, English is translated | user chose "transliterate and translate" |
| D-2 | How the forms are used | typed text and Telugu form are both searched and fused | user chose it; a bad form cannot remove what the typed text finds |
| D-3 | Telling Romanized Telugu from English | the marker test of ticket 034 (`is_romanized_telugu`) | already measured; deterministic |
| D-4 | Which model writes the form in an answer | the session's answer LLM, thinking off | the GPU holds one model at a time; no second model can be loaded during a chat |
| D-5 | Which text the reranker scores against | the Telugu form when there is one | the stories are in Telugu |
| D-6 | Done bar | before/after report plus NFR-1 | user chose "report, with a no-regression guard" |

## Open questions

None.
