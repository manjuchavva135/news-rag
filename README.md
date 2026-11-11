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

## Local models (GPU)

Model profiles live in `models.yaml`. Only one big model is on the GPU at a time: every GPU user
takes the file lock `{raw_root}/.gpu.lock`, and a second one waits. vLLM runs from its own venv
`envs/vllm/` and listens on 127.0.0.1 only.

Create the vLLM venv once (also installs FlashInfer's precompiled kernels, since the host has no
`nvcc`). It does nothing if a current vLLM is already there:

```bash
uv run newsrag models install
```

Download weights into `{raw_root}/hf-cache` (uses `HF_TOKEN` from `.env`):

```bash
uv run newsrag models download qwen3-30b-a3b
```

Check that every profile fits the 96 GB GPU:

```bash
uv run newsrag models fit
```

Send one test request (starts and stops the model unless a `models serve` of it is running):

```bash
uv run newsrag models smoke qwen3-30b-a3b
```

Keep a model running for other jobs to reuse (Ctrl-C stops it). Logs go to
`{raw_root}/logs/vllm-<profile>-<time>.log`:

```bash
uv run newsrag models serve qwen3-30b-a3b
```

Who holds the GPU:

```bash
uv run newsrag models status
```

From Python, `newsrag.models.use("<profile>")` gives a client with `chat()` / `chat_json()`
(or an embedder with `embed()`), and `newsrag.models.gpu_lock("<holder>")` takes the lock for
any other in-process GPU user.

## Run the pipeline

One command processes everything that is not done yet:

```bash
uv run newsrag run
```

It requeues `running` rows older than 2 h, unpacks new archives from `archive_dir`, registers
their pages (skipped when the unpacked archives did not change), then runs every stage in
dependency order, one pass per stage, so each model loads at most once per pass and not at all
for a stage with nothing to do. `editions build` + `editions apply` run after `folio`, and
`aliases build` after `enrich_core`, only when their input changed. The order repeats until a
cycle finds no work; an unfiltered run that reaches `index` ends with `index validate`.

A new year's archive is picked up by the same command: only its pages run, plus the date stages
of the dates that gained pages. Interrupted runs resume where they stopped.

```bash
uv run newsrag run --dry-run                                  # eligible items per stage
uv run newsrag run --pages-file data/dev-sample.txt --until group_final
uv run newsrag run --dates 2024-12-05,2024-12-06 --until index
uv run newsrag run --only chunk,index                         # one pass each, no corpus steps
uv run newsrag run --tag full
```

Each run writes `<reports_dir>/runs/<UTC timestamp>.json`: per-stage processed / failed /
seconds, archives unpacked, pages registered, models loaded, permanently failed items and the
final `stage status`. Ctrl-C (or SIGTERM) stops claiming new items, lets items in flight finish
(up to `run_stop_grace_s`, 10 min), writes the run file with `interrupted: true` and exits 130.
A stage that cannot run at all (preflight, model start, stores down) stops the run with exit 1;
item failures never do. When `data/editions.yaml` changes the run says so: commit it.

## Development

```bash
uv run ruff check . && uv run ruff format --check .
```

```bash
uv run pytest -q -m "not gpu and not data and not stores"
```

Test markers: `gpu` (needs the GPU), `data` (needs extracted archive data under `raw_root`),
`stores` (needs the docker stores running).
