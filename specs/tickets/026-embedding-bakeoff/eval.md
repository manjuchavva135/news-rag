# 026: Embedding model bake-off - Evaluation

## Setup

- Prerequisites: ticket 025 done; `data/eval/retrieval-v1.jsonl` exists; the indexed dates cover the gold stories of ≥ 200 questions; GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: `cp pipeline.yaml /tmp/pipeline.yaml.bak`

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Bake-off (covers FR-1, FR-2, FR-3, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag embed bakeoff`
  2. `cat /home/admin1/raw/reports/embed-bakeoff.md`
- **Expected:** exits 0; a `questions: <n>` line with n ≥ 200; the FR-3 table with one row per candidate; a `winner:` line.

### E-2: Apply (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag embed bakeoff --apply && grep embed_model pipeline.yaml && uv run newsrag stage status | grep -E '^(chunk|index) ' && uv run newsrag search "ఆంధ్రప్రదేశ్ అప్పులు" --k 3`
- **Expected:** finishes in < 30 s; prints one of the two FR-4 messages; if the winner ≠ `bge-m3`, `chunk` shows `eligible=` equal to its done count; the search still returns 3 hits (active generation unchanged). (Restore `/tmp/pipeline.yaml.bak` afterwards only if the router asks; the real choice is kept.)

## Edge cases

### E-3: Tie rule and small corpus (covers FR-3, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_embed_bakeoff.py -v`
- **Expected:** all pass, including `test_tie_breaks_by_mrr_then_dim_then_order` and `test_small_corpus_exits_1`.

### E-4: Cleanup (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag embed bakeoff --cleanup && uv run newsrag index collections`
- **Expected:** no `bakeoff__` collection listed; the active collection unchanged.

### E-5: Second-review clarifications (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_embed_bakeoff.py -k "reuses_open_embedder or payload_copied or failed_candidate_excluded or apply_without_report or size_check_first"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-1 |
| FR-3 | E-1, E-3 |
| FR-4 | E-2 |
| FR-5 | E-4 |
| NFR-1 | E-1, E-3 |
| FR-6 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-2 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (bake-off report) saved in `evidence/` and listed in `results.md`
