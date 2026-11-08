# 017: Cross-edition story dedup (per date) - Evaluation

## Setup

- Prerequisites: ticket 016 done (and 015 done); `stitch` and `transcribe` done for the dates `2026-04-03`, `2025-06-10`, `2020-04-02` (see ticket 016's setup).
- Start: `uv sync && uv run newsrag stores up && uv run newsrag ledger init`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Logic (covers FR-1, FR-2, FR-3, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_dedup.py -v`
- **Expected:** all pass, including `test_same_page_never_merged`, `test_transitive_clusters`, `test_both_thresholds_required`, `test_story_id_reused_on_rerun`, `test_unit_leaving_cluster_gets_new_id`, `test_canonical_is_longest`, `test_ads_and_masthead_excluded`, `test_no_id_collision_after_split`, `test_same_page_union_skipped`, `test_class_overrides_kind_and_notice_text`, `test_normalisation_keeps_vowel_signs`.

### E-2: Real dates (covers FR-1, FR-4, FR-5, FR-6, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `for d in 2026-04-03 2025-06-10 2020-04-02; do time uv run newsrag stage run dedup --date $d; uv run newsrag dedup stats --date $d; done`
- **Expected:** each date `done`; for 2026-04-03 and 2020-04-02 (many district editions) stories < units; each date's logged `dedup <date>: ... in <s> s` shows s < 120.

### E-3: Merge precision (covers NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag dedup verify --n 30 --seed 5`
- **Expected:** `yes: k/30` with k ≥ 28.

## Edge cases

### E-4: Re-run keeps ids (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag db sql "select md5(string_agg(unit_id||story_id, ',' order by unit_id)) from ledger.story_ids"`
  2. `uv run newsrag stage reset dedup --date 2026-04-03 && uv run newsrag stage run dedup --date 2026-04-03`
  3. repeat step 1
- **Expected:** steps 1 and 3 print the same hash.

### E-5: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_dedup.py -k "most_members_keep_id or cpu_device or standalone_item_printing or untranscribed_notice or item_flags_unioned"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-2 |
| FR-2 | E-1 |
| FR-3 | E-1, E-4 |
| FR-4 | E-1, E-2 |
| FR-5 | E-2 |
| FR-6 | E-2 |
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
