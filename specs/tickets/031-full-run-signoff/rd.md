# 031: Full-corpus run and phase-1 sign-off

**Status:** draft
**Mode:** solo
**Runs:** waits for 030: needs report; waits for 014: chosen grouping model; waits for 032: chosen text model

## Summary

The phase-1 finish line. Runs the whole pipeline over all three archives (~21k PDFs) with the chosen
models, reruns the evaluations on the full corpus, and builds the quality report. Phase 1 is done when
the report says `PHASE 1: PASS`, which includes the Telugu reader's OCR sign-off and the user having
read the 30 customer answers (grill D-14, D-21). This ticket runs and fixes what breaks; it does not
lower any bar.

## Functional requirements

- **FR-1** - Preconditions, checked by `uv run newsrag preflight` (new command) which prints one line per item, `ok` or `MISSING <what to do>`, and exits 1 if any is missing:
  - `pipeline.yaml` has `group_model` and `judge_model` (from ticket 014), `enrich_model`, `answer_model` and `answer_judge_model` (from ticket 032), and `embed_model` (from ticket 026);
  - OCR sanity check signed off (`verdicts.json` has `signoff`), and the rules in `ocr_fallback.yaml` equal the sign-off's `failing_page_types` with `note` removed (canonical JSON compare; `[]` ↔ `rules: []`); the user (not the worker) writes those rules; if rules are non-empty, `SARVAM_API_KEY` is set;
  - `HF_TOKEN` is set;
  - `stores status` all ok; `models status` prints `GPU free` and `nvidia-smi --query-compute-apps` lists nothing; disk free under `/home/admin1/raw` ≥ 600 GB;
  - `baseline status` all ok and the baseline row counts equal the bundle manifest counts (ticket 027).
- **FR-2** - `uv run newsrag run --tag full` processes everything to `index` (expected ~1 week of GPU time, grill D-11). The worker runs it in the background (e.g. `nohup` or `systemd-run --user`), monitors `stage status` and the run files, and fixes code bugs that make items fail (each fix as its own commit with a test). Re-applying a fix: if the fix changes the output of already-done items, bump that stage's `version` (it and later stages rerun); if it only affects failed items, use `stage reset <stage> --page ...` for those. Pages that still fail a stage after the fixes are quarantined with `uv run newsrag stage quarantine-failed <stage>` (new command: quarantines every page whose row for that stage is `failed` at `max_attempts`, reason `stage_failed:<stage>: <error first line>`); they count against the ≤ 1% quarantine bar (user decision). If the summed wall time of `full`-tagged runs passes 10 days, the worker reports progress to the router and continues unless told otherwise.
- **FR-3** - After the run the worker runs `uv run newsrag index activate <embed_model>__v1` (ticket 024; older generations are kept), and `index validate` passes for every date of the active generation.
- **FR-4** - Then, on the full corpus: `transcribe spotcheck --n 50`; `eval build-retrieval --out data/eval/retrieval-v2.jsonl` and `eval retrieval --set data/eval/retrieval-v2.jsonl` (final retrieval numbers, committed set); `baseline calibrate` and `baseline compare`; `eval customer`; `report build`.
- **FR-5** - The user (not the worker) reads `customer-answers.md` and signs with `newsrag eval customer-signoff --name <name>`; the worker asks for this through the router once FR-4 is done and `supported_pct ≥ 90`.
- **FR-6** - Final `report build` shows `PHASE 1: PASS`. If a bar fails because of a code bug, fix it (FR-2). If it fails for another reason (e.g. judge mean 0.87, 88% supported claims), the worker may change prompts (new prompt version), model choices or retry settings — never the bars or thresholds — rerun only the affected stages within the time budget, and record each change and its effect in `results.md` (user decision). If the bars still fail after that, `results.md` lists each failing bar with its value and the most likely cause, and the ticket is not done.
- **FR-7** - Clarifications (second review):
  - DateStages that fail at `max_attempts` after fixes: `stage quarantine-failed <date stage>` quarantines every page of that date (reason `stage_failed:<stage>: …`), then the worker runs `run --tag full` again so `eligible=0`.
  - Order in FR-4: `baseline calibrate` and `baseline compare` (own output files, ticket 027) first, then `eval retrieval --set data/eval/retrieval-v2.jsonl` (default output `retrieval-new.json`).
  - Preflight reads `<raw_root>/baseline/manifest-loaded.json` (ticket 027) and compares the baseline row counts.
  - This ticket adds `tests/test_report.py::test_bars_constants`, asserting the seven thresholds kept as constants in `src/newsrag/report/bars.py` (ticket 030).
  - Run files use keys `start`, `end` (ISO 8601 with `+00:00`), `tag`; report bars use key `status`.
  - Time: the full run should take ≤ 10 days; tuning reruns (FR-6) may add up to 5 more days, after which the worker stops and asks through the router. Any change after the user's customer-answer sign-off voids it (new answers file), so the worker asks the user to read and sign again.

## Non-functional requirements

- **NFR-1** - Total GPU wall time for the run ≤ 10 days (grill D-11: about a week is fine).

## Affected areas

- `src/newsrag/preflight.py`, `src/newsrag/cli.py` (new / changed)
- Bug fixes anywhere in `src/newsrag/**` with tests (changed)
- `ocr_fallback.yaml` (changed only to reflect the reader's sign-off)
- `src/newsrag/stages/runner.py` / `cli.py` - `stage quarantine-failed` command (new)
- `data/eval/retrieval-v2.jsonl` (new, committed)
- `tests/test_report.py` (changed), `src/newsrag/report/bars.py` (read)

## Don't touch

- Thresholds and bars (`judge_threshold`, D-21 bars in ticket 030, dedup thresholds) — changing them needs a new user decision.
- `/home/admin1/RAG/**`, the AWS system (grill D-15).

## Out of scope

- Phase 2 (search/answer API, UI, deployment), the 600k-page archive (grill A-35).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Done | report shows `PHASE 1: PASS` | grill D-14, D-21 |
| D-2 | Human steps | OCR sign-off by the Telugu reader; customer answers read and signed by the user | grill A-7, D-21 |
| D-3 | Bars | fixed, not lowered by the worker | the user set them |
| D-4 | Pages that keep failing | quarantined, counted in the ≤ 1% bar | user decision |
| D-5 | Bar fails without a bug | worker may tune prompts/models/retries and rerun affected stages, then report | user decision; bars themselves never change |
| D-6 | Final retrieval numbers | from `retrieval-v2.jsonl` built on the full corpus | v1 was built from a few dates for the bake-off |

## Open questions

None.
