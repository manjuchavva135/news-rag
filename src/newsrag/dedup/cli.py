"""`newsrag dedup`: statistics and the merge-precision check (ticket 017 FR-6, NFR-1)."""

from __future__ import annotations

import math
from datetime import date
from typing import Annotated, Any

import typer

app = typer.Typer(help="Cross-edition story dedup.", no_args_is_help=True)

MANY_PRINTINGS = 5  # FR-6: stories with at least this many printings are counted


def totals(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """FR-6 numbers over the given `dedup` artifacts."""
    sizes = [len(s["printings"]) for art in artifacts for s in art["stories"]]
    return {
        "units": sum(sizes),
        "stories": len(sizes),
        "mean_printings": round(sum(sizes) / len(sizes), 2) if sizes else 0.0,
        "max_printings": max(sizes, default=0),
        f"stories_with_{MANY_PRINTINGS}plus_printings": sum(
            1 for n in sizes if n >= MANY_PRINTINGS
        ),
    }


@app.command()
def stats(
    day: Annotated[
        str | None, typer.Option("--date", help="Print date YYYY-MM-DD (default: all done dates).")
    ] = None,
) -> None:
    """Prints units, stories, mean and max printings per story and the number of stories with
    5 or more printings, over the done `dedup` artifacts."""
    from newsrag.dedup import load

    done = load.done_dedup(date.fromisoformat(day) if day else None)
    if not done:
        typer.echo(f"no done dedup artifact for {day or 'any date'}", err=True)
        raise typer.Exit(1)
    for key, value in totals([load.load_dedup(p) for p in done.values()]).items():
        typer.echo(f"{key}={value}")


@app.command()
def verify(
    n: Annotated[int | None, typer.Option("--n", help="Merged pairs to check.")] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="Sampling seed.")] = None,
    day: Annotated[
        str | None, typer.Option("--date", help="Only the pairs of this date (default: all).")
    ] = None,
) -> None:
    """The text model is asked "same news story (local edits allowed)? yes/no" for random merged
    pairs (canonical vs another printing); prints `yes: k/n`."""
    from newsrag import config
    from newsrag.dedup import verify as vf
    from newsrag.stages.base import write_json_atomic

    s = config.settings
    pairs = vf.collect_pairs(date.fromisoformat(day) if day else None)
    if not pairs:
        typer.echo("no merged pairs to verify", err=True)
        raise typer.Exit(1)
    report = vf.run_verify(
        pairs,
        n if n is not None else s.dedup_verify_n,
        seed if seed is not None else s.dedup_verify_seed,
    )
    path = write_json_atomic(s.dedup_verify_file, report)
    typer.echo(f"model={report['model']} pairs={report['n_pairs_available']} report={path}")
    typer.echo(f"yes: {report['yes']}/{report['n']}")
    if report["yes"] < math.ceil(report["n"] * s.dedup_verify_min_yes_ratio - 1e-9):
        raise typer.Exit(1)
