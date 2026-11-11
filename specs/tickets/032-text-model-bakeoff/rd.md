# 032: Text model Telugu bake-off

**Status:** ready
**Mode:** solo
**Runs:** waits for 017: needs real canonical stories; waits for 006: model runner

## Summary

Picks the text model that understands Telugu best for enrichment (tickets 019–022), test questions
(025) and the customer answers (028), by measuring it on our own Telugu newspaper stories rather than
assuming (grill D-25). Four open models that fit the GPU compete: Gemma 3 27B, Gemma 4 31B,
Qwen3-30B-A3B and Sarvam-M 24B.

## Functional requirements

- **FR-1** - `models.yaml` gains `sarvam-m-24b` (`sarvamai/sarvam-m`, family `mistral`, kind `llm`, bfloat16, `max_model_len: 32768`, `gpu_memory_utilization: 0.90`), passing `models fit` and `models smoke`. Candidates: `gemma3-27b` (text use of the VLM), `gemma4-31b` (text use of the existing VLM profile, added by the user 2026-11-11), `qwen3-30b-a3b`, `sarvam-m-24b`.
- **FR-2** - `uv run newsrag textmodel bakeoff [--n 40] [--seed 21]` samples n canonical stories from the `dedup` artifacts of the dates that have them (≥ 70% Telugu letters, 300–4,000 characters) and writes the list to `data/textmodel-bakeoff-stories.txt` (committed; reused on reruns). Each candidate, one at a time through `newsrag.models.use()`, runs four Telugu tasks per story with ticket 019's settings (temperature 0):
  1. the ticket-019 `enrich_core` prompt (category, people, organisations, event date);
  2. the ticket-021 facts prompt;
  3. a 2–3 sentence Telugu summary;
  4. writing one Telugu question answerable from the story (the ticket-025 question prompt).
- **FR-3** - Scores per candidate:
  - **Objective:** `json_ok` (share of replies that parse and pass the schema), `names_verbatim` (share of people/organisation names that are verbatim in the story), `facts_verbatim` (share of facts that pass ticket 021's verbatim and parse rules), `telugu_output` (share of summaries/questions whose letters are ≥ 90% Telugu script).
  - **Cross-judged:** each candidate's summary, question and extracted names are graded by every other candidate of a **different family** (a gemma candidate is never judged by the other gemma) on a 1–5 scale for correctness and natural Telugu (prompt `src/newsrag/prompts/textmodel_judge_v1.md`); `judged` = mean over those judges, scaled to 0–1.
  - `total` = 0.5 × `judged` + 0.125 × each of the four objective scores.
- **FR-4** - Writes `<reports_dir>/textmodel-bakeoff.md`: one row per candidate with all scores, seconds/story, and `winner: <profile>` (highest `total`; within 0.01 → higher `judged`; then faster). A candidate that fails to start is reported `failed` and excluded; no finisher → `winner: none`, exit 1.
- **FR-5** - `--apply` reads the report (no rerun) and writes with `set_pipeline_key`: `enrich_model: <winner>`, `answer_model: <winner>`, and the text `judge_model` for ticket 028 = the best-ranked candidate of a **different family** from the winner (setting `answer_judge_model`). It prints the three values. With `winner: none` it exits 1 and changes nothing.

## Non-functional requirements

- **NFR-1** - The whole bake-off (4 models, 40 stories, cross-judging) finishes in < 4 h.

## Affected areas

- `models.yaml` (changed); `src/newsrag/textmodel/bakeoff.py`, `src/newsrag/textmodel/cli.py`, `src/newsrag/prompts/textmodel_judge_v1.md`, `src/newsrag/prompts/summary_v1.md` (new)
- `src/newsrag/config.py` - `answer_judge_model` (changed; ticket 028 uses it for its claim check when set)
- `data/textmodel-bakeoff-stories.txt` (new, committed); `pipeline.yaml` (changed by `--apply`)
- `tests/test_textmodel_bakeoff.py` (new, fake models)

## Don't touch

- Ledger rows of the real enrich stages; ticket 019/021/025 prompts (reuse them, don't change them).

## Out of scope

- Fine-tuning; vision models (ticket 014 picks those).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | How the text model is chosen | measured on our Telugu stories | grill D-25 (user chose) |
| D-2 | Candidates | Gemma 3 27B, Gemma 4 31B, Qwen3-30B-A3B, Sarvam-M 24B | strong open models with Indian-language focus that fit 96 GB; Gemma 4 31B added by the user (2026-11-11) |
| D-3 | Judging without a gold set | objective checks + cross-judging by every candidate of another family | no model grades itself or its own family |

## Open questions

None.
