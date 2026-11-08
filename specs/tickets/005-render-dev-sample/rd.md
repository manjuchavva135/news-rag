# 005: Render pages and dev sample

**Status:** draft
**Mode:** solo
**Runs:** waits for 004: needs registered pages

## Summary

Renders every registered PDF page to a 300 DPI PNG (the input for OCR, the article-box overlays, the
judge and all visual checks), quarantines PDFs that can't be rendered, and builds a fixed, committed
"dev sample" of 20 varied pages that later tickets use for quick checks.

## Functional requirements

- **FR-1** - PageStage `render` (`src/newsrag/stages/render.py`, `version = "pdftoppm-{dpi}dpi-v1"` with dpi from `settings.render_dpi`, `depends_on = ()`) runs `pdftoppm -r <dpi> -png -f 1 -l 1 -singlefile <source_path> <images_dir>/.tmp-<page_id>` (timeout 120 s; `pdfinfo` timeout 30 s; a timeout is a failed attempt) and renames the result to `<images_dir>/<page_id>.png` only on success (atomic). Artifact = that PNG path; detail = `{width, height, bytes, pdf_pages}` (`pdf_pages` from `pdfinfo`). A PDF with more than one page renders page 1 and gets `detail.multi_page = true`; `uv run newsrag render stats` prints `rendered=<n> multi_page=<n> small_render=<n> quarantined=<n>` (also exposed as `stats()` for the quality report's Corpus section).
- **FR-2** - If `pdftoppm` exits non-zero, times out or produces no file, `handle` raises; the stage's `on_final_failure` hook (ticket 003 FR-3) quarantines the page with reason `render_failed: <first stderr line or 'timeout'>` via `quarantine_page` — but only when poppler itself rejected the PDF (non-zero exit with stderr, or timeout); environment errors (output dir not writable, disk full) raise without quarantining. The stage creates `images_dir` if missing. `uv run newsrag stage reset render --page P` also clears a `render_failed:` quarantine for P.
- **FR-3** - The stage's `preflight()` hook raises `StageUnavailable` when `pdftoppm` or `pdfinfo` is missing, so `stage run render` exits 2 with `poppler not found: run  sudo apt install poppler-utils` before connecting to the DB.
- **FR-4** - `uv run newsrag sample build` writes `data/dev-sample.txt` (committed in the repo): 20 page_ids, one per line, sorted, chosen deterministically from non-quarantined pages whose `render` is `done` (it refuses with `render not finished` while render has eligible or running items). Strata are filled in this order, each from candidates sorted by page_id with `random.Random(42).sample`, never picking a page already chosen:
  for each year 2020, 2025, 2026: (1) 2 front pages (`section=main`, `page_num=1`); (2) 2 pages with a single-letter edition code (letters only, length 1, any page number except 1) — for 2025, `ALL` pages with page ≠ 1 instead; (3) 1 page with a letters-only edition code of length ≥ 2 other than `ALL`/`FH` (no `-VK` variants; page ≠ 1) — for 2025, an `FH` page with `page_num` ≠ 1; (4) 1 `fea` or `splus` page; then (5) 2 more 2020 front pages. If a stratum has too few candidates, the shortfall is filled from that year's remaining pages and a line `# short: <year> <stratum>` is written at the top of the file. If `data/dev-sample.txt` already exists, `sample build` refuses with `data/dev-sample.txt exists (use --force)` and exits 1.
- **FR-5** - `uv run newsrag sample show` prints one line per sample page: `<page_id>\t<image path>\tok|missing`.

## Non-functional requirements

- **NFR-1** - `stage run render --workers 32` renders at ≥ 5 pages/s on this machine (128 cores).
- **NFR-2** - Rendered PNG width at 300 DPI is ≥ 3000 px for a normal broadsheet page (sanity guard: a page < 1500 px wide gets `detail.small_render = true`).

## Affected areas

- `src/newsrag/stages/render.py`, `src/newsrag/stages/registry.py` (new / changed)
- `src/newsrag/sample.py`, `src/newsrag/cli.py` (`sample` group) (new / changed)
- `data/dev-sample.txt` (new, committed)
- `tests/test_render.py` (new; uses a tiny generated PDF, no archive data)
- `src/newsrag/stages/runner.py`, `base.py` - only if a hook from ticket 003 needs a fix (changed)

## Don't touch

- `/home/admin1/raw/source/**` (read only), `/home/admin1/data/**`, `/home/admin1/RAG/**`.

## Out of scope

- OCR (007). Re-rendering at a higher DPI for retries (that is ticket 013's choice, through a separate version).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | DPI and format | 300 DPI PNG, kept on disk | grill A-21 |
| D-2 | Every page OCR'd? | yes, no legacy-font text-layer check | grill A-21 / F-4 |
| D-3 | Bad PDFs | quarantined after max attempts, never guessed | grill A-13 |
| D-4 | Dev sample | 20 fixed, stratified pages committed in `data/dev-sample.txt` | gives every later ticket the same quick test set |

## Open questions

None.
