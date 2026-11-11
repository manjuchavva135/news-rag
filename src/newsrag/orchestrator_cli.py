"""`newsrag run`: process everything, stage after stage, until no work is left (ticket 029)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    help="Run the whole pipeline: unpack, register, every stage in order, index validate.",
    invoke_without_command=True,
)


def _split(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _dates(value: str | None) -> list[date] | None:
    parts = _split(value)
    if parts is None:
        return None
    try:
        return [date.fromisoformat(p) for p in parts]
    except ValueError:
        raise typer.BadParameter(f"{value!r}: give dates as YYYY-MM-DD,YYYY-MM-DD") from None


@app.callback()
def run(
    until: Annotated[
        str | None, typer.Option("--until", help="Run up to and including this stage.")
    ] = None,
    only: Annotated[
        str | None,
        typer.Option("--only", help="Just these stages (comma-separated), one pass each."),
    ] = None,
    pages_file: Annotated[
        Path | None,
        typer.Option("--pages-file", help="Only the page_ids in this file (one per line)."),
    ] = None,
    dates: Annotated[
        str | None, typer.Option("--dates", help="Only these print dates: D1,D2 (YYYY-MM-DD).")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print eligible counts per stage, process nothing.")
    ] = False,
    tag: Annotated[
        str | None, typer.Option("--tag", help="Recorded in the run file, e.g. full.")
    ] = None,
) -> None:
    """Unpack and register new archives, then every stage in dependency order (one pass per
    stage, repeated until no work is left), then `index validate`. Writes
    `<runs_dir>/<UTC timestamp>.json`. Exit 0 ok, 1 a stage could not run, 130 interrupted."""
    from newsrag.orchestrator import Orchestrator, RunOptions, stop_on_signals

    if until and only:
        raise typer.BadParameter("give --until or --only, not both")
    options = RunOptions(
        until=until,
        only=_split(only),
        pages_file=pages_file,
        dates=_dates(dates),
        dry_run=dry_run,
        tag=tag,
    )
    orchestrator = Orchestrator(options)
    with stop_on_signals(orchestrator.stop_event, orchestrator.echo):
        code = orchestrator.run()
    raise typer.Exit(code)
