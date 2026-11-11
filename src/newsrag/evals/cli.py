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
    langs: Annotated[
        str | None,
        typer.Option(help="Exact questions per language, e.g. te:100,en:100,te_latn:100."),
    ] = None,
    window_n: Annotated[
        int | None, typer.Option(help="Questions from the baseline window (default 100).")
    ] = None,
    from_date: Annotated[
        str | None, typer.Option(help="Only stories printed on or after YYYY-MM-DD.")
    ] = None,
) -> None:
    """Write questions from the stories of the active generation, one JSON line each."""
    from datetime import date

    from newsrag import config
    from newsrag.evals import retrieval

    total = n if n is not None else config.settings.retrieval_n
    try:
        lang_counts = retrieval.parse_langs(langs, total) if langs else None
        start = date.fromisoformat(from_date) if from_date else None
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from None
    summary = retrieval.build_retrieval_set(
        n=total,
        seed=seed,
        out=out,
        window_n=window_n,
        echo=typer.echo,
        langs=lang_counts,
        from_date=start,
    )
    per_lang = " ".join(f"{c}={k}" for c, k in summary["by_lang"].items() if k or c != "te_latn")
    typer.echo(
        f"wrote {summary['n']} questions to {summary['out']} "
        f"(in_window={summary['in_window']} {per_lang} "
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
    translit: Annotated[
        bool,
        typer.Option("--translit", help="Also search te_latn questions after transliteration."),
    ] = False,
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
            translit=translit,
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


@app.command("answers")
def answers_cmd(
    set_file: Annotated[
        Path | None, typer.Option("--set", help="Question set (default eval_2026_set_file).")
    ] = None,
    retrieval_file: Annotated[
        Path | None,
        typer.Option("--retrieval", help="eval retrieval --translit report of the set."),
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Default eval_2026_answers_file.")] = None,
) -> None:
    """Answer every question from its hits and grade it correct / partial / wrong."""
    from newsrag.evals import answers, customer

    try:
        report = answers.run_answers(
            set_file=set_file, retrieval_file=retrieval_file, out=out, echo=typer.echo
        )
    except customer.FamilyError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from None
    except answers.InputError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(answers.summary_line(report))


@app.command("report-2026")
def report_2026_cmd() -> None:
    """Write the trilingual 2026 test page (eval_2026_report_file)."""
    from newsrag.evals import report_2026

    try:
        path = report_2026.build()
    except report_2026.MissingInput as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"wrote {path}")
