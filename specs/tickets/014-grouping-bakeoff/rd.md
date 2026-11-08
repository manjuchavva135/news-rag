# 014: Grouping model bake-off

**Status:** draft
**Mode:** solo
**Runs:** waits for 013: needs grouping + judge

## Summary

Picks the grouping VLM by evidence instead of by name: 2-3 open VLMs group the same 5 pages, the judge
scores them, and the winner is written to `pipeline.yaml` for the full run (grill A-28).

## Functional requirements

- **FR-1** - `models.yaml` gains full profiles (all fields of ticket 006 FR-1): `qwen3-vl-30b-a3b` (`Qwen/Qwen3-VL-30B-A3B-Instruct`, qwen, vlm, bfloat16, `max_model_len: 32768`, `gpu_memory_utilization: 0.90`, same `extra_args` as `qwen3-vl-32b`) and `internvl3.5-38b` (`OpenGVLab/InternVL3_5-38B`, internvl, vlm, `dtype: fp8` (vLLM `--quantization fp8`, ≈ 40 GB weights instead of ≈ 77 GB in bf16, so it fits with room for long page prompts), `max_model_len: 32768`, `gpu_memory_utilization: 0.90`, `extra_args: ["--trust-remote-code", "--limit-mm-per-prompt", "{\"image\": 1}", "--mm-processor-kwargs", "{\"max_dynamic_patch\": 24}"]` so the page is not shrunk to 12 tiles), each passing `models smoke`.
- **FR-2** - `uv run newsrag group bakeoff [--candidates qwen3-vl-32b,qwen3-vl-30b-a3b,internvl3.5-38b] [--pages-file data/bakeoff-pages.txt]` writes `data/bakeoff-pages.txt` (committed) if it doesn't exist: 5 distinct pages from `data/dev-sample.txt` (`#` lines skipped, sorted order, a page already chosen is skipped): the first 2020 `main_01`, the first 2026 `main_01`, the first 2025 `ALL` page, the first 2026 single-letter-edition page with page ≠ 1 (else 2020), the first `fea`/`splus` page of any year; a slot with no match takes the next unused sample line. For each candidate it runs the ticket-012 grouping code on those pages, writing to `<work_dir>/group_bakeoff/<candidate>-group-p1/` (never into `work/group/`, no ledger rows), then judges every candidate's output with `judge_model` (ticket 013's `judge_grouping()` function), writing to `<work_dir>/group_bakeoff_judge/<candidate>/<judge_model>-judge-p1/`. The grouping wall time per candidate is saved as `timing.json` next to its outputs and read back when outputs are reused. Outputs already present for a candidate+version are reused (a rerun is cheap; `--rerun` forces). A candidate from the judge's family is skipped with a printed reason; an unknown candidate name exits 2. A page that fails grouping after the retries, or whose judging still fails after its retries, scores 0; a `null` (judge_skipped) score counts as 1; a candidate whose model fails to start is reported `failed` and excluded.
- **FR-3** - It writes `<reports_dir>/group-bakeoff.md` with a table: candidate, status, mean judge score (over the 5 pages), pages below `judge_threshold`, seconds/page (wall time of that candidate's 5 grouping requests after its model is ready, ÷ 5), and the line `winner: <profile>`. Winner = highest mean score; if the top two are within 0.01, the one with fewer seconds/page. If no candidate finished, it writes `winner: none` and exits 1.
- **FR-4** - `uv run newsrag group bakeoff --apply` reads the winner from the existing `group-bakeoff.md` (it does not rerun), writes `group_model: <winner>` into `pipeline.yaml` (keeping other keys) and prints `group_model set to <winner>`; with `winner: none` or no report it exits 1 and changes nothing.

## Non-functional requirements

- **NFR-1** - Models are loaded one at a time through the ticket-006 lock; the whole bake-off for 3 candidates finishes in < 3 h.

## Affected areas

- `models.yaml` (changed); `src/newsrag/grouping/bakeoff.py` (new); `src/newsrag/cli.py` (changed)
- `data/bakeoff-pages.txt` (new, committed); `pipeline.yaml` (changed by `--apply`)
- `tests/test_bakeoff.py` (new, fake models)

## Don't touch

- Real `group`/`judge` stage rows in the ledger.

## Out of scope

- Prompt tuning per model (one shared prompt).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | How the model is chosen | 5-page bake-off scored by the judge | grill A-28 |
| D-2 | Candidates | Qwen3-VL-32B (bf16), Qwen3-VL-30B-A3B (bf16), InternVL3.5-38B (FP8) | strong open VLMs that fit the 96 GB GPU (ticket 006 fit check); none from the judge's family (Gemma) |
| D-3 | Tie rule | within 0.01 → faster | grill D-11 quality first, speed only breaks ties |
| D-4 | A candidate that won't start | reported `failed` and excluded; the bake-off continues | a missing candidate must not block the pick |

## Open questions

None.
