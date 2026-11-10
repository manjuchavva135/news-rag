"""`newsrag judge`: statistics of the judged groupings and the judge's sanity check (ticket 013)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Grouping judge: statistics and sanity check.", no_args_is_help=True)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def stats_lines(pages: list[dict], original_scores: dict[str, float | None]) -> list[str]:
    """The five lines of `judge stats` from `group_final` artifacts (each with a `page_id`);
    `original_scores` are the `judge` scores of the first groupings."""
    n = len(pages)
    kept = [p["judge_score"] for p in pages if p["judge_score"] is not None]
    low = sum(1 for p in pages if "low_confidence:judge_low" in p.get("page_flags", []))
    retried = [p for p in pages if p["retried"]]
    gains = [
        p["judge_score"] - original_scores[p["page_id"]]
        for p in retried
        if p["judge_score"] is not None and original_scores.get(p["page_id"]) is not None
    ]
    mean_score = _mean(kept)
    gain = _mean(gains)
    return [
        f"pages={n}",
        f"mean_score={mean_score:.3f}" if mean_score is not None else "mean_score=n/a",
        f"judge_low_pct={low / n * 100:.1f}" if n else "judge_low_pct=n/a",
        f"retried_pct={len(retried) / n * 100:.1f}" if n else "retried_pct=n/a",
        f"retry_gain={gain:.3f}" if gain is not None else "retry_gain=n/a",
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
    from newsrag.grouping.input import load_artifact
    from newsrag.ledger import db
    from newsrag.stages.runner import read_pages_file

    where = "stage = %s and status = 'done' and artifact_path is not null"
    params: list = ["group_final"]
    if pages_file is not None:
        where += " and page_id = any(%s)"
        params.append(read_pages_file(pages_file))
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            f"select page_id, artifact_path from stage_runs where {where} order by page_id",
            params,
        )
        judged = db.fetch_all(
            c,
            "select page_id, artifact_path from stage_runs where stage = 'judge' "
            "and status = 'done' and artifact_path is not null and page_id = any(%s)",
            [[r["page_id"] for r in rows]],
        )
    pages = [{**load_artifact(r["artifact_path"]), "page_id": r["page_id"]} for r in rows]
    original = {r["page_id"]: load_artifact(r["artifact_path"]).get("score") for r in judged}
    for line in stats_lines(pages, original):
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
