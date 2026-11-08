"""`newsrag ledger` commands."""

import typer

app = typer.Typer(help="Pipeline ledger (Postgres).", no_args_is_help=True)


@app.command()
def init() -> None:
    """Create the ledger schema if missing and apply new migrations (safe to repeat)."""
    from newsrag.ledger import db

    applied = db.migrate()
    for name in applied:
        typer.echo(f"applied {name}")
    typer.echo(f"ledger schema {db.schema()}: up to date ({len(applied)} new migrations)")
