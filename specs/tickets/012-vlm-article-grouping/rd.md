# 012: VLM article grouping

**Status:** done
**Mode:** solo
**Runs:** waits for 010: needs final OCR blocks; waits for 006: model runner

## Summary

The core fix for the old system's poor results. The old code grouped blocks into articles purely in
reading order, so kickers and pull-quotes opened fake articles, "in brief" columns merged stories, and
mastheads and ads leaked in (grill F-6). Here a local vision-language model looks at the page with every
block numbered and returns the articles: which blocks belong together and what role each block plays.
Geometry rules then check the answer and flag anything doubtful (grill D-5). Geometry also sets the
reading order inside each article and drops duplicate boxes: the ticket-009 OCR review found Surya's
block numbers often run across a story's columns, and a headline read twice by overlapping boxes.

## Functional requirements

- **FR-1** - PageStage `group` (`depends_on=("ocr_final",)`, `version="<group_model>-group-p<prompt_version>"`; `group_model` setting, default `qwen3-vl-32b`, read from `pipeline.yaml` if present; prompt in `src/newsrag/prompts/group_v1.md`, `prompt_version = 1`). `models_needed = (group_model,)`; `concurrency = group_concurrency` (default 16 threads). `open()` enters `newsrag.models.use(group_model)` once per `stage run` and shares the client with all threads; `close()` exits it. A page whose `ocr_final` has 0 blocks makes no model call: its artifact has empty `articles` and page flag `low_confidence:no_blocks`. Requests use `temperature: 0`, `max_tokens: 4096`.
- **FR-2** - Model input per page: (a) the page image with every `ocr_final` block's box drawn and its number written on it (reuse ticket 008's overlay), resized so the long side is `group_image_max_px` (default 3584); (b) a text list with one line per block: `#<order> [<label>] (x1,y1,x2,y2) <first 80 chars of text>` with coordinates in 300-DPI page pixels (the prompt states the page size so the model can relate them to the image); when a page has more than 150 blocks the text is cut to 40 chars.
- **FR-3** - The prompt asks for strict JSON (each article's `blocks` listed in the order a reader reads them): `{"articles":[{"kind": "news"|"editorial"|"brief_item"|"notice_tender"|"table_only"|"ad_commercial"|"masthead"|"other", "blocks":[{"n": <order>, "role": "headline"|"kicker"|"subheadline"|"byline"|"dateline"|"body"|"caption"|"photo"|"table"|"infographic"|"pull_quote"|"continuation_marker"}]}], "unassigned":[<order>...]}`. It explains: each story in a briefs column is its own `brief_item`; mastheads, folios, page headers and section banners are `masthead`; product ads, classifieds and matrimonials are `ad_commercial`; government notices and tenders are `notice_tender`; a pull-quote or kicker belongs to the story it sits in and never starts an article.
- **FR-4** - Output is parsed with a JSON schema check. Schema errors include an unknown kind or role and a block number that doesn't exist on the page. On invalid JSON or schema errors the request is retried up to 2 times with the error appended; then the page fails. An article with an empty `blocks` list is dropped silently.
- **FR-5** - Geometry checks (in `src/newsrag/grouping/geometry.py`), results stored as flags:
  - every block number appears in exactly one article or in `unassigned`; numbers the model omitted are added to `unassigned` with page flag `blocks_omitted`; a number used twice → page flag `block_duplicated` (first use kept);
  - all distances are in 300-DPI page pixels; the distance between two blocks is the edge-to-edge gap between their rectangles (0 if they touch or overlap);
  - per article: blocks form one connected region (each block within `group_max_gap_px`, default 60, of another block of the same article) else flag `disconnected`;
  - the article's union box overlaps another article's blocks by > 20% of that block's area → flag `overlaps_other`;
  - a `news`/`editorial` article with no `headline` block → flag `no_headline`; a headline whose top y is more than 50 px below the smallest top y of its body blocks (`headline.y1 > min(body.y1) + 50`) → flag `headline_below_body`.
- **FR-6** - Artifact: `{page_id, model, prompt_version, articles:[{article_id: "<page_id>#aNN", kind, blocks:[{block_id, role}], headline, text, bbox_union, flags:[]}], unassigned:[block_id...], page_flags:[]}`. `headline` = headline blocks' text joined by a space (`""` when there is none); `text` = kicker, subheadline, byline, dateline, then body blocks joined by blank lines (caption, photo, table, infographic, pull-quote and continuation-marker blocks are excluded from `text` but kept in `blocks`). Inside each role, blocks are in the FR-9 reading order, never in ascending `order`; duplicates (FR-10) are left out of `headline` and `text`. Article numbering `aNN` follows the lowest block `order` of each article.
- **FR-7** - `uv run newsrag group overlay <page_id> [--stage group|group_final] [--out PATH]` writes a PNG with each article's blocks filled in one translucent colour, its number and kind written at its top-left, and flagged articles outlined in red.
- **FR-8** - Clarifications (second review):
  - `pipeline.yaml` already exists (tickets 001/007): this ticket adds `group_model` with `set_pipeline_key`, keeping other keys.
  - Transport/size failures: per-request timeout 300 s; timeouts, connection errors and 5xx are retried 2 times with backoff; HTTP 400 for context length, or `finish_reason == "length"`, is handled by splitting: the page is grouped in two halves (blocks left/right of the page's vertical centre line, each with the full image) and the two results are merged, articles renumbered; if a half still fails, the page fails. `max_tokens` is 8192.
  - The version includes the config hash of ticket 003 (covers `group_image_max_px`, `group_max_gap_px`, the four FR-9/FR-10 settings, the 150-block/40-char cut and the prompt).
  - `group stats` prints exactly `pages=<n> failed=<n> text_blocks=<b> unassigned=<u> pct=<x.x>` where text blocks = blocks in `text_labels` + `title_labels`.
  - Function `newsrag.grouping.overlay.draw_articles(image_path, artifact, max_long_side=None, mark_flags=True, kinds=None) -> PIL.Image` backs the CLI; ticket 013 calls it with `mark_flags=False` and only the judged kinds. `--stage group_final` before ticket 013 prints `no group_final artifact for <page_id>` and exits 1.
  - `ocr_final.page_flags` are copied into the artifact's `page_flags`.

- **FR-9** - Reading order inside an article (ticket 009 review: on `2024-12-06_GNOCLT_main_16` Surya numbered one story 4, 5, 8, 12, 32 where a reader reads 5, 32, 4, 12, 8). `geometry.column_order(blocks)` orders a set of blocks; all sizes in 300-DPI page pixels:
  - (a) **Same column:** two blocks are in the same column when their x-ranges overlap by at least `group_order_same_column_frac` (default 0.5) of the narrower block's width; columns are the groups this links together (transitively).
  - (b) **Wide blocks:** a block whose x-range overlaps two or more blocks of the set that are not in the same column as each other is *wide*. Wide blocks cut the set into horizontal bands: each wide block is its own band; every other block goes in the band between the wide blocks above and below its top y.
  - (c) **Order:** bands top to bottom; inside a band, columns left to right (by smallest x1), each column top to bottom (by y1). Ties go to the lower `order`.
  - Each role of FR-6 (headline, kicker, subheadline, byline, dateline, body, then each excluded role) is ordered on its own with `column_order`, and the article's `blocks` list is stored in that order (roles in the FR-6 sequence).
  - **Unsure:** when two blocks of the same role overlap in x by more than `group_order_min_overlap_px` (default 30) but less than the same-column fraction, the rule can't tell their columns apart: that role keeps the order the model listed (FR-3) and the article gets flag `order_from_model`.
  - Flag `order_changed` when the final body order differs from ascending `order` (informational, for stats).
- **FR-10** - Duplicate blocks: inside one article, a block in the `headline`, `kicker`, `subheadline`, `byline`, `dateline` or `body` role is a duplicate of a bigger block of the same role group when at least `group_dup_overlap_frac` (default 0.8) of its box area lies inside the bigger block's box **and** at least `group_dup_text_frac` (default 0.8) of its characters (whitespace collapsed) are matched in the bigger block's text (`difflib.SequenceMatcher(autojunk=False)`, sum of matching-block sizes ÷ its length). The duplicate stays in `blocks` with `"duplicate_of": <block_id>`, is left out of `headline` and `text`, and the article gets flag `duplicate_block`. A block with empty text is never a duplicate. (009 example: `b032` lies 88% inside `b005` and matches 100% of its text.)

## Non-functional requirements

- **NFR-1** - On the dev sample, the run completes with 0 failed pages, and the share of text blocks left `unassigned` is ≤ 5% (`uv run newsrag group stats --pages-file data/dev-sample.txt` prints it).
- **NFR-2** - Throughput ≥ 0.2 pages/s with Qwen3-VL-32B on this GPU (dev sample). At the end of a run the stage prints `group: <n> pages in <s> s (excl. model load) = <r> pages/s`, timing from the first request after the model is ready.

## Affected areas

- `src/newsrag/stages/group.py`, `src/newsrag/grouping/{__init__,input,parse,geometry,overlay}.py`, `src/newsrag/prompts/group_v1.md` (new)
- `pipeline.yaml` (new; `group_model: qwen3-vl-32b`)
- `src/newsrag/config.py` (changed: `group_*` settings incl. `group_order_same_column_frac`, `group_order_min_overlap_px`, `group_dup_overlap_frac`, `group_dup_text_frac`), `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_grouping_parse.py`, `tests/test_grouping_geometry.py` (new, CPU), `tests/test_grouping_live.py` (new, `gpu`+`data`)

## Don't touch

- `ocr_final` artifacts; `models.yaml` profiles other than adding new ones.

## Out of scope

- Judging and retries (013), picking the best grouping model (014), transcribing tables/notices (015), cross-page continuation (016).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Grouping method | local VLM on a numbered-box page image + geometry validation | grill D-5 |
| D-2 | Default model | Qwen3-VL-32B until the bake-off (014) picks | grill A-28 |
| D-3 | Brief columns | each item is its own article (`brief_item`) | fixes F-6 "briefs merged" |
| D-4 | Captions | kept in blocks, excluded from text | grill A-8 |
| D-5 | Ads | `ad_commercial` kind, later dropped; notices → `notice_tender` | grill D-9 |
| D-6 | Photos | role `photo`, excluded from text | photos belong to stories but carry no text |
| D-7 | Model lifetime | loaded once per `stage run` in `open()` | a 32B VLM must not start per page |
| D-8 | Order of blocks inside an article (2026-11-09) | geometry column order (FR-9); the model's listed order only where columns overlap ambiguously | ticket 009 review: Surya's numbers cross columns inside stories; on the 009 page the column rule gives exactly the reader's order for both stories |
| D-9 | Overlapping duplicate boxes (2026-11-09) | dropped from text when box ≥ 80% inside and text ≥ 80% matched (FR-10) | ticket 009 review: a headline was read twice (`b005` + `b032`) |

## Open questions

None.
