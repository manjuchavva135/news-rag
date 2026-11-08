# 003: Pipeline ledger and stage runner

**Status:** draft
**Mode:** solo
**Runs:** waits for 002: ledger lives in Postgres

## Summary

The backbone every processing stage plugs into. A Postgres ledger tracks every page (and every print
date) through every stage, with the version (model + prompt) that produced each output. A shared runner
claims eligible work, calls the stage's `handle`, and records the result. Stages are resumable, safe to
re-run, and a changed version reprocesses only that stage and the stages after it (grill A-13, A-30).
It replaces the old SQLite manifest (`/home/admin1/RAG/pipeline/common/manifest_db.py`, `stage_base.py`
are the reference pattern).

## Functional requirements

- **FR-1** - SQL migrations in `src/newsrag/ledger/sql/NNN_*.sql` (unqualified table names), applied in order by `uv run newsrag ledger init` (idempotent; records applied files in `schema_migrations`). All tables live in the Postgres schema named by setting `ledger_schema` (default `ledger`): every ledger connection runs `SET search_path TO <ledger_schema>, public` when it is taken from the pool (`newsrag.ledger.db.conn()`), and `ledger init` creates the schema if missing. Later tickets' SQL and eval text that say `ledger.<table>` mean `<ledger_schema>.<table>`; their migrations go in the same folder.
- **FR-2** - Tables (columns at least):
  - `pages(page_id text PK, pub_date date not null, edition_code text not null, section text not null, page_num int not null, page_end int null, source_path text not null, source_archive text not null, revision int not null, quarantine_reason text null, registered_at timestamptz, updated_at timestamptz)`
  - `stage_runs(page_id text FK→pages, stage text, version text, input_versions jsonb, status text check in ('running','done','failed'), attempt int, artifact_path text, detail jsonb, error text, started_at, finished_at, PK(page_id, stage))`
  - `date_runs(pub_date date, stage text, version text, input_versions jsonb, status, attempt, artifact_path, detail jsonb, error, started_at, finished_at, PK(pub_date, stage))`
  - `quarantine(source_path text PK, source_archive text, reason text, detected_at timestamptz)`
- **FR-3** - `src/newsrag/stages/base.py` defines `PageStage` and `DateStage` with:
  - attributes `name: str`, `version: str`, `depends_on: tuple[str, ...]`, `concurrency: int = 1` (threads that call `handle` in parallel inside one `stage run`), `models_needed: tuple[str, ...] = ()` (model profiles the stage may use; read by the orchestrator, ticket 029);
  - `version_for(item) -> str` (default: `self.version`) — a stage may give some items a different version (ticket 010 uses this);
  - rule for every stage: `version` ends with `-<first 8 hex of sha1 over the canonical JSON of every setting the stage reads plus the bytes of every prompt/data file it uses>` (helper `newsrag.stages.base.config_hash(settings_names, files)`), so changing a threshold, prompt or reference file reruns that stage and its downstream; version strings written in other tickets are the prefix before this hash;
  - `handle(page: PageRow)` (PageStage) or `handle(pub_date: date, pages: list[PageRow])` (DateStage) returning `StageResult(artifact_path: str | None, detail: dict)`;
  - lifecycle hooks, all optional: `preflight()` — called before connecting to the DB or claiming anything; raising `StageUnavailable(msg)` makes `stage run` print `msg` and exit 2; `open()` — called once after the first item is claimed (load a model, start a worker, take the GPU lock); `close()` — called once at the end, also on error/Ctrl-C; `on_final_failure(item, error)` — called when an item fails its `max_attempts`-th attempt (ticket 005 quarantines there).
  Stages register in `src/newsrag/stages/registry.py` (`REGISTRY: dict[str, Stage]`); importing the registry must not touch the GPU, the network, or files that may not exist (versions that depend on installed files are computed lazily).
