"""Reading a date's inputs and the done `dedup` artifacts."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from newsrag.dedup.units import Unit, build_units
from newsrag.grouping import input as gin
from newsrag.stages.base import PageRow


def date_items(page_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """The `items` of each page's done `transcribe` artifact."""
    out: dict[str, list[dict[str, Any]]] = {}
    for page_id in page_ids:
        path = gin.done_artifact("transcribe", page_id)
        if path is None:
            raise FileNotFoundError(f"no done transcribe artifact for {page_id}")
        out[page_id] = list(gin.load_artifact(path).get("items") or [])
    return out


def ocr_boxes(page_id: str) -> dict[str, list[float]]:
    """block_id -> bbox of the page's `ocr_final` blocks."""
    data = gin.load_ocr_final(page_id)
    return {b["block_id"]: b["bbox"] for b in data.get("blocks") or [] if b.get("bbox")}


def date_page_rows(pub_date: date | str) -> list[PageRow]:
    """The date's non-quarantined pages."""
    from newsrag.ledger import db

    day = date.fromisoformat(pub_date) if isinstance(pub_date, str) else pub_date
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            "select * from pages where pub_date = %s and quarantine_reason is null "
            "order by page_id",
            (day,),
        )
    return [PageRow.from_row(r) for r in rows]


def date_units(pub_date: date, pages: list[PageRow]) -> tuple[list[Unit], dict[str, int]]:
    """The units of the date, from its stitched articles and its transcribe items. `pages` are
    the date's non-quarantined `PageRow`s."""
    from newsrag.stitch import stitched_articles

    page_ids = [p.page_id for p in pages]
    items = date_items(page_ids)
    return build_units(stitched_articles(pub_date), items, {p.page_id: p for p in pages}, ocr_boxes)


def done_dedup(pub_date: date | None = None) -> dict[str, Path]:
    """Print date (ISO) -> the done `dedup` artifact (of one date, or of every date)."""
    from newsrag.ledger import db

    where, params = "stage = 'dedup' and status = 'done' and artifact_path is not null", []
    if pub_date is not None:
        where += " and pub_date = %s"
        params.append(pub_date)
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            f"select pub_date, artifact_path from date_runs where {where} order by pub_date",
            params,
        )
    return {
        r["pub_date"].isoformat(): Path(r["artifact_path"])
        for r in rows
        if Path(r["artifact_path"]).is_file()
    }


def load_dedup(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
