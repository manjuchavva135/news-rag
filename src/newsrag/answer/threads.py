"""The threads and their turns in Postgres. Every connection has ``search_path`` set to
``settings.answer_schema`` (tests use a throw-away schema)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from newsrag import config
from newsrag.stores import pg

SQL_DIR = Path(__file__).parent / "sql"
TITLE_CHARS = 80


class UnknownThread(LookupError):
    """No thread with this id."""


@dataclass
class Turn:
    turn_no: int
    question: str
    answer: str
    search_query: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)  # Source.record() of each
    cited: list[int] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


def schema() -> str:
    return config.settings.answer_schema


def new_thread_id() -> str:
    return uuid.uuid4().hex[:12]


def title_of(question: str) -> str:
    return " ".join(question.split())[:TITLE_CHARS]


@contextmanager
def conn() -> Iterator[psycopg.Connection]:
    """A pooled connection with search_path = <answer schema>, public; one transaction."""
    with pg().connection() as c:
        c.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema())))
        yield c


def migrate() -> list[str]:
    """Create the schema if missing and apply new migrations in order. Returns applied names."""
    applied: list[str] = []
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
        for path in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            if path.name in done:
                continue
            c.execute(path.read_text(encoding="utf-8"))
            c.execute("insert into schema_migrations (filename) values (%s)", (path.name,))
            applied.append(path.name)
    return applied


class PgStore:
    """The thread store of the service (tests pass an in-memory one with the same methods)."""

    def create_thread(self, question: str) -> str:
        thread_id = new_thread_id()
        with conn() as c:
            c.execute(
                "insert into threads (thread_id, title) values (%s, %s)",
                (thread_id, title_of(question)),
            )
        return thread_id

    def load_turns(self, thread_id: str) -> list[Turn]:
        """Every turn of the thread, oldest first; `UnknownThread` when there is no such thread."""
        with conn() as c, c.cursor(row_factory=dict_row) as cur:
            cur.execute("select 1 from threads where thread_id = %s", (thread_id,))
            if cur.fetchone() is None:
                raise UnknownThread(thread_id)
            cur.execute(
                "select turn_no, question, answer, search_query, sources, cited, provider, "
                "model, usage from turns where thread_id = %s order by turn_no",
                (thread_id,),
            )
            return [Turn(**row) for row in cur.fetchall()]

    def add_turn(self, thread_id: str, turn: Turn) -> int:
        """Append the turn; its number is the next one of the thread (returned)."""
        with conn() as c:
            c.execute("select 1 from threads where thread_id = %s for update", (thread_id,))
            row = c.execute(
                "insert into turns (thread_id, turn_no, question, search_query, answer, sources,"
                " cited, provider, model, usage) "
                "select %s, coalesce(max(turn_no), 0) + 1, %s, %s, %s, %s, %s, %s, %s, %s "
                "from turns where thread_id = %s returning turn_no",
                (
                    thread_id,
                    turn.question,
                    turn.search_query,
                    turn.answer,
                    Jsonb(turn.sources),
                    turn.cited,
                    turn.provider,
                    turn.model,
                    Jsonb(turn.usage),
                    thread_id,
                ),
            ).fetchone()
            c.execute("update threads set updated_at = now() where thread_id = %s", (thread_id,))
        return int(row[0])

    def list_threads(self, limit: int = 20) -> list[dict[str, Any]]:
        with conn() as c, c.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "select t.thread_id, t.title, t.updated_at, count(u.turn_no) as turns "
                "from threads t left join turns u using (thread_id) "
                "group by t.thread_id order by t.updated_at desc limit %s",
                (limit,),
            )
            return cur.fetchall()
