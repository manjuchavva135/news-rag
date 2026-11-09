"""`newsrag render` commands: stats."""

from __future__ import annotations

import typer

app = typer.Typer(help="Page renders (the `render` stage).", no_args_is_help=True)


@app.callback()
def _group() -> None:
    """Page renders (the `render` stage)."""


@app.command()
def stats() -> None:
    """rendered=<n> multi_page=<n> small_render=<n> quarantined=<n>"""
    from newsrag.stages.render import stats as render_stats

    n = render_stats()
    typer.echo(
        f"rendered={n['rendered']} multi_page={n['multi_page']} "
        f"small_render={n['small_render']} quarantined={n['quarantined']}"
    )
