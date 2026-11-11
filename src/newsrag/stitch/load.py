"""Reading a date's pages and artifacts from the ledger and the work folder."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.stitch import markers as mk
from newsrag.stitch.match import PageInfo


def page_info(row: Any) -> PageInfo:
    """A `PageRow` (or a ledger row dict) as a `PageInfo`."""
    get = row.get if isinstance(row, dict) else lambda k: getattr(row, k)
    return PageInfo(
        page_id=get("page_id"),
        edition_code=get("edition_code"),
        section=get("section"),
        page_num=int(get("page_num")),
        page_end=get("page_end"),
    )


def patterns() -> list[mk.Pattern]:
    return mk.load_patterns(config.settings.continuations_file)


def date_pages(pub_date: date) -> list[PageInfo]:
    """The date's non-quarantined pages, in page order."""
    from newsrag.ledger import db

    with db.conn() as c:
        rows = db.fetch_all(
            c,
            "select * from pages where pub_date = %s and quarantine_reason is null "
            "order by section, page_num, edition_code, page_id",
            (pub_date,),
        )
    return [page_info(r) for r in rows]


def group_final_artifacts(pages: Sequence[PageInfo]) -> dict[str, dict[str, Any]]:
    """The done `group_final` artifact of each page that has one."""
    out: dict[str, dict[str, Any]] = {}
    for page in pages:
        path = gin.done_artifact("group_final", page.page_id)
        if path is not None:
            out[page.page_id] = gin.load_artifact(path)
    return out


class OcrBlocks(Mapping[str, Mapping[str, Mapping[str, Any]]]):
    """`blocks[page_id][block_id]`: the page's `ocr_final` blocks, read when first asked for."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Mapping[str, Any]]] = {}

    def __getitem__(self, page_id: str) -> Mapping[str, Mapping[str, Any]]:
        if page_id not in self._cache:
            data = gin.load_ocr_final(page_id)
            self._cache[page_id] = {b["block_id"]: b for b in data.get("blocks") or []}
        return self._cache[page_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._cache)

    def __len__(self) -> int:
        return len(self._cache)


def done_stitch(pub_date: date | None = None) -> dict[str, Path]:
    """Print date (ISO) -> the done `stitch` artifact (of one date, or of every date)."""
    from newsrag.ledger import db

    where, params = "stage = 'stitch' and status = 'done' and artifact_path is not null", []
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


def load_stitch(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
