"""`newsrag editions` commands: build, apply, report."""

from __future__ import annotations

import typer

app = typer.Typer(help="Edition names and states from the printed folio.", no_args_is_help=True)


@app.command()
def build() -> None:
    """Aggregate the folio artifacts per (year, code) into data/editions.yaml."""
    from newsrag.editions import store

    path, built = store.build()
    for year, codes in built.items():
        n_ok = sum(1 for e in codes.values() if e["status"] == "ok")
        typer.echo(f"{year}: {len(codes)} codes, {n_ok} ok")
    typer.echo(f"wrote {path}")


@app.command()
def apply() -> None:
    """Replace ledger table page_editions from editions.yaml and the folio artifacts."""
    from newsrag.editions import store

    n = store.apply()
    typer.echo(f"page_editions: {n} rows")


@app.command()
def report() -> None:
    """Per year: codes, ok, ambiguous, unknown_edition pages, date_mismatch pages."""
    from newsrag.editions import store

    for line in store.report():
        typer.echo(line)
