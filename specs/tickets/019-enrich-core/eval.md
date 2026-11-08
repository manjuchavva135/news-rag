# 019: Enrichment: category, people/orgs, event date - Evaluation

## Setup

- Prerequisites: ticket 017 done; `dedup` done for `2026-04-03`, `2025-06-10`, `2020-04-02`; GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Validation rules (covers FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_enrich_core.py -v`
- **Expected:** all pass, including `test_non_verbatim_name_dropped`, `test_future_event_date_rejected`, `test_default_event_date_is_print_date`, `test_invalid_json_falls_back_to_other`, `test_category_must_be_in_list`, `test_future_date_within_366_kept`, `test_event_date_never_null`, `test_names_deduplicated`, `test_transport_failure_flags_story`, `test_date_fails_over_20_percent`, `test_load_merges_partial`.

### E-2: Run on three dates (covers FR-1, FR-4, FR-5, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `for d in 2026-04-03 2025-06-10 2020-04-02; do uv run newsrag stage run enrich_core --date $d; uv run newsrag enrich stats --date $d; done`
- **Expected:** each date `done`; category lines only for the 7 values; `enrich_failed` ≤ 1% of stories; `stories_per_s` ≥ 5.

### E-3: Quality (covers NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag enrich verify-core --n 50 --seed 11`
- **Expected:** prints `category_ok=<a>/50 names_ok=<b>/50` with a ≥ 45 and b ≥ 45.

## Edge cases

### E-4: Relative date (covers FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m gpu tests/test_enrich_core.py -k yesterday_live` (one real call: a story printed 2026-04-03 saying "నిన్న విలేకరుల సమావేశంలో")
- **Expected:** passes with `event_date == 2026-04-02`.

### E-5: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_enrich_core.py -k "pool_inside_handle or load_flags_union or fail_rule_counts_all or stats_multi_date"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2 |
| FR-2 | E-1, E-4 |
| FR-3 | E-1, E-4 |
| FR-4 | E-2 |
| FR-5 | E-2 |
| NFR-1 | E-3 |
| NFR-2 | E-2 |
| FR-7 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (logs) saved in `evidence/` and listed in `results.md`
