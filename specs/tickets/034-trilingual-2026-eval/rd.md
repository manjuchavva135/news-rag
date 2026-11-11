# 034: Trilingual 2026 test (retrieval + answers)

**Status:** draft
**Mode:** solo
**Runs:** waits for 031: needs the 2026 corpus run to `index` and the generation activated (031 FR-2, FR-3), not its sign-off

## Summary

A 300-question synthetic test built only from the 2026 stories (`corpus_from_date` 2026-01-01; the
data is October–November 2026), split equally over three ways a reader types: Telugu script,
English, and Romanized Telugu (Telugu typed in Latin letters, English words mixed in, e.g.
"Tirumala lo vaikunta dwara darshanam tokens eppudu issue chesaru?"). Romanized queries get a new,
optional transliteration step (local LLM rewrites them in Telugu script) so BM25 can match the
Telugu-script stories. The test reports, per language, how often search finds the right story and
how often the answer built from the hits is correct, and a report page explains exactly how each
number was measured. Report only: no pass bar.

## Functional requirements

- **FR-1** - Question set. `uv run newsrag eval build-retrieval --n 300 --langs te:100,en:100,te_latn:100 --window-n 0 --from-date 2026-01-01 --out data/eval/retrieval-2026.jsonl` (new options on the ticket 025 command; `build_retrieval_set(...)` gets matching keyword arguments `langs`, `window_n`, `from_date`):
  - Only stories of the active generation with `pub_date >= --from-date` (kinds news/editorial/notice_tender, as in 025). `--window-n 0` means no baseline-window stratum; all 300 are spread evenly over the year-months present (2026-10, 2026-11), shortfalls allowed as in 025 (`short: <stratum>` printed).
  - `--langs` gives exact counts per language code, assigned to the slots by the seed (`retrieval_seed`, 7). Without `--langs` the 025 behaviour (`retrieval_te_share`) is unchanged.
  - Language codes: `te` (Telugu script), `en` (English), `te_latn` (Romanized Telugu with English words allowed). `LANG_NAMES["te_latn"] = "Romanized Telugu"`.
  - The `te_latn` prompt tells the model to write the question the way people type Telugu on a phone keyboard: Telugu words in Latin letters, common English words (tokens, results, MLA, collector …) may stay English, no Telugu script.
  - A `te_latn` question is accepted only when its Latin-letter share is ≥ `retrieval_lang_share` (0.5), it has no Telugu-script letter, and at least `retrieval_te_latn_min_markers` (new setting, 2) of its lower-cased words are in `data/eval/te-latn-markers.txt` (new, committed; one common Romanized Telugu word per line, at least 60 words, e.g. `lo`, `ki`, `ni`, `emi`, `enti`, `eppudu`, `ekkada`, `enduku`, `evaru`, `entha`, `chesaru`, `ayyindi`, `undi`, `gurinchi`, `kosam`, `ela`, `ante`). Otherwise the problem is `not in Romanized Telugu` and the 025 retry/replace rules apply. The other checks (headline copy, empty reply) apply to every language.
  - Each line has the 025 fields (`in_baseline_window` is `false` for every line). The summary line prints counts per language: `te=… en=… te_latn=…`.
