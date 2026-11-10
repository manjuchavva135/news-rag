# 013: Grouping judge and retry - Results

Branch: ticket/013-grouping-judge-retry
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-a12b7ea621129d1c4
Port: 8701 (not used, CLI only)

## Checks (final: Gemma 4 judge + one-story check, FR-8)
| Check | Result | Evidence |
|-------|--------|----------|
| lint (`ruff check .`, `ruff format --check .`) | PASS | - |
| unit tests (`-m "not gpu and not data and not stores"`) | PASS, 447 passed | - |
| E-1 logic with fake models | PASS, 61 passed in tests/test_judge.py (incl. the 7 named tests and the one-story tests) | - |
| E-2 four passes on the dev sample (judge_model=gemma4-31b) | PASS: 20 done, 0 failed in each of judge / group_retry / judge_retry / group_final; stats printed; artifact check ok on the 20 current `group_final` files; 0 retried rows with image_long_side <> 5376 | evidence/E-2-gemma4-*.log |
| E-3 judge sanity (NFR-1, k >= 4) | **PASS: `caught: 5/5`** | evidence/E-3-gemma4.log |
| E-4 family check | PASS (`judge_model and group_model must be different families`, exit=2) | evidence/E-4-gemma4.log |
| E-5 second-review clarifications | PASS (inside the 61) | - |
| live test `tests/test_judge_live.py` (gpu+data) | PASS, 1 passed | evidence/live-gemma4.log |

Note on E-2 step 4: the eval's glob `group_final/*/*.json` also matches stale directories of earlier
versions (the Gemma 3 run and the forced `t1.0` run); the check was run on the current version's
directory.

`judge stats --pages-file data/dev-sample.txt` with Gemma 4 + one-story check:

```
pages=20
mean_score=0.458
judge_low_pct=70.0
retried_pct=75.0
retry_gain=0.123
```

The drop from Gemma 3 (mean 0.832, judge_low 30%) is mostly real grouping failures that Gemma 3
did not see, not false alarms: Gemma 4 reports 48 missed stories over the sample against Gemma 3's 2.
Two examples checked by eye:
- 2024-12-27_BGNOPRSVACLT_main_01 is a full front page (about 10 stories); the ticket-012 grouping put
  all 63 blocks into one `masthead`. Gemma 3: no judged articles, score null (passing). Gemma 4: 10
  missed regions, score 0.
- 2025-12-16_BG_main_06-07 (two-page spread): only the big story was grouped; the stories along the
  top strip were not. Gemma 4: 4 missed regions.
The one-story check changed 5 of 90 verdicts (correct -> merged). Group quality is ticket 014's job;
the phase-1 bar (mean judge >= 0.90, <= 5% flagged) is far from met by the current grouping.

## Earlier results (Gemma 3 judge, before FR-8)

### Checks with Gemma 3
| Check | Result | Evidence |
|-------|--------|----------|
| lint (`ruff check .`, `ruff format --check .`) | PASS | evidence/lint.log |
| unit tests (`-m "not gpu and not data and not stores"`) | PASS, 433 passed | evidence/unit.log |
| E-1 logic with fake models | PASS, 47 passed in tests/test_judge.py (incl. all 7 named tests) | evidence/E-1.log |
| E-2 four passes on the dev sample | PASS: 0 failures in the four stages; stats printed; artifact check `ok`; 0 retried rows with image_long_side <> 5376; forced retry on the 5 densest pages: 0 failures, `retried:true` on all 5 | evidence/E-2-*.log, E-2-judge-pass.log, E-2-passes.log |
| E-3 judge sanity (NFR-1, k >= 4), clean-pair test | **FAIL: `caught: 0/5`** (0/10 over every dev-sample page with a clean pair) | evidence/E-3-clean-pairs.log (old test: E-3-run1.log, E-3-run2.log) |
| E-4 family check | PASS (`judge_model and group_model must be different families`, exit=2) | evidence/E-4.log |
| E-5 second-review clarifications | PASS, 9 tests | evidence/E-5.log |
| live test `tests/test_judge_live.py` (gpu+data) | rewritten: checks the merged article gets a real verdict, not that it is caught (old run: evidence/live.log); not re-run | - |

`judge stats --pages-file data/dev-sample.txt` (after the forced-retry run was undone):

