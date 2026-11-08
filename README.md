# news-rag

Sakshi newspaper RAG, phase 1: turns the newspaper archives into searchable stores
(Postgres, Qdrant, OpenSearch). Requirements: `specs/grill/newspaper-rag-v2.md`.

## One-time prerequisites (user, needs sudo)

```bash
sudo apt install unrar poppler-utils
```

- Docker Engine + the compose plugin, with `admin1` in the `docker` group
  (`sudo usermod -aG docker admin1`, then log out and back in) so `docker ps` works without sudo.
- Accept the `bodhan-ai/indic-ocr` licence (and the Gemma licence) on Hugging Face, then put your
  token in `/home/admin1/news-rag/.env`:

  ```bash
  HF_TOKEN=hf_...
  ```

  `.env.example` lists every secret. `.env` is never committed. Worker worktrees have no `.env`;
  they fall back to `/home/admin1/news-rag/.env` automatically.

## Setup

Install uv (no sudo):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
```

Create the virtualenv (uv downloads Python 3.12; the system Python is not used):

```bash
uv sync
```

Check the settings:

```bash
uv run newsrag config show
```

## Settings

All settings live in `src/newsrag/config.py`. Override any of them with `NEWSRAG_<NAME>`
(for example `NEWSRAG_RAW_ROOT=/tmp/x`). Precedence, highest first: real environment →
`.env` → `pipeline.yaml` (committed, non-secret pipeline choices) → code default.
Paths under `raw_root` follow an overridden `raw_root`.

## Start the stores

Postgres (port 5442), Qdrant (6343, gRPC 6344) and OpenSearch with the ICU plugin (9210,
transport 9310) run in Docker under compose project `newsrag`. They use the host network and
ports that don't clash with the old system (5432 / 6333 / 9200).

You need Docker plus the compose plugin, and `admin1` must be in the `docker` group. If you get
`cannot talk to the docker daemon: permission denied`, run
`sudo usermod -aG docker admin1`, then log out and back in.

```bash
uv run newsrag stores up
```

`stores up` builds the OpenSearch image the first time, then waits up to 180 s
(`stores_up_timeout_s`) for all three stores to answer.

```bash
uv run newsrag stores status
```

```bash
uv run newsrag stores down
```

`stores down` stops the containers. The data stays in the named volumes `newsrag-pg`,
`newsrag-qdrant` and `newsrag-os`. Containers restart on their own after a reboot
(`restart: unless-stopped`).

There is no `psql` on the host. Run SQL with:

```bash
uv run newsrag db sql "select 1"
```

## Development

```bash
uv run ruff check . && uv run ruff format --check .
```

```bash
uv run pytest -q -m "not gpu and not data and not stores"
```

Test markers: `gpu` (needs the GPU), `data` (needs extracted archive data under `raw_root`),
`stores` (needs the docker stores running).
