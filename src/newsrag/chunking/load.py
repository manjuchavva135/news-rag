"""Reading the inputs of a date's chunks: dedup stories, enrichment, ticket-015 items, page
editions, page flags, the alias table and `editions.yaml` (ticket 023)."""

from __future__ import annotations

from datetime import date
from typing import Any

from newsrag.chunking.build import DateInputs, chunkable, story_page_ids


def done_dates() -> dict[date, dict[str, Any]]:
    """Print date -> the detail of its done `chunk` run."""
    from newsrag.ledger import db

    with db.conn() as c:
        rows = db.fetch_all(
            c,
            "select pub_date, detail from date_runs where stage = 'chunk' and status = 'done' "
            "order by pub_date",
        )
    return {r["pub_date"]: r["detail"] or {} for r in rows}


def page_artifacts(stage: str, page_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The done artifact of `stage` of each page that has one."""
    from newsrag.grouping import input as gin

    out: dict[str, dict[str, Any]] = {}
    for page_id in page_ids:
        path = gin.done_artifact(stage, page_id)
        if path is not None:
            out[page_id] = gin.load_artifact(path)
    return out


def load_page_editions(page_ids: list[str]) -> dict[str, dict[str, Any]]:
    from newsrag.ledger import db

    if not page_ids:
        return {}
    with db.conn() as c:
        rows = db.fetch_all(
            c, "select * from page_editions where page_id = any(%s)", (list(page_ids),)
        )
    return {r["page_id"]: r for r in rows}


def load_inputs(pub_date: date) -> DateInputs:
    from newsrag import aliases, enrich
    from newsrag.dedup import load as dedup_load
    from newsrag.editions import store

    day = pub_date.isoformat()
    path = dedup_load.done_dedup(pub_date).get(day)
    if path is None:
        raise FileNotFoundError(f"no done dedup artifact for {day}")
    stories = dedup_load.load_dedup(path)["stories"]
    page_ids = sorted({p for s in stories if chunkable(s) for p in story_page_ids(s)})
    transcribed = page_artifacts("transcribe", page_ids)
    missing = [p for p in page_ids if p not in transcribed]
    if missing:
        raise FileNotFoundError(f"no done transcribe artifact for {', '.join(missing[:3])}")
    grouped = page_artifacts("group_final", page_ids)
    page_flags = {
        p: [
            *(grouped.get(p, {}).get("page_flags") or []),
            *(transcribed[p].get("page_flags") or []),
        ]
        for p in page_ids
    }
    editions = store.as_plain(store.load_editions()).get(pub_date.year, {})
    return DateInputs(
        pub_date=pub_date,
        stories=stories,
        enriched=enrich.load(pub_date),
        items={p: list(transcribed[p].get("items") or []) for p in page_ids},
        page_editions=load_page_editions(page_ids),
        page_flags=page_flags,
        name_to_entity=aliases.load_entities()["name_to_entity"],
        editions=editions,
    )
