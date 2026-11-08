import shutil
import subprocess
import time

import pytest
from typer.testing import CliRunner

from newsrag.cli import app
from newsrag.config import settings
from newsrag.stores import cli as stores_cli
from newsrag.stores import os_store, pg_store, qdrant_store

runner = CliRunner()

ALL_OK = {"postgres": True, "qdrant": True, "opensearch": True}


def _done(cmd, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, code, stdout, stderr)


@pytest.fixture
def fake_docker(monkeypatch):
    """docker on PATH; every docker command succeeds unless a test overrides `results`."""
    results: dict[tuple[str, ...], subprocess.CompletedProcess] = {}
    calls: list[list[str]] = []

    def fake_run(cmd):
        calls.append(cmd)
        for prefix, result in results.items():
            if tuple(cmd[: len(prefix)]) == prefix or prefix[-1] in cmd:
                return result
        return _done(cmd)

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(stores_cli, "_run", fake_run)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return results, calls


def _set(monkeypatch, **update):
    monkeypatch.setattr(stores_cli, "settings", settings.model_copy(update=update))


# --- docker failure cases (unit, no docker needed) ---


def test_missing_docker(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = runner.invoke(app, ["stores", "up"])
    assert result.exit_code == 2
    assert (
        "docker not found: install Docker Engine and the compose plugin (see README)"
        in result.output
    )


def test_compose_plugin_missing(fake_docker):
    results, _ = fake_docker
    results[("docker", "compose", "version")] = _done([], 1, stderr="unknown command")
    for command in ("up", "down"):
        result = runner.invoke(app, ["stores", command])
        assert result.exit_code == 2
        assert "docker compose plugin not found: install docker-compose-plugin" in result.output


def test_daemon_unreachable(fake_docker):
    results, _ = fake_docker
    results[("docker", "info")] = _done(
        [], 1, stderr="permission denied while trying to connect to the docker API\nsecond line\n"
    )
    result = runner.invoke(app, ["stores", "up"])
    assert result.exit_code == 2
    assert (
        "cannot talk to the docker daemon: permission denied while trying to connect to the "
        "docker API" in result.output
    )
    assert "second line" not in result.output


def test_compose_up_fails_prints_last_20_lines(fake_docker):
    results, _ = fake_docker
    output = "\n".join(f"line {i}" for i in range(1, 31))
    results[("up",)] = _done([], 1, stdout=output)
    result = runner.invoke(app, ["stores", "up"])
    assert result.exit_code == 1
    assert "line 11" in result.output and "line 30" in result.output
    assert "line 10\n" not in result.output


def test_up_timeout(fake_docker, monkeypatch):
    _set(monkeypatch, stores_up_timeout_s=0.0)
    monkeypatch.setattr(
        stores_cli, "_ping_all", lambda: {"postgres": True, "qdrant": False, "opensearch": True}
    )
    result = runner.invoke(app, ["stores", "up"])
    assert result.exit_code == 1
    assert "postgres: ok\nqdrant: down\nopensearch: ok" in result.output


def test_up_runs_compose_and_waits(fake_docker, monkeypatch):
    _, calls = fake_docker
    answers = iter([{**ALL_OK, "opensearch": False}, ALL_OK])
    monkeypatch.setattr(stores_cli, "_ping_all", lambda: next(answers))
    result = runner.invoke(app, ["stores", "up"])
    assert result.exit_code == 0, result.output
    compose_up = next(c for c in calls if "up" in c)
    assert compose_up == [
        "docker", "compose", "-p", "newsrag", "-f", str(settings.compose_file),
        "up", "-d", "--build",
    ]  # fmt: skip
    assert settings.compose_file == settings.repo_root / "docker" / "docker-compose.yml"


def test_down_stops_and_keeps_volumes(fake_docker):
    _, calls = fake_docker
    result = runner.invoke(app, ["stores", "down"])
    assert result.exit_code == 0, result.output
    assert calls[-1][-1] == "stop"
    assert "-v" not in calls[-1]


def test_status_lines_and_exit_code(monkeypatch):
    monkeypatch.setattr(stores_cli, "_ping_all", lambda: ALL_OK)
    result = runner.invoke(app, ["stores", "status"])
    assert result.exit_code == 0
    assert result.output == "postgres: ok\nqdrant: ok\nopensearch: ok\n"

    monkeypatch.setattr(stores_cli, "_ping_all", lambda: {**ALL_OK, "postgres": False})
    result = runner.invoke(app, ["stores", "status"])
    assert result.exit_code == 1
    assert result.output.splitlines()[0] == "postgres: down"


# --- clients against closed ports (unit) ---


@pytest.fixture
def closed_ports(monkeypatch):
    dead = settings.model_copy(
        update={
            "postgres_dsn": "postgresql://newsrag:newsrag@127.0.0.1:1/newsrag",
            "qdrant_url": "http://127.0.0.1:1",
            "opensearch_url": "http://127.0.0.1:1",
        }
    )
    for module in (pg_store, qdrant_store, os_store, stores_cli):
        monkeypatch.setattr(module, "settings", dead)


def test_ping_false_when_down_and_fast(closed_ports):
    from newsrag.stores import ping_all

    start = time.monotonic()
    assert ping_all() == {"postgres": False, "qdrant": False, "opensearch": False}
    assert time.monotonic() - start < 10


def test_db_sql_error_exits_1(closed_ports):
    result = runner.invoke(app, ["db", "sql", "select 1"])
    assert result.exit_code == 1
    assert result.output.startswith("error:")


# --- live stores (needs `newsrag stores up`) ---


@pytest.mark.stores
def test_ping_all_true():
    from newsrag.stores import ping_all

    assert ping_all() == ALL_OK


@pytest.mark.stores
def test_clients_are_lazy_singletons():
    from opensearchpy import OpenSearch
    from psycopg_pool import ConnectionPool
    from qdrant_client import QdrantClient

    from newsrag.stores import opensearch, pg, qdrant

    assert isinstance(pg(), ConnectionPool) and pg() is pg()
    assert isinstance(qdrant(), QdrantClient) and qdrant() is qdrant()
    assert isinstance(opensearch(), OpenSearch) and opensearch() is opensearch()
    with pg().connection() as conn:
        assert conn.execute("select 1").fetchone() == (1,)
    assert qdrant().get_collections() is not None
    assert opensearch().cluster.health()["status"] != "red"


@pytest.mark.stores
def test_db_sql_rows_and_null():
    result = runner.invoke(app, ["db", "sql", "select 1, null, 'a' union all select 2, 3, 'b'"])
    assert result.exit_code == 0, result.output
    assert result.output == "1\tNULL\ta\n2\t3\tb\n"
    result = runner.invoke(app, ["db", "sql", "set search_path = public"])
    assert result.exit_code == 0 and result.output == ""
    result = runner.invoke(app, ["db", "sql", "select * from no_such_table_xyz"])
    assert result.exit_code == 1 and "no_such_table_xyz" in result.output
