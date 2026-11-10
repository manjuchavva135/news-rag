"""`newsrag geo` commands: check."""

from __future__ import annotations

import typer

app = typer.Typer(help="District and mandal reference list (data/geo).", no_args_is_help=True)


@app.command("check")
def check_cmd() -> None:
    """Validate data/geo and the aliases; print the counts; exit 1 on any problem."""
    from newsrag.config import settings
    from newsrag.geo import check, counts, summary_line

    problems = check(settings.geo_dir, settings.geo_aliases_file)
    if problems and any(p.startswith("missing geo file") for p in problems):
        for p in problems:
            typer.echo(p, err=True)
        raise typer.Exit(1)
    typer.echo(summary_line(settings.geo_dir))
    for key, n in counts(settings.geo_dir).items():
        typer.echo(f"{key}={n}")
    if problems:
        typer.echo(f"{len(problems)} problem(s):", err=True)
        for p in problems:
            typer.echo(f"  {p}", err=True)
        raise typer.Exit(1)
