"""`newsrag stage` commands: run, status, failures, requeue-stale, reset, quarantine-failed."""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Run pipeline stages and inspect the ledger.", no_args_is_help=True)


def _stage(name: str):
    from newsrag.stages.registry import REGISTRY

    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY)) or "(none registered)"
        typer.echo(f"unknown stage {name}; known stages: {known}", err=True)
        raise typer.Exit(2)
    return REGISTRY[name]


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"{value!r} is not YYYY-MM-DD") from None


@app.command()
def run(
    names: Annotated[list[str], typer.Argument(help="Stage names, run one after another.")],
    limit: Annotated[int | None, typer.Option(help="Process at most N items per stage.")] = None,
    page: Annotated[
        list[str] | None, typer.Option("--page", help="Only this page_id (repeatable).")
    ] = None,
    pages_file: Annotated[
        Path | None,
        typer.Option("--pages-file", help="Only the page_ids in this file (one per line)."),
    ] = None,
    pub_date: Annotated[
        str | None, typer.Option("--date", help="Only this print date YYYY-MM-DD.")
    ] = None,
    workers: Annotated[int | None, typer.Option(help="Threads (overrides concurrency).")] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Run matching items once, whatever status.")
    ] = False,
) -> None:
    """Run the named stages over their eligible items."""
    from newsrag.stages.base import StageUnavailable
    from newsrag.stages.runner import RunFilters, read_pages_file, run_stage

    stages = [_stage(name) for name in names]
    page_ids: list[str] | None = None
    if page:
        page_ids = list(page)
    if pages_file is not None:
        page_ids = [*(page_ids or []), *read_pages_file(pages_file)]
    filters = RunFilters(
        page_ids=page_ids,
        pub_date=_parse_date(pub_date),
        limit=limit,
        force=force,
        workers=workers,
    )
    stop = threading.Event()
    for stage in stages:
        if stop.is_set():
            break
        try:
            result = run_stage(stage, filters, stop_event=stop)
        except StageUnavailable as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from None
        typer.echo(
            f"{stage.name}: done={result.processed} failed={result.failed} "
            f"skipped={result.skipped} in {result.seconds:.1f}s"
        )


@app.command()
def status() -> None:
    """One line per registered stage: done / failed / running / eligible counts."""
    from newsrag.stages.runner import status_lines

    for line in status_lines():
        typer.echo(line)


@app.command()
def failures(name: str) -> None:
    """List failed items of a stage: key, attempt, first line of the error."""
    from newsrag.stages.runner import failure_lines

    for line in failure_lines(_stage(name)):
        typer.echo(line)


@app.command("requeue-stale")
def requeue_stale(
    older_than: Annotated[str, typer.Option(help="e.g. 90s, 30m, 2h, 1d")] = "2h",
) -> None:
    """Mark `running` rows older than the limit as failed so they can be retried."""
    from newsrag.stages.runner import parse_duration
    from newsrag.stages.runner import requeue_stale as do_requeue

    try:
        parse_duration(older_than)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(f"requeued {do_requeue(older_than)} stale rows")


@app.command()
def reset(
    name: str,
    page: Annotated[str | None, typer.Option("--page", help="Only this page_id.")] = None,
    pub_date: Annotated[
        str | None, typer.Option("--date", help="Only this print date YYYY-MM-DD.")
    ] = None,
    all_rows: Annotated[bool, typer.Option("--all", help="Every row of the stage.")] = False,
) -> None:
    """Delete the matching rows of a stage so they run again."""
    from newsrag.stages.runner import reset_stage

    stage = _stage(name)
    try:
        count = reset_stage(stage, page, _parse_date(pub_date), all_rows)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(f"{name}: deleted {count} rows")


@app.command("quarantine-failed")
def quarantine_failed(name: str) -> None:
    """Quarantine the pages whose item of this stage failed `max_attempts` times (every page of
    the date for a date stage); they count against the quarantine bar."""
    from newsrag.stages.runner import quarantine_failed as do_quarantine

    pages = do_quarantine(_stage(name))
    for page_id in pages:
        typer.echo(page_id)
    typer.echo(f"{name}: quarantined {len(pages)} pages")
