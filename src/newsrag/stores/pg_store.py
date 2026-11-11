"""Postgres client: a lazy psycopg 3 connection pool on ``settings.postgres_dsn``."""

from __future__ import annotations

import math
import threading

import psycopg
from psycopg_pool import ConnectionPool

from newsrag.config import settings

_pool: ConnectionPool | None = None
_lock = threading.Lock()


def _reset(conn: psycopg.Connection) -> None:
    """Undo session settings (e.g. the ledger's search_path) when a connection is returned."""
    conn.autocommit = True
    try:
        conn.execute("RESET ALL")
    finally:
        conn.autocommit = False


def client() -> ConnectionPool:
    """The shared connection pool, created on first use."""
    global _pool
    with _lock:
        if _pool is None:
            _pool = ConnectionPool(
                settings.postgres_dsn,
                min_size=1,
                max_size=settings.pg_pool_max_size,
                open=True,
                name="newsrag",
                reset=_reset,
            )
        return _pool


def ping(timeout_s: float = 2.0, dsn: str | None = None) -> bool:
    """True when ``select 1`` succeeds. Uses a direct connection so a down server fails fast.

    `dsn` defaults to ``settings.postgres_dsn`` (ticket 027 pings the baseline store)."""
    try:
        connect_timeout = max(1, math.ceil(timeout_s))  # libpq takes whole seconds
        with psycopg.connect(dsn or settings.postgres_dsn, connect_timeout=connect_timeout) as conn:
            conn.execute(f"set statement_timeout = {int(timeout_s * 1000)}")
            return conn.execute("select 1").fetchone() == (1,)
    except Exception:  # noqa: BLE001 - ping() must never raise (FR-4)
        return False
