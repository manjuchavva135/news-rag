# news-rag

This repo (`/home/admin1/news-rag`) is the main dev **and** prod codebase for the Sakshi newspaper
RAG, phase 1: process the newspaper archives into stores.

- Requirements record: `specs/grill/newspaper-rag-v2.md`. Tickets: `specs/tickets/`.
- `/home/admin1/RAG` is the OLD system: read-only reference. Never modify anything under it.
- `/home/admin1/data` holds the input archives: read-only. Everything derived goes under
  `/home/admin1/raw` (`settings.raw_root`).

## Layout

- `src/newsrag/<module>/` - one package per module (e.g. `stores`, `models`, `ocr`)
- `src/newsrag/stages/<stage>.py` - one file per pipeline stage
- `src/newsrag/config.py` - all settings; `src/newsrag/cli.py` - the `newsrag` command line
- `tests/` - pytest tests
- `docker/` - compose file and images for the stores
- `specs/` - grill record, tickets, board

## Conventions

- **Never hardcode a path or knob: add it to `config.py`.** Fields are grouped under one comment
  header per module (`# --- stores ---`, `# --- ocr ---` …); a ticket adds its settings to its own
  module's block so parallel tickets edit different blocks. Secrets are `SecretStr` fields.
- Settings are overridden with `NEWSRAG_<FIELD_UPPER>`. Precedence: real environment → `.env` →
  `pipeline.yaml` → code default. Write `pipeline.yaml` only with
  `newsrag.config.set_pipeline_key(key, value)`.
- Subprocesses (vLLM, OCR workers) are started with `env=settings.subprocess_env()`; never write
  secrets into `os.environ`.
- **CLI:** each module that adds commands exposes its own `typer.Typer()` named `app` in
  `src/newsrag/<module>/cli.py`; `src/newsrag/cli.py` only registers sub-apps from its `SUB_APPS`
  list (`app.add_typer(...)`), one line per group.
- **Flags:** a flag that marks a doubtful item starts with `low_confidence:`; other flags are
  informational.
- No `psql` on the host: use `uv run newsrag db sql "<query>"` (from ticket 002 on).

## How to add a stage

1. Create `src/newsrag/stages/<stage>.py` with a subclass of `PageStage` (one page per item) or
   `DateStage` (one print date per item; runs once every non-quarantined page of the date is
   through its page dependencies) from `newsrag.stages.base`.
2. Set `name`, `depends_on` (stage names; a page stage may only depend on page stages) and
   `version`. **The version ends with the config hash**:
   `version = versioned("<model>@<prompt-rev>", ["setting_a", ...], [prompt_file, ...])`, i.e.
   `<prefix>-<config_hash(settings_names, files)>`. List every setting and every prompt/data file
   the stage reads, so changing one reruns this stage and everything downstream. If the version
   depends on files that may be missing, make it a `functools.cached_property`: importing the
   registry must not touch the GPU, the network or missing files.
3. Implement `handle(page)` / `handle(pub_date, pages)` returning
   `StageResult(artifact_path, detail)`. Write outputs to
   `artifact_path(stage, version, key)` with `write_json_atomic(...)`. Raise on failure: the
   runner records it and retries up to `max_attempts`.
4. Optional: `concurrency` (threads), `models_needed`, `version_for(item)` (per-item versions),
   `preflight()` (raise `StageUnavailable` when the stage can't run; checked before any DB
   access), `open()` / `close()` (once per run: load a model, take the GPU lock),
   `on_final_failure(item, error)`.
5. Register it in `src/newsrag/stages/registry.py`: `REGISTRY["<name>"] = MyStage()`.
6. Add any SQL the stage needs as the next `src/newsrag/ledger/sql/NNN_*.sql` (unqualified table
   names; they land in `settings.ledger_schema`). Run with `uv run newsrag stage run <name>`;
   inspect with `stage status`, `stage failures <name>`, `stage reset <name> --page/--date/--all`.
7. Tests: use the `ledger` fixture (throw-away `test_<random>` schema) and the `registry`
   fixture (empty REGISTRY), and mark DB tests `@pytest.mark.stores`.

## Commands

- Install: `uv sync`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Unit tests: `uv run pytest -q -m "not gpu and not data and not stores"`

## Test markers

- `gpu` - needs the GPU
- `data` - needs extracted archive data under `raw_root`
- `stores` - needs the docker stores running (`uv run newsrag stores up`)

Tests that touch Postgres use a throw-away schema; never write test data into `ledger` or
`warehouse`.
