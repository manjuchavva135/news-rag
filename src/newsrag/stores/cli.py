"""`newsrag stores` (bring the docker stores up/down, status) and `newsrag db` (run SQL)."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

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


def _compose(
    *args: str, project: str = COMPOSE_PROJECT, compose_file: Path | None = None
) -> list[str]:
    file = compose_file if compose_file is not None else settings.compose_file
    return ["docker", "compose", "-p", project, "-f", str(file), *args]


def _status_lines(states: dict[str, bool]) -> list[str]:
    return [f"{name}: {'ok' if states.get(name) else 'down'}" for name in STORE_NAMES]


def _ping_all() -> dict[str, bool]:
    from newsrag.stores import ping_all

    return ping_all(settings.stores_ping_timeout_s)


def compose_up(
    project: str, compose_file: Path, ping: Callable[[], dict[str, bool]], label: str = "stores"
) -> None:
    """`docker compose up -d --build`, then wait until `ping()` reports all three stores ok."""
    _check_docker()
    typer.echo(f"starting {label} from {compose_file} ...")
    result = _run(_compose("up", "-d", "--build", project=project, compose_file=compose_file))
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        tail = output.rstrip().splitlines()[-UP_FAILURE_TAIL_LINES:]
        typer.echo("docker compose up failed:", err=True)
        for line in tail:
            typer.echo(line, err=True)
        raise typer.Exit(1)

    deadline = time.monotonic() + settings.stores_up_timeout_s
    while True:
        states = ping()
        if all(states.get(name) for name in STORE_NAMES):
            for line in _status_lines(states):
                typer.echo(line)
            return
        if time.monotonic() >= deadline:
            typer.echo(
                f"{label} did not all answer within {settings.stores_up_timeout_s:g} s:", err=True
            )
            for line in _status_lines(states):
                typer.echo(line)
            raise typer.Exit(1)
        time.sleep(UP_POLL_INTERVAL_S)


def compose_stop(project: str, compose_file: Path, label: str = "stores") -> None:
    """`docker compose stop`: volumes and data are kept."""
    _check_docker()
    result = _run(_compose("stop", project=project, compose_file=compose_file))
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        for line in output.rstrip().splitlines()[-UP_FAILURE_TAIL_LINES:]:
            typer.echo(line, err=True)
        raise typer.Exit(1)
    typer.echo(f"{label} stopped (data kept)")


def report_status(states: dict[str, bool]) -> None:
    """Print `<store>: ok|down` for each store; exit 1 unless all are ok."""
    for line in _status_lines(states):
        typer.echo(line)
    if not all(states.get(name) for name in STORE_NAMES):
        raise typer.Exit(1)


@app.command()
def up() -> None:
    """Start the stores (docker compose up -d --build) and wait until all three answer."""
    compose_up(COMPOSE_PROJECT, settings.compose_file, lambda: _ping_all())


@app.command()
def down() -> None:
    """Stop the stores (docker compose stop). Volumes and data are kept."""
    compose_stop(COMPOSE_PROJECT, settings.compose_file)


@app.command()
def status() -> None:
    """Print `<store>: ok|down` for each store; exit 0 only when all are ok."""
    report_status(_ping_all())


def _format_value(value: object) -> str:
    return "NULL" if value is None else str(value)


@db_app.command("init")
def db_init() -> None:
    """Create the ledger and the warehouse schemas and apply new migrations (safe to repeat)."""
    from newsrag.ledger import db as ledger_db
    from newsrag.warehouse import db as warehouse_db

    for label, module in (("ledger", ledger_db), ("warehouse", warehouse_db)):
        applied = module.migrate()
        for name in applied:
            typer.echo(f"applied {name}")
        typer.echo(f"{label} schema {module.schema()}: up to date ({len(applied)} new migrations)")


@db_app.command("sql")
def sql(
    query: str = typer.Argument(..., help="One SQL statement."),
    dsn: str = typer.Option("new", "--dsn", help="new | baseline | a postgresql:// DSN"),
) -> None:
    """Run one SQL statement (autocommit); print rows tab-separated, no header, NULL as NULL."""
    import psycopg

    target = {"new": settings.postgres_dsn, "baseline": settings.baseline_postgres_dsn}.get(dsn)
    if target is None and not dsn.startswith(("postgresql://", "postgres://")):
        typer.echo(f"error: unknown --dsn {dsn!r} (new | baseline | postgresql://...)", err=True)
        raise typer.Exit(1)
    try:
        with psycopg.connect(target or dsn, autocommit=True) as conn:
            cursor = conn.execute(query)
            rows = cursor.fetchall() if cursor.description is not None else []
    except Exception as exc:  # noqa: BLE001 - report any failure as exit 1
        typer.echo(f"error: {str(exc).strip()}", err=True)
        raise typer.Exit(1) from None
    for row in rows:
        typer.echo("\t".join(_format_value(value) for value in row))
