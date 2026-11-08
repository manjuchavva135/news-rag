# 023: Chunking - Evaluation

## Setup

- Prerequisites: tickets 015 and 022 done; `enrich_facts` done and `aliases build` run for `2026-04-03`, `2025-06-10`, `2020-04-02`.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Split rules (covers FR-3, FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_chunking.py -v`
- **Expected:** all pass, including `test_short_story_one_chunk`, `test_paragraph_split_with_one_paragraph_overlap`, `test_long_paragraph_sentence_split`, `test_prefix_counted`, `test_table_chunk_linked_to_story`, `test_low_confidence_propagates`, `test_ads_not_chunked`, `test_overlap_shrinks_when_too_big`, `test_window_always_has_new_text`, `test_n_tokens_matches_embedded_string`, `test_table_split_repeats_header`, `test_table_in_continuation_part_found`, `test_alias_change_changes_version`, `test_decimal_not_sentence_end`.

### E-2: Real dates (covers FR-1, FR-2, FR-6, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `for d in 2026-04-03 2025-06-10 2020-04-02; do uv run newsrag stage run chunk --date $d; uv run newsrag chunk stats --date $d; done`
- **Expected:** each date `done`; token max ≤ 512 for every date.

## Edge cases

### E-3: Missing alias output (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_chunking.py -k missing_aliases`
- **Expected:** passes; `stage run chunk` exits 2 with `run 'newsrag aliases build' first`.

### E-4: Changing embed model re-chunks (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `NEWSRAG_EMBED_MODEL=qwen3-embedding-8b uv run newsrag stage status | grep '^chunk'`
- **Expected:** the `eligible=` count equals the number of dates that are `done` for `chunk` (version changed). (If the `qwen3-embedding-8b` profile does not exist yet, use any other tokenizer name the setting accepts.)

### E-5: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_chunking.py -k "stale_dates_only_changed or inlined_items_skipped or table_story_row_split or empty_story_skipped or flag_sources or unknown_embed_profile"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2, E-3, E-4 |
| FR-2 | E-1, E-2 |
| FR-3 | E-1 |
| FR-4 | E-1 |
| FR-5 | E-1 |
| FR-6 | E-2 |
| NFR-1 | E-2 |
| FR-7 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-2 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (stats logs) saved in `evidence/` and listed in `results.md`
