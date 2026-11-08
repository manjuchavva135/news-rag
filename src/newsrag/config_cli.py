"""`newsrag config` commands."""

import typer

app = typer.Typer(help="Show settings.", no_args_is_help=True)


@app.command()
def show() -> None:
    """Print every setting as `name = value`, sorted by name, secrets masked."""
    from newsrag.config import settings

    for line in settings.display_lines():
        typer.echo(line)
