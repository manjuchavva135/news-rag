"""`newsrag sample` commands: build, show."""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(help="The fixed dev sample of 20 rendered pages.", no_args_is_help=True)


@app.command()
def build(
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing sample.")] = False,
) -> None:
    """Choose the 20 dev-sample pages and write data/dev-sample.txt."""
    from newsrag.sample import SampleError
    from newsrag.sample import build as do_build

    try:
        path, ids, short = do_build(force)
    except SampleError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    for line in short:
        typer.echo(line)
    typer.echo(f"wrote {len(ids)} page ids to {path}")


@app.command()
def show() -> None:
    """One line per sample page: page_id, image path, ok|missing."""
    from newsrag.sample import SampleError, show_lines

    try:
        lines = show_lines()
    except SampleError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    for line in lines:
        typer.echo(line)
