# 005: Render pages and dev sample - Results

Built in the main checkout on `main`, on top of ticket 004's work (which is not committed yet
either). Nothing is committed.

Two spec changes were made with the user before building and recorded in rd.md / eval.md:

- **D-5 dev-sample strata.** The corpus is now Dec 2024 (746 pages) + 2025 (2,751). Every page is
  `section=main`; there are no `fea`/`splus`/`FH` pages, 2 `ALL` pages and 1 spread. New strata:
  2024 = 3 front + 3 single-letter + 2 combined-code; 2025 = 4 front + 3 single-letter +
  3 combined-code + 1 `ALL` + 1 spread. E-3 now expects 2024 = 8, 2025 = 12, at least 7 front
  pages, at least 1 `ALL` page and at least 1 spread.
- **D-6 render speed.** FR-1's pdftoppm PNG command spends about 85% of its time encoding the PNG.
  Measured: 2.8 pages/s at 32 workers, 5.1 at 64, 6.5 at 96. The command is unchanged; the default
  concurrency is now `settings.render_workers = 96`, and NFR-1 / E-1 use `--workers 96`.

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| `ruff check .` / `ruff format --check .` | PASS | `evidence/auto-ruff.log` |
| unit tests | PASS, 99 passed | `evidence/auto-pytest-unit.log` |
| stores tests (all) | PASS, 48 passed | `evidence/auto-pytest-stores.log` |
| E-1 render all pages | PASS: 3,497 pages in 328 s real (budget 699 s), so 10.7 pages/s. 0 failed. Re-runs did nothing. `done=3497 eligible=0` matches the 3,497 non-quarantined pages. Median width 4052 px (min 4050, max 8103). | `evidence/E-1.log`, `evidence/E-1-run1.log` |
| E-2 PNG of a known page | PASS: `2025-02-15_B_main_01.png: PNG image data, 4052 x 6804` | `evidence/E-2.log` |
| E-3 dev sample | PASS: `20`; `same` then `exit=1`; 2024 = 8 and 2025 = 12; `show` gives 20 `ok`; front / ALL / spread = 7 / 1 / 1; no `# short:` lines | `evidence/E-3.log`, `evidence/dev-sample.txt` |
| E-4 corrupt PDF, timeout, partial PNG | PASS (1 + 2 tests) | `evidence/E-4.log` |
| E-5 missing poppler | PASS: exit 2 and the message; the test also checks the DB is not touched | `evidence/E-5.log` |

Real-data notes:
- No page failed to render, so nothing was quarantined (`render stats`: `rendered=3497
  multi_page=0 small_render=0 quarantined=0`). Every PDF has exactly 1 page.
- The PNGs take 40 GB under `/home/admin1/raw/images` (about 11.5 MB per page). The future
  ~21k-page corpus would need about 240 GB.

## Where I read the spec a particular way

- **Version** = `versioned("pdftoppm-<dpi>dpi-v1", ["render_dpi"])`, i.e.
  `pdftoppm-300dpi-v1-<hash>`, following CLAUDE.md's "version ends with the config hash" rule.
  Timeouts, binaries and worker count are not part of the version, because they don't change the
  output.
- **What counts as "poppler rejected the PDF"** (quarantined after the last attempt): a non-zero
  exit with stderr, or a timeout. The exception is when the failure is the machine's fault:
  - `images_dir` is not writable;
  - less than `render_min_free_bytes` (1 GB) is free;
  - stderr says `Could not write` / `No space left` / `disk quota` / `read-only file system`.

  Needed because pdftoppm reports an unwritable output folder as exit 1 + `Could not write
  image ...`, the same shape as a corrupt PDF. A missing source file is not a rejection either
  (that is register's `source_missing`). Non-zero exit with empty stderr, or exit 0 with no PNG:
  plain failure, never quarantined.
- **Quarantine reason** = `render_failed: <first non-empty stderr line>`. For a 10-byte junk file
  that line is poppler's `Syntax Warning: May not be a PDF file (continuing anyway)`. Timeout:
  exactly `render_failed: timeout`.
- **Timeout** kills the whole process group (`start_new_session`), so a child process can't keep
  the run waiting.
- **`pdfinfo`** runs after pdftoppm succeeded; the PNG is renamed into place only after both
  succeeded. A failure leaves no `<page_id>.png` and no `.tmp-*` file. An older PNG from an
  earlier successful run is left alone.
- **`detail`** always has `multi_page` and `small_render` (true/false), on top of
  `width/height/bytes/pdf_pages`. Width and height are read from the PNG header (no Pillow).
- **`stage reset render`** lifts `render_failed:` quarantines (pages column + `quarantine` table
  row) for `--page`, `--date` and `--all`, not only `--page`. Other quarantine reasons are kept.
  This needed a small generic hook from ticket 003: `on_reset(c, page_id, pub_date, all_rows)` in
  `stages/base.py`, called by `reset_stage` in the same transaction.
- **`sample build`** checks "file exists" before "render not finished". It uses one
  `random.Random(dev_sample_seed=42)` for the whole build. "Letters only" is `[A-Za-z]`. The `ALL`
  and spread strata take any page number. `sample show` with no sample file exits 1 with a hint.
- **Layout:** the ticket names `src/newsrag/sample.py`. CLAUDE.md wants one package per module
  with its own `cli.py`, so the code is in `src/newsrag/sample/{__init__,cli}.py` and
  `src/newsrag/render/cli.py` (`render stats`). The stage itself is `src/newsrag/stages/render.py`,
  as specified.
- **E-3 step 2** copied the sample to the session scratchpad instead of `/tmp/s1`; nothing else
  changed.

## Not done / follow-ups

- No reviewer pass was run (built inline, not by a worker agent).
- Ticket 004 flagged two pages that may sit in the wrong folder: `15GNORSVACLT-MAIN_10.pdf` and
  `15P-MAIN_10.pdf`, registered as 2024-12-17. Their images now exist
  (`2024-12-17_GNORSVACLT_main_10.png`, `2024-12-17_P_main_10.png`), so the printed date on them
  can be checked by eye.

## Files

- `src/newsrag/stages/render.py` (new), `src/newsrag/stages/registry.py` (registers `render`)
- `src/newsrag/stages/base.py`, `src/newsrag/stages/runner.py` (the `on_reset` hook)
- `src/newsrag/render/__init__.py`, `src/newsrag/render/cli.py` (new: `render stats`)
- `src/newsrag/sample/__init__.py`, `src/newsrag/sample/cli.py` (new: `sample build|show`)
- `src/newsrag/cli.py` (adds `render`, `sample` groups)
- `src/newsrag/config.py`: `# --- render ---` gains `render_workers`, `pdftoppm_bin`,
  `pdfinfo_bin`, `render_timeout_s`, `pdfinfo_timeout_s`, `render_small_width_px` and
  `render_min_free_bytes`. New `# --- sample ---` block with `dev_sample_file` (follows
  `repo_root`) and `dev_sample_seed`.
- `data/dev-sample.txt` (new, to be committed)
- `tests/test_render.py` (new, 25 tests)
- `specs/tickets/005-render-dev-sample/rd.md`, `eval.md` (D-5, D-6)
