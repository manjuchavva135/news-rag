# 001: Project foundation (repo, config, CLI, tests)

**Status:** ready
**Mode:** solo
**Runs:** alongside

## Summary

Creates the skeleton of the new main codebase `/home/admin1/news-rag`: a Python 3.12 package managed by
uv, one central config module, a `newsrag` command line, test and lint setup, and a `CLAUDE.md` that
fixes the conventions every later ticket follows. Nothing processes data yet. Every other ticket
builds on this layout.

## Functional requirements

- **FR-1** - `pyproject.toml` defines package `newsrag` (src layout: `src/newsrag/`), `requires-python = ">=3.12,<3.13"`, and a console script `newsrag = newsrag.cli:app`. `uv sync` creates `.venv` with Python 3.12 (uv downloads the interpreter; system Python 3.14 is not used).
- **FR-2** - `src/newsrag/config.py` exposes a frozen `Settings` object `settings` with at least these fields and defaults:
  - `archive_dir` = `/home/admin1/data` (input RARs, read-only)
  - `raw_root` = `/home/admin1/raw`
  - `source_dir` = `{raw_root}/source` (extracted PDFs)
  - `images_dir` = `{raw_root}/images` (rendered PNGs)
  - `work_dir` = `{raw_root}/work` (stage outputs)
  - `reports_dir` = `{raw_root}/reports`
  - `gpu_lock_path` = `{raw_root}/.gpu.lock`
  - `postgres_dsn` = `postgresql://newsrag:newsrag@localhost:5442/newsrag`
  - `qdrant_url` = `http://localhost:6343`
  - `opensearch_url` = `http://localhost:9210`
  - `render_dpi` = `300`
  - `repo_root` = the checkout that contains the running package (`Path(newsrag.__file__).resolve().parents[2]`)
  - `env_file` = `{repo_root}/.env` if it exists, else `/home/admin1/news-rag/.env` (so worker worktrees, which have no `.env`, use the main checkout's file)
  - `pipeline_file` = `{repo_root}/pipeline.yaml`
  - `hf_token` (secret; read from `HF_TOKEN` or `NEWSRAG_HF_TOKEN`), `sarvam_api_key` (secret; read from `SARVAM_API_KEY` or `NEWSRAG_SARVAM_API_KEY`)
  - `hf_home` = `{raw_root}/hf-cache`, `logs_dir` = `{raw_root}/logs`
- **FR-3** - Every setting can be overridden by an environment variable `NEWSRAG_<FIELD_UPPER>` (e.g. `NEWSRAG_RAW_ROOT=/tmp/x`); the two secrets also accept their plain names (`HF_TOKEN`, `SARVAM_API_KEY`). Precedence, highest first: real environment → `env_file` → `pipeline_file` (a committed YAML of non-secret pipeline choices such as model names and thresholds; missing file = no values) → code default. Derived paths (`source_dir`, `hf_home` etc.) follow an overridden `raw_root` unless they are overridden themselves.
- **FR-4** - `uv run newsrag --help` lists the command groups. `uv run newsrag config show` prints every setting as `name = value`, one per line, sorted by name, with every secret field (typed `SecretStr`: `hf_token`, `sarvam_api_key`, and any later secret) shown as `***` when set and `(unset)` when not. Non-secret fields are never masked (e.g. `chunk_max_tokens` prints its number).
- **FR-5** - `.gitignore` excludes `.venv/`, `.env`, `__pycache__/`, `*.egg-info`, `.pytest_cache/`, `.ruff_cache/`. Ticket `evidence/` folders are NOT ignored (they are committed).
- **FR-6** - pytest is configured with markers `gpu` (needs the GPU), `data` (needs extracted archive data under `raw_root`) and `stores` (needs the docker stores running). `uv run pytest -q -m "not gpu and not data and not stores"` runs the unit tests only.
- **FR-7** - `CLAUDE.md` in the repo root documents: the repo is the main dev and prod codebase; `/home/admin1/RAG` is old, read-only reference and must never be modified; the layout (`src/newsrag/<module>`, `src/newsrag/stages/<stage>.py`, `tests/`, `docker/`, `specs/`); "never hardcode a path or knob, add it to `config.py`"; the test markers; that `specs/grill/newspaper-rag-v2.md` is the requirements record.
- **FR-8** - `README.md` explains setup in commands: install uv without sudo (`curl -LsSf https://astral.sh/uv/install.sh | sh`), `uv sync`, `uv run newsrag config show`, and lists the one-time user prerequisites: `sudo apt install unrar poppler-utils`, Docker Engine + compose plugin, accepting the `bodhan-ai/indic-ocr` licence on Hugging Face and putting `HF_TOKEN=...` in `/home/admin1/news-rag/.env`. `.env.example` (committed) lists `HF_TOKEN=` and `SARVAM_API_KEY=` with one comment line each.
- **FR-9** - `settings.subprocess_env() -> dict[str, str]` returns a copy of `os.environ` plus `HF_TOKEN`, `HF_HOME` (= `hf_home`) and `SARVAM_API_KEY` when set. Every later subprocess (vLLM, indic-ocr worker) is started with it. Importing config never writes to `os.environ`.
- **FR-10** - CLI structure: each module that adds commands exposes its own `typer.Typer()` named `app` in `src/newsrag/<module>/cli.py`; `src/newsrag/cli.py` only registers sub-apps from a list (`app.add_typer(...)`). Settings: all fields live in `src/newsrag/config.py`, grouped under one comment header per module (`# --- stores ---`, `# --- ocr ---` …) so parallel tickets edit different blocks. Where a later ticket says it adds a command group to `src/newsrag/cli.py`, it means: create `src/newsrag/<module>/cli.py` and add one registration line in `src/newsrag/cli.py`; where it adds a setting, it adds it to its module's block in `config.py` (even if `config.py` is not listed in its Affected areas). `CLAUDE.md` states both conventions plus: "a flag that marks a doubtful item starts with `low_confidence:`; other flags are informational".
- **FR-11** - Unknown keys are ignored with one warning line on stderr (`ignoring unknown setting <key> from <source>`), never an error: `extra="ignore"` for env, `.env` and `pipeline.yaml` (other tickets add keys over time). `env_file` and `pipeline_file` can be overridden only from the real environment (`NEWSRAG_ENV_FILE`, `NEWSRAG_PIPELINE_FILE`); `/dev/null` means "no file".
- **FR-12** - `pipeline.yaml` is committed by this ticket as an empty mapping with a header comment. Helper `newsrag.config.set_pipeline_key(key, value)` rewrites one key in it, keeping the other keys, their order and comments (ruamel.yaml); every ticket that writes `pipeline.yaml` (007, 014, 026) uses it.

## Non-functional requirements

- **NFR-1** - `uv run ruff check .` and `uv run ruff format --check .` pass with ruff's default rules plus `I` (import sorting); line length 100.
- **NFR-2** - Importing `newsrag.config` has no side effects other than reading env and `.env` (no directories created, no network).

## Affected areas

- `pyproject.toml` - package, deps, scripts, ruff and pytest config (new)
- `uv.lock` - (new)
- `src/newsrag/__init__.py`, `src/newsrag/config.py`, `src/newsrag/cli.py` - (new)
- `tests/test_config.py`, `tests/test_cli.py` - (new)
- `CLAUDE.md`, `README.md`, `.gitignore`, `.env.example` - (new)
- `pipeline.yaml` (new, committed); `pyproject.toml` - `pydantic-settings`, `pyyaml`, `ruamel.yaml` (changed)

## Don't touch

- `/home/admin1/RAG/**` - old system, read-only reference.
- `/home/admin1/data/**` - the input archives.
- `specs/**` - except this ticket's `results.md` and `evidence/`.

## Out of scope

- Docker stores (ticket 002), the ledger (003), any data processing (004+).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Where is the code? | `/home/admin1/news-rag`, main dev+prod codebase | grill D-4 |
| D-2 | Python version / tool | Python 3.12 via uv | grill A-14 (3.14 too new for torch/vLLM) |
| D-3 | CLI library | Typer | small, typed subcommand groups; later tickets add groups (`stage`, `ingest`, `models`, ...) |
| D-4 | Settings library | pydantic-settings with prefix `NEWSRAG_` | env + `.env` support with typed fields |
| D-5 | Store ports | Postgres 5442, Qdrant 6343, OpenSearch 9210 | avoid the old system's default ports; ticket 027 runs the old baseline stores on other ports |
| D-6 | Data locations | RARs stay in `/home/admin1/data`; everything derived goes under `/home/admin1/raw` | grill A-4, A-21 |
| D-7 | Secrets | `SecretStr` fields with plain-name aliases; passed to subprocesses via `subprocess_env()` | huggingface_hub, vLLM and the indic-ocr worker read `HF_TOKEN`/`HF_HOME` from their environment |
| D-8 | `.env` in worktrees | fall back to the main checkout's `.env` | workers build in their own worktree with no `.env` |
| D-9 | `pipeline.yaml` | committed non-secret choices (models, thresholds) below env in precedence | bake-offs (014, 026) write their winners there |
| D-10 | uv | installed by the user or worker with the official no-sudo script | not present on this machine (grill F-8) |

## Open questions

None.
