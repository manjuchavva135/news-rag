# 010: Sarvam fallback for named page types - Evaluation

## Setup

- Prerequisites: ticket 008 done; `ocr_split` done for `data/dev-sample.txt`.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Pass-through with default rules (covers FR-1, FR-2)

- **Type:** CLI
- **Steps:**
  1. `grep -E "^rules: *\[\] *$" ocr_fallback.yaml`
  2. `env -u SARVAM_API_KEY uv run newsrag stage run ocr_final --pages-file data/dev-sample.txt`
  3. `uv run newsrag stage status | grep '^ocr_final'`
  4. `uv run python -c "import json,glob;bs=[b for f in glob.glob('/home/admin1/raw/work/ocr_final/*/*.json') for b in json.load(open(f))['blocks']];print(len(bs), {b['ocr_source'] for b in bs})"`
- **Expected:** step 1 prints the line; `done=20 failed=0`; step 4 prints a count > 0 and `{'indic'}`.

### E-2: Fallback path with a fake Sarvam (covers FR-3, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_ocr_final.py -v`
- **Expected:** all pass, including `test_matching_page_replaces_block_text_keeps_ids` (text joined with `\n`), `test_image_tag_blocks_discarded`, `test_empty_sarvam_keeps_indic_text`, `test_ten_crops_per_job`, `test_retry_on_429_four_attempts`, `test_max_four_concurrent_jobs`, `test_failed_job_keeps_indic_and_flags`, `test_cache_prevents_second_call`, `test_tiny_blocks_not_sent`, `test_spread_matches_page_nums`.

### E-3: Preview counts (covers FR-5)

(Changed with the user while building: the check was written for the old 2020/2025/2026 corpus;
the ledger now holds 2024 and 2025 pages only, so 2025 stands in for 2020 and 2024 for the years
that must not appear.)

- **Type:** CLI
- **Steps:**
  1. `printf 'rules:\n  - years: [2025]\n    page_nums: [1]\n' > /tmp/fb.yaml && NEWSRAG_OCR_FALLBACK_FILE=/tmp/fb.yaml uv run newsrag ocr fallback preview`
- **Expected:** prints a line matching `^2025\tmain\t[1-9][0-9]*$` and no line starting `2024`; `uv run pytest -q tests/test_ocr_final.py -k preview_no_network` passes (the Sarvam client is patched to raise if called).

## Edge cases

### E-4: Missing key with a matching rule (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_ocr_final.py -k missing_key`
- **Expected:** passes; the page is `failed` with the exact error text from FR-4.

### E-5: Changing rules changes the version (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_ocr_final.py -k "version_changes_with_rules or unmatched_pages_keep_version"`
- **Expected:** 2 passed (adding a rule changes `version_for` only for pages it matches).

### E-6: Second-review clarifications (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_ocr_final.py -k "page_flags_present or empty_list_means_any or rule_normalisation"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-1, E-5 |
| FR-3 | E-2 |
| FR-4 | E-4 |
| FR-5 | E-3 |
| NFR-1 | E-2 |
| FR-6 | E-6 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (command logs) saved in `evidence/` and listed in `results.md`