- **FR-2** - Transliteration. New `src/newsrag/search/translit.py: to_telugu(query, client) -> str` asks `enrich_model` (temperature 0, JSON `{"telugu": "..."}`, prompt `src/newsrag/prompts/translit_v1.md`) to rewrite a Romanized Telugu query in Telugu script, writing English words as Telugu-script loanwords (`tokens` → `టోకెన్లు`). An empty/invalid reply or one with Telugu-letter share < `retrieval_lang_share` is asked again up to `retrieval_max_tries` (3) times, then the original query is used and the item records `translit_failed: true`. `uv run newsrag search "<query>" --translit` prints the rewritten query on its first line (`translit: <text>`), then the hits.
- **FR-3** - Retrieval scoring. `uv run newsrag eval retrieval --set data/eval/retrieval-2026.jsonl --translit --out <reports_dir>/retrieval-2026.json`:
  - Hit rule unchanged from ticket 025 FR-4 (page overlap with `gold_page_ids` **and** ≥ `retrieval_hit_overlap` (0.3) of the hit text's character 5-grams in `gold_text`).
  - `metrics()` adds `recall_at_1` and `recall_at_5` (same rule, top 1 / top 5) next to `recall_at_10` and `mrr`; existing keys and the summary line stay.
  - `by_lang` lists every language code present in the set (not only `te`/`en`).
  - With `--translit`, every `te_latn` question is searched twice: as typed (`by_lang.te_latn`) and after `to_telugu` (`by_lang.te_latn_translit`). Overall `recall_at_10`/`mrr` use the as-typed run, so they stay comparable to other sets. Without `--translit` nothing is transliterated.
  - The report JSON also has `per_question: [{qid, lang, query, translit_query?, rank, rank_translit?}]` (rank = first correct rank or null).
- **FR-4** - Answer check. New `uv run newsrag eval answers --set data/eval/retrieval-2026.jsonl --retrieval <reports_dir>/retrieval-2026.json [--out <reports_dir>/answers-2026.json]` in `src/newsrag/evals/answers.py`:
  - For each question it searches again (k = `customer_k`, 12, as typed; for `te_latn` with the transliterated query from the retrieval report) and has `answer_model` answer with the ticket 028 answer prompt (`answer_check_v1.md`, via `newsrag.evals.customer.answer_prompt` / `make_sources`).
  - `answer_judge_model` (sarvam-m-24b; `customer.check_families` must pass) grades the answer against the question and `gold_text` with new prompt `src/newsrag/prompts/answer_grade_v1.md`: `correct` (the facts the question asks for are given and match the gold story), `partial` (some asked facts right, some missing, none contradicting), `wrong` (missing, contradicting the gold story, or says the sources do not cover it). JSON `{"label": "correct"|"partial"|"wrong"}`; unparseable replies are asked again `customer_judge_retries` (2) times, then the item is `grade: null` and counted in `n_failed`.
  - Models are loaded one at a time (all answers first, then all grades), through `newsrag.models.use` as in 028.
  - Output JSON: `{set, n, n_failed, overall:{correct_pct, partial_pct, wrong_pct}, by_lang:{te:{n, correct_pct, partial_pct, wrong_pct}, en:{…}, te_latn:{…}}, items:[{qid, lang, query, answer, grade, retrieved_gold (bool: a correct hit in the sources)}]}`. Percentages are over graded items, rounded to 2 decimals. Also `answers-2026.md`: one section per question (question, answer, grade, gold headline).
- **FR-5** - Report. New `uv run newsrag eval report-2026` writes `<reports_dir>/eval-2026.md` from the two JSON files with:
  - a table per language (`te`, `en`, `te_latn`, `te_latn (transliterated)`): n, recall@1, recall@5, recall@10, MRR, answers correct / partial / wrong %;
  - a section `## How it was measured` stating: the set file and its sha1, the date range and stories it was drawn from, the writer model (`enrich_model`) and the question checks, the hit rule with its numbers (5-grams, 0.3, page overlap, k), the formulas of recall@k (share of questions with a correct hit in the top k) and MRR (mean of 1/rank of the first correct hit, 0 when none in the top k), the transliteration model, the answer and judge models, the grade definitions, and that the questions are synthetic (written by an LLM from one story each), so these numbers are an estimate, not customer accuracy;
  - a section `## Examples`: for each language 3 questions with a correct top-1 hit and 3 with no correct hit in the top 10 (question, gold headline, top-1 headline).
- **FR-6** - New settings in `config.py` `# --- evals ---` block: `retrieval_te_latn_min_markers: int = 2`, `te_latn_markers_file: Path` (default `{repo_root}/data/eval/te-latn-markers.txt`), `eval_2026_set_file: Path` (default `{repo_root}/data/eval/retrieval-2026.jsonl`). `data/eval/retrieval-2026.jsonl` is committed.

- **FR-7** - Clarifications (gap check):
  - **Model order.** No command holds two models at once (`newsrag.models.use` holds one profile per process). `eval retrieval --translit` first transliterates every `te_latn` question under `use(enrich_model)`, leaves that scope, then runs all searches. `search --translit` does the same for its one query. `eval answers` runs all searches first, then all answers under `use(answer_model)`, then all grades under `use(answer_judge_model)`.
  - **Translit call.** Up to `retrieval_max_tries` (3) calls in total (not 1 + 3). It uses `retrieval_max_tokens` (256), Qwen3 thinking off (`THINK_OFF` as in 028), and a JSON-schema `response_format` with one required string `telugu`. If all 3 fail, `translit_query` equals the original query and `translit_failed: true` is stored on that `per_question` entry; `search --translit` then prints `translit: failed, searching as typed`.
  - **`--langs`.** Format `code:count,code:count` with codes from `LANG_NAMES`. An unknown code, bad syntax or counts not summing to `--n` exits 2 with `--langs counts must sum to --n (<sum> != <n>)` or `unknown language <code>`. When strata are short, the shortfall is taken from each language in proportion (largest-remainder rounding) and printed as `short: lang <code> -<k>`.
  - **`--from-date`.** Default `None` = no date filter (025 behaviour). The check for an `en` question also rejects one that passes the Romanized marker test (`not in English`), so Romanized Telugu never counts as English.
  - **Recorded metadata.** `build-retrieval` writes `<out>.meta.json` next to the set: `{n, langs, from_date, window_n, seed, writer_model, stories_available, strata, built_at, sha1 of the set}` (committed with the set). The retrieval JSON adds `k`, `hit_overlap`, `ngram`, `translit_model`, `generation`. The answers JSON adds `k`, `answer_model`, `judge_model`, `retrieval_file`. `report-2026` takes every value in "How it was measured" from these files, never from current settings.
  - **Per-question fields.** FR-3 `per_question` also stores `top1_headline`, `top1_headline_translit` and `translit_failed`.
  - **Answer inputs.** `eval answers` reads the transliterated queries from `--retrieval`. It exits 1 with `<file>: no transliterated queries (run eval retrieval --translit)` when that file has no `te_latn_translit`, when its `set` differs from `--set`, or when a `te_latn` qid is missing. For `te_latn`, the transliterated text is both searched and put into `answer_prompt`, and `items[].query` stores it (`items[].query_typed` keeps the original). `today` = UTC date and `covered_years` = `customer.load_covered_years()`, as in 028. `gold_text` goes to the judge cut to `retrieval_story_chars` (4000).
  - **Answer failures.** No hits → grade `wrong` without a judge call (`no_sources: true`). An answer still failing after `customer_answer_retries` → `grade: null`, counted in `n_failed` (as are unparseable grades).
  - **Report rows.** The answer % of `te_latn` goes on the `te_latn (transliterated)` row (answers use that query, D-11); the `te_latn` (as typed) row shows `-` for the answer columns. Examples cover the four rows; when a row has fewer than 3 hits or misses, it shows those it has and the line `(only <k>)`.
  - **Paths (settings in `# --- evals ---`).** `eval_2026_set_file` is the default `--set` of `eval answers` and the set read by `report-2026`. New `eval_2026_retrieval_file` (default `{reports_dir}/retrieval-2026.json`), `eval_2026_answers_file` (`{reports_dir}/answers-2026.json`; the `.md` sits next to it with suffix `.md`) and `eval_2026_report_file` (`{reports_dir}/eval-2026.md`) are the defaults of `--retrieval`/`--out` and the inputs/output of `report-2026`. `report-2026` exits 1 with `missing <path>` if an input is absent.

## Non-functional requirements

- **NFR-1** - Building the set, the retrieval run and the answer check together take ≤ 3 h of wall time on the GPU box (sum of the three `time` outputs in E-1, E-2, E-3).
- **NFR-2** - Every new function that calls a model takes the client/ask function as an argument, so the unit tests run with fakes (no GPU).

## Affected areas

- `src/newsrag/evals/retrieval.py` - langs, from_date, window_n, te_latn check, recall@1/5, by_lang, translit pass (changed)
- `src/newsrag/evals/answers.py` (new), `src/newsrag/evals/report_2026.py` (new), `src/newsrag/evals/cli.py` (changed)
- `src/newsrag/search/translit.py` (new), `src/newsrag/search/cli.py` (changed: `--translit`)
- `src/newsrag/prompts/translit_v1.md`, `src/newsrag/prompts/answer_grade_v1.md` (new)
- `src/newsrag/config.py` `# --- evals ---` block (changed)
- `data/eval/te-latn-markers.txt`, `data/eval/retrieval-2026.jsonl` (new, committed)
- `tests/test_eval_retrieval.py` (changed), `tests/test_translit.py`, `tests/test_eval_answers.py` (new)

## Don't touch

- `data/eval/retrieval-v1.jsonl`, `data/eval/retrieval-v2.jsonl`, `data/eval/customer-questions.json` and their reports (`retrieval-new.json`, `customer-answers.*`).
- The hit rule numbers (`retrieval_hit_overlap`, `retrieval_ngram`, `retrieval_k`), the phase-1 bars (`src/newsrag/report/bars.py`), `judge_threshold`.
- `search()` default behaviour: without `--translit` / the translit pass, queries are searched exactly as before.
- Store contents (read only); the ledger and the 031 run.

## Out of scope

- Using transliteration in a search API or UI (phase 2); reranking or query rewriting beyond transliteration.
- Pass bars for these numbers (user decides after seeing them).
- Questions from 2024/2025 stories, or from the baseline window.
- Adding this test to the quality report (ticket 030).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Which data | only stories printed on/after 2026-01-01 (Oct–Nov 2026) | user: only 2026 data is processed |
| D-2 | Language split | 100 Telugu script / 100 English / 100 Romanized Telugu | user chose; equal precision per language |
| D-3 | Third language | Telugu in Latin letters, English words allowed (how people type) | user chose |
| D-4 | Accuracy | retrieval (recall@1/5/10, MRR, 025 hit rule) **and** an answer check | user chose |
| D-5 | Answer grades | correct / partial / wrong by `answer_judge_model` against the gold story; accuracy = correct % | user chose; judge is another model family than the answerer |
| D-6 | Search change | add transliteration of Romanized queries to Telugu script, report before and after | user chose; BM25 cannot match Latin letters to Telugu-script stories |
| D-7 | Transliteration method | local LLM (`enrich_model`) rewrite | user chose; handles casual spellings, no new dependency |
| D-8 | Pass bar | none; report only | user chose |
| D-9 | Telling Romanized Telugu from English | Latin share ≥ 0.5, no Telugu letter, ≥ 2 marker words from a committed list | deterministic and testable; avoids a second model in the builder |
| D-10 | Baseline window | `--window-n 0`: no Jan–Jun 2026 stratum | the old pipeline's window has no stories in this corpus |
| D-11 | Answer search for `te_latn` | uses the transliterated query | measures the improved path; as-typed retrieval is still reported in FR-3 |
| D-12 | Details from the gap check (FR-7) | safest choices consistent with tickets 025/028 | technical, not user-facing; recorded so the builder does not guess |

## Open questions

None.
