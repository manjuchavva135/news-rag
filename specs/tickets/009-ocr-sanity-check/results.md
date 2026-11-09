# 009: OCR sanity-check page and sign-off - Results

Built on branch `009-ocr-sanity-check` (from main at `dc78707`, after 001-008). The checks ran
against the real ledger and the 20 done `ocr_split` artifacts of the dev sample. The live-server
checks used worker port 8701 and a scratch `NEWSRAG_OCR_CHECK_DIR`, so
`/home/admin1/raw/reports/ocr-check/` was never created: the real reader starts clean.

**One spec change, made with the user before building (D-5):** FR-1's slots were written for the
old 2020/2025/2026 corpus (`fea`/`splus`, `FH`). The dev sample is now Dec 2024 (8) + 2025 (12),
every page is `section=main`, and there are no `fea`/`splus`/`FH` pages (ticket 005 D-5). New
slots: 2024 = 2 front + 1 single-letter + 1 combined-code; 2025 = 2 front + 1 `ALL` + 1 spread +
1 single-letter + 1 combined-code. rd.md FR-1 / D-5 and eval.md E-1 have been updated.

## What was built

- `src/newsrag/ocr/check/pages.py`: `choose()` takes the slots from the `ledger.pages` columns
  (front = `section main` + `page_num 1`; the other predicates are the dev sample's).
  `build(force)` refuses with `… exists (use --force)` or `missing ocr_split for: <ids>`. In both
  cases it exits 1 and writes nothing.
- `src/newsrag/ocr/check/verdicts.py`: `Store` (thread-safe; every post is written atomically
  to `verdicts.json`), `row_to_json`, the sign-off rules with the four FR-2 messages, and
  `summary_lines`.
- `src/newsrag/ocr/check/server.py`: a stdlib WSGI app served by a threaded `wsgiref` server.
  There are no new dependencies. The templates are in `templates/` (`string.Template`, every
  value HTML-escaped). `static/` holds the CSS and JS, `NotoSansTelugu-Regular.woff2` (44 KB,
  converted from notofonts' unhinted TTF), and `OFL.txt`.
- CLI `newsrag ocr check build [--force] | serve [--port] [--host] | summary`.
- Settings under `# --- ocr_check ---`: `ocr_check_file`, `ocr_check_dir` (default
  `{reports_dir}/ocr-check`, a new `REPORTS_DIR_DERIVED` rule), `ocr_check_stage`,
  `ocr_check_host` (0.0.0.0), `ocr_check_port` (8700), `ocr_check_image_long_side` (2400),
  `ocr_check_image_max_bytes` (2,000,000), `ocr_check_jpeg_quality` (85).
- `data/ocr-check.txt` (committed), `tests/test_ocr_check.py` (19 tests; a WSGI test client and
  fake pages in tmp_path, no stores needed).

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| ruff check / format | PASS | `evidence/auto-ruff.log` |
| unit tests | PASS, 198 passed | `evidence/auto-pytest-unit.log` |
| E-1 page selection | PASS: `10`; 2024 → 4, 2025 → 6; `_main_01` = 4; `ALL` = 1; spread = 1; no `# short:` lines; `--force` → `same`; without `--force` exit 1 | `evidence/E-1.log` |
| E-2 review page | PASS: numbered boxes on the scan, Telugu blocks in the bundled font (`document.fonts.check` true), verdict and problem words present; clicking block 3 highlights box 3, and clicking a box selects its block and scrolls to it | `evidence/E-2.png`, `E-2-highlight.png`, `E-2-text.txt`, `index.png` |
| E-3 verdicts and sign-off | PASS: 10 × `acceptable numbers`, `SIGNED OFF by eval-tester at …`, `none`; changing one to `good` → `NOT SIGNED OFF` and the page says the sign-off was cleared | `evidence/E-3-summary.txt`, `E-3-summary-after-change.txt`, `E-3-*.png` |
| E-4 sign-off rules | PASS (`-k signoff_requires_all`: 1; `-k "signoff_validation or row_to_json"`: 2). In the browser, a note-only row shows `a row must narrow the pages` | `evidence/E-4.log`, `signoff-rejected.png` |
| E-5 restart | PASS: `summary` is the same before and after a restart; the index still shows the 10 verdicts | `evidence/E-5-before.txt`, `E-5-after.txt` |
| E-6 load time and size | PASS: page 0.001 s, warm JPEG 0.002 s, largest JPEG 1,744,339 B (the spread); `not-a-page` → 404; browser load event at 23-30 ms. Also answered on the LAN IP | `evidence/E-6.txt` |

## Where I read the spec a particular way

- **`ocr check` registration.** The `check` sub-app is registered in `src/newsrag/ocr/cli.py`
  (`app.add_typer(check_cli.app, name="check")`), not in `src/newsrag/cli.py`. `ocr` is already
  a sub-app there, and a nested group belongs to its module (CLAUDE.md CLI rule).
- **The verdict form is a bar pinned to the bottom of the window** (sticky; it drops back into
  the page below 900 px wide). It is "below" the two panes, and it stays visible while the reader
  compares scan and text. It also fixes a tester problem: agent-browser 0.27 does not scroll
  before a selector click, so a Save button below the fold was clicked off-screen.
- **"Changing a verdict"** means the verdict, problems or note differ from what is saved.
  Posting the same values again keeps the sign-off.
- **Blank sign-off rows.** A row with every field blank, the note included, is an unused slot
  and is ignored. A row with only a note is rejected with `a row must narrow the pages`. A
  blank note is left out of the JSON like the other fields. `years` / `page_nums` that are not
  whole numbers are rejected.
- **Order of the sign-off checks:** name, then box/rows, then each row, then "every page needs a
  verdict before sign-off (N missing)". The Sign off button is also disabled until all 10 pages
  have a verdict.
- **`summary`** prints `-` for a page with no verdict or no problems. Failing page types are
  printed one JSON object per line.
- **Images.** The JPEGs are cached in `<ocr_check_dir>/cache/` and rebuilt when the PNG is
  newer. If one is over 2 MB, the quality drops by 10 down to 40. `serve` builds all 10 JPEGs in
  a background thread at start (about 10 s), and each page has its own lock, so a cached image
  never waits behind another page's build. The slowest cold build is the 8103 px spread, at
  about 2.2 s. Static files are sent `no-cache`, so an edited CSS file is never served stale.
