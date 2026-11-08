# 015: Tables, infographics and notices transcription - Evaluation

## Setup

- Prerequisites: ticket 013 done; `group_final` done for `data/dev-sample.txt`; GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Target selection and output shape (covers FR-1, FR-2, FR-3, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_transcribe.py -v`
- **Expected:** all pass, including `test_targets_tables_infographics_notices`, `test_photo_no_text_dropped`, `test_commercial_not_kept`, `test_table_in_article_keeps_article_id`, `test_standalone_table_has_null_article`, `test_block_transcribed_once`, `test_class_overrides_kind`, `test_bad_output_dropped_and_flagged`, `test_item_ids_stable_when_photo_dropped`, `test_spotcheck_line_rules`.

### E-2: Dev sample run (covers FR-1..FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag stage run transcribe --pages-file data/dev-sample.txt`
  2. `uv run newsrag stage status | grep '^transcribe'`
  3. `uv run python -c "import json,glob,collections;c=collections.Counter(i['type'] for f in glob.glob('/home/admin1/raw/work/transcribe/*/*.json') for i in json.load(open(f))['items']);print(dict(c))"`
- **Expected:** no sample page in `stage failures transcribe`; step 3 prints a dict with at least one of `table`/`infographic` and the keys only from `table, infographic, notice_tender, commercial`.

### E-3: Spot-check bar (covers FR-6, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag transcribe spotcheck --n 50`
- **Expected:** prints `invented_pct=<x>` with x ≤ 2.0, and `/home/admin1/raw/reports/transcription-spotcheck.json` exists.

## Edge cases

### E-4: Telugu stays Telugu (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run python -c "import json,glob,re;t=' '.join(i['markdown'] for f in glob.glob('/home/admin1/raw/work/transcribe/*/*.json') for i in json.load(open(f))['items'] if i['type']!='commercial');print(len(re.findall('[ఀ-౿]',t))>0)"`
- **Expected:** `True`.

### E-5: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_transcribe.py -k "page_flags_and_item_flags or label_or_role or masthead_table_standalone or table_only_inlined or lazy_model or spotcheck_zero_items"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-2 |
| FR-2 | E-1, E-2 |
| FR-3 | E-1, E-2 |
| FR-4 | E-1, E-2 |
| FR-5 | E-4 |
| FR-6 | E-3 |
| NFR-1 | E-3 |
| FR-7 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (spot-check JSON, logs) saved in `evidence/` and listed in `results.md`
