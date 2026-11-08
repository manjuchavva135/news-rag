# 002: Backend stores (docker compose + clients) - Evaluation

## Setup

- Prerequisite (user, one-time): Docker Engine + compose plugin installed and `admin1` can run `docker ps` without sudo.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0
- `uv run pytest -q -m stores` exits 0 (stores running; at least `test_ping_all_true` is marked `stores`; markers are per test, so `test_missing_docker` runs in the unit set)
- `uv run pytest -q tests/test_stores.py -k "compose_plugin_missing or daemon_unreachable or up_timeout"` exits 0 (fakes for each FR-2 failure case)

## Acceptance checks

### E-1: Stores come up and report ok (covers FR-1, FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag stores up`
  2. `uv run newsrag stores status; echo "exit=$?"`
- **Expected:** step 2 prints `postgres: ok`, `qdrant: ok`, `opensearch: ok` and `exit=0`.

### E-2: Ports are the new ones (covers FR-1)

- **Type:** API
- **Steps:**
  1. `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:6343/healthz`
  2. `curl -s http://localhost:9210 | grep -o '"number" : "[0-9.]*"'`
  3. `uv run newsrag db sql "select 1"`
- **Expected:** `200`; `"number" : "2.17.1"`; `1`.

### E-3: ICU plugin present (covers NFR-2)

- **Type:** API
- **Steps:**
  1. `curl -s http://localhost:9210/_cat/plugins`
- **Expected:** output contains `analysis-icu`.

### E-4: Python clients ping (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run python -c "from newsrag.stores import pg, qdrant, opensearch; from newsrag.stores import ping_all; print(ping_all())"` 
- **Expected:** prints a mapping where all three values are `True`.

### E-5: Status fails when a store is down (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `docker compose -p newsrag -f docker/docker-compose.yml stop qdrant`
  2. `time (uv run newsrag stores status; echo "exit=$?")`
  3. `uv run newsrag stores up`
- **Expected:** step 2 prints `qdrant: down` and `exit=1`, real time < 10 s.

## Edge cases

### E-6: Data survives restart (covers NFR-1, FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag db sql "create table if not exists e6_probe(x int)" && uv run newsrag db sql "insert into e6_probe values (42)"`
  2. `uv run newsrag stores down && uv run newsrag stores up`
  3. `uv run newsrag db sql "select x from e6_probe" && uv run newsrag db sql "drop table e6_probe"`
- **Expected:** step 3 prints `42`.

### E-7: Missing docker message (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_stores.py::test_missing_docker` (the test makes `shutil.which("docker")` return `None`, invokes `stores up` through Typer's `CliRunner`, and asserts exit code 2 and the exact message `docker not found: install Docker Engine and the compose plugin (see README)`).
- **Expected:** 1 passed.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-2 |
| FR-2 | E-1, E-6 |
| FR-3 | E-1, E-5 |
| FR-4 | E-4 |
| FR-5 | E-7 |
| NFR-1 | E-6 |
| NFR-2 | E-3 |
| NFR-3 | E-5 |
| FR-6 | E-2, E-6 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-5 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (command logs) saved in `evidence/` and listed in `results.md`
