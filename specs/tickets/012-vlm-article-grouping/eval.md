# 012: VLM article grouping - Evaluation

## Setup

- Prerequisites: tickets 006 and 010 done; `ocr_final` done for `data/dev-sample.txt`; GPU free; `HF_TOKEN` set.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Parse and geometry rules (covers FR-3, FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_grouping_parse.py tests/test_grouping_geometry.py -v`
- **Expected:** all pass, including `test_invalid_json_retried_twice_then_fails`, `test_omitted_blocks_go_unassigned`, `test_duplicate_block_flag`, `test_disconnected_flag`, `test_overlaps_other_flag`, `test_no_headline_flag`, `test_headline_below_body_flag`, `test_unknown_block_number_is_schema_error`, `test_empty_article_dropped`, `test_zero_block_page_no_model_call`, `test_text_order_and_exclusions`, `test_article_numbering_by_lowest_order`, `test_block_list_format`.

### E-2: Group the dev sample (covers FR-1, FR-2, FR-6, NFR-1, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag stage run group --pages-file data/dev-sample.txt`
  2. `uv run newsrag stage status | grep '^group '`
  3. `uv run newsrag group stats --pages-file data/dev-sample.txt`
  4. `uv run python -c "import json,glob;d=json.load(open(sorted(glob.glob('/home/admin1/raw/work/group/*/*.json'))[0]));a=d['articles'][0];print(sorted(d), sorted(a))"`
- **Expected:** none of the sample pages in `stage failures group`; unassigned share ≤ 5%; step 4 prints `['articles', 'model', 'page_flags', 'page_id', 'prompt_version', 'unassigned']` and `['article_id', 'bbox_union', 'blocks', 'flags', 'headline', 'kind', 'text']`; the `group: ... pages/s` line from step 1 shows ≥ 0.2.

### E-3: Overlays for visual inspection (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `for p in $(grep -v '^#' data/dev-sample.txt | head -3); do uv run newsrag group overlay $p --out evidence/E-3-$p.png; done; ls evidence/E-3-*.png | wc -l`
- **Expected:** `3`; the tester opens one and sees coloured article regions with numbers and kinds.

## Edge cases

### E-4: Every block accounted for (covers FR-5, FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run python - <<'PY'
import json,glob
for f in glob.glob('/home/admin1/raw/work/group/*/*.json'):
    d=json.load(open(f)); pid=d['page_id']
    o=json.load(open(glob.glob(f'/home/admin1/raw/work/ocr_final/*/{pid}.json')[0]))
    ids={b['block_id'] for b in o['blocks']}
    used=[b['block_id'] for a in d['articles'] for b in a['blocks']]+d['unassigned']
    assert set(used)==ids and len(used)==len(set(used)), pid
print('ok')
PY`
- **Expected:** prints `ok`.

### E-5: Second-review clarifications (covers FR-8)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_grouping_parse.py tests/test_grouping_geometry.py -k "split_on_length or transport_retry or stats_line or draw_articles_kinds or ocr_page_flags_copied"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2 |
| FR-2 | E-2 |
| FR-3 | E-1 |
| FR-4 | E-1 |
| FR-5 | E-1, E-4 |
| FR-6 | E-2, E-4 |
| FR-7 | E-3 |
| NFR-1 | E-2 |
| NFR-2 | E-2 |
| FR-8 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (overlay PNGs, logs) saved in `evidence/` and listed in `results.md`
