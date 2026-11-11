"""Warehouse connections and migrations. Every connection comes from the shared pool with
``search_path`` set to ``settings.warehouse_schema`` (tests use a throw-away schema)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from newsrag import config
from newsrag.stores import pg

SQL_DIR = Path(__file__).parent / "sql"


def schema() -> str:
    return config.settings.warehouse_schema


@contextmanager
def conn() -> Iterator[psycopg.Connection]:
    """A pooled connection with search_path = <warehouse schema>, public.

    The block is one transaction: committed on normal exit, rolled back on an exception.
    """
    with pg().connection() as c:
        c.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema())))
        yield c


def fetch_all(c: psycopg.Connection, query: Any, params: Any = None) -> list[dict[str, Any]]:
    with c.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


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


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def is_initialised() -> bool:
    """True when the warehouse schema holds the `chunks` and `active_generation` tables."""
    with pg().connection() as c:
        row = c.execute(
            "select to_regclass(%s) is not null and to_regclass(%s) is not null",
            (f"{_quote(schema())}.chunks", f"{_quote(schema())}.active_generation"),
        ).fetchone()
    return bool(row and row[0])


def active_generation() -> str | None:
    """The generation the `chunks` aliases and the search read, or None before the first one."""
    with conn() as c:
        row = c.execute("select generation from active_generation").fetchone()
    return row[0] if row else None


def set_active_generation(generation: str) -> None:
    with conn() as c:
        c.execute(
            "insert into active_generation (singleton, generation) values (true, %s) "
            "on conflict (singleton) do update set generation = excluded.generation",
            (generation,),
        )
