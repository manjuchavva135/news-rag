"""`newsrag judge`: statistics of the judged groupings and the judge's sanity check (ticket 013)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Grouping judge: statistics and sanity check.", no_args_is_help=True)


def stats_lines(pages: list[dict], original_scores: dict[str, float | None]) -> list[str]:
    """The five lines of `judge stats` from `group_final` artifacts (each with a `page_id`);
    `original_scores` are the `judge` scores of the first groupings."""
    from newsrag.grouping.stats import summarize

    return lines_of(summarize(pages, original_scores))


def lines_of(n: dict) -> list[str]:
    def fmt(key: str, spec: str) -> str:
        return f"{key}={n[key]:{spec}}" if n[key] is not None else f"{key}=n/a"

    return [
        f"pages={n['pages']}",
        fmt("mean_score", ".3f"),
        fmt("judge_low_pct", ".1f"),
        fmt("retried_pct", ".1f"),
        fmt("retry_gain", ".3f"),
    ]


@app.command()
def stats(
    pages_file: Annotated[
        Path | None,
        typer.Option("--pages-file", help="Page ids, one per line ('#' comments); default all."),
    ] = None,
) -> None:
    """Pages judged, mean score of the kept groupings, share below the threshold, share regrouped
    and the mean score gain of the regrouped pages."""
    from newsrag.grouping.stats import stats as judge_stats
    from newsrag.stages.runner import read_pages_file

    page_ids = read_pages_file(pages_file) if pages_file is not None else None
    for line in lines_of(judge_stats(page_ids)):
        typer.echo(line)


@app.command()
def sanity(
    pages_file: Annotated[Path, typer.Option("--pages-file", help="Page ids, one per line.")],
    n: Annotated[int, typer.Option("--n", help="How many pages to corrupt.")] = 5,
) -> None:
    """Merge two articles that are surely different stories (both judged `correct` by the stored
    `judge`, distinct headlines) in memory only, judge the corrupted grouping and count the pages
    where the judge does not call the merged article `correct`."""
    from newsrag import config, models
    from newsrag.grouping import input as gin
    from newsrag.grouping import judge as jg
    from newsrag.stages.base import StageUnavailable
    from newsrag.stages.group import send
    from newsrag.stages.runner import read_pages_file

    try:
        jg.check_families()
    except StageUnavailable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    chosen = []
    for page_id in read_pages_file(pages_file):
        if len(chosen) >= n:
            break
        group_path = gin.done_artifact("group", page_id)
        row = gin.stage_row("judge", page_id)
        if group_path is None or row is None or row["status"] != "done":
            typer.echo(f"skip {page_id}: group and judge are not both done")
            continue
        grouping = gin.load_artifact(group_path)
        stored = gin.load_artifact(row["artifact_path"])
        pair = jg.pick_clean_pair(grouping, stored.get("verdicts") or {})
        if pair is None:
            typer.echo(f"skip {page_id}: no two correct news articles with distinct headlines")
            continue
        chosen.append((page_id, pair, jg.merge_articles(grouping, *pair), stored.get("score")))
    if not chosen:
        typer.echo("no page with a clean pair of news articles and a stored judge score", err=True)
        raise typer.Exit(1)
    caught = 0
    with models.use(config.settings.judge_model) as client:
        ask = send(jg.make_create(client))
        for page_id, (first, second), corrupted, stored in chosen:
            result = jg.judge_grouping(gin.load_ocr_final(page_id), corrupted, ask=ask)
            verdict = result["verdicts"].get(first, jg.MISSING)
            hit = jg.merge_caught(verdict)
            caught += hit
            pair = f"{first.rsplit('#', 1)[-1]}+{second.rsplit('#', 1)[-1]}"
            typer.echo(
                f"{page_id} pair={pair} verdict={verdict} stored={stored} "
                f"merged={result['score']} {'caught' if hit else 'missed'}"
            )
    typer.echo(f"caught: {caught}/{len(chosen)}")
