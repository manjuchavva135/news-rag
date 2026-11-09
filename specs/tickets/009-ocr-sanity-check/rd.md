# 009: OCR sanity-check page and sign-off

**Status:** done
**Mode:** solo
**Runs:** waits for 008: shows split OCR blocks

## Summary

The user decided OCR is not the main problem, so indic-ocr gets one sanity check instead of a gold set
(grill D-3, A-7): a Telugu reader looks at ~10 varied pages, scan and OCR text side by side, and either
signs off or names the page types that fail. Failing page types become the Sarvam fallback rules
(ticket 010, grill A-38). This ticket builds the small local review tool and records the verdicts.

## Functional requirements

- **FR-1** - `uv run newsrag ocr check build` writes `data/ocr-check.txt` (committed): 10 page_ids taken from `data/dev-sample.txt` (`#` lines skipped), each slot filled by the first not-yet-chosen sorted line matching it (slots per D-5) — 2024: 2 × `main_01`, 1 × single-letter edition with page ≠ 1, 1 × combined-code edition (2+ letters, not `ALL`) with page ≠ 1; 2025: 2 × `main_01`, 1 × `ALL`, 1 × spread (`page_end` set), 1 × single-letter edition with page ≠ 1, 1 × combined-code edition with page ≠ 1. Matching uses the `ledger.pages` columns (edition_code, section, page_num), not the id string. A slot with no match is filled by the next unused line of that year and noted as `# short: <year> <slot>` at the top. It refuses (exit 1, `missing ocr_split for: <ids>`) if any chosen page has no `ocr_split` artifact, and refuses to overwrite an existing file without `--force`.
- **FR-2** - `uv run newsrag ocr check serve [--port 8700]` starts a local web server (stdlib or FastAPI, bound to `0.0.0.0` so the reader can open it from another machine on the LAN) with:
  - `/` — list of the 10 pages with their verdict status.
  - `/page/<page_id>` (404 for a page not in the list) — left: the page as a downscaled JPEG (long side 2400 px, ≤ 2 MB, cached in `<reports_dir>/ocr-check/cache/`, served at `/page/<page_id>/image.jpg`) with the block boxes and numbers drawn in the browser as an SVG layer scaled from the block bboxes, and a link to the full PNG; right: the `ocr_split` blocks in reading order, each with its number, label and full text. Hovering/clicking a block on either side highlights it on the other. Below: a form with verdict radio buttons `good` / `acceptable` / `bad`, a free-text "what is wrong" field, and checkboxes for problem types: `headlines`, `body text`, `numbers`, `names`, `reading order`, `tables`, `old scan quality`, `other`.
  - `/signoff` — reader name, a checkbox `No page types need Sarvam`, or one or more structured "failing page type" rows (text fields `years`, `sections`, `page_nums`, `edition_codes`, values comma-separated — same shape as a ticket-010 `ocr_fallback.yaml` rule — plus a `note`; stored as JSON with `years`/`page_nums` as int lists, `sections`/`edition_codes` as string lists, and a blank field left out of the JSON). The server rejects with a visible message: both the checkbox and rows given (`choose either no page types or rows`), neither given (`tick the box or add a row`), a blank name (`enter your name`), a row with all four fields blank (`a row must narrow the pages`), and a `Sign off` button (enabled only when all 10 pages have a verdict). Changing any page verdict after sign-off clears the sign-off (it must be signed again); signing again overwrites it.
- **FR-3** - Verdicts are saved to `<reports_dir>/ocr-check/verdicts.json`: `{pages:{<page_id>:{verdict, problems:[...], note, updated_at}}, signoff:{name, failing_page_types: [] | [{years, sections, page_nums, edition_codes, note}], signed_at} | null}`. Saving is immediate (each form post) and survives a server restart.
- **FR-4** - `uv run newsrag ocr check summary` prints one line per page `page_id verdict problems`, then `SIGNED OFF by <name> at <time>` or `NOT SIGNED OFF`, then the failing page types (one line each, or `none`).
- **FR-5** - The UI text is English with Telugu content shown as-is, rendered with a bundled webfont `static/NotoSansTelugu-Regular.woff2` (SIL OFL, licence file alongside) so it works on any machine without installed fonts.

## Non-functional requirements

- **NFR-1** - The page view loads in < 2 s (measured on localhost) (image served as a downscaled JPEG ≤ 2 MB plus the full-size PNG on click).

## Affected areas

- `src/newsrag/ocr/check/` (new: server, templates, static)
- `src/newsrag/ocr/cli.py` - registers the `ocr check` group (changed; `ocr` is already a sub-app of `src/newsrag/cli.py`)
- `src/newsrag/config.py` - `# --- ocr_check ---` settings (changed)
- `data/ocr-check.txt` (new, committed)
- `tests/test_ocr_check.py` (new; uses the server's test client and a fake page)

## Don't touch

- OCR stage outputs (read only).

## Out of scope

- Turning the reader's "failing page types" into rules (that is a config edit in ticket 010's `ocr_fallback.yaml`, done by the user or the 031 run).
- Any accuracy metric (no gold set, grill D-3).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | How OCR is checked | ~10 varied pages, side by side, by a Telugu reader | grill A-7, D-3 |
| D-2 | Where verdicts live | `<reports_dir>/ocr-check/verdicts.json` | read by the quality report (030) and the sign-off (031) |
| D-3 | Failing page types | structured rows in the rule shape of ticket 010 | ticket 031 can check `ocr_fallback.yaml` against them by machine |
| D-4 | Edit after sign-off | clears the sign-off | the sign-off must match the verdicts it covers |
| D-5 | Page slots after the input changed (2026-11-09) | the dev sample is now Dec 2024 (8) + 2025 (12), every page `section=main`, no `fea`/`splus`/`FH`; slots are 2024: 2 front + 1 single-letter + 1 combined-code, 2025: 2 front + 1 `ALL` + 1 spread + 1 single-letter + 1 combined-code (4 + 6 = 10) | user choice; the old 2020/2025/2026 slots had no candidates (as in ticket 005 D-5) |

## Open questions

None.
