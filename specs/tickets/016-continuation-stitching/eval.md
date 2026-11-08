# 016: Continuation stitching (per date) - Evaluation

## Setup

- Prerequisites: ticket 013 done. `group_final` must be done for every page of at least 3 full dates; use the dates `2026-04-03`, `2025-06-10` and `2020-04-02` (run `uv run newsrag stage run render ocr ocr_split ocr_final group judge group_retry judge_retry group_final --date <d>` for each first — `stage run` accepts several stage names (ticket 003); the worker may do this, the logs are evidence).
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Matching logic (covers FR-1, FR-2, FR-3, FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_stitch.py -v`
- **Expected:** all pass, including `test_front_page_marker_means_page_1`, `test_edition_overlap_required`, `test_page_ref_bonus`, `test_ambiguous_flag`, `test_unmatched_reasons`, `test_stitched_text_and_parts`, `test_greedy_once_per_edition`, `test_chain_three_parts`, `test_spread_target_page`, `test_all_fh_overlap_both_sides`, `test_unassigned_marker_attached`, `test_reverse_match`, `test_unmatched_flag_added_at_read_time`.

### E-2: Run on three dates (covers FR-2, FR-4, FR-6)

- **Type:** CLI
- **Steps:**
  1. `for d in 2026-04-03 2025-06-10 2020-04-02; do uv run newsrag stage run stitch --date $d; done`
  2. `for d in 2026-04-03 2025-06-10 2020-04-02; do uv run newsrag stitch stats --date $d; done`
- **Expected:** each date's `stitch` row is `done`; across the three dates `linked` ≥ 3 and `linked` ≥ 50% of `source_markers` (report the exact share in `results.md`).

### E-3: Link quality (covers NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag stitch verify --n 20 --seed 3` (if fewer than 20 links exist, all links are used and the bar is ≥ 90%)
- **Expected:** prints `yes: k/n` with k/n ≥ 0.9.

## Edge cases

### E-4: Date waits for all its pages (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag stage run render ocr ocr_split ocr_final group judge group_retry judge_retry group_final --date 2026-04-04 --limit 3` (only some pages of that date get `group_final`)
  2. `uv run newsrag stage run stitch --date 2026-04-04`
  3. `uv run newsrag db sql "select count(*) from ledger.date_runs where stage='stitch' and pub_date='2026-04-04'"`
- **Expected:** step 2 reports `done=0`; step 3 prints `0`.

### E-5: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_stitch.py -k "one_link_per_continuation or taken_reason or reverse_after_greedy or merged_fields_from_first_part or orphan_markers"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-1, E-2, E-4 |
| FR-3 | E-1 |
| FR-4 | E-1, E-2 |
| FR-5 | E-1 |
| FR-6 | E-2 |
| NFR-1 | E-3 |
| FR-7 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (logs, list of patterns added) saved in `evidence/` and listed in `results.md`
