# 012: VLM article grouping

**Status:** draft
**Mode:** solo
**Runs:** waits for 010: needs final OCR blocks; waits for 006: model runner

## Summary

The core fix for the old system's poor results. The old code grouped blocks into articles purely in
reading order, so kickers and pull-quotes opened fake articles, "in brief" columns merged stories, and
mastheads and ads leaked in (grill F-6). Here a local vision-language model looks at the page with every
block numbered and returns the articles: which blocks belong together and what role each block plays.
Geometry rules then check the answer and flag anything doubtful (grill D-5).

## Functional requirements

- **FR-1** - PageStage `group` (`depends_on=("ocr_final",)`, `version="<group_model>-group-p<prompt_version>"`; `group_model` setting, default `qwen3-vl-32b`, read from `pipeline.yaml` if present; prompt in `src/newsrag/prompts/group_v1.md`, `prompt_version = 1`). `models_needed = (group_model,)`; `concurrency = group_concurrency` (default 16 threads). `open()` enters `newsrag.models.use(group_model)` once per `stage run` and shares the client with all threads; `close()` exits it. A page whose `ocr_final` has 0 blocks makes no model call: its artifact has empty `articles` and page flag `low_confidence:no_blocks`. Requests use `temperature: 0`, `max_tokens: 4096`.
- **FR-2** - Model input per page: (a) the page image with every `ocr_final` block's box drawn and its number written on it (reuse ticket 008's overlay), resized so the long side is `group_image_max_px` (default 3584); (b) a text list with one line per block: `#<order> [<label>] (x1,y1,x2,y2) <first 80 chars of text>` with coordinates in 300-DPI page pixels (the prompt states the page size so the model can relate them to the image); when a page has more than 150 blocks the text is cut to 40 chars.
- **FR-3** - The prompt asks for strict JSON: `{"articles":[{"kind": "news"|"editorial"|"brief_item"|"notice_tender"|"table_only"|"ad_commercial"|"masthead"|"other", "blocks":[{"n": <order>, "role": "headline"|"kicker"|"subheadline"|"byline"|"dateline"|"body"|"caption"|"photo"|"table"|"infographic"|"pull_quote"|"continuation_marker"}]}], "unassigned":[<order>...]}`. It explains: each story in a briefs column is its own `brief_item`; mastheads, folios, page headers and section banners are `masthead`; product ads, classifieds and matrimonials are `ad_commercial`; government notices and tenders are `notice_tender`; a pull-quote or kicker belongs to the story it sits in and never starts an article.
- **FR-4** - Output is parsed with a JSON schema check. Schema errors include an unknown kind or role and a block number that doesn't exist on the page. On invalid JSON or schema errors the request is retried up to 2 times with the error appended; then the page fails. An article with an empty `blocks` list is dropped silently.
- **FR-5** - Geometry checks (in `src/newsrag/grouping/geometry.py`), results stored as flags:
  - every block number appears in exactly one article or in `unassigned`; numbers the model omitted are added to `unassigned` with page flag `blocks_omitted`; a number used twice → page flag `block_duplicated` (first use kept);
  - all distances are in 300-DPI page pixels; the distance between two blocks is the edge-to-edge gap between their rectangles (0 if they touch or overlap);
  - per article: blocks form one connected region (each block within `group_max_gap_px`, default 60, of another block of the same article) else flag `disconnected`;
  - the article's union box overlaps another article's blocks by > 20% of that block's area → flag `overlaps_other`;
  - a `news`/`editorial` article with no `headline` block → flag `no_headline`; a headline whose top y is more than 50 px below the smallest top y of its body blocks (`headline.y1 > min(body.y1) + 50`) → flag `headline_below_body`.
- **FR-6** - Artifact: `{page_id, model, prompt_version, articles:[{article_id: "<page_id>#aNN", kind, blocks:[{block_id, role}], headline, text, bbox_union, flags:[]}], unassigned:[block_id...], page_flags:[]}`. `headline` = headline blocks' text joined by a space (`""` when there is none); `text` = kicker, subheadline, byline, dateline, then body blocks in reading order joined by blank lines (caption, photo, table, infographic, pull-quote and continuation-marker blocks are excluded from `text` but kept in `blocks`). Article numbering `aNN` follows the lowest block `order` of each article.
- **FR-7** - `uv run newsrag group overlay <page_id> [--stage group|group_final] [--out PATH]` writes a PNG with each article's blocks filled in one translucent colour, its number and kind written at its top-left, and flagged articles outlined in red.
- **FR-8** - Clarifications (second review):
  - `pipeline.yaml` already exists (tickets 001/007): this ticket adds `group_model` with `set_pipeline_key`, keeping other keys.
  - Transport/size failures: per-request timeout 300 s; timeouts, connection errors and 5xx are retried 2 times with backoff; HTTP 400 for context length, or `finish_reason == "length"`, is handled by splitting: the page is grouped in two halves (blocks left/right of the page's vertical centre line, each with the full image) and the two results are merged, articles renumbered; if a half still fails, the page fails. `max_tokens` is 8192.
  - The version includes the config hash of ticket 003 (covers `group_image_max_px`, `group_max_gap_px`, the 150-block/40-char cut and the prompt).
  - `group stats` prints exactly `pages=<n> failed=<n> text_blocks=<b> unassigned=<u> pct=<x.x>` where text blocks = blocks in `text_labels` + `title_labels`.
  - Function `newsrag.grouping.overlay.draw_articles(image_path, artifact, max_long_side=None, mark_flags=True, kinds=None) -> PIL.Image` backs the CLI; ticket 013 calls it with `mark_flags=False` and only the judged kinds. `--stage group_final` before ticket 013 prints `no group_final artifact for <page_id>` and exits 1.
  - `ocr_final.page_flags` are copied into the artifact's `page_flags`.

## Non-functional requirements

- **NFR-1** - On the dev sample, the run completes with 0 failed pages, and the share of text blocks left `unassigned` is ≤ 5% (`uv run newsrag group stats --pages-file data/dev-sample.txt` prints it).
- **NFR-2** - Throughput ≥ 0.2 pages/s with Qwen3-VL-32B on this GPU (dev sample). At the end of a run the stage prints `group: <n> pages in <s> s (excl. model load) = <r> pages/s`, timing from the first request after the model is ready.

## Affected areas

- `src/newsrag/stages/group.py`, `src/newsrag/grouping/{__init__,input,parse,geometry,overlay}.py`, `src/newsrag/prompts/group_v1.md` (new)
- `pipeline.yaml` (new; `group_model: qwen3-vl-32b`)
- `src/newsrag/config.py`, `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
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

## Open questions

None.
