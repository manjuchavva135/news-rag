# 013: Grouping judge and retry - Evaluation

## Setup

- Prerequisites: ticket 012 done; `group` done for `data/dev-sample.txt`; GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Logic with fake models (covers FR-1, FR-2, FR-3, FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_judge.py -v`
- **Expected:** all pass, including the one-story tests (`test_story_check_*`, `test_story_prompt_lists_the_headline_boxes`, `test_make_create_sends_the_story_schema`) and `test_same_family_refused`, `test_score_formula`, `test_high_score_passes_through_without_model_call`, `test_low_score_regroups_at_1_5x`, `test_better_grouping_kept`, `test_still_low_flagged`, `test_article_verdict_flags`.

### E-2: Four passes on the dev sample (covers FR-2..FR-6)

- **Type:** CLI
- **Steps:**
  1. `for s in judge group_retry judge_retry group_final; do uv run newsrag stage run $s --pages-file data/dev-sample.txt; done`
  2. `uv run newsrag stage status | grep -E '^(judge|group_retry|judge_retry|group_final) '`
  3. `uv run newsrag judge stats --pages-file data/dev-sample.txt`
- **Expected:** no sample page in `stage failures` of the four stages; step 3 prints the five lines of FR-6. Then:
  4. `uv run python - <<'PY'
import json,glob
for f in glob.glob('/home/admin1/raw/work/group_final/*/*.json'):
    d=json.load(open(f)); assert {'articles','page_flags','unassigned','judge_score','retried'} <= set(d), f
    assert all('judge_verdict' in a for a in d['articles'] if a['kind'] not in ('masthead','ad_commercial')), f
    if d['judge_score'] is not None and d['judge_score'] < 0.8: assert all('low_confidence:judge_low' in a['flags'] for a in d['articles']), f
print('ok')
PY`
  5. `uv run newsrag db sql "select count(*) from ledger.stage_runs where stage='group_retry' and (detail->>'retried')::bool and (detail->>'image_long_side')::int <> 5376"`
- **Expected (4-5):** `ok`; `0`.

### E-3: Judge sanity (covers NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag judge sanity --pages-file data/dev-sample.txt --n 5`
- **Expected:** one `pair=aNN+aMM verdict=<v> ... caught|missed` line per page (no page pairs an article with a piece of its own story), then `caught: k/5` with k ≥ 4.

## Edge cases

### E-4: Family check on real config (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `NEWSRAG_JUDGE_MODEL=qwen3-vl-32b uv run newsrag stage run judge --limit 1; echo "exit=$?"`
- **Expected:** prints `judge_model and group_model must be different families` and `exit=2`.

### E-5: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_judge.py -k "judge_grouping_function or judge_failed_passthrough or retry_failed_keeps_original or retry_concurrency or overlay_hides_unjudged"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-4 |
| FR-2 | E-1, E-2 |
| FR-3 | E-1, E-2 |
| FR-4 | E-1, E-2 |
| FR-5 | E-1, E-2 |
| FR-6 | E-2 |
| NFR-1 | E-3 |
| FR-7 | E-5 |
| FR-8 | E-1, E-3 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (logs) saved in `evidence/` and listed in `results.md`
