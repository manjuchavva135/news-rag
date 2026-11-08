"""`newsrag stores` (bring the docker stores up/down, status) and `newsrag db` (run SQL)."""

from __future__ import annotations

import shutil
import subprocess
import time

import typer

from newsrag.config import settings

app = typer.Typer(help="Start, stop and check the backend stores.", no_args_is_help=True)
db_app = typer.Typer(help="Run SQL against the Postgres store.", no_args_is_help=True)

COMPOSE_PROJECT = "newsrag"
UP_FAILURE_TAIL_LINES = 20
UP_POLL_INTERVAL_S = 2.0
STORE_NAMES = ("postgres", "qdrant", "opensearch")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing stdout and stderr as text."""
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def _check_docker() -> None:
    """Exit 2 with a clear message when docker, compose or the daemon are not usable."""
    if shutil.which("docker") is None:
        raise _fail(
            "docker not found: install Docker Engine and the compose plugin (see README)", 2
        )
    if _run(["docker", "compose", "version"]).returncode != 0:
        raise _fail("docker compose plugin not found: install docker-compose-plugin", 2)
    info = _run(["docker", "info"])
    if info.returncode != 0:
        lines = (info.stderr or info.stdout or "").strip().splitlines()
        first = lines[0] if lines else f"docker info exited {info.returncode}"
        raise _fail(f"cannot talk to the docker daemon: {first}", 2)


def _compose(*args: str) -> list[str]:
    return ["docker", "compose", "-p", COMPOSE_PROJECT, "-f", str(settings.compose_file), *args]


def _status_lines(states: dict[str, bool]) -> list[str]:
    return [f"{name}: {'ok' if states.get(name) else 'down'}" for name in STORE_NAMES]


def _ping_all() -> dict[str, bool]:
    from newsrag.stores import ping_all

    return ping_all(settings.stores_ping_timeout_s)


@app.command()
def up() -> None:
    """Start the stores (docker compose up -d --build) and wait until all three answer."""
    _check_docker()
    typer.echo(f"starting stores from {settings.compose_file} ...")
    result = _run(_compose("up", "-d", "--build"))
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        tail = output.rstrip().splitlines()[-UP_FAILURE_TAIL_LINES:]
        typer.echo("docker compose up failed:", err=True)
        for line in tail:
            typer.echo(line, err=True)
        raise typer.Exit(1)

    deadline = time.monotonic() + settings.stores_up_timeout_s
    while True:
        states = _ping_all()
        if all(states.get(name) for name in STORE_NAMES):
            for line in _status_lines(states):
                typer.echo(line)
            return
        if time.monotonic() >= deadline:
            typer.echo(
                f"stores did not all answer within {settings.stores_up_timeout_s:g} s:", err=True
            )
            for line in _status_lines(states):
                typer.echo(line)
            raise typer.Exit(1)
        time.sleep(UP_POLL_INTERVAL_S)


@app.command()
def down() -> None:
    """Stop the stores (docker compose stop). Volumes and data are kept."""
    _check_docker()
    result = _run(_compose("stop"))
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        for line in output.rstrip().splitlines()[-UP_FAILURE_TAIL_LINES:]:
            typer.echo(line, err=True)
        raise typer.Exit(1)
    typer.echo("stores stopped (data kept)")


@app.command()
def status() -> None:
    """Print `<store>: ok|down` for each store; exit 0 only when all are ok."""
    states = _ping_all()
    for line in _status_lines(states):
        typer.echo(line)
    if not all(states.get(name) for name in STORE_NAMES):
        raise typer.Exit(1)


def _format_value(value: object) -> str:
    return "NULL" if value is None else str(value)


@db_app.command("sql")
def sql(query: str = typer.Argument(..., help="One SQL statement.")) -> None:
    """Run one SQL statement (autocommit); print rows tab-separated, no header, NULL as NULL."""
    import psycopg

    try:
        with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
            cursor = conn.execute(query)
            rows = cursor.fetchall() if cursor.description is not None else []
    except Exception as exc:  # noqa: BLE001 - report any failure as exit 1
        typer.echo(f"error: {str(exc).strip()}", err=True)
        raise typer.Exit(1) from None
    for row in rows:
        typer.echo("\t".join(_format_value(value) for value in row))
