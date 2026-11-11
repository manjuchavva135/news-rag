"""`newsrag enrich`: statistics and the quality check of `enrich_core` (ticket 019 FR-5, NFR-1),
the place statistics (ticket 020 FR-5) and the facts statistics and quality check (ticket 021
FR-4, NFR-1)."""

from __future__ import annotations

import math
from typing import Annotated, Any

import typer

app = typer.Typer(help="Story enrichment.", no_args_is_help=True)


def totals(artifacts: list[dict[str, Any]], details: list[dict[str, Any]]) -> dict[str, Any]:
    """The FR-5 numbers over `enrich_core` artifacts and the details of their runs.

    `stories_per_s` is the total of stories over the total request seconds of the runs.
    """
    from newsrag.enrich import validate as v

    records = [r for art in artifacts for r in (art.get("stories") or {}).values()]
    n = len(records)
    out: dict[str, Any] = {
        f"category_{name}": sum(1 for r in records if r["category"] == name)
        for name in v.CATEGORIES
    }
    out["people_per_story"] = round(sum(len(r["people"]) for r in records) / n, 2) if n else 0.0
    out["orgs_per_story"] = (
        round(sum(len(r["organisations"]) for r in records) / n, 2) if n else 0.0
    )
    out["dropped_names"] = sum(int(d.get("dropped_names") or 0) for d in details)
    out["event_date_differs"] = sum(
        1 for art in artifacts for r in (art.get("stories") or {}).values()
        if r["event_date"] != art["pub_date"]
    )  # fmt: skip
    out["enrich_failed"] = sum(1 for r in records if v.FAILED_FLAG in r["flags"])
    seconds = sum(float(d.get("request_seconds") or 0) for d in details)
    stories = sum(int(d.get("stories") or 0) for d in details)
    out["stories_per_s"] = round(stories / seconds, 2) if seconds > 0 else 0.0
    return out


