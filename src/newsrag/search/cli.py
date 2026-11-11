"""`newsrag search "<query>" [--k 10] [--store new|baseline]` (ticket 025 FR-2)."""

from __future__ import annotations

from typing import Annotated

import typer
from typer.core import TyperGroup

from newsrag.search.hybrid import Hit


class _OneCommand(TyperGroup):
    """A group without sub-commands whose callback is the command: options may follow the query
    (`newsrag search "<query>" --k 5`)."""

    allow_interspersed_args = True


app = typer.Typer(help="Hybrid search over the stores.", no_args_is_help=True, cls=_OneCommand)


def hit_line(rank: int, hit: Hit) -> str:
    """rank, score, pub_date, first page_id, headline (60 characters)."""
    page = hit.page_ids[0] if hit.page_ids else "-"
    headline = " ".join(hit.headline.split())[:60]
    return f"{rank:>2}  {hit.score:.4f}  {hit.pub_date or '-'}  {page}  {headline}"


@app.callback(invoke_without_command=True)
def search(
    query: Annotated[str, typer.Argument(help="The question or keywords.")],
    k: Annotated[int, typer.Option("--k", help="Hits to print.")] = 10,
    store: Annotated[str, typer.Option(help="new | baseline")] = "new",
    translit: Annotated[
        bool, typer.Option("--translit", help="Rewrite a Romanized Telugu query in Telugu script.")
    ] = False,
) -> None:
    """Print the top hits: rank, score, date, first page id, headline."""
    from newsrag import search as search_mod

    if translit:
        from newsrag.search.translit import to_telugu_many

        (result,) = to_telugu_many([query])  # the model is released before the search
        if result.failed:
            typer.echo("translit: failed, searching as typed")
        else:
            typer.echo(f"translit: {result.text}")
            query = result.text
    try:
        hits = search_mod.search(query, k=k, store=store)
    except search_mod.StoreNotConfigured as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    finally:
        search_mod.close()
    for rank, hit in enumerate(hits, start=1):
        typer.echo(hit_line(rank, hit))
