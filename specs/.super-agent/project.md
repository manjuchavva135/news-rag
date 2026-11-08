# Project facts for the Super Agent

Every agent reads this file. Keep it short and exact.

## Basics

- Name: news-rag (Sakshi newspaper RAG, phase 1: data processing into stores)
- Stack: Python 3.12 package `newsrag` (src layout) managed by uv; Typer CLI `newsrag`; Postgres 16 + Qdrant 1.16.1 + OpenSearch 2.17.1 (ICU) in Docker; local models via vLLM and sentence-transformers on one RTX PRO 6000 (96 GB)
- Requirements record: `specs/grill/newspaper-rag-v2.md`
- Main branch: main
- Install: `uv sync` (uv itself: `curl -LsSf https://astral.sh/uv/install.sh | sh`, no sudo)
- Start: there is no app server. Commands run as `uv run newsrag <group> <command>`. Stores: `uv run newsrag stores up` (from ticket 002 on)
- Build: none (Python package)
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Tests: `uv run pytest -q -m "not gpu and not data and not stores"` (unit); `-m stores` needs the stores up; `-m gpu` / `-m data` need the GPU / extracted archives

## Ports

There is no web app. Fixed shared services (one instance for the whole machine, never start a second copy):

| Service | Port |
|---------|------|
| Postgres (main stores) | 5442 |
| Qdrant (main stores) | 6343 / 6344 |
| OpenSearch (main stores) | 9210 / 9310 |
| Baseline stores (ticket 027) | 5452 / 6353 / 9220 |
| vLLM model servers | 8100–8199 (picked automatically) |

Small local web pages only in ticket 009 (`newsrag ocr check serve`):

| Who | Port |
|-----|------|
| Main checkout (checker) | 8700 |
| Worker 1 / 2 / 3 / ... | 8701 / 8702 / 8703 / ... |
| Reproducer | 8790 |

## Test accounts

None — the phase-1 code has no login or user accounts (grill A-18).

## Per-worktree setup

- Run `uv sync` in the worktree.
- Do not copy `.env`: the config falls back to `/home/admin1/news-rag/.env` automatically (ticket 001 FR-3); secrets live only there.
- Data is shared and lives outside the repo: archives `/home/admin1/data` (read-only, never modify), everything derived under `/home/admin1/raw` (source PDFs, images, work artifacts, reports, hf-cache).
- Tests that touch Postgres use a throw-away schema (ticket 003 NFR-2); never write test data into the `ledger` or `warehouse` schemas.

## Notes

- **One GPU, one big model at a time.** Every GPU use goes through `newsrag.models.use()` / `gpu_lock()` (ticket 006); a second GPU job waits. GPU tickets therefore effectively run one at a time even if the board allows parallel work.
- `/home/admin1/RAG` is the old system: read-only reference, never modify it.
- No `psql` on the host: use `uv run newsrag db sql "<query>"` (ticket 002).
- One-time user prerequisites before ticket 002+: `sudo apt install unrar poppler-utils`, Docker Engine + compose plugin with admin1 in the `docker` group, Hugging Face licences accepted (indic-ocr, Gemma) and `HF_TOKEN=` in `/home/admin1/news-rag/.env`; old export bundle copied to `/home/admin1/raw/baseline/` before ticket 027.
- Browser checks: only tickets 009 (review page) and 030 (static report) use `agent-browser`.
- Node 22 and `agent-browser` are installed via nvm in the user's home: run `source ~/.nvm/nvm.sh` first if `node`/`agent-browser` is not on PATH. On this machine Chromium needs the sandbox off: open pages with `agent-browser --args "--no-sandbox" open <url>` (later commands in the same session need no flag).
