# 001: Project foundation (repo, config, CLI, tests) - Evaluation

## Setup

- Start: none (CLI only). Run every command from the repo root (the worker's worktree).
- Base URL: n/a
- Test account: n/a
- Prerequisite: uv on PATH (`curl -LsSf https://astral.sh/uv/install.sh | sh`, then `source ~/.local/bin/env`).
- Seed / reset: `uv sync`

## Automated checks

- `uv sync` exits 0
- `uv run ruff check .` exits 0
- `uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Python 3.12 venv and console script (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run python -c "import sys; print(sys.version_info[:2])"`
  2. `uv run newsrag --help`
- **Expected:** step 1 prints `(3, 12)`; step 2 exits 0 and the output contains `config`.

### E-2: Defaults printed (covers FR-2, FR-4)

- **Type:** CLI
- **Steps:**
  1. `env -u NEWSRAG_RAW_ROOT uv run newsrag config show`
- **Expected:** exits 0; output contains the lines `raw_root = /home/admin1/raw`, `source_dir = /home/admin1/raw/source`, `images_dir = /home/admin1/raw/images`, `work_dir = /home/admin1/raw/work`, `postgres_dsn = postgresql://newsrag:newsrag@localhost:5442/newsrag` (local dev credentials are not masked), `qdrant_url = http://localhost:6343`, `opensearch_url = http://localhost:9210`, `render_dpi = 300`; lines are sorted by name.

### E-3: Env override and derived paths (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `NEWSRAG_RAW_ROOT=/tmp/nr-test uv run newsrag config show`
  2. `NEWSRAG_RAW_ROOT=/tmp/nr-test NEWSRAG_IMAGES_DIR=/tmp/imgs uv run newsrag config show`
- **Expected:** step 1 shows `raw_root = /tmp/nr-test` and `source_dir = /tmp/nr-test/source`; step 2 shows `images_dir = /tmp/imgs` and `source_dir = /tmp/nr-test/source`.

### E-4: .env loading and precedence (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `cp .env .env.bak 2>/dev/null; echo "NEWSRAG_RENDER_DPI=200" > .env`
  2. `uv run newsrag config show | grep render_dpi`
  3. `NEWSRAG_RENDER_DPI=150 uv run newsrag config show | grep render_dpi`
  4. `rm .env; mv .env.bak .env 2>/dev/null; true`
- **Expected:** step 2 prints `render_dpi = 200`; step 3 prints `render_dpi = 150`.

### E-5: Secrets masked, plain names accepted, numbers not masked (covers FR-2, FR-3, FR-4)

- **Type:** CLI
- **Steps:**
  1. `HF_TOKEN=hf_secret123 SARVAM_API_KEY=sk_abc uv run newsrag config show`
  2. `env -u HF_TOKEN -u NEWSRAG_HF_TOKEN NEWSRAG_ENV_FILE=/dev/null uv run newsrag config show | grep hf_token`
- **Expected:** step 1 contains `hf_token = ***` and `sarvam_api_key = ***` and neither secret string; step 2 prints `hf_token = (unset)`.

### E-6: Docs and ignore rules (covers FR-5, FR-7, FR-8)

- **Type:** CLI
- **Steps:**
  1. `grep -c "/home/admin1/RAG" CLAUDE.md`
  2. `grep -E "unrar|poppler-utils" README.md`
  3. `grep -E "^\.env$" .gitignore && grep -E "^\.venv/?$" .gitignore`
  4. `grep -c "newspaper-rag-v2.md" CLAUDE.md`
- **Expected:** step 1 prints a number ≥ 1; step 2 prints at least one line; step 3 prints both lines; step 4 prints ≥ 1.

### E-7: Test markers registered (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest --markers`
- **Expected:** output contains `@pytest.mark.gpu`, `@pytest.mark.data` and `@pytest.mark.stores`.

### E-10: Subprocess env and pipeline.yaml precedence (covers FR-3, FR-9)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_config.py -k "subprocess_env or pipeline_yaml_precedence or env_file_fallback or unknown_keys_ignored or set_pipeline_key_keeps_others"`
- **Expected:** 5 tests pass: `subprocess_env()` contains `HF_TOKEN` and `HF_HOME` when set; a key in `pipeline.yaml` is overridden by `.env`, which is overridden by the environment; with no `{repo_root}/.env` the main checkout's path is used.

### E-11: CLI and docs conventions (covers FR-10)

- **Type:** CLI
- **Steps:**
  1. `grep -c "low_confidence:" CLAUDE.md; grep -c "add_typer" src/newsrag/cli.py`
- **Expected:** both ≥ 1.

## Edge cases

### E-8: Import has no side effects (covers NFR-2)

- **Type:** CLI
- **Steps:**
  1. `NEWSRAG_RAW_ROOT=/tmp/nr-noside-$$ uv run python -c "import newsrag.config" && test ! -e /tmp/nr-noside-$$ && echo clean`
- **Expected:** prints `clean`.

### E-9: Lint config (covers NFR-1)

- **Type:** CLI
- **Steps:**
  1. `grep -E "line-length *= *100" pyproject.toml`
- **Expected:** prints one line.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-2 |
| FR-3 | E-3, E-4, E-5, E-10 |
| FR-4 | E-2, E-5 |
| FR-5 | E-6 |
| FR-6 | E-7 |
| FR-7 | E-6 |
| FR-8 | E-6 |
| FR-9 | E-10 |
| FR-10 | E-11 |
| NFR-1 | E-9, automated checks |
| NFR-2 | E-8 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-7, E-10, E-11 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (command logs) saved in `evidence/` and listed in `results.md`
