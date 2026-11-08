# 022: Name alias table - Evaluation

## Setup

- Prerequisites: ticket 021 done; `enrich_core` done for `2026-04-03`, `2025-06-10`, `2020-04-02` (more dates make the check stronger); GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Rules (covers FR-2, FR-4, FR-5, FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_aliases.py -v`
- **Expected:** all pass, including `test_transliteration_match_te_en`, `test_low_confidence_goes_to_review`, `test_rule_d_never_auto_applied`, `test_transitive_merge`, `test_ids_stable_across_runs`, `test_overrides_win`, `test_split_is_cannot_link`, `test_person_and_org_never_merge`, `test_ids_persist_when_canonical_changes`, `test_invalid_llm_reply_to_review`.

### E-2: Build on real data (covers FR-1, FR-3, FR-5, FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag aliases build && uv run newsrag aliases stats`
  2. `ls /home/admin1/raw/work/aliases/*/entities.json && head -3 /home/admin1/raw/reports/alias-review.csv`
- **Expected:** stats show entities < names and auto-merged pairs ≥ 1; the entities file exists; the CSV has the header from FR-4.

### E-3: Precision (covers NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag aliases verify --n 40 --seed 17` (if fewer than 40 auto-merged pairs exist, all are used and the bar is ≥ 95%)
- **Expected:** `same: k/n` with k/n ≥ 0.95.

## Edge cases

### E-4: Re-run gives the same ids (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `md5sum /home/admin1/raw/work/aliases/*/entities.json; uv run newsrag aliases build; md5sum /home/admin1/raw/work/aliases/*/entities.json`
- **Expected:** both hashes equal.

### E-5: Second-review clarifications (covers FR-8)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_aliases.py -k "folding_te_en or entity_ids_created_at or id_split_two_groups or loops_until_no_pending or key_helper"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2 |
| FR-2 | E-1 |
| FR-3 | E-2 |
| FR-4 | E-1 |
| FR-5 | E-1, E-2, E-4 |
| FR-6 | E-1 |
| FR-7 | E-2 |
| NFR-1 | E-3 |
| FR-8 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (stats, review CSV head) saved in `evidence/` and listed in `results.md`
