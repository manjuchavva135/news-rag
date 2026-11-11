# 028: Customer-question answer check - Results

Built on `main` in the main checkout (not committed yet).

## What was built

- `data/eval/customer-questions.json`: byte copy of the old system's 30 questions.
- `src/newsrag/evals/customer.py`: the three passes (search inside one embed scope → `answer_model` → text judge `answer_judge_model or judge_model`), unit splitting, the claim check, `customer-answers.{md,json}`, and the sign-off.
- `src/newsrag/prompts/answer_check_v1.md`, `claim_check_v1.md` (the claim-check file has a `## cited` and a `## uncited` template).
- `newsrag eval customer [--k]` and `newsrag eval customer-signoff --name [--note]` in `src/newsrag/evals/cli.py`.
- `config.py` (`# --- evals ---`): `customer_*` settings. The paths are derived from `repo_root`/`reports_dir`; `answer_model`/`answer_judge_model` already existed (ticket 032).
- `tests/test_eval_customer.py`: 21 tests with a fake search and fake models.

Decisions the ticket left open:
- One source per search hit (a chunk), numbered 1..k. A citation outside 1..len(sources) is `out_of_range` and supports nothing.
- A cited claim is judged against each cited source in turn and stops at the first `supported`.
- An uncited unit whose judge reply stays unparseable counts as `factual` (the worse case for `uncited`).
- Thinking is turned off for both models (Gemma 4 thinks by default), and `<think>` blocks are stripped.
- `customer_answer_repetition_penalty = 1.05` was added after run 1 (see below).

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| `ruff check .` / `ruff format --check .` | PASS | |
| unit tests `-m "not gpu and not data and not stores"` | PASS (943 passed) | |
| E-1 questions copied | PASS (`same`) | |
| E-2 run and outputs | PASS: `supported_pct=92.97 n_claims=128 uncited=7 n_failed=0`, real 4m18s, `30`, `30 True True` | `evidence/run.log`, `evidence/customer-answers.md`, `evidence/customer-answers.json` |
| E-3 logic tests | PASS (21 passed) | `evidence/e3-pytest.log` |
| E-4 sign-off record | PASS (six keys; the real sign-off was restored, though none existed) | `evidence/e4-signoff.log` |
| E-5 clarifications | PASS (4 passed) | `evidence/e5.log` |

## Runs

The index currently holds only the dev dates (432 chunks in `bge-m3__v1`), so most questions get "not covered in the sources" answers. The ≥ 90% bar applies in ticket 031 on the full corpus.

- **Run 1** (`evidence/run1-*`): `supported_pct=68.24 n_claims=148`. At temperature 0, Qwen3 got stuck repeating one sentence up to max_tokens (Q06: 25 claims, 22 copies of one line; Q14 the same). It also cited sources on "no information" sentences (Q01).
- **Run 2** (the current reports): a repetition penalty of 1.05 plus two prompt rules ("no citation on a not-covered sentence", "state each fact once") gave `supported_pct=92.97 n_claims=128 uncited=7`.

## Known weaknesses (for the reader and ticket 031)

- Some repetition remains: Q27 repeats one supported bullet 13 times, and Q09, Q12 and Q13 have 4 duplicates each. Every unit counts per FR-3, so duplicates weigh on the score (here they raise it).
- Q27 and Q09 sometimes cite every source at once (`[1]…[11]`). Under "supported if any cited source supports it", that is easy to pass. The judge cannot tell whether the claim came from the source it was meant to.
