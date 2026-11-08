# 020: Enrichment: places and districts - Evaluation

## Setup

- Prerequisites: tickets 018 and 019 done; `enrich_core` done for `2026-04-03`, `2025-06-10`, `2020-04-02`; GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Resolution logic (covers FR-1, FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_enrich_places.py -v`
- **Expected:** all pass, including `test_2020_story_gets_old_and_new_district` (a 2020 story naming తిరుపతి as a city gets `districts_at_print=["chittoor"]`, `districts_current=["tirupati"]`), `test_ambiguous_flag`, `test_unresolved_kept`, `test_case_suffix_stripped` (`తిరుపతిలో` → Tirupati), `test_state_type_maps_state`, `test_village_not_resolved_no_flag`, `test_matches_collapse_when_districts_agree`, `test_name_as_written_tried_first` (అరకు stays అరకు), `test_zwnj_removed_after_strip` (హైదరాబాద్‌లో).

### E-2: Real dates (covers FR-4, FR-5, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `for d in 2026-04-03 2025-06-10 2020-04-02; do uv run newsrag stage run enrich_places --date $d; done`
  2. `uv run newsrag enrich stats --places --date 2026-04-03 --date 2025-06-10 --date 2020-04-02`
- **Expected:** all three dates `done`; the combined resolved share over the three type lines (weighted by count, printed as `combined_resolved=<pct>`) ≥ 70.

## Edge cases

### E-3: Non-verbatim places dropped (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_enrich_places.py -k non_verbatim`
- **Expected:** passes.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-3 |
| FR-2 | E-1 |
| FR-3 | E-1 |
| FR-4 | E-2 |
| FR-5 | E-2 |
| NFR-1 | E-2 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-2 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (logs) saved in `evidence/` and listed in `results.md`
