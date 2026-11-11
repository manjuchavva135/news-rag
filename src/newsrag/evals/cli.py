"""`newsrag eval build-retrieval | retrieval` (ticket 025) and `customer | customer-signoff` (028)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Retrieval test set and scoring.", no_args_is_help=True)


@app.command("build-retrieval")
def build_retrieval(
    n: Annotated[int | None, typer.Option(help="Questions (default retrieval_n).")] = None,
    seed: Annotated[int | None, typer.Option(help="Default retrieval_seed.")] = None,
    out: Annotated[Path | None, typer.Option(help="Default retrieval_set_file.")] = None,
) -> None:
    """Write questions from the stories of the active generation, one JSON line each."""
    from newsrag.evals import retrieval

    summary = retrieval.build_retrieval_set(n=n, seed=seed, out=out, echo=typer.echo)
    typer.echo(
        f"wrote {summary['n']} questions to {summary['out']} "
        f"(in_window={summary['in_window']} te={summary['te']} "
        f"replaced={summary['replaced']} stories={summary['stories_available']})"
    )


@app.command("retrieval")
def retrieval_cmd(
    set_file: Annotated[
        Path | None, typer.Option("--set", help="Question set (default retrieval_set_file).")
    ] = None,
    store: Annotated[str, typer.Option(help="new | baseline")] = "new",
    only_window: Annotated[
        bool, typer.Option("--only-window", help="Only questions of the baseline window.")
    ] = False,
    restrict_pages: Annotated[
        Path | None, typer.Option(help="File of page ids (one per line) to search within.")
    ] = None,
    qdrant_collection: Annotated[
        str | None, typer.Option(help="Dense side from this Qdrant collection.")
    ] = None,
    embed_profile: Annotated[str | None, typer.Option(help="Query embed profile.")] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="Report JSON (default <reports_dir>/retrieval-<store>.json)."),
    ] = None,
) -> None:
    """Search every question and print recall@10, MRR and the median search time."""
    from newsrag import search
    from newsrag.evals import retrieval

    try:
        report = retrieval.run_retrieval(
            set_file=set_file,
            store=store,
            only_window=only_window,
            restrict_pages=restrict_pages,
            qdrant_collection=qdrant_collection,
            embed_profile=embed_profile,
            out=out,
        )
    except (search.StoreNotConfigured, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(retrieval.summary_line(report))


@app.command("customer")
def customer_cmd(
    k: Annotated[
        int | None, typer.Option(help="Sources per question (default customer_k).")
    ] = None,
) -> None:
    """Answer the 30 customer questions from the new stores and check every claim (ticket 028)."""
    from newsrag import search
    from newsrag.evals import customer

    try:
        report = customer.run_customer(k=k, echo=typer.echo)
    except customer.FamilyError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from None
    except (search.StoreNotConfigured, RuntimeError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(customer.summary_line(report))


@app.command("customer-signoff")
def customer_signoff_cmd(
    name: Annotated[str, typer.Option("--name", help="Who read the answers.")],
    note: Annotated[str, typer.Option("--note", help="Free text.")] = "",
) -> None:
    """Record that the current customer answers were read (ticket 028 FR-5)."""
    from newsrag.evals import customer

    try:
        record = customer.signoff(name, note)
    except customer.NothingToSign as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"signed by {record['name']} at {record['signed_at']} (run {record['run_id']})")
