"""`newsrag transcribe`: the transcription spot-check (ticket 015 FR-6)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Tables, infographics and notices transcription.", no_args_is_help=True)


@app.command()
def spotcheck(
    n: Annotated[int | None, typer.Option("--n", help="Items to check.")] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="Sampling seed.")] = None,
    pages_file: Annotated[
        Path | None,
        typer.Option("--pages-file", help="Page ids, one per line ('#' comments); default all."),
    ] = None,
) -> None:
    """The judge checks every line of a sample of kept items; prints `invented_pct=<x>`."""
    from newsrag import config
    from newsrag.grouping.input import load_ocr_final
    from newsrag.stages.base import write_json_atomic
    from newsrag.stages.runner import read_pages_file
    from newsrag.transcribe import spotcheck as sc

    s = config.settings
    page_ids = read_pages_file(pages_file) if pages_file is not None else None
    artifacts = sc.done_artifacts(page_ids)
    report = sc.run_spotcheck(
        artifacts,
        n if n is not None else s.transcribe_spotcheck_n,
        seed if seed is not None else s.transcribe_spotcheck_seed,
        load_ocr_final,
    )
    if report["n_items"] == 0 or report["invented_pct"] is None:
        typer.echo("invented_pct=n/a")
        raise typer.Exit(1)
    path = write_json_atomic(s.transcribe_spotcheck_file, report)
    typer.echo(f"items={report['n_items']} lines={report['n_lines']} report={path}")
    typer.echo(f"invented_pct={report['invented_pct']:.2f}")
