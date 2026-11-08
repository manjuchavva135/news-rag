# 030: Quality report

**Status:** draft
**Mode:** solo
**Runs:** waits for 029: reads all run stats; waits for 026, 027, 028: their scores

## Summary

One static HTML page that says whether phase 1 is done: every pass bar from the grill record with its
number and PASS/FAIL, plus the stats behind them (grill D-14, D-21, A-17). It is generated from the
ledger, the stores and the eval outputs, so anyone can rebuild it at any time.

## Functional requirements

- **FR-1** - `uv run newsrag report build` writes `<reports_dir>/quality/index.html` (self-contained: inline CSS, no external requests, readable on a phone) and `<reports_dir>/quality/report.json` with the same numbers.
- **FR-2** - Bars (each row: name, value, threshold, PASS/FAIL; FAIL when the input is missing):
  1. Pages: from the ledger, quarantined % = (filename rejects in `ledger.quarantine` without a page + pages with `quarantine_reason`) / (registered pages + filename rejects) × 100; **≤ 1%** (A-34).
  2. Indexed: non-quarantined pages whose date has `index` done (active generation) / non-quarantined pages; **= 100%**, and `index validate` (run by `report build` itself) passes.
  3. Grouping: over non-quarantined pages with `group_final` done (null scores excluded from the mean): mean kept judge score **≥ 0.90**, and pages with `low_confidence:judge_low` / those pages **≤ 5%** (D-21; "flagged" here means the grouping judge's flag — other flags are shown in the stats).
  4. Transcription: `invented_pct` from `transcription-spotcheck.json` **≤ 2%**.
  5. OCR sanity check: `verdicts.json` signed off (name + date) — **signed**.
  6. Retrieval vs old: `baseline-compare.json` **new recall@10 > old recall@10**.
  7. Customer answers: `customer-answers.json` **supported_pct ≥ 90** with `n_failed = 0`, and `customer-answers-signoff.json` whose `answers_sha1` equals the sha1 of the current answers file and whose `name` is not `eval-tester` — **signed**.
  The page header shows `PHASE 1: PASS` only if every bar passes, else `PHASE 1: FAIL (<n> bars)`.
- **FR-3** - Stats sections (no bars), with these exact titles: `Corpus` (per year pages/stories/chunks), `Dedup` (stories, printings-per-story distribution, stories with ≥ 5 printings), `Continuations` (markers, linked, unmatched by reason), `Low confidence` (each `low_confidence:` reason with counts), `Editions` (`ok`/`ambiguous`/`unknown_edition` per year), `Aliases` (entities, auto pairs, review pairs), `Bake-offs` (the embedding and grouping bake-off reports pasted as preformatted text plus their parsed `winner:` lines), `OCR fallback` (current rules and pages sent to Sarvam), `Runs` (each run file: start, duration, tag, models_loaded, interrupted). Numbers come from `stats() -> dict` functions in the modules behind the `judge stats`, `stitch stats`, `dedup stats`, `aliases stats` and `editions report` commands (this ticket adds such a function where a module lacks one, without changing the CLI output). A section whose input is missing shows `not available`.
- **FR-4** - Each bar links to the source it read: bars 4–7 to their files; bars 1–3 to snapshot files `quality/inputs/bar-<n>.json` that `report build` writes with the queried numbers (relative links inside `reports_dir`). The page has `<meta name="viewport" content="width=device-width, initial-scale=1">` and no horizontal scrolling at 390 px width.
- **FR-5** - Clarifications (second review):
  - Bar 5 also requires the sign-off `name` ≠ `eval-tester`.
  - "Done" in bars 2 and 3 means `done` at the stage's current version and not eligible again (ticket 003 rules); bar 2 additionally requires `warehouse.active_generation` = `<embed_model>__v1` and the date's `index` row version to start with `index-v1-<embed_model>`.
  - Bar 1 counts filename rejects except `test_or_dummy`, de-duplicated by (date, edition, section, page) where parseable, plus pages with `quarantine_reason`.
  - Section sources: `Corpus` — pages from the ledger (non-quarantined), stories and chunks from the warehouse's active generation, plus ticket 005's render `stats()`; `Low confidence` — counts of **stories** in the active generation per reason, from `warehouse.stories.low_confidence_reasons` (which ticket 023 fills from all flag sources); `OCR fallback` — current rules and the number of `ocr_final` rows with `detail.fallback = true` and `detail.n_sent > 0`; `Dedup` — printings-per-story buckets 1, 2, 3–4, ≥ 5. The `stats()` for `judge stats` lives with ticket 013's code wherever it is.
  - Bar thresholds are constants in `src/newsrag/report/bars.py`.

## Non-functional requirements

- **NFR-1** - `report build` finishes in < 2 min on the full corpus.

## Affected areas

- `src/newsrag/report/` (new: builder + one HTML template); `src/newsrag/cli.py` (changed)
- `tests/test_report.py` (new; fixture inputs for PASS and FAIL cases)
- `src/newsrag/{grouping,stitch,dedup,aliases,editions}/stats.py` - `stats()` functions if missing (new/changed)

## Don't touch

- The inputs (read only).

## Out of scope

- A live dashboard or server; editing results.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Bars | exactly the D-21 / A-34 bars plus "all pages indexed" | grill D-14, D-21 |
| D-2 | Missing input | counts as FAIL | nothing passes by default |
| D-3 | Format | static HTML + JSON | grill A-17 |

## Open questions

None.
