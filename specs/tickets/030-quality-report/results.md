# 030: Quality report - Results

Built on `main` in the main checkout. Not committed yet.

## What was built

- `uv run newsrag report build` writes `<quality_report_dir>/index.html` (default
  `/home/admin1/raw/reports/quality/`), `report.json` and the bar snapshots
  `inputs/bar-{1,2,3}.json`.
- `src/newsrag/report/`: `bars.py` (the seven bars and their thresholds as constants, pure
  functions), `collect.py` (read-only queries and file reads), `build.py` (snapshots, JSON,
  HTML), `template.html` (inline CSS, no scripts or external requests), `cli.py`.
- New `stats() -> dict` modules: `grouping/stats.py`, `stitch/stats.py`, `dedup/stats.py`
  (adds the 1 / 2 / 3–4 / ≥5 printings buckets), `aliases/stats.py`, `editions/stats.py`. Their
  commands now call these. `judge stats`, `stitch stats`, `dedup stats`, `aliases stats` and
  `editions report` print byte-for-byte the same as before; I diffed each command's output on
  the real ledger before and after.
- `editions/mapping.py`: the per-year counts were moved into `year_counts()`, which
  `report_lines()` now uses.
- `config.py`: a new `# --- report ---` block with `quality_report_dir`. The customer-answers
  paths are ticket 028's settings (`customer_answers_file`, `customer_signoff_file`).

## How the rules were read

- "Done" (bars 2 and 3) uses the runner's own eligibility code (`PageContext.current` and
  `DateContext.current`): `done` at the current version with current inputs, so the row is not
  eligible again.
- Bar 1 counts the `no_date`, `bad_filename` and `bad_date` rows of `ledger.quarantine` that have
  no page. Two files count once when their folders give a date and their names parse to the same
  (date, edition, section, page). Pages with a `quarantine_reason` are added on top.
- Bar 2 takes the expected generation literally as `<embed_model>__v1`, as the rule says. It also
  runs `index validate` (`generations.validate`) on the active generation.
- Sign-offs (bars 5 and 7) are rejected when the name is `eval-tester`. Bar 7 also needs the
  sign-off's `answers_sha1` to equal the sha1 of the bytes of the current `customer-answers.json`,
  which is how 028 computes it.
- Low confidence: stories that have at least one chunk in the active generation, counted per
  `low_confidence_reasons` entry, with each story counted once per reason.
- The Aliases section shows only the numeric fields of `stats.json`, not the auto-merged group
  lists.
- A section or bar whose input raises an exception (for example, stores down) shows
  `not available` or FAIL, with the error under it.
- Every URL scheme outside the bake-off `<pre>` blocks is removed from the page text, so the page
  never links outside.

## Checks

| Check | Result | Evidence |
|---|---|---|
| `uv run ruff check .` / `ruff format --check .` | PASS | — |
| `pytest -m "not gpu and not data and not stores"` | PASS (957 passed) | — |
| E-1 `pytest tests/test_report.py -v` | PASS (17 passed, all 8 named tests included) | `evidence/E-1.txt` |
| E-2 build time | PASS: 1.8 s wall | `evidence/report-build.txt` |
| E-2 page text | PASS: `PHASE 1: FAIL (4 bars)`, 7 bars each PASS/FAIL, all 9 section titles with numbers | `evidence/E-2.png`, `evidence/E-2-text.txt` |
| E-2 phone (390×844) | PASS: scrollWidth 375 ≤ 390, bars shown as stacked cards | `evidence/E-2-phone.png` |
| E-3 JSON matches HTML | PASS: `7 FAIL`, matching the header | `evidence/E-3.txt`, `evidence/report.json` |
| E-4 no external requests | PASS: `0` | `evidence/E-4.txt` |
| E-5 clarification tests | PASS (5 passed) | `evidence/E-5.txt` |

## Current verdict on the dev data: PHASE 1: FAIL (4 bars)

| Bar | Value | Why |
|---|---|---|
| 1 Pages | 0.02% | PASS |
| 2 Indexed | 0.35% | Only one date is indexed at the current version. `embed_model` is `multilingual-e5-large-instruct` (026's winner), but the active generation is still `bge-m3__v1`. |
| 3 Grouping | mean 0.801, judge_low 35.22% | 159 pages, below the bar |
| 4 Transcription | 1.53% | PASS |
| 5 OCR sanity check | signed by manju | PASS |
| 6 Retrieval vs old | missing | `baseline-compare.json` doesn't exist yet (027 is still a draft) |
| 7 Customer answers | 92.97% supported, not signed | `customer-answers-signoff.json` doesn't exist yet |
