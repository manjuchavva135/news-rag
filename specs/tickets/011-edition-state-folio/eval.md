# 011: Edition and state from the folio - Evaluation

## Setup

- Prerequisites: tickets 010 and 018 done; `ocr_final` done for `data/dev-sample.txt`. For E-3 `ocr_final` must be done for all pages of at least the year 2026 (run `uv run newsrag stage run render ocr ocr_split ocr_final` for 2026 pages first if needed — the worker may run it, it is required evidence).
- Start: `uv sync && uv run newsrag stores up && uv run newsrag ledger init`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Folio parsing on fixtures (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_folio_parse.py -v`
- **Expected:** all pass; fixtures include the real old-system folio string `బుధవారం । 14 । జనవరి । 2026 విజయవాడ । www.sakshi.com` → place `విజయవాడ`, state `andhra_pradesh`, date `2026-01-14`.

### E-2: Folio stage on the dev sample (covers FR-1, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag stage run folio --pages-file data/dev-sample.txt`
  2. `uv run python -c "import json,glob;d=[json.load(open(f)) for f in glob.glob('/home/admin1/raw/work/folio/*/*.json')];print(len(d), sum(1 for x in d if x['place_te']))"`
- **Expected:** `20` and a number ≥ 15.

### E-3: Edition map for 2026 (covers FR-3, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag stage run folio` (all pages with `ocr_final` done)
  2. `uv run newsrag editions build && uv run python -c "import yaml;y=yaml.safe_load(open('data/editions.yaml'));e=y[2026];print(len(e), sum(v['status']=='ok' for v in e.values()))"` (years are int keys)
  3. `uv run newsrag editions report`
- **Expected:** step 2 prints a code count ≥ 20 and an `ok` count ≥ 10; step 3 prints a line for 2026 with the five numbers from FR-5.

### E-4: Pages get editions and flags (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag editions apply`
  2. `uv run newsrag db sql "select count(*), count(*) filter (where 'low_confidence:unknown_edition' = any(flags)), count(*) filter (where cardinality(edition_names)>0) from ledger.page_editions"`
  3. `uv run newsrag db sql "select count(*) from ledger.stage_runs where stage='folio' and status='done'"`
- **Expected:** step 2's first number = step 3's number; first = second + third.

## Edge cases

### E-5: Multi-letter codes and ambiguity (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_editions.py -v`
- **Expected:** all pass, including `test_multi_letter_gets_names_from_letters`, `test_all_and_fh_not_split_into_letters`, `test_partial_letters_flag`, `test_low_agreement_is_ambiguous`, `test_fewer_than_three_pages_is_ambiguous`, `test_date_mismatch_flag`, `test_hand_added_keys_kept`, `test_ambiguous_place_prefers_town`, `test_state_only_folio`.

### E-6: Second-review clarifications (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_editions.py -k "letters_before_code_map or state_only_page or copies_entry_districts or report_lists_ambiguous"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2 |
| FR-2 | E-1 |
| FR-3 | E-3, E-5 |
| FR-4 | E-4, E-5 |
| FR-5 | E-3 |
| NFR-1 | E-2 |
| FR-6 | E-6 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (logs, `data/editions.yaml`) saved in `evidence/` and listed in `results.md`