def _done_runs(
    days: list[str] | None, stage: str = "enrich_core"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(artifacts, run details) of the done `stage` runs of the given dates (default all)."""
    from datetime import date

    from newsrag import enrich
    from newsrag.ledger import db

    where = "stage = %s and status = 'done' and artifact_path is not null"
    params: list[Any] = [stage]
    if days:
        where += " and pub_date = any(%s)"
        params.append([date.fromisoformat(d) for d in days])
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            f"select pub_date, artifact_path, detail from date_runs where {where} "
            "order by pub_date",
            params,
        )
    artifacts, details = [], []
    for row in rows:
        artifacts.append(enrich.read_artifact(row["artifact_path"]))
        details.append(row["detail"] or {})
    return artifacts, details


def _done_place_artifacts(days: list[str] | None) -> list[dict[str, Any]]:
    """The artifacts of the done `enrich_places` runs of the given dates (default all)."""
    from newsrag import enrich

    if not days:
        return [enrich.read_artifact(p) for p in enrich.done_artifacts("enrich_places").values()]
    found = {}
    for d in days:
        found.update(enrich.done_artifacts("enrich_places", d))
    return [enrich.read_artifact(p) for _, p in sorted(found.items())]


def _places_stats(days: list[str] | None) -> None:
    from newsrag.enrich import places

    artifacts = _done_place_artifacts(days)
    if not artifacts:
        typer.echo(
            f"no done enrich_places artifact for {', '.join(days) if days else 'any date'}",
            err=True,
        )
        raise typer.Exit(1)
    out = places.place_totals(artifacts)
    typer.echo(f"places_per_story={out['places_per_story']}")
    for kind in places.RESOLVED_TYPES:
        t = out[kind]
        typer.echo(
            f"{kind}: resolved={t['resolved']} ambiguous={t['ambiguous']} "
            f"unresolved={t['unresolved']}"
        )
    typer.echo(f"combined_resolved={out['combined_resolved']}")
    typer.echo("top_unresolved (count name):")
    for name, count in out["top_unresolved"]:
        typer.echo(f"{count} {name}")


def _facts_stats(days: list[str] | None) -> None:
    from newsrag.enrich import facts

    artifacts, details = _done_runs(days, "enrich_facts")
    if not artifacts:
        typer.echo(
            f"no done enrich_facts artifact for {', '.join(days) if days else 'any date'}",
            err=True,
        )
        raise typer.Exit(1)
    for key, value in facts.fact_totals(artifacts, details).items():
        typer.echo(f"{key}={value}")


@app.command()
def stats(
    day: Annotated[
        list[str] | None,
        typer.Option("--date", help="Print date YYYY-MM-DD, repeatable (default: all done dates)."),
    ] = None,
    places: Annotated[
        bool,
        typer.Option("--places", help="Statistics of `enrich_places` instead of `enrich_core`."),
    ] = False,
    facts: Annotated[
        bool,
        typer.Option("--facts", help="Statistics of `enrich_facts` instead of `enrich_core`."),
    ] = False,
) -> None:
    """Prints category counts, people and organisations per story, dropped names, event dates that
    differ from the print date, failed stories and stories per second of `enrich_core`; with
    `--places` the place statistics of `enrich_places`: places per story, the share of resolved /
    ambiguous / unresolved district, mandal and city places, `combined_resolved` and the most
    frequent unresolved names; with `--facts` the statistics of `enrich_facts`: `facts_per_story`,
    the facts per unit and the facts dropped (not verbatim, not parseable)."""
    if places:
        _places_stats(day)
        return
    if facts:
        _facts_stats(day)
        return
    artifacts, details = _done_runs(day)
    if not artifacts:
        typer.echo(
            f"no done enrich_core artifact for {', '.join(day) if day else 'any date'}", err=True
        )
        raise typer.Exit(1)
    for key, value in totals(artifacts, details).items():
        typer.echo(f"{key}={value}")


@app.command("verify-core")
def verify_core(
    n: Annotated[int | None, typer.Option("--n", help="Stories to check.")] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="Sampling seed.")] = None,
    day: Annotated[
        str | None, typer.Option("--date", help="Only the stories of this date (default: all).")
    ] = None,
) -> None:
    """A second model judges the category and the names of random enriched stories; prints
    `category_ok=<a>/<n> names_ok=<b>/<n>` and exits 1 below the threshold."""
    from newsrag import config
    from newsrag.enrich import verify as vf
    from newsrag.stages.base import write_json_atomic

    s = config.settings
    stories = vf.collect_stories(day)
    if not stories:
        typer.echo("no enriched stories to verify", err=True)
        raise typer.Exit(1)
    report = vf.run_verify(
        stories,
        n if n is not None else s.enrich_verify_n,
        seed if seed is not None else s.enrich_verify_seed,
    )
    path = write_json_atomic(s.enrich_verify_file, report)
    typer.echo(f"model={report['model']} stories={report['n_stories_available']} report={path}")
    typer.echo(
        f"category_ok={report['category_ok']}/{report['n']} names_ok={report['names_ok']}/{report['n']}"
    )
    need = math.ceil(report["n"] * s.enrich_verify_min_ok_ratio - 1e-9)
    if report["category_ok"] < need or report["names_ok"] < need:
        raise typer.Exit(1)


@app.command("verify-facts")
def verify_facts(
    n: Annotated[int | None, typer.Option("--n", help="Facts to check.")] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="Sampling seed.")] = None,
    day: Annotated[
        str | None, typer.Option("--date", help="Only the facts of this date (default: all).")
    ] = None,
) -> None:
    """A second model judges random enriched facts (value, unit, scale and what it refers to);
    prints `correct: <k>/<n>` and exits 1 below the threshold."""
    from newsrag import config
    from newsrag.enrich import facts
    from newsrag.stages.base import write_json_atomic

    s = config.settings
    items = facts.collect_facts(day)
    if not items:
        typer.echo("no enriched facts to verify", err=True)
        raise typer.Exit(1)
    report = facts.run_verify(
        items,
        n if n is not None else s.enrich_facts_verify_n,
        seed if seed is not None else s.enrich_facts_verify_seed,
    )
    path = write_json_atomic(s.enrich_facts_verify_file, report)
    typer.echo(f"model={report['model']} facts={report['n_facts_available']} report={path}")
    typer.echo(f"correct: {report['correct']}/{report['n']}")
    need = math.ceil(report["n"] * s.enrich_verify_min_ok_ratio - 1e-9)
    if report["correct"] < need:
        raise typer.Exit(1)
