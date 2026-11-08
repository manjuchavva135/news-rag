# 002: Backend stores - Results

Built in the main checkout on branch `ticket-001`, on top of ticket 001's uncommitted files. No
worker worktree was used. Docker 29.1.3 + Compose 2.40.3 come from Ubuntu 26.04's own packages
(`docker.io`, `docker-compose-v2`, `docker-buildx`). This session started before `admin1` joined
the `docker` group, so every live check ran under `sg docker -c "..."`.

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| `uv run ruff check .` | PASS | `evidence/auto-ruff-check.log` |
| `uv run ruff format --check .` | PASS | `evidence/auto-ruff-format.log` |
| unit tests | PASS, 25 passed | `evidence/auto-pytest-unit.log` |
| `-m stores` | PASS, 3 passed (run with `-W error::UserWarning`) | `evidence/auto-pytest-stores.log` |
| fakes: compose plugin missing / daemon unreachable / up timeout | PASS, 3 passed | `evidence/auto-pytest-docker-fakes.log` |
| E-1 stores up + status | PASS: three `ok`, `exit=0`; containers `newsrag-*-1` | `evidence/E-1.log` |
| E-2 new ports | PASS: `200`, `"number" : "2.17.1"`, `1` | `evidence/E-2.log` |
| E-3 ICU plugin | PASS: `analysis-icu 2.17.1` | `evidence/E-3.log` |
| E-4 Python clients | PASS: all `True` | `evidence/E-4.log` |
| E-5 status with qdrant stopped | PASS: `qdrant: down`, `exit=1`, real 1.6 s | `evidence/E-5.log` |
| E-6 data survives down/up | PASS: `42`; volumes `newsrag-pg/-qdrant/-os` | `evidence/E-6.log` |
| E-7 missing docker | PASS, 1 passed | `evidence/E-7.log` |

## Notes for later tickets

- **New settings** in the `# --- stores ---` block of `config.py`:
  - `compose_file` (follows `repo_root`)
  - `stores_up_timeout_s` (180)
  - `stores_ping_timeout_s` (2)
  - `pg_pool_max_size` (10)
- **Command groups:** `stores` and `db` both live in `src/newsrag/stores/cli.py`, as `app` and
  `db_app`. Each has one registration line in `cli.py`.
- **qdrant-client is pinned `>=1.16,<1.17`.** The newest client (1.20) warns that it is
  incompatible with the 1.16.1 server. Raise the pin together with the server image.
- **Pings use `httpx` for Qdrant and OpenSearch, and a direct `psycopg.connect` for Postgres**,
  not the pooled clients. Each is bounded by the timeout, so `stores status` stays fast while a
  store is down.
- **Volumes have fixed names** (`name:` in compose), so they are `newsrag-pg`, `newsrag-qdrant`
  and `newsrag-os` without a project prefix.
- **The Dockerfile keeps the ICU zip in `/tmp`.** Deleting it in the same `RUN` would fail: the
  file is root-owned and the build runs as the `opensearch` user.
- **OpenSearch's cluster health is `yellow`**, which is normal for a single node and counts as `ok`.

## Out of scope / don't touch

No ledger tables, indexes, collections or baseline stores. The ICU zip was copied from
`/home/admin1/RAG/docker/`, which was not modified. No old-system containers or volumes exist on
this machine, and none were touched.
