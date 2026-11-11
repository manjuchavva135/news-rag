"""Writing the warehouse (ticket 024 FR-3, FR-6): one transaction replaces a date's rows."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from newsrag import config
from newsrag.warehouse import db
from newsrag.warehouse.rows import COLUMNS, ENTITY_COLUMNS, JSON_COLUMNS, DateRows, entity_rows

# tables keyed by story id, deleted and refilled with the date's stories
STORY_TABLES = ("story_printings", "story_entities", "story_places", "facts")


def _insert(table: str, columns: Sequence[str], conflict: str = "") -> sql.Composed:
    return sql.SQL("insert into {} ({}) values ({}){}").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.SQL(conflict),
    )


def _params(rows: Iterable[dict[str, Any]], columns: Sequence[str]) -> list[tuple[Any, ...]]:
    return [tuple(Jsonb(r[c]) if c in JSON_COLUMNS else r[c] for c in columns) for r in rows]


def _batches(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _insert_rows(
    c: psycopg.Connection,
    table: str,
    rows: list[dict[str, Any]],
    conflict: str = "",
    columns: Sequence[str] | None = None,
) -> None:
    columns = columns or COLUMNS[table]
    query = _insert(table, columns, conflict)
    size = max(1, config.settings.index_write_batch)
    with c.cursor() as cur:
        for batch in _batches(_params(rows, columns), size):
            cur.executemany(query, batch)


def load_pages(pub_date: date, page_ids: Iterable[str]) -> list[dict[str, Any]]:
    """The ledger rows (`pages` joined with `page_editions`) of every page of the date and of
    the given page ids."""
    from newsrag.ledger import db as ledger_db

    with ledger_db.conn() as c:
        return ledger_db.fetch_all(
            c,
            "select p.page_id, p.pub_date, p.edition_code, p.section, p.page_num, p.page_end, "
            "e.edition_names, e.districts_current, e.states "
            "from pages p left join page_editions e using (page_id) "
            "where p.pub_date = %s or p.page_id = any(%s) order by p.page_id",
            (pub_date, sorted(set(page_ids))),
        )


def replace_date(pub_date: date, generation: str, rows: DateRows) -> dict[str, int]:
    """FR-3: in one transaction delete every warehouse row of the date's stories (the stories
    already stored for the date, those no longer present, and the new ones) and the `chunks` of
    `generation` of the date, then insert the new rows. Pages are upserted.

    Returns the rows deleted from `stories` and `chunks` and `removed`, the number of stories of
    the date that are no longer present."""
    new_ids = rows.story_ids()
    deleted: dict[str, int] = {}
    with db.conn() as c:
        old_ids = [
            r[0] for r in c.execute("select story_id from stories where pub_date = %s", (pub_date,))
        ]
        ids = sorted({*old_ids, *new_ids})
        deleted["removed"] = len(set(old_ids) - set(new_ids))
        for table in STORY_TABLES:
            c.execute(
                sql.SQL("delete from {} where story_id = any(%s)").format(sql.Identifier(table)),
                (ids,),
            )
        deleted["stories"] = c.execute(
            "delete from stories where pub_date = %s or story_id = any(%s)", (pub_date, ids)
        ).rowcount
        deleted["chunks"] = c.execute(
            "delete from chunks where generation = %s and (pub_date = %s or story_id = any(%s))",
            (generation, pub_date, ids),
        ).rowcount
        page_cols = COLUMNS["pages"]
        updates = ", ".join(f'"{col}" = excluded."{col}"' for col in page_cols[1:])
        _insert_rows(c, "pages", rows.pages, f" on conflict (page_id) do update set {updates}")
        for table in ("stories", "story_printings", "chunks", "story_entities", "story_places"):
            _insert_rows(c, table, getattr(rows, table))
        _insert_rows(c, "facts", rows.facts)
    return deleted


def sync_entities(entities: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """FR-6: upsert every entity of the alias table and delete the rows whose id is not in it.
    Returns (upserted, deleted)."""
    rows = entity_rows(entities)
    ids = [r["entity_id"] for r in rows]
    updates = ", ".join(f'"{col}" = excluded."{col}"' for col in ENTITY_COLUMNS[1:])
    with db.conn() as c:
        _insert_rows(
            c,
            "entities",
            rows,
            f" on conflict (entity_id) do update set {updates}",
            columns=ENTITY_COLUMNS,
        )
        deleted = c.execute("delete from entities where entity_id <> all(%s)", (ids,)).rowcount
    return len(rows), deleted


def counts(generation: str, pub_date: date | None = None) -> dict[date, int]:
    """Postgres `chunks` rows of the generation, per print date (of one date when given)."""
    where, params = "generation = %s", [generation]
    if pub_date is not None:
        where += " and pub_date = %s"
        params.append(pub_date)  # type: ignore[arg-type]
    with db.conn() as c:
        rows = c.execute(
            f"select pub_date, count(*) from chunks where {where} group by pub_date", params
        ).fetchall()
    return {row[0]: row[1] for row in rows}
