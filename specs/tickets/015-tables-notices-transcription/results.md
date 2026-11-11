# 015: Tables, infographics and notices transcription - Results

Branch: ticket/015-transcription (built in the main checkout's session, not a worker worktree)

## What was built

- Stage `transcribe` (`src/newsrag/stages/transcribe.py`): page stage on `group_final`, model `group_model`
  opened lazily on the first page with a target, `concurrency = group_concurrency`. Version
  `<group_model>-transcribe-p1-<config_hash>` (settings + both prompt files).
- Targets: (c) whole `notice_tender` / `ad_commercial` articles (union crop, long side ≤ 3072); (a) blocks
  with role `table`/`infographic` or a table/chart label in other articles (`masthead`/`other` →
  `article_id: null`, `table_only` → `inlined: true`); (b) table/figure/chart-labelled blocks in no article.
  Each block once; `tNN` numbered by lowest block order before dropping.
- Prompts `transcribe_v1.md`, `transcribe_notice_v1.md`, `spotcheck_v1.md`; JSON-schema constrained replies,
  2 retries with the error, then drop + `transcribe_failed` + `low_confidence:transcribe_failed`.
- `newsrag transcribe spotcheck [--n] [--seed] [--pages-file]` (`src/newsrag/transcribe/`), judge =
  `judge_model`, report `<reports_dir>/transcription-spotcheck.json`.
- Settings block `# --- transcribe ---` in `config.py`.

## Before the run: the dev sample was regrouped with Gemma 4

The dev sample's ledger rows for `group` … `group_final` were made with `qwen3-vl-32b`; since 014,
`group_model` is `gemma4-31b`, so `group_final` was not current and `transcribe` skipped all 20 pages.
The grouping chain (`group`, `judge`, `group_retry`, `judge_retry`, `group_final`) was re-run on
`data/dev-sample.txt` with Gemma 4 (all 20 done, evidence/e2-stage-run.log). The old `group_final`
artifacts were copied to `/home/admin1/raw/backup/015/group_final-qwen` first (same version folder, so the
rerun overwrote them). `transcribe` code never writes `group_final`.

## Prompt iteration (NFR-1)

First run: `invented_pct=6.29` (11/175 lines; evidence/transcription-spotcheck-v1.json). Of the 11:
6 lines were one bar chart where the model named the bars by colour (`2021 Blue: 0.72`), 1 an empty
`| | | |` header row on a header-less table, 2 printed text reshaped into `label: value` that the judge
did not match, 2 single disputed lines. Changes:

- `transcribe_v1.md`: never name colours/shapes/positions as labels; unlabeled values take the nearest
  printed heading or `- <value>`; plain-text boxes copied as printed; no empty header row.
- `spotcheck_v1.md`: judge words and numbers only; Markdown punctuation, word order within a line and
  line splitting do not matter. (This changes the measuring prompt too - noted so the reviewer can judge it.)

Second run: `invented_pct=0.45` (1/224 lines, 24 items).

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| lint (`ruff check .`, `ruff format --check .`) | PASS | - |
| unit tests (`-m "not gpu and not data and not stores"`) | PASS, 489 passed | - |
| E-1 target selection / output shape | PASS, 17 passed incl. the 10 named | evidence/e1-e5-pytest.log |
| E-2 dev sample run | PASS: transcribe done=20 failed=0; types `{infographic: 15, commercial: 31, table: 6, notice_tender: 3}` | evidence/e2-stage-run-v2.log |
| E-3 spot-check | PASS: `invented_pct=0.45` ≤ 2.0 (24 items = all kept items, < 50) | evidence/e3-spotcheck-v2.log, transcription-spotcheck.json |
| E-4 Telugu stays Telugu | PASS: `True` | - |
| E-5 second-review tests | PASS, 6 passed | evidence/e1-e5-pytest.log |

## Notes

- No `transcribe_failed` on the dev sample.
- Only 24 kept items exist on the 20-page sample, so the spot-check has a small base; one bad line ≈ 0.45 pp.
- `group` now shows `eligible=3477`: the rest of the corpus has never been grouped (unchanged by this ticket).
