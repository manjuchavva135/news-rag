# 013: Grouping judge and retry

**Status:** ready
**Mode:** solo
**Runs:** waits for 012: grades its output

## Summary

A second local vision model from a different family (so no model grades itself) scores every page's
article grouping (grill D-6, A-9). Pages scoring below 0.8 are grouped again at higher resolution and
judged again; the better grouping is kept, and pages still below 0.8 are flagged `low_confidence`
(grill A-28, D-20). The result, `group_final`, is what every later stage reads. Because only one big
model fits at a time, this is done as four separate passes.

## Functional requirements

- **FR-1** - Settings `judge_model` (default `gemma3-27b`) and `judge_threshold` (default 0.8) live in `config.py` and may be set in `pipeline.yaml`. PageStage `judge` (`depends_on=("group",)`, `version="<judge_model>-judge-p1"`, `models_needed=(judge_model,)`, `concurrency=16`, model opened once in `open()`). The judge must be from a different model family than `group_model`: its `preflight()` compares the `family` fields in `models.yaml` and raises `StageUnavailable("judge_model and group_model must be different families")` (exit 2) if equal.
- **FR-2** - Judge input: the ticket-012 article overlay (each article coloured and numbered) at long side 3584 px, plus per article: number, kind, headline, first 300 chars of text. Prompt `src/newsrag/prompts/judge_v1.md` asks for JSON `{"articles":[{"n", "verdict": "correct"|"merged"|"split"|"wrong_blocks"|"not_an_article"}], "missed_regions": <count of visible stories with no article>}`. Articles of kind `masthead` and `ad_commercial` are not sent and not counted. Page score = correct / (number of judged articles + missed_regions), 3 decimals; a page with no judged articles and `missed_regions = 0` gets `score = null` (`judge_skipped`, treated as passing). An article number that is missing, repeated or unknown in the reply is a schema error (same retry rule as ticket 012, 2 retries); after the retries, missing numbers count as not correct.
- **FR-3** - PageStage `group_retry` (`depends_on=("group", "judge")`, `version="<group_model>-regroup-p1-t<judge_threshold>"`, `models_needed=(group_model,)`): if the judge score ≥ `judge_threshold` or is null it is a pass-through (no model call, detail `{retried:false}`); otherwise it re-runs the ticket-012 grouping with `group_image_max_px` × 1.5 (5376) and writes a new grouping artifact (detail `{retried:true, image_long_side: 5376}`). The model is opened lazily at the first page that needs a retry, so a pass with no retries loads nothing.
- **FR-4** - PageStage `judge_retry` (`depends_on=("group_retry",)`, `version="<judge_model>-judge-p1-t<judge_threshold>"`, `models_needed=(judge_model,)`, lazy model as in FR-3): pass-through when not retried; otherwise judges the retry grouping like FR-2.
- **FR-5** - PageStage `group_final` (`depends_on=("group", "judge", "group_retry", "judge_retry")`, `version="final-t<judge_threshold>"`, CPU only): picks the grouping with the higher judge score (tie → the first), and writes it with the ticket-012 schema unchanged plus top-level `judge_score`, `retried`, and per article `judge_verdict`. When the kept score < `judge_threshold`, page flag `low_confidence:judge_low` is added to `page_flags` **and to every article's `flags`**. Articles with verdict ≠ `correct` get flag `judge:<verdict>`. Articles with any ticket-012 geometry flag, and all articles of a page with page flag `blocks_omitted` or `block_duplicated`, get `low_confidence:geometry`.
- **FR-6** - `uv run newsrag judge stats [--pages-file F]` prints one line each: `pages=<n>`, `mean_score=<x>` (kept groupings, null scores excluded), `judge_low_pct=<x>`, `retried_pct=<x>`, `retry_gain=<x>` (mean of kept score − original score over retried pages).
- **FR-7** - Clarifications (second review):
  - Function `newsrag.grouping.judge.judge_grouping(page, grouping, image_long_side=3584) -> {score, verdicts}` is used by the stages, the sanity check and ticket 014; it draws the overlay with `draw_articles(mark_flags=False, kinds=<judged kinds>)` so the judge only sees numbered judged articles.
  - Failures: invalid JSON after the retries fails that `judge`/`judge_retry` item; unknown or repeated numbers left after the retries are ignored (first use kept); missing numbers stay in the denominator. If `judge` fails for good, `group_final` passes the original grouping through with `judge_score: null` and flag `low_confidence:judge_failed`; if `group_retry` or `judge_retry` fails for good, `group_final` keeps the original grouping with flag `low_confidence:retry_failed`. (`group_final` depends on those stages being done *or* failed at max attempts.)
  - `group_retry` and `judge_retry` use `concurrency = group_concurrency` (16). `retry_gain=n/a` when nothing was retried.
  - NFR-1 corrupts the `group` grouping, choosing the pair from that page's stored `judge` verdicts.
  - E-2 adds a forced retry: `NEWSRAG_JUDGE_THRESHOLD=1.0 uv run newsrag stage run group_retry judge_retry group_final --pages-file <5 densest sample pages> --force` must give 0 failures and `retried:true` on all 5.

## Non-functional requirements

- **NFR-1** - Sanity of the judge: `uv run newsrag judge sanity --pages-file data/dev-sample.txt --n 5` takes the first 5 sample pages that have a *clean pair*: two `news` articles that the page's stored `judge` called `correct` and whose headlines are distinct (they share fewer than two words and neither word set contains the other, so a broken-off piece of the same story is never used). Of those pairs it takes the one with the longest texts, merges the two articles in memory only (nothing written; blocks united, id and headline of the earlier one, text rebuilt), judges the corrupted grouping and prints one line per page plus `caught: k/5`, where caught means the judge's verdict on the merged article is not `correct` (and not missing); k must be ≥ 4. Pages without a clean pair are skipped with a `skip` line. (Changed after the first build: the old test merged the two longest articles and compared page scores, which sometimes repaired a split story and moved with unrelated verdicts.)

## Affected areas

- `src/newsrag/stages/judge.py`, `group_retry.py`, `judge_retry.py`, `group_final.py`, `src/newsrag/prompts/judge_v1.md` (new)
- `models.yaml` - `family` field on every profile (changed)
- `src/newsrag/grouping/` - reuse grouping with an image-size override (changed)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_judge.py` (new, CPU with fake clients), `tests/test_judge_live.py` (new, `gpu`+`data`)
- `src/newsrag/config.py` - `judge_model`, `judge_threshold` (changed)

## Don't touch

- The ticket-012 prompt and parsing (reuse, don't change behaviour).

## Out of scope

- Choosing the grouping model (014); human review sets (grill D-6).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Who judges | a local VLM of another family (default Gemma 3 27B) | grill D-6, A-9 |
| D-2 | Threshold and retry | < 0.8 → regroup at 1.5× resolution, keep the better | grill A-28 |
| D-3 | Still low | indexed with `low_confidence:judge_low` | grill D-20 |
| D-4 | Passes | four stages so each model loads once per pass | grill A-28, one model at a time |

## Open questions

None.
