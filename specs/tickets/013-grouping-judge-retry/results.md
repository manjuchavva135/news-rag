# 013: Grouping judge and retry - Results

Branch: ticket/013-grouping-judge-retry
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-a12b7ea621129d1c4
Port: 8701 (not used, CLI only)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| lint (`ruff check .`, `ruff format --check .`) | PASS | evidence/lint.log |
| unit tests (`-m "not gpu and not data and not stores"`) | PASS, 433 passed | evidence/unit.log |
| E-1 logic with fake models | PASS, 47 passed in tests/test_judge.py (incl. all 7 named tests) | evidence/E-1.log |
| E-2 four passes on the dev sample | PASS: 0 failures in the four stages; stats printed; artifact check `ok`; 0 retried rows with image_long_side <> 5376; forced retry on the 5 densest pages: 0 failures, `retried:true` on all 5 | evidence/E-2-*.log, E-2-judge-pass.log, E-2-passes.log |
| E-3 judge sanity (NFR-1, k >= 4) | **FAIL: `lower: 3/5`** (two runs, identical) | evidence/E-3-run1.log, E-3-run2.log |
| E-4 family check | PASS (`judge_model and group_model must be different families`, exit=2) | evidence/E-4.log |
| E-5 second-review clarifications | PASS, 9 tests | evidence/E-5.log |
| live test `tests/test_judge_live.py` (gpu+data) | FAIL, same cause as E-3 (merged grouping scored 1.0 vs 0.75) | evidence/live.log |

`judge stats --pages-file data/dev-sample.txt` (after the forced-retry run was undone):

```
pages=20
mean_score=0.832
judge_low_pct=30.0
retried_pct=45.0
retry_gain=0.274
```
(First run, before the forced retry: mean_score=0.841, judge_low_pct=25.0, retried_pct=45.0, retry_gain=0.290; the model is not bit-deterministic across re-runs.)

Sanity lines (`judge sanity --pages-file data/dev-sample.txt --n 5`):

```
2024-12-02_BG_main_01 stored=0.636 merged=0.6 lower
2024-12-06_GNOCLT_main_16 stored=0.75 merged=1.0 NOT lower
2024-12-08_BGNOPSVACLT_main_10 stored=0.6 merged=0.5 lower
2024-12-11_B_main_03 stored=1.0 merged=1.0 NOT lower
2024-12-11_V_main_12 stored=0.812 merged=0.8 lower
lower: 3/5
```

## Attempts
- Attempt 1: E-3 gave 3/5 (< 4). Investigated whether it is the prompt, the input or the model (scratch experiments, all over the 12 dev-sample pages that have >= 2 news articles, `lower` count of merged vs original score):
  - Gemma 3 27B, spec input (one overlay at 3584 px, 300-char list): 4/12.
  - + stricter prompt (count headlines, check connectivity): 4/12. + bigger overlay labels: 4/12. + structural cues in the list (blocks, headline boxes, area): about 3/5 on the first five.
  - Gemma only sees about 900 px per image (pan-and-scan gives 2 crops + 1 global on a portrait page), and in a probe it hallucinated the labels on a 3584 px overlay. Cutting the overlay into 3 bands (labels then readable) gave no better detection (2/12 and 3/12).
  - Qwen3-VL-32B as judge (probe only, not allowed by FR-1: same family as the grouper): 7/12.
  - Conclusion: with Gemma 3 27B the judge does not reliably notice two merged stories; none of the cheap fixes worked, so the shipped code keeps the spec input unchanged (all experiments reverted: models.yaml untouched, no tiling code). NFR-1 is therefore not met on this model. A decision is needed (see router notes).
- Attempt 2 (after review): fixes for reviewer findings (below), E-1/E-5/lint/unit re-run: all pass. E-3 unchanged.

