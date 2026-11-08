# 029: Pipeline orchestrator and incremental ingest

**Status:** draft
**Mode:** solo
**Runs:** waits for 024: drives every stage up to indexing

## Summary

One command processes everything: unpack and register new archives, then every stage in dependency
order, one pass per stage so each GPU model loads once per pass, until no work is left. Interrupted
runs resume, and a new year's RAR added later is processed without redoing finished pages (grill
A-16, A-28, A-30, A-43).

## Functional requirements

- **FR-1** - `uv run newsrag run [--until <stage>] [--only <stage,...>] [--pages-file F] [--dates D1,D2] [--dry-run] [--tag T]` executes: `ingest unpack` → `ingest register` (all archives) → page and date stages in topological order of `depends_on` from the registry (expected: render, ocr, ocr_split, ocr_final, folio, then `editions build` + `editions apply`, group, judge, group_retry, judge_retry, group_final, transcribe, stitch, dedup, enrich_core, enrich_places, enrich_facts, then `aliases build`, chunk, index), then `index validate`. Corpus-level steps run right after their inputs and only when their input changed: `editions build` + `editions apply` after `folio`, when the fingerprint of `folio` done rows (page_id, version, finished_at) differs from the one stored in `ledger.corpus_steps(step PK, fingerprint, finished_at)`; `aliases build` after `enrich_core`, when the fingerprint of `enrich_core` date rows differs. `ingest register` likewise skips (in < 5 s) when the fingerprint of the stems' `.unpacked` markers and PDF counts is unchanged. When `data/editions.yaml` changes, the run prints `data/editions.yaml changed: commit it`.
- **FR-2** - Each stage runs as one pass over all its eligible items, in-process through the ticket-003 runner with the stage's own `concurrency`; a stage with 0 eligible items is skipped, so its `open()` (model load) never runs. Stages load their own models in `open()` (some lazily, ticket 013); the orchestrator never opens models itself and records in the run file which profiles were loaded (`models_loaded`, reported by `newsrag.models`). The orchestrator repeats the whole order until a full cycle finds no eligible work. Filters: `--pages-file`/`--dates` restrict page and date stages; unpack and register always run; corpus steps and `index validate` run only without filters. `--until S` runs up to and including S (and `index validate` only when S is `index`). `--only` runs just the listed stages, one pass each, no corpus steps, no repeat.
- **FR-3** - `--dry-run` prints, per stage in order, `<stage>  eligible=<n>` and exits without processing.
- **FR-4** - Every run writes `<reports_dir>/runs/<UTC timestamp>.json`: start/end time, per stage `{processed, failed, seconds}`, archives unpacked, pages registered, and the final `stage status` snapshot. On SIGINT/SIGTERM it sets the runner's stop event (ticket 003): no new items are claimed, items in flight get up to 10 min to finish, items still unfinished are marked `failed` with error `interrupted`; then `close()` hooks run (models stopped, GPU lock released), the run file is written with `interrupted: true`, and it exits 130. If a whole stage cannot run (preflight exit 2, model failed to start, stores down), the run stops, writes the run file with `failed_stage` and the message, and exits 1. Item failures never stop the run; permanently failed items are listed in the run file. `index validate` failing → exit 1. The run file also records `tag` (e.g. `full`).
- **FR-5** - Before starting (not on `--dry-run`) it runs `stage requeue-stale --older-than 2h`.
- **FR-6** - `uv run newsrag run --until index` on a corpus where everything is done loads no model (`models_loaded: []` in the run file) and finishes in < 2 min ("nothing to do").
- **FR-7** - Clarifications (second review):
  - Filtered runs (`--pages-file`/`--dates`) still run a corpus step when its fingerprint changed, so `chunk` never starts without aliases or edition data. After each corpus step the orchestrator calls ticket 023's `stale_dates()` and resets `chunk` for those dates only (so a new archive does not re-chunk the whole corpus); versions and eligibility are re-evaluated after every corpus step (nothing cached across steps).
  - `models_loaded` comes from ticket 006's `newsrag.models.loaded_profiles()` plus `gpu_lock` holders recorded by `newsrag.models` (file `src/newsrag/models/lock.py` records holder names per process).
  - The `aliases build` fingerprint includes `data/aliases/overrides.yaml`; it is stored only after the step succeeds. On SIGINT during a corpus step, the step finishes its current batch and the fingerprint is not stored.
  - `runner.py` is used, not changed (ticket 003 already provides `stop_event`). E-4 adds an unfiltered "everything done" run timed < 2 min after the full dev-sample set is indexed.

## Non-functional requirements

- **NFR-1** - Adding a new archive processes only its pages: after a full run, adding a RAR with N new pages leads to exactly N new `render` rows and no re-run of existing pages' stages (dates that gain pages re-run their date stages, grill A-43).

## Affected areas

- `src/newsrag/orchestrator.py` (new); `src/newsrag/cli.py` (changed)
- `tests/test_orchestrator.py` (new; fake stages, `stores`)
- `README.md` - "Run the pipeline" section (changed)
- `src/newsrag/stages/runner.py` - a `stop_event` the runner checks before each claim (changed)
- `src/newsrag/ledger/sql/00N_corpus_steps.sql` (new migration)
- `src/newsrag/models/lock.py` - record `gpu_lock` holders for `loaded_profiles()` (changed)

## Don't touch

- Individual stage implementations (call them, don't change them).

## Out of scope

- Scheduling/cron, multi-machine runs.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Order | topological order of declared dependencies | ticket 003 D-4 |
| D-2 | GPU use | one pass per stage, model loaded once per pass | grill A-28 |
| D-3 | New archives | same command, versions decide what reruns | grill A-16, A-30 |

## Open questions

None.