- **FR-4** - Eligibility. A page is eligible for stage S when: it is not quarantined; every stage in `S.depends_on` has status `done` for that page **at that dependency's current `version_for(page)`**; and S's row is absent, or `failed` with `attempt < max_attempts` (setting, default 3), or `done` with `version` ≠ `S.version_for(page)`, or `done` with `input_versions` ≠ the current `{dep: [dep_row.version, dep_row.finished_at]}` map (so a dependency that reran — even at the same version — makes this stage eligible again, and changes travel down any number of stages). A dependency counts as satisfied only if it is `done` and not itself eligible. A `running` row is never eligible (only `requeue-stale` frees it). Eligibility is checked again after the claim lock is taken (FR-5), so two processes never claim the same item. A date is eligible for a DateStage when, for each name in its `depends_on`: if it is a PageStage, every non-quarantined page of that date has it `done`; if it is a DateStage, that date's `date_runs` row for it is `done`. The same version rules apply (`input_versions` maps each dependency to its current version). For DateStages, `input_versions` also holds `pages_fingerprint` = sha1 over the sorted `(page_id, dep, dep_version, dep_finished_at)` of all its non-quarantined pages, and each DateStage dependency is recorded as `[version, finished_at]` of that date's row, so adding, re-registering or redoing any page of a date, or rerunning an upstream DateStage, makes that date's DateStages eligible again once the inputs are through. A date whose pages are all quarantined is never eligible.
- **FR-5** - `uv run newsrag stage run <name> [<name> ...] [--limit N] [--page PAGE_ID] [--pages-file FILE] [--date YYYY-MM-DD] [--workers K] [--force]` runs the named stages one after another. Claiming: in one short transaction the runner locks candidate `pages` rows (PageStage) or takes a transaction advisory lock on `hashtext(stage||date)` (DateStage) with `SKIP LOCKED` semantics, then upserts the stage row as `running` with the current `version`, `input_versions`, `started_at`, and `attempt` = previous attempt + 1 (reset to 1 when the version or input_versions changed), and commits **before** `handle` runs. After `handle` it writes `done` (artifact, detail, `finished_at`) or `failed` (exception text). Several runner processes can work at once. An exception in one item never stops the run. Filters: `--page`/`--pages-file` (one page_id per line, `#` lines skipped) restrict to those pages; `--date` restricts to pages (PageStage) or the date (DateStage); items not eligible under the filter are skipped and counted as `skipped`, unless `--force`, which makes them eligible once regardless of status. `--workers K` overrides `concurrency` (threads). At the end it prints `<stage>: done=<n> failed=<n> skipped=<n> in <s>s`. The runner API `newsrag.stages.runner.run_stage(stage, filters, stop_event=None, stop_grace_s=600) -> RunResult(processed, failed, skipped, seconds, failed_items)` accepts a `threading.Event`; once set, no new items are claimed, in-flight items get `stop_grace_s` to finish, and those still unfinished are marked `failed` with error `interrupted` (the attempt counts). Ctrl-C on `stage run` sets the same event. If `open()` raises, the claimed row is put back to its previous state (attempt not used), `close()` still runs, `run_stage` raises `StageUnavailable(<message>)` and `stage run` exits 2.
- **FR-6** - `uv run newsrag stage status` prints one line per registered stage: `<stage>  done=<n> failed=<n> running=<n> eligible=<n>` (rows of quarantined pages are not counted). `uv run newsrag stage failures <name>` lists `page_id<TAB>attempt<TAB>error-first-line` for failed rows.
- **FR-7** - `uv run newsrag stage requeue-stale [--older-than 2h]` (formats `90s`, `30m`, `2h`, `1d`) turns `running` rows whose `started_at` is older than the limit into `failed` with error `stale: requeued after <limit>` (the attempt already counted at claim stays counted, so a page that kills the process ends after `max_attempts`). `uv run newsrag stage reset <name> [--page P | --date D | --all]` deletes the matching rows of that stage so they run again.
- **FR-8** - Helper `newsrag.stages.base.artifact_path(stage, version, key) -> Path` = `{work_dir}/{stage}/{version_slug}/{key}.json` where `version_slug` replaces any character outside `[A-Za-z0-9._-]` with `_`. Artifacts are written atomically (temp file + rename).
- **FR-9** - Helper `newsrag.ledger.db.quarantine_page(page_id, reason)` sets `pages.quarantine_reason` and upserts into `quarantine` (keyed by the page's `source_path`); quarantined pages are never eligible for any stage.
- **FR-10** - On a TTY the runner shows a progress bar with rate and ETA; otherwise it prints one line per item: `done <key>` or `FAILED <key>: <error first line>`.

## Non-functional requirements

- **NFR-1** - Two `stage run` processes on the same stage never process the same item (tested with 2 processes × 50 dummy pages: every page done exactly once, `attempt` = 1).
- **NFR-2** - Tests that touch Postgres use a throw-away schema (`test_<random>`) created and dropped by a pytest fixture; they never write to the `ledger` schema.

## Affected areas

- `src/newsrag/ledger/__init__.py`, `db.py`, `sql/001_init.sql` (new)
- `src/newsrag/stages/__init__.py`, `base.py`, `registry.py`, `runner.py` (new)
- `src/newsrag/config.py` - add `ledger_schema`, `max_attempts` (changed)
- `src/newsrag/cli.py` - add `ledger` and `stage` groups (changed)
- `tests/test_ledger.py`, `tests/test_runner.py` (new, marked `stores`)
- `CLAUDE.md` - "How to add a stage" section (changed)

## Don't touch

- `/home/admin1/RAG/**`; the store containers' config from ticket 002.

## Out of scope

- Any real stage (004+), the orchestrator that runs all stages in order (029).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Ledger DB | Postgres (not SQLite) | grill A-12; SQLite blocked parallel fan-out in the old system |
| D-2 | What makes work redo | version or upstream versions changed, or a failed attempt below max | grill A-30: a changed model reprocesses only affected stages (and their downstream) |
| D-3 | Per-date stages | a separate `date_runs` table and `DateStage` | grill A-43: dedup and stitching run per date once all pages of the date are ready |
| D-4 | Stage order | no global order list; each stage declares `depends_on` | eligibility follows dependencies; orchestrator (029) runs stages in topological order |
| D-5 | Claim protocol | short claim transaction, committed before `handle` | GPU stages run for minutes per item; no long open transactions |
| D-6 | Attempts | counted at claim, reset on version change | a crash still counts; a new version gets fresh attempts |
| D-7 | Stage hooks | `preflight`/`open`/`close`/`on_final_failure`, `concurrency`, `models_needed`, `version_for` | needed by GPU stages (load once per run), render quarantine (005), and per-page versions (010) |

## Open questions

None.
