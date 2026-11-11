"""`newsrag warehouse init`."""

import typer

app = typer.Typer(help="Structured warehouse (Postgres).", no_args_is_help=True)


@app.command()
def init() -> None:
    """Create the warehouse schema if missing and apply new migrations (safe to repeat)."""
    from newsrag.warehouse import db

    applied = db.migrate()
    for name in applied:
        typer.echo(f"applied {name}")
    typer.echo(f"warehouse schema {db.schema()}: up to date ({len(applied)} new migrations)")
