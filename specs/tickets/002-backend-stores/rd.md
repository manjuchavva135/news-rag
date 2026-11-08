# 002: Backend stores (docker compose + clients)

**Status:** draft
**Mode:** solo
**Runs:** waits for 001: needs package, config and CLI

## Summary

Brings up the three stores the new pipeline writes to (Postgres, Qdrant, OpenSearch with the ICU
plugin for Telugu) with one command, on ports that don't clash with the old system, plus thin Python
clients and a status command. Postgres also holds the pipeline ledger (ticket 003).

## Functional requirements

- **FR-1** - `docker/docker-compose.yml` (compose project name `newsrag`; no `container_name` keys, so names never clash with the old system's `rag-*` containers) defines three services, all with `network_mode: host` (the old setup found bridge ports blocked on this network; see `/home/admin1/RAG/docker/docker-compose.yml`):
  - `postgres`: image `postgres:16`, listening on port **5442** (`command: postgres -p 5442`), user/password/db `newsrag`/`newsrag`/`newsrag`, named volume `newsrag-pg`.
  - `qdrant`: image `qdrant/qdrant:v1.16.1`, HTTP port **6343**, gRPC **6344** (via `QDRANT__SERVICE__HTTP_PORT` / `QDRANT__SERVICE__GRPC_PORT`), named volume `newsrag-qdrant`.
  - `opensearch`: built from `docker/opensearch.Dockerfile` = `opensearchproject/opensearch:2.17.1` plus the `analysis-icu` plugin installed from the local zip `docker/analysis-icu-2.17.1.zip` (copy it from `/home/admin1/RAG/docker/analysis-icu-2.17.1.zip`); image tag `newsrag-opensearch:2.17.1-icu` (never the old `rag-opensearch` tag); single node, security plugin disabled with `DISABLE_INSTALL_DEMO_CONFIG=true` (OpenSearch 2.12+ refuses to start otherwise), `http.port=9210`, `transport.port=9310`, heap `-Xms4g -Xmx4g`, named volume `newsrag-os`.
- **FR-2** - `uv run newsrag stores up` runs `docker compose -p newsrag -f <compose_file> up -d --build` (setting `compose_file`, default `{repo_root}/docker/docker-compose.yml`, so it works from any directory) and then waits up to 180 s until all three answer; `uv run newsrag stores down` runs `... stop` (volumes kept). Services use `restart: unless-stopped`. Failures:
  - docker missing → FR-5; `docker compose version` fails → exit 2 `docker compose plugin not found: install docker-compose-plugin`;
  - `docker info` fails (daemon down / permission denied) → exit 2 `cannot talk to the docker daemon: <first stderr line>` (README says: add admin1 to the `docker` group);
  - `compose up` exits non-zero → exit 1 and print the last 20 lines of its output;
  - not all stores answer after 180 s → exit 1 and print the `stores status` lines.
  `stores down` runs the same docker checks. Settings `postgres_dsn` etc. may point at a remote stack; the compose file's ports do not follow them.
- **FR-3** - `uv run newsrag stores status` prints exactly three lines `postgres: ok|down`, `qdrant: ok|down`, `opensearch: ok|down` and exits 0 only when all three are `ok`, else exits 1.
- **FR-4** - `src/newsrag/stores/pg_store.py`, `qdrant_store.py`, `os_store.py` each define a lazy singleton `client()` and `ping(timeout_s=2.0) -> bool` that catches every exception and returns False. `ok` means: postgres `select 1` succeeds; qdrant `GET /healthz` is 200; opensearch `GET /_cluster/health` status is not `red`. `newsrag.stores` exports the functions `pg()`, `qdrant()`, `opensearch()` (returning the clients: a psycopg 3 `ConnectionPool`, a `QdrantClient`, an `OpenSearch`) and `ping_all() -> dict[str, bool]` (keys `postgres`, `qdrant`, `opensearch`) used by `stores status`.
- **FR-5** - If `docker` is not installed, `newsrag stores up` exits 2 and prints `docker not found: install Docker Engine and the compose plugin (see README)`.
- **FR-6** - `uv run newsrag db sql "<query>"` runs one SQL statement against `settings.postgres_dsn` in autocommit mode, prints result rows tab-separated without a header (nothing for statements that return no rows; NULL printed as `NULL`), and exits 1 with the error on failure. (Ticket 027 adds a `--dsn baseline` option later.) All later evals use it instead of `psql` (no PostgreSQL client is installed on the host).

## Non-functional requirements

- **NFR-1** - Store data survives `stores down` + `stores up` (named volumes).
- **NFR-2** - `opensearch` reports the `analysis-icu` plugin in `GET /_cat/plugins`.
- **NFR-3** - `stores status` finishes in < 10 s when any store is down.

## Affected areas

- `docker/docker-compose.yml`, `docker/opensearch.Dockerfile`, `docker/analysis-icu-2.17.1.zip` (new)
- `src/newsrag/stores/__init__.py`, `pg_store.py`, `qdrant_store.py`, `os_store.py` (new)
- `src/newsrag/stores/cli.py` - `stores` and `db` command groups (new); `src/newsrag/cli.py` - register them (changed)
- `src/newsrag/config.py` - `compose_file` in the `# --- stores ---` block (changed)
- `pyproject.toml` - add `psycopg[binary,pool]`, `qdrant-client`, `opensearch-py` (changed)
- `tests/test_stores.py` (new, marked `stores`)
- `README.md` - "Start the stores" section (changed)

## Don't touch

- `/home/admin1/RAG/**` (copy the ICU zip, never move it).
- Any container or volume of the old system.

## Out of scope

- Ledger tables (003), search indexes and collections (024), baseline stores for the old bundle (027).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Which stores | Postgres + Qdrant + OpenSearch (ICU) | grill A-12 |
| D-2 | Ports | 5442 / 6343 (+6344) / 9210 (+9310) | ticket 001 D-5, no clash with old defaults |
| D-3 | Networking | host network | the old repo documents blocked bridge ports here |
| D-4 | Versions | same versions as the old system (postgres 16, qdrant 1.16.1, opensearch 2.17.1) | known to work; ICU zip matches 2.17.1 |
| D-5 | SQL access in checks | `newsrag db sql` instead of `psql` | the host has no `postgresql-client` |
| D-6 | Restart policy | `unless-stopped` | stores come back after a reboot, as in the old setup |

## Open questions

None.
