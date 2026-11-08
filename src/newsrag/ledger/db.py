"""Ledger connections, migrations and page helpers.

Every ledger connection comes from the shared pool with ``search_path`` set to the ledger schema
(``settings.ledger_schema``, or a schema set with :func:`use_schema` - tests use a throw-away one).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from newsrag.config import settings
from newsrag.stores import pg

SQL_DIR = Path(__file__).parent / "sql"

_schema_override: str | None = None
_override_lock = threading.Lock()


def schema() -> str:
    """The schema the ledger lives in right now."""
    return _schema_override or settings.ledger_schema


@contextmanager
def use_schema(name: str) -> Iterator[None]:
    """Point every ledger connection in this process at schema ``name`` (tests)."""
    global _schema_override
    with _override_lock:
        previous, _schema_override = _schema_override, name
    try:
        yield
    finally:
        with _override_lock:
            _schema_override = previous


@contextmanager
def conn() -> Iterator[psycopg.Connection]:
    """A pooled connection with search_path = <ledger schema>, public.

    The block is one transaction: committed on normal exit, rolled back on an exception.
    """
    with pg().connection() as c:
        c.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema())))
        yield c


def fetch_all(c: psycopg.Connection, query: Any, params: Any = None) -> list[dict[str, Any]]:
    with c.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def fetch_one(c: psycopg.Connection, query: Any, params: Any = None) -> dict[str, Any] | None:
    with c.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchone()


def migration_files() -> list[Path]:
    return sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def migrate() -> list[str]:
    """Create the schema if missing and apply new migrations in order. Returns applied names."""
    applied_now: list[str] = []
    with pg().connection() as c:
        name = schema()
        c.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(name)))
        c.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(name)))
        c.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"newsrag-migrate:{name}",))
        c.execute(
            "create table if not exists schema_migrations ("
            " filename text primary key, applied_at timestamptz not null default now())"
        )
        done = {row[0] for row in c.execute("select filename from schema_migrations")}
        for path in migration_files():
            if path.name in done:
                continue
            c.execute(path.read_text(encoding="utf-8"))
            c.execute("insert into schema_migrations (filename) values (%s)", (path.name,))
            applied_now.append(path.name)
    return applied_now


PAGE_COLUMNS = (
    "page_id",
    "pub_date",
    "edition_code",
    "section",
    "page_num",
    "page_end",
    "source_path",
    "source_archive",
    "revision",
)


def upsert_page(**page: Any) -> None:
    """Insert or update one row of ``pages`` (registration itself is ticket 004)."""
    optional = ("page_end", "revision")
    missing = [col for col in PAGE_COLUMNS if col not in page and col not in optional]
    if missing:
        raise ValueError(f"upsert_page: missing {', '.join(missing)}")
    values = {col: page.get(col) for col in PAGE_COLUMNS}
    if values["revision"] is None:
        values["revision"] = 1
    cols = sql.SQL(", ").join(sql.Identifier(col) for col in PAGE_COLUMNS)
    placeholders = sql.SQL(", ").join(sql.Placeholder(col) for col in PAGE_COLUMNS)
    updates = sql.SQL(", ").join(
        sql.SQL("{0} = excluded.{0}").format(sql.Identifier(col)) for col in PAGE_COLUMNS[1:]
    )
    query = sql.SQL(
        "insert into pages ({cols}, registered_at, updated_at) values ({vals}, now(), now()) "
        "on conflict (page_id) do update set {updates}, updated_at = now()"
    ).format(cols=cols, vals=placeholders, updates=updates)
    with conn() as c:
        c.execute(query, values)


def quarantine_page(page_id: str, reason: str) -> None:
    """Mark a page quarantined; it is never eligible for any stage again."""
    with conn() as c:
        row = fetch_one(
            c,
            "update pages set quarantine_reason = %s, updated_at = now() where page_id = %s "
            "returning source_path, source_archive",
            (reason, page_id),
        )
        if row is None:
            raise KeyError(f"unknown page {page_id}")
        c.execute(
            "insert into quarantine (source_path, source_archive, reason, detected_at) "
            "values (%s, %s, %s, now()) on conflict (source_path) do update set "
            "source_archive = excluded.source_archive, reason = excluded.reason, "
            "detected_at = now()",
            (row["source_path"], row["source_archive"], reason),
        )
