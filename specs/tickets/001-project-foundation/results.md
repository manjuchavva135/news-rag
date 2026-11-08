# 001: Project foundation - Results

Built on branch `ticket-001` in the main checkout (no worker worktree). uv 0.13.7 was installed
with the official no-sudo script (D-10). Python 3.12.16 venv, ruff 0.17.4, pytest 9.2.

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| `uv sync` | PASS | `evidence/auto-uv-sync.log` |
| `uv run ruff check .` | PASS | `evidence/auto-ruff-check.log` |
| `uv run ruff format --check .` | PASS | `evidence/auto-ruff-format.log` |
| unit tests (`-m "not gpu and not data and not stores"`) | PASS, 15 passed | `evidence/auto-pytest-unit.log` |
| E-1 Python 3.12 + console script | PASS: `(3, 12)`, help lists `config` | `evidence/E-1.log` |
| E-2 defaults, sorted | PASS | `evidence/E-2.log` |
| E-3 env override + derived paths | PASS | `evidence/E-3.log` |
| E-4 .env precedence | PASS: 200, then 150; `.env` restored (sha256 checked) | `evidence/E-4.log` |
| E-5 secrets masked / unset | PASS: `***`, 0 secret matches, `(unset)` | `evidence/E-5.log` |
| E-6 docs and ignore rules | PASS | `evidence/E-6.log` |
| E-7 markers | PASS | `evidence/E-7.log` |
| E-8 import has no side effects | PASS: `clean` | `evidence/E-8.log` |
| E-9 line length 100 | PASS | `evidence/E-9.log` |
| E-10 five config tests | PASS: 5 passed | `evidence/E-10.log` |
| E-11 conventions | PASS: 1, 1 | `evidence/E-11.log` |

In `E-2.log` the real `hf_token` line from `/home/admin1/news-rag/.env` is replaced by a placeholder.
It printed `***`.

## Notes for later tickets

- **`config` group lives in `src/newsrag/config_cli.py`.** `config.py` is a module, not a package,
  so it can't have `config/cli.py`. `cli.py` only registers sub-apps from its `SUB_APPS` list.
- **ruff 0.17 changes:** it formats Markdown code blocks and enables more default rules
  (e.g. TRY004). `specs/` is excluded in `[tool.ruff] extend-exclude` so `ruff format .` never
  rewrites the requirements record.
- **Unknown keys:** in the real environment only `NEWSRAG_*` names are checked (other variables
  aren't ours). In `.env` every key that isn't a setting gets a warning. `NEWSRAG_ENV_FILE` /
  `NEWSRAG_PIPELINE_FILE` found in `.env` or `pipeline.yaml` are ignored with a warning.
- **Empty secrets** (`HF_TOKEN=` as in `.env.example`) count as unset.
- **`set_pipeline_key`** turns the committed `{}` into block style on the first write and keeps the
  header comment (`test_set_pipeline_key_on_committed_empty_file`).
- **No ticket worktree or reviewer subagent was used.** Status in `rd.md`/`BOARD.md` is left for
  the merger.

## Out of scope / don't touch

No stores, ledger or data processing were built. Nothing under `/home/admin1/RAG`,
`/home/admin1/data` or `specs/**` changed apart from this file and `evidence/`.
