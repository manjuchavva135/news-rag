# 024: Embed and index into stores - Evaluation

## Setup

- Prerequisites: ticket 023 done; `chunk` done for `2026-04-03`, `2025-06-10`, `2020-04-02`; GPU free.
- Start: `uv sync && uv run newsrag stores up && uv run newsrag db init`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0
- `uv run pytest -q -m stores tests/test_index.py` exits 0

## Acceptance checks

### E-1: Index three dates and validate (covers FR-1, FR-2, FR-5, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `for d in 2026-04-03 2025-06-10 2020-04-02; do uv run newsrag stage run index --date $d; done`
  2. `uv run newsrag index validate; echo "exit=$?"`
  3. `uv run newsrag index collections`
- **Expected:** three `ok <date> <n>` lines with n > 0 and `exit=0`; step 3 lists generation `bge-m3__v1` marked active with equal counts in all three stores; every logged `index <date>: ... chunks/s` line shows ≥ 200.

### E-2: Warehouse tables filled (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag db sql "select (select count(*) from warehouse.stories),(select count(*) from warehouse.story_printings),(select count(*) from warehouse.entities),(select count(*) from warehouse.story_places),(select count(*) from warehouse.facts)"`
- **Expected:** five numbers, all > 0, and printings ≥ stories.

### E-3: Telugu BM25 and filters (covers NFR-2, FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m "stores and data" tests/test_index.py -k "telugu_bm25 or district_filter"`
- **Expected:** passes; a Telugu word from a real chunk finds it in the top 10, and a Qdrant query filtered on `districts_current` returns only chunks with that district.

## Edge cases

### E-4: Re-index is idempotent and removes stale rows (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_index.py -k "reindex_idempotent or stale_removed"`
- **Expected:** passes.

### E-5: Activate another collection (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_index.py -k activate_generation`
- **Expected:** passes; Qdrant alias, OpenSearch alias and `warehouse.active_generation` all move to the new generation, and `index collections` marks it active.

### E-6: Second-review clarifications (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_index.py -k "test_schema_isolation or fake_embedder or warehouse_missing_preflight or doc_prefix_once or validate_union_dates or entities_pruned"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-1, E-2, E-3 |
| FR-3 | E-4 |
| FR-4 | E-1, E-5 |
| FR-5 | E-1 |
| NFR-1 | E-1 |
| NFR-2 | E-3 |
| FR-6 | E-6 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (logs) saved in `evidence/` and listed in `results.md`
