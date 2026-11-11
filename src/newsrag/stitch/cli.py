"""`newsrag stitch`: statistics and the link-quality check (ticket 016 FR-6, NFR-1)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer

app = typer.Typer(help="Continuation stitching.", no_args_is_help=True)

PASS_RATIO = 0.9  # NFR-1: at least 18 of 20


def totals(artifacts: list[dict]) -> dict[str, int]:
    """FR-6 numbers summed over the given `stitch` artifacts."""
    from newsrag.stitch.match import REASONS

    out = {"source_markers": 0, "continuation_markers": 0, "linked": 0}
    out.update({f"unmatched_{r}": 0 for r in REASONS})
    out.update({"ambiguous": 0, "orphan_markers": 0})
    for art in artifacts:
        stats = art.get("stats") or {}
        out["source_markers"] += stats.get("source_markers", 0)
        out["continuation_markers"] += stats.get("continuation_markers", 0)
        out["orphan_markers"] += stats.get("orphan_markers", 0)
        out["linked"] += len(art.get("links") or [])
        out["ambiguous"] += sum(
            1
            for link in art.get("links") or []
            if "continuation_ambiguous" in link.get("flags", [])
        )
        for item in art.get("unmatched") or []:
            out[f"unmatched_{item['reason']}"] += 1
    return out


@app.command()
def stats(
    day: Annotated[
        str | None, typer.Option("--date", help="Print date YYYY-MM-DD (default: all done dates).")
    ] = None,
) -> None:
    """Prints source_markers, continuation_markers, linked, unmatched_<reason>, ambiguous (and
    orphan_markers) over the done `stitch` artifacts."""
    from newsrag.stitch import load

    pub_date = date.fromisoformat(day) if day else None
    done = load.done_stitch(pub_date)
    if not done:
        typer.echo(f"no done stitch artifact for {day or 'any date'}", err=True)
        raise typer.Exit(1)
    for key, value in totals([load.load_stitch(p) for p in done.values()]).items():
        typer.echo(f"{key}={value}")


@app.command()
def verify(
    n: Annotated[int | None, typer.Option("--n", help="Links to check.")] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="Sampling seed.")] = None,
    day: Annotated[
        str | None, typer.Option("--date", help="Only the links of this date (default: all).")
    ] = None,
) -> None:
    """The text model is asked "same news story? yes/no" for random links; prints `yes: k/n`."""
    from newsrag import config
    from newsrag.stages.base import write_json_atomic
    from newsrag.stitch import verify as vf

    s = config.settings
    links = vf.collect_links(date.fromisoformat(day) if day else None)
    if not links:
        typer.echo("no links to verify", err=True)
        raise typer.Exit(1)
    report = vf.run_verify(
        links,
        n if n is not None else s.stitch_verify_n,
        seed if seed is not None else s.stitch_verify_seed,
    )
    path = write_json_atomic(s.stitch_verify_file, report)
    typer.echo(f"model={report['model']} links={report['n_links_available']} report={path}")
    typer.echo(f"yes: {report['yes']}/{report['n']}")
    if report["yes"] / report["n"] < PASS_RATIO:
        raise typer.Exit(1)
