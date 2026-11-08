# 021: Enrichment: amounts as structured facts - Evaluation

## Setup

- Prerequisites: ticket 020 done; `enrich_places` done for `2026-04-03`, `2025-06-10`, `2020-04-02`; GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Validation and normalisation (covers FR-1, FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_enrich_facts.py -v`
- **Expected:** all pass, including `test_crore_normalised` (`₹2,500 కోట్లు` → 2.5e10 INR), `test_telugu_digits_verbatim_match`, `test_non_verbatim_dropped`, `test_period_defaults_to_event_date`, `test_value_parsed_from_text_not_model` (model says 25000 lakh for `2,500 కోట్లు` → stored 2500 crore), `test_financial_year_period`, `test_unparseable_value_dropped`, `test_excluded_number_kinds_prompt` (prompt text lists the exclusions), `test_fact_id_format`.

### E-2: Real dates (covers FR-3, FR-4)

- **Type:** CLI
- **Steps:**
  1. `for d in 2026-04-03 2025-06-10 2020-04-02; do uv run newsrag stage run enrich_facts --date $d; done`
  2. `uv run newsrag enrich stats --facts --date 2026-04-03 --date 2025-06-10 --date 2020-04-02`
- **Expected:** all three dates `done`; `facts_per_story` > 0.5; `unit_` lines and both `dropped_` lines printed.

### E-3: Fact quality (covers NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag enrich verify-facts --n 50 --seed 13`
- **Expected:** `correct: k/50` with k ≥ 45.

## Edge cases

### E-4: Facts in tables (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_enrich_facts.py -k table_markdown`
- **Expected:** passes; with a fake model that returns a value span from a table row, a story whose only content is a Markdown table yields a fact (the verbatim check works on pipe-separated text and the table markdown is what was sent).

### E-5: Second-review clarifications (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_enrich_facts.py -k "scale_from_table_header or inr_markers or period_months_and_relative or state_field or judge_input"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-4 |
| FR-2 | E-1 |
| FR-3 | E-2 |
| FR-4 | E-2 |
| NFR-1 | E-3 |
| FR-5 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (logs) saved in `evidence/` and listed in `results.md`
