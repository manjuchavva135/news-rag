"""`newsrag preflight`: one line per precondition of the full run; exit 1 if any is missing."""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Check the preconditions of the full-corpus run (ticket 031).",
    invoke_without_command=True,
)


@app.callback()
def preflight() -> None:
    """Print `<item>: ok` or `<item>: MISSING <what to do>`; exit 1 if any is missing."""
    from newsrag.preflight import run_checks

    checks = run_checks()
    for check in checks:
        typer.echo(check.line())
    if not all(c.ok for c in checks):
        raise typer.Exit(1)
