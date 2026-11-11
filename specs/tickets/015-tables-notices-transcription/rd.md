# 015: Tables, infographics and notices transcription

**Status:** done
**Mode:** solo
**Runs:** waits for 013: needs final article groups

## Summary

indic-ocr does not transcribe figures, charts or ads, and the old system hid all table and infographic
text. Customer questions need the numbers in tables and infographics (debt, budgets) and the text of
government notices and tenders (mining leases). This pass has the grouping VLM read those regions into
Markdown, while commercial ads, classifieds and photos are dropped (grill D-9, A-25, A-40, A-48).

## Functional requirements

- **FR-1** - PageStage `transcribe` (`depends_on=("group_final",)`, `version="<group_model>-transcribe-p3"` (p2 since D-6, p3 since D-7), uses `group_model`). Targets on a page, each block transcribed at most once: (c) every article of kind `notice_tender` or `ad_commercial`, as one crop of its union box (padding 8 px, long side ≤ 3072) — blocks inside such an article are not also targets of (a)/(b); (a) blocks whose role is `table` or `infographic` in any other article; (b) blocks with a label in `table_labels`, `figure_labels` or chart labels (ticket 007 groups `table`, `figure`, `chart`) that are in no article. A `table_only` article's table blocks are (a) targets. Requests use `temperature: 0`; invalid JSON or a value outside the enums is retried 2 times, then the item is dropped and the page gets detail `transcribe_failed += 1` and page flag `low_confidence:transcribe_failed`.
- **FR-2** - For (a) and (b), the crop (padding 8 px, long side ≤ 2048) goes to the VLM with prompt `src/newsrag/prompts/transcribe_v1.md` asking for JSON `{"type": "table"|"infographic"|"photo_no_text", "title": str|null, "markdown": str}`: tables as a Markdown table with the header row; infographics as a title line followed by `- <label>: <value>` lines; `photo_no_text` with empty markdown. `photo_no_text` items are dropped.
- **FR-3** - For (c), the prompt asks `{"class": "notice_tender"|"commercial", "markdown": str, "department": str|null, "notice_number": str|null, "deadline": "YYYY-MM-DD"|null}`. The VLM's class overrides the grouping kind: a `notice_tender` item keeps its fields; a `commercial` item is stored with `type: commercial` and `title`, `markdown`, `department`, `notice_number`, `deadline` all `null`, and its article is excluded from indexing (ticket 017).
- **FR-4** - Artifact: `{page_id, items:[{item_id: "<page_id>#tNN", source: "block"|"article", block_ids, article_id|null, type: "table"|"infographic"|"notice_tender"|"commercial", title, markdown, department, notice_number, deadline, flags}]}`. (c) items carry their article's `article_id`; (a) items carry the article that holds the block (including `table_only` articles; grill A-11: linked to its story); (b) items have `article_id: null` and become standalone items. `flags` = the page's and the article's `low_confidence:*` flags. Items are numbered `tNN` by the lowest block `order` they cover, over all targets **before** dropping, so ids stay stable.
- **FR-5** - Prompts tell the model to copy text and numbers exactly and to write `[illegible]` rather than guess. The text must keep Telugu as Telugu (no translation).
- **FR-6** - `uv run newsrag transcribe spotcheck [--n 50] [--seed 1]` samples n kept items (tables, infographics, notices) across all done pages, gives the judge model (`judge_model`) the crop plus the transcription split into lines, and asks per line `present` | `not_present` | `unsure` (lines = non-empty lines of the markdown, excluding Markdown table separator rows like `|---|`; each table row is one line; the title line counts). If fewer than n items exist, all are used. It writes `<reports_dir>/transcription-spotcheck.json` with per-item results and `invented_pct` = not_present lines / all lines × 100 (2 decimals), and prints `invented_pct=<x>`.
- **FR-7** - Clarifications (second review):
  - The artifact has `page_flags` (`low_confidence:transcribe_failed` when set) and every item's `flags` also gets it; ticket 017 unions item flags into the units it builds.
  - Targets are chosen by role **or** label: a block with a label in `table_labels`/`chart_labels` inside an article is an (a) target whatever its role. Table/infographic blocks inside `masthead` or `other` articles become standalone items (`article_id: null`).
  - Items of a `table_only` article are marked `inlined: true` (ticket 017 puts them in the story text, ticket 023 does not chunk them again).
  - Stage attributes: `models_needed=(group_model,)`, `concurrency=group_concurrency`, model opened lazily on the first page with a target. The spot-check uses `newsrag.models.use(judge_model)` and takes `--pages-file`; with 0 kept items it prints `invented_pct=n/a` and exits 1. E-3 runs it with `--pages-file data/dev-sample.txt`. Notice items have `title: null`.

- **FR-8** - Added 2026-11-11 (D-6), after the 100-page check failed NFR-1 (2.87 %) mostly on big notices
  misread at the VLM's ~2.6 MP image budget:
  - Every request carries the indic-ocr text of its region (`ocr_final` block texts in `order`, at most
    `transcribe_hint_chars`) as a spelling reference; the image decides content, order and structure. A
    request that does not fit is sent with a third of the reference, then with none.
  - A notice/ad article whose padded crop is larger than `transcribe_tile_max_mp` is read in horizontal
    strips cut on lines no block crosses (else a forced cut with `transcribe_tile_overlap_px` overlap). One
    overview request (whole crop, `transcribe_article_max_px`) gives `class` and the notice fields; each strip
    is read with `transcribe_part_v1.md` and only its own blocks' reference text. The item's `markdown` is the
    strips joined by a blank line, and the item gets `parts: [{bbox, markdown}]`. A strip with no valid reply
    fails the item (FR-1 drop + flag).
  - The spot-check judges an item with `parts` strip by strip, each against its own crop.

- **FR-9** - Added 2026-11-11 (D-7): filler is not text. The prompts forbid dividers, image tags and rows with
  no printed word or number, and every reply's Markdown is cleaned the same way (`clean_markdown`: image tags,
  dividers and lines/rows without a letter or digit removed; a table whose header row was empty takes its
  first row as header). The spot-check counts only lines with a letter or digit after the same cleaning
  (extends FR-6's line rule).

## Non-functional requirements

- **NFR-1** - On the dev sample, `invented_pct` from the spot-check (n = all kept items, max 50) ≤ 2.0 (grill D-21).

## Affected areas

- `src/newsrag/stages/transcribe.py`, `src/newsrag/transcribe/spotcheck.py`, `src/newsrag/prompts/transcribe_v1.md`, `transcribe_notice_v1.md`, `spotcheck_v1.md` (new)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_transcribe.py` (new, fake model)

## Don't touch

- `group_final` artifacts.

## Out of scope

- Photo captions (grill A-8), commercial ads and classifieds content (D-9), chunking these items (023).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | What becomes searchable | tables, infographics, notices/tenders | grill D-9 |
| D-2 | Which model | the grouping VLM, separate pass | grill A-40 |
| D-3 | Format | Markdown; infographics as title + labelled values | grill A-25, A-40 |
| D-4 | Notice vs ad | the VLM classifies each ad-like region | grill A-25 |
| D-5 | Invented-text bar | ≤ 2% of lines judged not present | grill D-21 |
| D-7 | Filler lines (dividers, empty table rows, image tags) counted as invented | removed from the replies and not counted by the spot-check | user decision 2026-11-11 |
| D-6 | Big notices misread (100-page check 2.87 %) | read big notices in full-resolution strips and send the indic-ocr text as a reference with every request | user decision 2026-11-11 |

## Open questions

None.
