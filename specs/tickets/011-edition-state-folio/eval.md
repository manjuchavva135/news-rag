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
  2. `uv run python -c "import json;from newsrag.stages.registry import REGISTRY;from newsrag.stages.base import version_slug;v=version_slug(REGISTRY['folio'].version);ids=[l.strip() for l in open('data/dev-sample.txt') if l.strip() and not l.strip().endswith('_01')];d=[json.load(open(f'/home/admin1/raw/work/folio/{v}/{i}.json')) for i in ids];print(len(d), sum(1 for x in d if x['place_te']))"`
- **Expected:** two numbers (non-front dev-sample pages, those with a place); the second is
  ≥ 80% of the first. (Changed 2026-11-10, see rd.md NFR-1.)

### E-3: Edition map and report (covers FR-3, FR-5, FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag stage run folio` (all pages with `ocr_final` done)
  2. `uv run newsrag editions build && uv run python -c "import yaml;y=yaml.safe_load(open('data/editions.yaml')) or {};print({k: len(v) for k, v in y.items()})"`
  3. `uv run newsrag editions report`
- **Expected:** step 2 prints the codes per year built from non-state pages only; on the
  2024/2025 archives (all `-MAIN`) that is `{}`. Step 3 prints one line each for 2024 and 2025
  with `codes ok ambiguous unknown_edition date_mismatch state_pages`, where `state_pages`
  equals that year's `main` pages with `folio` done. (Changed 2026-11-10: was a 2026 map with
  ≥ 20 codes and ≥ 10 `ok`; the archives have no 2026 pages, and `-MAIN` folios name the
  printing centre, see rd.md FR-7.)

### E-4: Pages get editions and flags (covers FR-4, FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag editions apply`
  2. `uv run newsrag db sql "select count(*), count(*) filter (where 'low_confidence:unknown_edition' = any(flags)), count(*) filter (where cardinality(edition_names)>0), count(*) filter (where source='main' and not 'low_confidence:unknown_edition' = any(flags)) from ledger.page_editions"`
  3. `uv run newsrag db sql "select count(*) from ledger.stage_runs where stage='folio' and status='done'"`
  4. `uv run newsrag db sql "select count(*) from ledger.page_editions where source='main' and (cardinality(edition_names)>0 or cardinality(districts_current)>0 or cardinality(districts_at_print)>0)"`
- **Expected:** step 2's first number = step 3's number; first = second + third + fourth;
  step 4 prints `0`.

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

### E-7: State pages (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_editions.py -k "state_pages_do_not_feed_the_map or main_page_is_state_level or report_counts_state_pages"`
- **Expected:** 3 passed.

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
| FR-7 | E-3, E-4, E-7 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass (E-5, E-6, E-7)
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (logs, `data/editions.yaml`) saved in `evidence/` and listed in `results.md`
