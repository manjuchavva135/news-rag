"""`newsrag report build` (ticket 030)."""

from __future__ import annotations

import time

import typer

app = typer.Typer(help="The phase-1 quality report.", no_args_is_help=True)


@app.callback()
def _group() -> None:
    """The phase-1 quality report."""


@app.command()
def build() -> None:
    """Write <quality_report_dir>/index.html and report.json from the ledger, the stores and the
    eval files; print the verdict and one line per bar."""
    from newsrag.report.build import build as build_report

    started = time.monotonic()
    report, page = build_report()
    for bar in report.bars:
        status = "PASS" if bar.passed else "FAIL"
        typer.echo(f"{status}  {bar.number}. {bar.name}: {bar.value} ({bar.threshold})")
    for title, error in report.errors.items():
        typer.echo(f"not available: {title}: {error}")
    typer.echo(report.header)
    typer.echo(f"report: {page} ({time.monotonic() - started:.1f}s)")
