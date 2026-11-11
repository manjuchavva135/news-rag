"""`newsrag index`: activate a generation, list the generations, validate the three stores."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer

app = typer.Typer(
    help="The embedded chunk index (Qdrant, OpenSearch, Postgres).", no_args_is_help=True
)


@app.command()
def activate(generation: Annotated[str, typer.Argument(help="e.g. bge-m3__v1")]) -> None:
    """Make the generation the one the search reads: switches the Qdrant alias `chunks`, the
    OpenSearch alias `chunks` and `warehouse.active_generation` together."""
    from newsrag.embed import generations

    try:
        generations.activate(generation)
    except generations.UnknownGeneration as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"active generation: {generation}")


@app.command()
def collections() -> None:
    """List the generations with their Qdrant points, OpenSearch documents and Postgres chunk
    rows; the active one is marked."""
    from newsrag.embed import generations

    infos = generations.collections()
    if not infos:
        typer.echo("no generation yet: run 'newsrag stage run index'")
    for info in infos:
        typer.echo(
            f"{info.generation}  qdrant={info.qdrant} opensearch={info.opensearch} "
            f"postgres={info.postgres}" + ("  active" if info.active else "")
        )


@app.command()
def validate(
    pub_date: Annotated[
        str | None, typer.Option("--date", help="Only this print date YYYY-MM-DD.")
    ] = None,
    generation: Annotated[
        str | None, typer.Option("--generation", help="Default: the active generation.")
    ] = None,
) -> None:
    """Check per date that Qdrant, OpenSearch and Postgres hold the same number of chunks. Prints
    `ok <date> <n>` or `MISMATCH <date> q=<n> os=<n> pg=<n>`; exits 1 on any mismatch."""
    from newsrag.embed import generations
    from newsrag.warehouse import db as wdb

    try:
        only = date.fromisoformat(pub_date) if pub_date else None
    except ValueError:
        raise typer.BadParameter(f"{pub_date!r} is not YYYY-MM-DD") from None
    generation = generation or wdb.active_generation()
    if generation is None:
        typer.echo("error: no active generation: run 'newsrag stage run index' first", err=True)
        raise typer.Exit(1)
    results = generations.validate(generation, only)
    for result in results:
        typer.echo(generations.line(result))
    if any(not (q == o == p) for _, q, o, p in results):
        raise typer.Exit(1)


# --- `newsrag embed` (ticket 026) -------------------------------------------------------------

embed_app = typer.Typer(help="Embedding models: the bake-off that picks embed_model.")


@embed_app.callback()
def _embed_group() -> None:
    """Embedding models."""


@embed_app.command()
def bakeoff(
    candidates: Annotated[
        str | None,
        typer.Option(help="Comma-separated embed profiles (default embed_bakeoff_candidates)."),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Write the report's winner as embed_model (no rerun)."),
    ] = False,
    cleanup: Annotated[
        bool, typer.Option("--cleanup", help="Delete the bakeoff__* Qdrant collections.")
    ] = False,
) -> None:
    """Embed the active generation's chunks with every candidate, run the retrieval test on each
    and write <reports_dir>/embed-bakeoff.md with the winner."""
    from newsrag.embed import bakeoff as bk

    if apply and cleanup:
        raise typer.BadParameter("--apply and --cleanup do not go together")
    try:
        if apply:
            winner, changed = bk.apply()
            if changed:
                typer.echo(f"embed_model set to {winner}; chunk and index stages will rerun")
            else:
                typer.echo(f"embed_model unchanged ({winner})")
            return
        if cleanup:
            for name in bk.cleanup():
                typer.echo(f"deleted {name}")
            return
        names = candidates.split(",") if candidates else None
        outcome = bk.run_bakeoff(names)
    except bk.BakeoffError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(outcome.report_file.read_text(encoding="utf-8").rstrip())
    if outcome.winner is None:
        raise typer.Exit(1)
