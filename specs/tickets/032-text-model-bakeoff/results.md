# 032: Text model Telugu bake-off - Results

Branch: ticket/032-text-model-bakeoff
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-a026dd351198baf3e
Port: n/a (CLI only)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| ruff | PASS | evidence/tester-ruff.log |
| unit tests | PASS (813 passed) | evidence/tester-pytest-unit.log |
| E-1 | PASS (fit=yes, reply `OK`) | evidence/E-1.log, evidence/tester-E-1.log |
| E-2 | PASS (real 15m59s, 40 stories, 4 rows, winner line) | evidence/E-2.log, evidence/textmodel-bakeoff.md, evidence/tester-E-2.log |
| E-3 | PASS (idempotent) | evidence/E-3.log, evidence/tester-E-3.log |
| E-4 | PASS (29 tests) | evidence/tester-E-4-verbose.log |

## Score table (40 stories, seed 21; 5 dedup dates)
| candidate | family | json_ok | names_verbatim | facts_verbatim | telugu_output | judged | total | seconds/story |
|---|---|---|---|---|---|---|---|---|
| gemma3-27b | gemma | 1.0000 | 0.9109 | 0.8367 | 1.0000 | 0.8823 | 0.9096 | 1.69 |
| gemma4-31b | gemma | 1.0000 | 0.9167 | 0.7528 | 1.0000 | 0.8667 | 0.8920 | 3.25 |
| qwen3-30b-a3b | qwen | 1.0000 | 0.9292 | 0.8028 | 1.0000 | 0.8844 | 0.9087 | 0.81 |
| sarvam-m-24b | mistral | 1.0000 | 0.8493 | 0.8983 | 1.0000 | 0.9132 | 0.9250 | 1.21 |

winner: sarvam-m-24b. Applied: enrich_model = answer_model = sarvam-m-24b, answer_judge_model = qwen3-30b-a3b
(best-ranked other-family candidate; qwen3 and gemma3 tie within 0.01, qwen3 has the higher judged).
Wall time of the bake-off: 15m59s (NFR-1 < 4 h). The sarvam-m download (47 GB) took 40 min before it and is not counted.

## Attempts
- Attempt 1: reviewer found three majors (dead judge server saved as "unusable replies"; dead candidate server scored as 0 instead of failed; thinking off only in the bake-off, not in production). Fixed: transport failures now raise (candidate `failed` / judge noted and retried next run), thinking disabled in the sarvam-m-24b profile (`--default-chat-template-kwargs`), answer judge picked from the global ranking, fingerprint covers more settings.
- Attempt 2: all checks pass. First E-1 smoke (old profile, thinking on) replied with a long reasoning text instead of `OK` (evidence/E-1-first-run-thinking-on.log); after the profile fix the reply is `OK`.

## Changes
- `models.yaml` - `sarvam-m-24b` profile (thinking off via `--default-chat-template-kwargs`).
- `src/newsrag/textmodel/{__init__,bakeoff,cli}.py` - the bake-off, report, `--apply`; registered as `newsrag textmodel` in `src/newsrag/cli.py`.
- `src/newsrag/prompts/textmodel_judge_v1.md`, `summary_v1.md` - new prompts (as in rd.md).
- `src/newsrag/prompts/textmodel_question_v1.md` - NEW, not in rd.md "Affected areas": ticket 025 is not built, so there was no question prompt to reuse. Ticket 025 should reuse or replace it.
- `src/newsrag/config.py` - `# --- textmodel ---` block: `answer_judge_model` (as in rd.md) plus `textmodel_*` knobs and `answer_model` (default qwen3-30b-a3b; ticket 028 is meant to add it - added here so `--apply` does not write an unknown key; 028 will see a duplicate/identical field on merge).
- `data/textmodel-bakeoff-stories.txt` - the 40 sampled stories (committed).
- `pipeline.yaml` - applied values (separate commit, easy to revert).
- `tests/test_textmodel_bakeoff.py` (new), `tests/test_models_client.py` (profile list), `tests/conftest.py` (pins enrich_model/answer_model/answer_judge_model so an applied pipeline.yaml does not break unit tests, as it already does for group_model/judge_model).

## Notes for the router
- **The winner is not qwen3-30b-a3b.** Stages 019-022 and the alias build ran with qwen3-30b-a3b; with `enrich_model: sarvam-m-24b` in pipeline.yaml their versions change and they would be rerun. I did NOT rerun them. If the user prefers to keep qwen3-30b-a3b, revert commit 2f58667 (pipeline.yaml only).
- **Caveat on the margin.** Sarvam wins by 0.0154 over gemma3 (outside the 0.01 tie). But `judged` is structurally asymmetric: sarvam is the harshest judge (gives others 0.77-0.82 vs 0.92-0.97 from the rest) and never grades itself, so every non-sarvam candidate has its harsh judge counted while sarvam's three judges are the lenient ones. On the one judge that graded all three others (qwen3-30b-a3b): gemma4 0.956, gemma3 0.950, sarvam 0.923, i.e. sarvam last. Sarvam also has the lowest names_verbatim (0.849 vs 0.929 for qwen3), which matters for enrich_core, and the best facts_verbatim (0.898). The rule in rd.md was followed as written; the user may want to weigh this.
- Sample: dedup exists for 5 dates only (2024-12-26, 2025-01-28, 2025-04-11, 2025-06-10, 2025-08-02); 2026-04-03 and 2020-04-02 are not in the ledger. 482 stories, 332 eligible, 40 drawn (10/9/2/10/9 per date).
- All json_ok = 1.0 and telugu_output = 1.0 for every candidate: json is schema-constrained by vLLM and all outputs were Telugu, so these two scores do not discriminate.
- Facts task uses the print date as event date (independent of the core task).
- If fewer than n eligible stories exist the list is redrawn each run (not an issue with 332).
- No vLLM server or background job of mine is still running; GPU free.

## User decision (2026-11-11): keep qwen3-30b-a3b

The report's rule picked `sarvam-m-24b` (total 0.9250). The user chose to keep `qwen3-30b-a3b` after a
follow-up check: Sarvam-M grading its own 40 outputs gives 0.797 (`evidence/sarvam_self_judge.py`,
`evidence/sarvam_self_scores.json`), about what it gives the others (0.774–0.815), so it is a harsh judge
that never judged itself in the bake-off. With the same three judges (gemma3-27b, gemma4-31b, sarvam-m-24b)
for both, judged is qwen3 0.885 vs sarvam 0.871, total qwen3 0.909 vs sarvam 0.904. Qwen3 also has the best
`names_verbatim` (0.929) and is the fastest (0.81 s/story), and 019–022 already ran with it, so nothing
needs rerunning.

`pipeline.yaml` therefore has `enrich_model: qwen3-30b-a3b`, `answer_model: qwen3-30b-a3b` and
`answer_judge_model: sarvam-m-24b` (the best-ranked candidate of a different family from qwen), set by hand
instead of by `--apply`. `textmodel-bakeoff.md` still shows `winner: sarvam-m-24b`, so a later
`bakeoff --apply` would overwrite this choice.
