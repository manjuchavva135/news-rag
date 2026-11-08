# 014: Grouping model bake-off - Evaluation

## Setup

- Prerequisites: ticket 013 done; `ocr_final` done for `data/dev-sample.txt`; GPU free; `HF_TOKEN` set.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: `cp pipeline.yaml /tmp/pipeline.yaml.bak`

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: New profiles work (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `for p in qwen3-vl-30b-a3b internvl3.5-38b; do uv run newsrag models smoke $p; done`
- **Expected:** both replies contain `NEWS 2026`; if `internvl3.5-38b` cannot start on this GPU, the failure is recorded in `results.md` and E-2 must show it as `failed` (the check still passes).

### E-2: Bake-off runs and reports (covers FR-2, FR-3, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag group bakeoff`
  2. `wc -l < data/bakeoff-pages.txt; cat /home/admin1/raw/reports/group-bakeoff.md`
- **Expected:** real time < 3 h; `5`; the report has one row per candidate with the five columns of FR-3 and a line `winner: <one of the candidates>`.

### E-3: Apply (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag group bakeoff --apply`
  2. `grep group_model pipeline.yaml`
- **Expected:** finishes in < 30 s (no rerun); prints `group_model set to <winner>` and the yaml line names the same winner as the report.

## Edge cases

### E-4: Tie and family rules (covers FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_bakeoff.py -v`
- **Expected:** all pass, including `test_tie_within_001_picks_faster`, `test_judge_family_candidate_skipped`, `test_pages_distinct`, `test_failed_page_scores_zero`, `test_no_winner_exits_1`, `test_outputs_outside_group_dir`.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-2, E-4 |
| FR-3 | E-2, E-4 |
| FR-4 | E-3 |
| NFR-1 | E-2 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (bake-off report, logs) saved in `evidence/` and listed in `results.md`
