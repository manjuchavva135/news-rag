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