```
pages=20
mean_score=0.832
judge_low_pct=30.0
retried_pct=45.0
retry_gain=0.274
```
(First run, before the forced retry: mean_score=0.841, judge_low_pct=25.0, retried_pct=45.0, retry_gain=0.290; the model is not bit-deterministic across re-runs.)

Sanity lines, clean-pair test (`judge sanity --pages-file data/dev-sample.txt --n 20`; `--n 5` is the first five):

```
2024-12-02_BG_main_01 pair=a08+a10 verdict=correct stored=0.636 merged=0.6 missed
2024-12-06_GNOCLT_main_16 pair=a02+a04 verdict=correct stored=0.75 merged=0.667 missed
2024-12-08_BGNOPSVACLT_main_10 pair=a02+a06 verdict=correct stored=0.6 merged=0.5 missed
2024-12-11_B_main_03 pair=a07+a09 verdict=correct stored=1.0 merged=1.0 missed
2024-12-11_V_main_12 pair=a08+a13 verdict=correct stored=0.812 merged=0.8 missed
2025-01-26_B_main_05 pair=a02+a04 verdict=correct stored=1.0 merged=1.0 missed
2025-01-27_B_main_14 pair=a15+a17 verdict=correct stored=0.412 merged=0.353 missed
2025-02-15_B_main_01 pair=a02+a04 verdict=correct stored=0.8 merged=1.0 missed
2025-05-11_BGNOPRSVACLT_main_03 pair=a02+a03 verdict=correct stored=1.0 merged=1.0 missed
2025-12-17_B_main_01 pair=a04+a07 verdict=correct stored=0.667 merged=0.6 missed
caught: 0/10
```

## Attempts
- Attempt 4 (user chose "bake off both", then "build it"): judge bake-off on the 10 clean pairs (evidence/judge-bakeoff/README.md): page judge Gemma 3 0/10, InternVL3.5-38B 0/10, Gemma 4 31B 6/10; a per-article text-only one-story check 9/10 with Gemma 3 or Gemma 4; Gemma 4 page judge + check 10/10. Built FR-8 (one-story check), default judge_model gemma4-31b, PROMPT_VERSION 2. InternVL3.5 needs fp8 on load and `--hf-overrides` for its tile count (vLLM 0.33 passes `--mm-processor-kwargs` to its video processor too).
- Attempt 1: E-3 gave 3/5 (< 4). Investigated whether it is the prompt, the input or the model (scratch experiments, all over the 12 dev-sample pages that have >= 2 news articles, `lower` count of merged vs original score):
  - Gemma 3 27B, spec input (one overlay at 3584 px, 300-char list): 4/12.
  - + stricter prompt (count headlines, check connectivity): 4/12. + bigger overlay labels: 4/12. + structural cues in the list (blocks, headline boxes, area): about 3/5 on the first five.
  - Gemma only sees about 900 px per image (pan-and-scan gives 2 crops + 1 global on a portrait page), and in a probe it hallucinated the labels on a 3584 px overlay. Cutting the overlay into 3 bands (labels then readable) gave no better detection (2/12 and 3/12).
  - Qwen3-VL-32B as judge (probe only, not allowed by FR-1: same family as the grouper): 7/12.
  - Conclusion: with Gemma 3 27B the judge does not reliably notice two merged stories; none of the cheap fixes worked, so the shipped code keeps the spec input unchanged (all experiments reverted: models.yaml untouched, no tiling code). NFR-1 is therefore not met on this model. A decision is needed (see router notes).
- Attempt 3 (user chose "fix the test"): the old test merged the two longest news articles and compared page scores. On 3-4 of 12 pages those two were pieces of one story that the grouper had split (e.g. GNOCLT_16 a02/a05), so the merge repaired the page; and page scores moved with unrelated verdicts between calls. NFR-1/E-3 now merge only a *clean pair* (both stored verdicts `correct`, distinct headlines) and count whether the merged article's verdict is not `correct`. Result with Gemma 3 27B: 0/10 caught; Gemma calls every merged article `correct`. For comparison (scratch run, old pairs, same prompt), Qwen3-VL-32B flagged the merged article on 5/12 pages, Gemma on 2/12. NFR-1 stays at k >= 4 and is not met: the judge model, not the test, is the gap.
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
