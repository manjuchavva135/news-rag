# 014: Grouping model bake-off - Results

Branch: ticket/014-grouping-bakeoff (built in the main checkout's session, not a worker worktree)

## Ticket change before the build (user decision 2026-11-10, D-5)

The ticket was written before 013 finished. 013's judge bake-off found that InternVL3.5-38B caught 0/10
merged stories and Gemma 4 31B with the one-story check caught 10/10, so `judge_model` is now `gemma4-31b`.
The user chose: Gemma 4 judges the bake-off, and only the Qwen candidates are graded. `gemma3-27b` stays in
the default candidate list and is skipped by the judge-family rule. `--apply` writes `judge_model` = the
report's judge (FR-5 updated in rd.md; E-1/E-2/E-5 wording updated in eval.md).

## Second change (user decision 2026-11-11, D-6)

"Same model family can be used, use the best models for Telugu": new setting `judge_allow_same_family`
(default true) turns off the family rule in the bake-off and in 013's `judge`/`judge_retry` preflight;
`gemma4-31b` joined the candidates. The tie rule now compares every candidate within 0.01 of the best
(the old top-two rule could never pick a third tied candidate: found by a new test).

## Checks (final, four candidates, Gemma 4 judge)

| Check | Result | Evidence |
|-------|--------|----------|
| lint (`ruff check .`, `ruff format --check .`) | PASS | - |
| unit tests (`-m "not gpu and not data and not stores"`) | PASS, 472 passed | evidence/unit.log |
| E-1 profiles smoke | PASS: `qwen3-vl-30b-a3b` and `internvl3.5-38b` reply `NEWS 2026` | evidence/E-1-*.log |
| E-2 bake-off | PASS: Qwen-only run from scratch (`--rerun`) real 8m11s; adding the two Gemma models (Qwen outputs reused) real 9m37s, so about 18 min for all four (< 3 h); `wc -l` = 5; one row per candidate, no `skipped`; `winner: gemma4-31b` | evidence/E-2-bakeoff.log, E-2-bakeoff-gemma.log, E-2-pages.log, group-bakeoff.md |
| E-3 apply | PASS: 0.9 s; `group_model set to gemma4-31b`; pipeline.yaml `group_model: gemma4-31b`, `judge_model: gemma4-31b` | evidence/E-3-apply.log |
| E-4 tie/family rules | PASS: 24 tests incl. the 6 named | evidence/E-4.log |
| E-5 Telugu score + judge choice | PASS: 30 headlines; `telugu_read`, `judge:`, `winner:` lines; judge_model = report's judge; named tests pass | evidence/E-5.log |

## Result (judge gemma4-31b)

| candidate | mean judge | pages < 0.8 | s/page | telugu_read |
|---|---|---|---|---|
| **gemma4-31b** | **0.865** | 1 | 30.76 | **0.848** |
| qwen3-vl-32b | 0.461 | 4 | 12.36 | 0.343 |
| gemma3-27b | 0.356 | 5 | 12.41 | 0.662 |
| qwen3-vl-30b-a3b | 0.300 | 4 | 6.86 | 0.394 |

Winner `gemma4-31b`: best grouping score and best Telugu reader. Applied: `group_model: gemma4-31b`,
`judge_model: gemma4-31b` (the same model now judges its own grouping).

### Cross-check against self-grading (judge qwen3-vl-32b, same groupings)

evidence/group-bakeoff-crosscheck-qwen-judge.md: gemma3-27b 0.560, gemma4-31b 0.496, qwen3-vl-32b 0.495.
The judges disagree, so the disputed page `2024-12-06_GNOCLT_main_16` was checked by eye
(evidence/*-GNOCLT_main_16.png):
- gemma4-31b keeps story 1 whole and splits story 2, whose headline "మాయ 'లేడి'.. జగత్ కిలాడి!" spans two boxes,
  into two articles. Its own judge still gave 1.0, so it is lenient on splits.
- qwen3-vl-32b makes the same split and also cuts a piece of story 1's headline into its own article; the Qwen
  judge gave that 1.0, which is plainly wrong (and is 012's known E-7 failure).
- Counts that don't depend on a judge: on the front page `2024-12-02_BG_main_01`, gemma4-31b finds 24 articles
  (10 news) against qwen3-vl-32b's 8 (5 news). 013 already found Qwen lumping front pages together.
Conclusion: Gemma 4's grouping is at least as good as Qwen's and better on dense pages; the Qwen judge is the
less reliable one. Gemma 4's own score (0.865) is probably optimistic because it misses splits.

## Findings worth knowing

- **Throughput:** gemma4-31b groups at about 31 s/page with 5 requests in flight (0.03 pages/s), 2.5x slower than
  qwen3-vl-32b. This matters for the full-corpus run (031) and is far from 012's 0.2 pages/s target.
- **013's regroup pass does nothing for Gemma 4:** it re-sends the page at 1.5x resolution (5376 px), but Gemma 4's
  profile caps every image at about 1120 tokens (`max_soft_tokens`), so the model sees the same picture.
- **Self-judging:** with `judge_model == group_model`, the judge misses splits it made itself (example above).
- `2024-12-05_BGNOP_main_01` is `judge_skipped` (null → 1) for three candidates under the Gemma 4 judge; the Qwen judge
  scores the same groupings 0 (it sees missed stories). Without that page Gemma 4 is still 0.83 against 0.33.
- Run-to-run noise is about ±0.04 on 5 pages (32B scored 0.501 in the dry run, 0.461 in the final run).

## First result (Qwen candidates only, before D-6)

Qwen-only run: qwen3-vl-32b 0.461, qwen3-vl-30b-a3b 0.300, gemma3-27b skipped (evidence/group-bakeoff-qwen-only.md).

## Changes

- `models.yaml` - profile `qwen3-vl-30b-a3b` with `max_model_len: 65536` (rd said 32768; raised like `qwen3-vl-32b`
  so 013's 5376-px regroup fits). `internvl3.5-38b` is unchanged from 013.
- `src/newsrag/config.py` - `# --- bakeoff ---` block (candidates incl. gemma4-31b, files, Telugu filters, crop pad, read tokens, tie); `judge_allow_same_family` in `# --- judge ---`.
- `src/newsrag/grouping/judge.py` - `check_families` honours `judge_allow_same_family`.
- `src/newsrag/grouping/bakeoff.py` (new) - page/headline selection, Telugu similarity, candidate runs, judging,
  winner rule, report, apply.
- `src/newsrag/grouping/cli.py` - `newsrag group bakeoff [--candidates] [--judge] [--pages-file] [--rerun] [--apply]`.
- `data/bakeoff-pages.txt`, `data/telugu-headlines.txt` (new, committed, generated by the command).
- `tests/test_bakeoff.py` (new, 24 tests, fake models); `tests/test_models_client.py` - profile list; `tests/test_judge.py` - family tests switch the rule on; `tests/conftest.py` - unit tests pin `group_model`/`judge_model` to the code defaults instead of reading pipeline.yaml.
- `pipeline.yaml` - `group_model: gemma4-31b`, `judge_model: gemma4-31b` (by `--apply`).

## Notes

- Outputs: `<work_dir>/group_bakeoff/<candidate>-group-p1-<hash>/` (+ `timing.json`), `<candidate>-telugu-r1-<hash>/telugu.json`,
  `<work_dir>/group_bakeoff_judge/<group version>/<judge>-judge-p2-<hash>/`. Directory names end in the config hash (repo convention),
  and the judge dir sits under the grouping version, so a regroup never reuses stale judgements.
- No ledger rows are written; `work/group/` is untouched.
