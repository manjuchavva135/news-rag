# 008: Split column-spanning blocks - Evaluation

## Setup

- Prerequisites: ticket 007 done; `ocr` done for `data/dev-sample.txt`; GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Synthetic split (covers FR-1, FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_colsplit.py -v`
- **Expected:** all pass, including `test_two_columns_in_one_block_split_into_two`, `test_single_column_not_split`, `test_headline_never_split`, `test_piece_ids_and_order`, `test_edge_margins_not_gaps`, `test_empty_piece_keeps_original`, `test_no_candidates_no_gpu`, `test_column_rule_in_gap_split`.

### E-2: Run on the dev sample (covers FR-3, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag stage run ocr_split --pages-file data/dev-sample.txt`
  2. `uv run newsrag stage status | grep '^ocr_split'`
  3. `uv run newsrag ocr split-stats --pages-file data/dev-sample.txt`
- **Expected:** none of the sample pages in `stage failures ocr_split`; step 3 prints the FR-NFR-1 line with `pct` ≤ 10.0. The tester also runs `uv run pytest -q -m "gpu and data" tests/test_colsplit.py -k real_fused_block`: the worker picks one fused block on a dev-sample page (found by viewing overlays; if the sample has none, from any other 2026 page, noted in `results.md`), stores its page_id and block_id as constants `REAL_FUSED_PAGE`, `REAL_FUSED_BLOCK` in `tests/test_colsplit.py`, and the test asserts it is split into ≥ 2 pieces.

### E-3: Overlay image (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `P=$(grep -v '^#' data/dev-sample.txt | head -1); uv run newsrag ocr overlay $P --out evidence/E-3-overlay.png && file evidence/E-3-overlay.png`
- **Expected:** prints `PNG image data`; the image (opened by the tester) shows numbered boxes.

## Edge cases

### E-4: Split pieces carry provenance (covers FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run python -c "import json,glob;bs=[b for f in glob.glob('/home/admin1/raw/work/ocr_split/*/*.json') for b in json.load(open(f))['blocks'] if b.get('split_from')];print(len(bs), all(b['block_id'].startswith(b['split_from']) for b in bs))"`
- **Expected:** prints a number ≥ 0 and `True`.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-1, E-4 |
| FR-3 | E-2, E-4 |
| FR-4 | E-3 |
| NFR-1 | E-2 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (overlay PNG, logs) saved in `evidence/` and listed in `results.md`
