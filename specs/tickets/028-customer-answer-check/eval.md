# 028: Customer-question answer check - Evaluation

## Setup

- Prerequisites: ticket 025 done; data indexed (at least the dates indexed for ticket 025); GPU free.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Questions copied (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `cmp data/eval/customer-questions.json /home/admin1/RAG/evals/customer-questions.json && echo same`
- **Expected:** `same`.

### E-2: Run and outputs (covers FR-2, FR-3, FR-4, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag eval customer`
  2. `grep -c "^## Q" /home/admin1/raw/reports/customer-answers.md`
  3. `python3 -c "import json;d=json.load(open('/home/admin1/raw/reports/customer-answers.json'));print(len(d['per_question']), d['n_claims']>0, 0<=d['supported_pct']<=100)"`
- **Expected:** prints the FR-4 summary line; real time < 45 min; `30`; `30 True True`. Each question section in the .md shows a new answer, sources, verdicts and an "Old answer" block. (The ≥ 90% bar is checked in ticket 031 on the full corpus.)

### E-3: Logic (covers FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_eval_customer.py -v`
- **Expected:** all pass, including `test_judge_family_differs`, `test_supported_pct_formula`, `test_uncited_counted_separately`, `test_old_answer_extracted`, `test_multi_citation_any_supports`, `test_out_of_range_citation_not_supported`, `test_failed_question_counted`, `test_new_run_retires_signoff`, `test_one_model_at_a_time`.

## Edge cases

### E-4: Sign-off record (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `R=/home/admin1/raw/reports; [ -f $R/customer-answers-signoff.json ] && cp $R/customer-answers-signoff.json /tmp/signoff.bak; uv run newsrag eval customer-signoff --name eval-tester --note test && cat $R/customer-answers-signoff.json; rm $R/customer-answers-signoff.json; [ -f /tmp/signoff.bak ] && mv /tmp/signoff.bak $R/customer-answers-signoff.json; true`
- **Expected:** JSON with the six keys of FR-5; any real sign-off is restored afterwards.

### E-5: Second-review clarifications (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_eval_customer.py -k "family_check_exit_2 or single_embed_scope or editions_union or coverage_note_meta"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-2, E-3 |
| FR-3 | E-2, E-3 |
| FR-4 | E-2, E-3 |
| FR-5 | E-4 |
| NFR-1 | E-2 |
| FR-6 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (`customer-answers.md`, logs) saved in `evidence/` and listed in `results.md`
