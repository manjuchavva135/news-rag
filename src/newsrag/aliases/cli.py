"""`newsrag aliases`: build the name alias table, print its statistics, check its precision
(ticket 022 FR-1, FR-7, NFR-1)."""

from __future__ import annotations

import json
import math
from typing import Annotated

import typer

app = typer.Typer(help="Name alias table: which names are one entity.", no_args_is_help=True)


def stats_line(stats: dict) -> str:
    return (
        f"names={stats['names']} entities={stats['entities']} auto_pairs={stats['auto_pairs']} "
        f"review_pairs={stats['review_pairs']} largest_group={stats['largest_group']}"
    )


@app.command()
def build() -> None:
    """Collect the names of every `enrich_core` artifact, find candidate pairs, ask the model
    about the new ones, group the names, keep the entity ids and write `entities.json`, the
    review CSV and the statistics. Prints the statistics line."""
    from newsrag.aliases import build as builder

    try:
        result = builder.build()
    except LookupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"asked={result.asked} entities={result.paths['entities']}")
    typer.echo(f"review={result.paths['review']}")
    typer.echo(stats_line(result.stats))


@app.command()
def stats() -> None:
    """Prints `names=<n> entities=<n> auto_pairs=<n> review_pairs=<n> largest_group=<n>` of the
    last build."""
    from newsrag.aliases import version_dir

    path = version_dir() / "stats.json"
    if not path.is_file():
        typer.echo(f"no alias build yet ({path} is missing): run `newsrag aliases build`", err=True)
        raise typer.Exit(1)
    typer.echo(stats_line(json.loads(path.read_text(encoding="utf-8"))))


@app.command()
def verify(
    n: Annotated[int | None, typer.Option("--n", help="Pairs to check.")] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="Sampling seed.")] = None,
) -> None:
    """The judge model (`alias_verify_model`) is asked about random pairs of names of one
    auto-merged group; prints `same: <k>/<n>` and exits 1 below the threshold."""
    from newsrag import config
    from newsrag.aliases import build as builder
    from newsrag.aliases import verify as vf
    from newsrag.aliases.names import collect
    from newsrag.stages.base import write_json_atomic

    s = config.settings
    try:
        groups = vf.load_auto_groups()
    except FileNotFoundError:
        typer.echo("no alias build yet: run `newsrag aliases build`", err=True)
        raise typer.Exit(1) from None
    if not any(len(g) >= 2 for g in groups):
        typer.echo("no auto-merged group to verify", err=True)
        raise typer.Exit(1)
    artifacts, texts = builder.load_inputs()
    report = vf.run_verify(
        groups,
        collect(artifacts),
        texts,
        n if n is not None else s.alias_verify_n,
        seed if seed is not None else s.alias_verify_seed,
    )
    path = write_json_atomic(s.alias_verify_file, report)
    typer.echo(f"model={report['model']} groups={report['groups']} report={path}")
    typer.echo(f"same: {report['same']}/{report['n']}")
    need = math.ceil(report["n"] * s.alias_verify_min_ok_ratio - 1e-9)
    if report["same"] < need:
        raise typer.Exit(1)
