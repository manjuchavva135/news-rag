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

## 100-page check (user request: the 20-page base was too small)

Pages: `data/transcribe-check-100.txt` = the 20 dev-sample pages + 80 random non-quarantined pages
(seed 15). Grouping chain re-run with Gemma 4 on the 80 new pages (all done), then `transcribe`:
99 done, 1 failed (`2024-12-04_B_main_10`: request timeout at 300 s). Evidence: evidence/run-100-pages.log.

| Spot-check | Items | Lines | not_present | invented_pct |
|------------|-------|-------|-------------|--------------|
| `--n 50` (ticket's E-3) | 50 | 584 | 15 | **2.57** (FAIL, bar 2.0) |
| all kept items | 127 | 1254 | 36 | **2.87** (FAIL) |

Of the 36 not-present lines, 28 are in notices, 4 in tables, 4 in infographics. The notices fail
mostly by misreading clearly printed Telugu (e.g. "స్టయిఫండరీ స్కీమ్" read as "స్వయంబందీ సీఎం"
in a 2681×3915 px notice): a whole-notice crop is shrunk to Gemma 4's ~2.6 MP image budget, and small
Telugu becomes unreadable. A few lines are invented table headers (`| అంశం | వివరాలు |`, `| | |`) and
garbled Hindi. NFR-1 was not met on 100 pages, which led to D-6 below.

## After D-6: strips + indic-ocr reference (FR-8), same 100 pages

`transcribe` (p2): 100/100 done, 0 failed, 666 s; the page that timed out before (`2024-12-04_B_main_10`)
is done. 6 notices were read in strips. Evidence: evidence/run-100-pages-v2.log.

| Spot-check | Items | Lines | not_present | invented_pct |
|------------|-------|-------|-------------|--------------|
| `--n 50` (ticket's E-3) | 50 | 505 | 8 | **1.58** (PASS) |
| all kept items | 129 | 1304 | 24 | **1.84** (PASS) |

By type (all items): notices 11/494 lines, infographics 9/392, tables 4/418. 7 of the 24 lines carry no
text: Markdown dividers `***`, empty rows `| | |`, an image tag `![Emblem of India]`. The rest are still
misread Telugu words in small infographics, and invented header cells. The margin to 2.0 is thin.
Evidence: evidence/transcription-spotcheck-100p-n50-v2.json, transcription-spotcheck-100p-all-v2.json.

Unit tests: 496 passed (24 in tests/test_transcribe.py, incl. 7 for strips, the reference and the
per-strip spot-check).

## After D-7: filler lines removed (FR-9), same 100 pages

`transcribe` (p3): 100/100 done, 0 failed, 672 s. Items: table 37, infographic 57, notice_tender 36,
commercial 125. No filler line (no letter or digit, not a separator) is left in any artifact; Telugu present.
Evidence: evidence/run-100-pages-v3.log.

| Spot-check | Items | Lines | not_present | invented_pct |
|------------|-------|-------|-------------|--------------|
| `--n 50` (ticket's E-3) | 50 | 492 | 1 | **0.20** (PASS) |
| all kept items | 130 | 1303 | 20 | **1.53** (PASS) |

The 20 remaining lines: 9 in notices (4 from one Hindi/English notice whose Hindi is misread), 7 in small
infographics (misread Telugu), 4 table header cells. Evidence: evidence/transcription-spotcheck-100p-n50-v3.json,
transcription-spotcheck-100p-all-v3.json.

Unit tests: 500 passed (28 in tests/test_transcribe.py).
