"""`newsrag group`: overlay of a page's articles, the unassigned share (ticket 012) and the
model bake-off (ticket 014)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from newsrag import config

app = typer.Typer(help="Article grouping: overlays and statistics.", no_args_is_help=True)

OVERLAY_STAGES = ("group", "group_final")


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


@app.command()
def overlay(
    page_id: str,
    stage: Annotated[
        str, typer.Option("--stage", help="group | group_final (default group).")
    ] = "group",
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", help="PNG to write (default <reports_dir>/overlays/<page_id>-<stage>.png)."
        ),
    ] = None,
) -> None:
    """Draw the page's articles (coloured blocks, number and kind, flagged ones in red)."""
    from newsrag.grouping.input import done_artifact, load_ocr_final
    from newsrag.grouping.overlay import draw_articles

    if stage not in OVERLAY_STAGES:
        raise _fail(f"--stage must be one of {', '.join(OVERLAY_STAGES)}", 2)
    path = done_artifact(stage, page_id)
    if path is None:
        raise _fail(f"no {stage} artifact for {page_id}", 1)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    final = load_ocr_final(page_id)
    image = (final.get("image") or {}).get("path") or (
        Path(config.settings.images_dir) / f"{page_id}.png"
    )
    target = out or Path(config.settings.reports_dir) / "overlays" / f"{page_id}-{stage}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    draw_articles(image, artifact, blocks=final["blocks"]).save(target, format="PNG")
    typer.echo(str(target))


def stats_line(pages: int, failed: int, text_blocks: int, unassigned: int) -> str:
    pct = unassigned / text_blocks * 100 if text_blocks else 0.0
    return (
        f"pages={pages} failed={failed} text_blocks={text_blocks} "
        f"unassigned={unassigned} pct={pct:.1f}"
    )


def count_page(final: dict, artifact: dict) -> tuple[int, int]:
    """(text blocks, unassigned text blocks) of one page; text blocks = blocks whose label is in
    `text_labels` or `title_labels`."""
    s = config.settings
    text = {*s.text_labels, *s.title_labels}
    ids = {b["block_id"] for b in final.get("blocks") or [] if b.get("label") in text}
    return len(ids), len(ids & set(artifact.get("unassigned") or []))


@app.command()
def stats(
    pages_file: Annotated[
        Path, typer.Option("--pages-file", help="Page ids, one per line ('#' comments).")
    ],
) -> None:
    """Pages grouped, failed pages and the share of text blocks left unassigned."""
    from newsrag.grouping.input import load_ocr_final
    from newsrag.ledger import db
    from newsrag.stages.runner import read_pages_file

    page_ids = read_pages_file(pages_file)
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            "select page_id, status, artifact_path from stage_runs "
            "where stage = 'group' and page_id = any(%s)",
            (page_ids,),
        )
    failed = sum(1 for r in rows if r["status"] == "failed")
    pages = blocks = unassigned = 0
    for row in rows:
        if row["status"] != "done" or not row["artifact_path"]:
            continue
        artifact = json.loads(Path(row["artifact_path"]).read_text(encoding="utf-8"))
        n_text, n_left = count_page(load_ocr_final(row["page_id"]), artifact)
        pages, blocks, unassigned = pages + 1, blocks + n_text, unassigned + n_left
    typer.echo(stats_line(pages, failed, blocks, unassigned))


@app.command()
def bakeoff(
    candidates: Annotated[
        str | None,
        typer.Option("--candidates", help="Comma-separated profiles (default bakeoff_candidates)."),
    ] = None,
    judge: Annotated[
        str | None, typer.Option("--judge", help="Judge profile (default judge_model).")
    ] = None,
    pages_file: Annotated[
        Path | None,
        typer.Option("--pages-file", help="Bake-off pages (default bakeoff_pages_file)."),
    ] = None,
    rerun: Annotated[bool, typer.Option("--rerun", help="Ignore saved outputs.")] = False,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Write the existing report's winner into pipeline.yaml."),
    ] = False,
) -> None:
    """Grouping model bake-off (ticket 014): candidates group the same pages, the judge scores
    them, the report names the winner; `--apply` writes it as group_model (and the judge as
    judge_model)."""
    from newsrag.grouping import bakeoff as bk

    s = config.settings
    if apply:
        try:
            winner, judge_name = bk.apply_report()
        except (FileNotFoundError, ValueError) as exc:
            raise _fail(str(exc), 1) from None
        typer.echo(f"group_model set to {winner}")
        typer.echo(f"judge_model set to {judge_name}")
        return
    names = (
        [c.strip() for c in candidates.split(",") if c.strip()]
        if candidates
        else list(s.bakeoff_candidates)
    )
    judge_name = judge or s.judge_model
    try:
        bk.check_request(names, judge_name)
    except bk.BakeoffError as exc:
        raise _fail(str(exc), 2) from None
    pages = bk.ensure_pages_file(pages_file or s.bakeoff_pages_file, s.dev_sample_file)
    headlines = bk.ensure_headlines_file(s.bakeoff_telugu_file, s.dev_sample_file, _load_ocr_final)
    _rows, winner, path = bk.run_bakeoff(
        names, judge_name, pages, headlines, rerun=rerun, echo=typer.echo
    )
    typer.echo(path.read_text(encoding="utf-8"))
    typer.echo(str(path))
    if winner is None:
        raise typer.Exit(1)


def _load_ocr_final(page_id: str) -> dict:
    from newsrag.grouping.input import load_ocr_final

    return load_ocr_final(page_id)
