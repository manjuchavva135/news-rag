"""`newsrag ingest` commands: unpack, register."""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(help="Unpack the archives and register their pages.", no_args_is_help=True)


@app.command()
def unpack(
    archive: Annotated[
        str | None, typer.Option("--archive", help="Only this archive, e.g. 2025.zip.")
    ] = None,
) -> None:
    """Extract every .zip/.rar in archive_dir into source_dir/<stem>/ (already done: skipped)."""
    from newsrag.ingest.unpack import unpack_all

    raise typer.Exit(unpack_all(archive, echo=typer.echo))


@app.command()
def register() -> None:
    """Register one ledger page per unique page of every unpacked archive, and write reports."""
    import psycopg

    from newsrag.ingest.register import register_all

    try:
        register_all(echo=typer.echo)
    except psycopg.errors.UndefinedTable as exc:
        typer.echo(f"ledger not initialised ({exc}): run  uv run newsrag ledger init", err=True)
        raise typer.Exit(2) from None
