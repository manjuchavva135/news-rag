"""`newsrag textmodel`: the text model Telugu bake-off (ticket 032)."""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(help="Text model choice.", no_args_is_help=True)


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


@app.callback()
def main() -> None:
    """Text model choice."""


@app.command()
def bakeoff(
    n: Annotated[
        int | None, typer.Option("--n", help="Stories to sample (default textmodel_n).")
    ] = None,
    seed: Annotated[
        int | None, typer.Option("--seed", help="Sampling seed (default textmodel_seed).")
    ] = None,
    candidates: Annotated[
        str | None,
        typer.Option(
            "--candidates", help="Comma-separated profiles (default textmodel_candidates)."
        ),
    ] = None,
    rerun: Annotated[
        bool, typer.Option("--rerun", help="Ignore the saved outputs and run every model again.")
    ] = False,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Write the existing report's winner as enrich_model and answer_model, and "
            "answer_judge_model, into pipeline.yaml (no rerun).",
        ),
    ] = False,
) -> None:
    """Text model Telugu bake-off (ticket 032): the candidates run four Telugu tasks on the same
    stories, are cross-judged by candidates of another family, and the report names the winner."""
    from newsrag import config
    from newsrag.textmodel import bakeoff as bk

    s = config.settings
    if apply:
        try:
            winner, answer, judge = bk.apply_report()
        except (FileNotFoundError, ValueError) as exc:
            raise _fail(str(exc), 1) from None
        typer.echo(f"enrich_model set to {winner}")
        typer.echo(f"answer_model set to {answer}")
        typer.echo(f"answer_judge_model set to {judge}")
        return
    names = (
        [c.strip() for c in candidates.split(",") if c.strip()]
        if candidates
        else list(s.textmodel_candidates)
    )
    try:
        bk.check_request(names)
        stories = bk.ensure_stories_file(
            s.textmodel_stories_file,
            s.textmodel_n if n is None else n,
            s.textmodel_seed if seed is None else seed,
            echo=typer.echo,
        )
    except bk.BakeoffError as exc:
        raise _fail(str(exc), 2) from None
    _rows, winner, path = bk.run_bakeoff(names, stories, rerun=rerun, echo=typer.echo)
    typer.echo(path.read_text(encoding="utf-8"))
    typer.echo(str(path))
    if winner is None:
        raise typer.Exit(1)
