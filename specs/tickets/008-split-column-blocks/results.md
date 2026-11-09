# 008: Split column-spanning blocks - Results

Built on branch `008-split-column-blocks` (from main at `515b9a7`, after 001-007). GPU runs on
the RTX PRO 6000 Blackwell, with indic-ocr's text model on vLLM as pinned by ticket 007.

**One departure from the ticket: `colsplit_min_gap_px` defaults to 40, not 15.** With 15, the
dev sample split 10 of 435 text blocks, and 7 of the 10 were wrong. They were one-to-three-line
display/ad blocks in large type: their word spaces (and the white under centred short lines)
are 15-32 px of blank columns. All three real fused blocks had gaps of 54-63 px
(scratch measurement over every candidate's gaps: width plus ink coverage on each side). At
40 px, 4 blocks split: the 3 real ones, plus the "BLANK SPACE" ad-slot filler (one line of
~100 px type, cut between its two words), which does no harm. The value is a setting
(`NEWSRAG_COLSPLIT_MIN_GAP_PX`) and part of the stage's version hash; the reason is in the
comment in `config.py`. `test_display_type_word_spaces_not_split` covers this case.

## What was built

- `src/newsrag/ocr/colsplit.py`: candidates (FR-1: `text_labels` only, width > 1.6 × median,
  ≥ 3 text blocks), the gap test (Otsu, middle-90 % band, ≤ 0.01 blank, ≤ 4 px rules ≥ 0.8 count
  as blank, no edge-touching runs, ≥ `colsplit_min_piece_px` inked columns on each side), cuts at
  gap middles, piece ids `<id>a, b, …`, and the merge of the pieces back into reading order.
- `src/newsrag/stages/ocr_split.py`: `OcrSplitStage` (`depends_on=("ocr",)`, concurrency 1,
  version `colsplit-v1-<hash of the colsplit settings + text_labels>`). Reads the page's done
  `ocr` artifact from the ledger. `open()` does nothing. The first page with a candidate takes
  `gpu_lock("indic-ocr")` and starts the text model, and `close()` releases both only if they
  were taken. All of a page's pieces go in one `TextOcr.read` request (`type: "text"`). An empty
  piece, or a request that raises, keeps the original block and adds 1 to `split_errors` per block.
- The artifact has the `ocr` schema (`page_id, image, model, blocks, markdown`). Pieces add
  `split_from`. Detail is `{n_blocks, n_text_blocks, n_split_blocks, n_pieces, split_errors}`,
  where `n_text_blocks` counts the `text_labels` blocks of the input `ocr` artifact (the
  split-rate denominator).
- `src/newsrag/ocr/overlay.py`: `draw_blocks(image_path, blocks, max_long_side=None)`: 3 px
  boxes coloured by type, the order number in a filled label at the top-left, font 32 px at
  300 DPI, scaled with the image.
- CLI (in `src/newsrag/ocr/cli.py`, the module's own sub-app): `ocr overlay <page_id>
  [--stage] [--out]` and `ocr split-stats --pages-file`.
- Settings `colsplit_width_factor`, `colsplit_min_gap_px`, `colsplit_min_piece_px` under
  `# --- colsplit ---` in `config.py`.

Departure in wiring: the ticket says the pieces go "to the ticket-007 worker client
(`op: blocks`)". Since 007 moved indic-ocr's text model onto vLLM, that request is
`TextOcr.read` (what `OcrClient.blocks` calls). The stage starts only the text model and never
Surya. No change to `client.py` or `stages/ocr.py`.

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| `ruff check .` / `ruff format --check .` | PASS | `evidence/e1-unit.log` |
| unit tests (`not gpu and not data and not stores`) | PASS, 179 passed | `evidence/e1-unit.log` |
| E-1 `tests/test_colsplit.py -v` | PASS, 19 passed, including all 8 named tests | `evidence/e1-unit.log` |
| E-2 dev sample run | PASS: `done=20 failed=0`, `stage failures ocr_split` empty | `evidence/e2-dev-sample.log`, `evidence/e2-status-stats.log` |
| E-2 split-stats | PASS: `split_blocks=4 text_blocks=435 pct=0.9 pieces=8` (≤ 10.0) | `evidence/e2-status-stats.log` |
| E-2 real fused block (`gpu and data`, `-k real_fused_block`) | PASS, 1 passed | `evidence/e2-real-fused-block.log` |
| E-3 overlay | PASS: `PNG image data, 4052 x 6804`; numbered boxes visible | `evidence/e3-e4.log`, `evidence/E-3-overlay.png` (the same overlay redrawn with `max_long_side=2400` to keep the repo small: the full-size PNG is 13 MB) |
| E-4 provenance | PASS: `8 True` | `evidence/e3-e4.log` |

The `stores` tests were not rerun: this ticket adds no SQL and no store code.

## The real fused block

`REAL_FUSED_PAGE = "2025-10-15_B_main_10"`, `REAL_FUSED_BLOCK = "b017"` (a dev-sample page,
found by viewing crops of every planned split and the overlay). Two columns of body text that
Surya boxed as one block. The `ocr` text interleaves both columns:

    17  Text  2697,2396,3962,2537  మూర్తులు కానున్నారు. వీరి పేర్లను హైకోర్టు కొలీజి నుంచి ఇంటె…

After `ocr_split`, there is one piece per column, each reading on correctly:

    17  Text  2697,2396,3339,2537  మూర్తులు కానున్నారు. వీరి పేర్లను హైకోర్టు కొలీజి యం ఇటీవల స…
    18  Text  3339,2396,3962,2537  నుంచి ఇంటెలిజెన్స్ నివేదిక వెళ్లగానే సుప్రీంకోర్టు కొలీ జియం…

`evidence/E-3-overlay-split-2025-10-15_B_main_10.png` (a full-resolution crop of the page's
`ocr_split` overlay) shows the two pieces as boxes 17 and 18.
The other real splits are `2024-12-02_BG_main_01` b029 (two bulleted columns either side of a
photo) and `2025-11-03_BG_main_01` b041 (a boxed side-headline fused with the text beside it,
across a printed column rule).
