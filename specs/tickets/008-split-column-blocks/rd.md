# 008: Split column-spanning blocks

**Status:** draft
**Mode:** solo
**Runs:** waits for 007: edits the OCR block output

## Summary

The old system's worst OCR-side defect was one block fused across two newspaper columns (two stories'
lines interleaved). This stage finds text blocks that span a column gap, splits them at the gap and
re-OCRs each piece, so the grouping step only ever sees single-column blocks (grill A-39). It also adds
the numbered-box overlay image that later checks and the grouping prompt use.

## Functional requirements

- **FR-1** - PageStage `ocr_split` (`depends_on=("ocr",)`, `version="colsplit-v1"`, `concurrency = 1`) reads the `ocr` artifact. A block is a split candidate when its label is in `text_labels` (ticket 007 FR-6; `title_labels` are never split), its width > `colsplit_width_factor` (default 1.6) × the median width of the page's `text_labels` blocks (pages with < 3 such blocks have no candidates), and its crop has an interior vertical gap. Gap test: binarise the crop with Otsu's threshold (dark = ink); a pixel column is *blank* when its dark-pixel ratio over the middle 90% of the block height (5% trimmed top and bottom) is ≤ 0.01; a gap is a run of ≥ `colsplit_min_gap_px` (default 15) blank columns — where a run of ≤ 4 columns whose dark ratio is ≥ 0.8 over that band (a printed column rule) counts as blank — that does not touch the crop's left or right edge, with ≥ `colsplit_min_piece_px` (default 120) of non-blank columns on each side.
- **FR-2** - A candidate is split at the middle of every such gap into left-to-right pieces; each piece's bbox spans from the previous cut (or the block's left edge) to the next cut (or its right edge), full block height. All pieces of the page are re-OCR'd in one `op: blocks` request to the ticket-007 worker client (each `type: "text"`). `open()` does nothing; `handle` takes `gpu_lock("indic-ocr")` and starts the worker the first time a page has a candidate; `close()` releases them only if they were taken. Pages without candidates are copied without touching the GPU. Piece texts are NFC-normalised. If any piece comes back empty or the request errors, the original block is kept unsplit and the page gets detail `split_errors += 1`. Pieces get ids `<block_id>a`, `<block_id>b`, … , keep the original `label`, carry `split_from: <block_id>`, and take the original's reading-order position (pieces ordered left to right).
- **FR-3** - The artifact has the same schema as the `ocr` artifact: top-level `page_id`, `image`, `model` copied, `markdown` rebuilt from the new block texts in order, and blocks with `block_id, order, label, type, bbox, conf, text`, `order` renumbered 1..n; a piece's `conf` is the original block's `conf`. Detail = `{n_blocks, n_text_blocks, n_split_blocks, n_pieces, split_errors}`.
- **FR-4** - Function `newsrag.ocr.overlay.draw_blocks(image_path, blocks, max_long_side=None) -> PIL.Image` draws every block box (3 px outline) and its order number in a filled label at the box's top-left with a font height of 32 px at 300 DPI (scaled with the image). CLI `uv run newsrag ocr overlay <page_id> [--stage ocr|ocr_split|ocr_final] [--out PATH]` (default stage: the newest of `ocr_final`, `ocr_split`, `ocr` that is done for the page; with none, it prints `no <stage> artifact for <page_id>` and exits 1) writes it as PNG (default `<reports_dir>/overlays/<page_id>.png`). Ticket 012 calls the function.

## Non-functional requirements

- **NFR-1** - On the dev sample, the split rate is between 0% and 10%: `uv run newsrag ocr split-stats --pages-file data/dev-sample.txt` prints exactly `split_blocks=<a> text_blocks=<b> pct=<a/b×100, 1 decimal> pieces=<c>` where `b` = `text_labels` blocks in the `ocr` artifacts.

## Affected areas

- `src/newsrag/stages/ocr_split.py`, `src/newsrag/stages/registry.py` (new / changed)
- `src/newsrag/ocr/overlay.py`, `src/newsrag/ocr/colsplit.py` (new)
- `src/newsrag/cli.py` - `ocr overlay`, `ocr split-stats` (changed)
- `tests/test_colsplit.py` (new; synthetic images, no GPU)
- `src/newsrag/ocr/client.py` - uses the `op: blocks` request from ticket 007 (no change expected)
- `src/newsrag/config.py` - `colsplit_width_factor`, `colsplit_min_gap_px`, `colsplit_min_piece_px` (changed)

## Don't touch

- The `ocr` stage artifacts (read only) and `src/newsrag/stages/ocr.py` behaviour.

## Out of scope

- Merging blocks, grouping into articles (012).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Fused blocks | split at the column gap and re-OCR before grouping | grill A-39 |
| D-2 | Headlines | never split | a banner headline legitimately spans columns |

## Open questions

None.
