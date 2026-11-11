# 032: Text model Telugu bake-off - Evaluation

## Setup

- Prerequisites: tickets 006 and 017 done; `dedup` done for at least the dates 2026-04-03, 2025-06-10, 2020-04-02; GPU free; `HF_TOKEN` set.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: `cp pipeline.yaml /tmp/pipeline.yaml.bak`

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: New profile fits and works (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag models fit sarvam-m-24b && uv run newsrag models smoke sarvam-m-24b`
- **Expected:** `fit=yes`; the reply contains `OK`.

### E-2: Bake-off runs and reports (covers FR-2, FR-3, FR-4, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag textmodel bakeoff --n 40 --seed 21`
  2. `wc -l < data/textmodel-bakeoff-stories.txt; cat /home/admin1/raw/reports/textmodel-bakeoff.md`
- **Expected:** real time < 4 h; `40`; one row per candidate with `json_ok`, `names_verbatim`, `facts_verbatim`, `telugu_output`, `judged`, `total`, seconds/story, and a `winner:` line.

### E-3: Apply (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag textmodel bakeoff --apply && grep -E '^(enrich_model|answer_model|answer_judge_model):' pipeline.yaml`
- **Expected:** `enrich_model` and `answer_model` equal the winner; `answer_judge_model` is a candidate of a different family.

## Edge cases

### E-4: Scoring and rules (covers FR-3, FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_textmodel_bakeoff.py -v`
- **Expected:** all pass, including `test_no_self_judging`, `test_no_same_family_judging`, `test_total_formula`, `test_tie_breaks`, `test_failed_candidate_excluded`, `test_apply_picks_other_family_judge`, `test_no_winner_exits_1`.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-2 |
| FR-3 | E-2, E-4 |
| FR-4 | E-2, E-4 |
| FR-5 | E-3, E-4 |
| NFR-1 | E-2 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (bake-off report, logs) saved in `evidence/` and listed in `results.md`
