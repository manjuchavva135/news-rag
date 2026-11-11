"""`newsrag chunk`: statistics of the done `chunk` artifacts (ticket 023 FR-6)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(help="Retrieval chunks.", no_args_is_help=True)


def percentile(values: list[int], q: float) -> int:
    """Nearest-rank percentile (`q` in 0..1) of a non-empty list."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def stats_of(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """chunks, stories, chunks per story (mean, max), token p50/p95/max, low_confidence share."""
    per_story: dict[str, int] = {}
    for chunk in chunks:
        per_story[chunk["story_id"]] = per_story.get(chunk["story_id"], 0) + 1
    tokens = [c["n_tokens"] for c in chunks]
    n = len(chunks)
    return {
        "chunks": n,
        "stories": len(per_story),
        "chunks_per_story_mean": round(n / len(per_story), 2) if per_story else 0.0,
        "chunks_per_story_max": max(per_story.values(), default=0),
        "tokens_p50": percentile(tokens, 0.5) if tokens else 0,
        "tokens_p95": percentile(tokens, 0.95) if tokens else 0,
        "tokens_max": max(tokens, default=0),
        "low_confidence_share": round(sum(1 for c in chunks if c["low_confidence"]) / n, 3)
        if n
        else 0.0,
    }


def stats_line(stats: dict[str, Any]) -> str:
    return (
        f"chunks={stats['chunks']} stories={stats['stories']} "
        f"chunks_per_story_mean={stats['chunks_per_story_mean']} "
        f"chunks_per_story_max={stats['chunks_per_story_max']} "
        f"tokens_p50={stats['tokens_p50']} tokens_p95={stats['tokens_p95']} "
        f"tokens_max={stats['tokens_max']} low_confidence_share={stats['low_confidence_share']}"
    )


def done_chunks(day: str | None) -> list[dict[str, Any]]:
    """The chunks of the done `chunk` runs (of one date, or of every date)."""
    from datetime import date

    from newsrag.ledger import db

    where, params = "stage = 'chunk' and status = 'done' and artifact_path is not null", []
    if day:
        where += " and pub_date = %s"
        params.append(date.fromisoformat(day))
    with db.conn() as c:
        rows = db.fetch_all(c, f"select artifact_path from date_runs where {where}", params)
    chunks: list[dict[str, Any]] = []
    for row in rows:
        path = Path(row["artifact_path"])
        if path.is_file():
            chunks.extend(json.loads(path.read_text(encoding="utf-8"))["chunks"])
    return chunks


@app.command()
def stats(
    pub_date: Annotated[
        str | None, typer.Option("--date", help="Only this print date YYYY-MM-DD.")
    ] = None,
) -> None:
    """Prints `chunks= stories= chunks_per_story_mean= ... tokens_max= low_confidence_share=` of
    the done `chunk` runs (all dates, or one)."""
    try:
        chunks = done_chunks(pub_date)
    except ValueError:
        raise typer.BadParameter(f"{pub_date!r} is not YYYY-MM-DD") from None
    if not chunks:
        typer.echo("no done chunk run" + (f" for {pub_date}" if pub_date else ""), err=True)
        raise typer.Exit(1)
    typer.echo(stats_line(stats_of(chunks)))