## Changes
- `src/newsrag/config.py` - `# --- judge ---` block: `judge_model`, `judge_threshold`, `judge_concurrency`, `judge_image_max_px`, `judge_text_chars`, `judge_skip_kinds`, `judge_max_tokens`, `judge_retry_scale`.
- `src/newsrag/grouping/judge.py` (new) - `judge_grouping(page, grouping, image_long_side=None, *, ask=None)`, reply parsing/retries, score, family check, `merge_two_longest`.
- `src/newsrag/grouping/judge_cli.py` (new) - `newsrag judge stats|sanity`; registered in `src/newsrag/cli.py`.
- `src/newsrag/prompts/judge_v1.md` (new) - judge prompt.
- `src/newsrag/stages/judge.py` (new) - `JudgeStage`, `JudgeRetryStage` (in the same file instead of a separate `judge_retry.py`), shared `LazyModel`.
- `src/newsrag/stages/group_retry.py`, `group_final.py` (new); registered in `stages/registry.py`.
- `src/newsrag/grouping/input.py`, `src/newsrag/stages/group.py` - optional `image_long_side` override (default unchanged, ticket-012 prompt/parsing/version hash untouched); `input.py` also got `stage_row` and `load_artifact` helpers.
- `src/newsrag/stages/base.py`, `eligibility.py` - new optional `tolerate_failed` on page stages: a dependency counts as satisfied when it failed for good (failed at `max_attempts`, or blocked by an upstream that did). Needed for FR-7 (group_final runs after a failed judge / retry). Other stages are unaffected.
- `models.yaml` - already had `family` on every profile; no change.
- `tests/test_judge.py` (47 tests, CPU, fake models and fake ledger), `tests/test_judge_live.py` (gpu+data).

## Notes for the router
- **NFR-1 / E-3 fails (3/5, needs >= 4).** The judge with Gemma 3 27B is close to blind for "two stories merged into one article". Options: A) accept Gemma and relax NFR-1 (e.g. k >= 3, or judge on a bigger sample); B) pick another non-Qwen VLM with native high-resolution input as `judge_model` (needs the user: download + licence; no code change beyond `models.yaml` and `pipeline.yaml`); C) change the sanity test. Qwen3-VL-32B scored 7/12 on the same test, so even a stronger model is not clean: the test itself is noisy (merging removes an article, so a page that already had a wrong article can score higher after the merge). Recommendation: B for the real judge, and A/C for the sanity gate; the judge score is still usable as a rough signal (mean 0.83, spread 0.0 to 1.0 on the sample).
- Running a model from the worktree needs `NEWSRAG_VLLM_ENV_DIR=/home/admin1/news-rag/envs/vllm` (the vLLM env lives in the main checkout only).
- Judge input is as specified (one overlay at 3584 px). If the page exceeds Gemma's 16384-token context the article texts are shortened (100 chars, then none); the length used is recorded as `text_chars` in the judge artifact/detail.
- `group_final` also adds `low_confidence:geometry` to `page_flags` for pages with `blocks_omitted`/`block_duplicated` (FR-5 asks for it on articles); `judge_failed`/`retry_failed` flags go on the page and on every article. `retried` in `group_final` means the regrouping was produced and judged (even when the original was kept). A null retry score never beats the original.
- A failed-to-start model in the lazy stages (`group_retry`, `judge_retry`) is remembered for the run, so pages fail fast instead of each restarting vLLM; this still counts an attempt per page (a halt without counting would need a runner change: not done, outside the ticket).
- The forced run (`NEWSRAG_JUDGE_THRESHOLD=1.0`) left stale artifact directories `final-t1.0-*` and `*-t1.0-*` under /home/admin1/raw/work/group_retry and group_final; ledger rows were restored to the normal threshold.
- Pre-existing, unrelated: `tests/test_ledger.py::test_migrations_idempotent_and_tables` (`-m stores`) fails because ticket 011 added the `page_editions` table.
- The tester could not run the E-2 step 5 SQL as written (the sandbox filter refuses `count(*)` in `db sql`); it counted the same rows from a dump: 0.
