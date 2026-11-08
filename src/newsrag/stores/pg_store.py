"""Postgres client: a lazy psycopg 3 connection pool on ``settings.postgres_dsn``."""

from __future__ import annotations

import math
import threading

import psycopg
from psycopg_pool import ConnectionPool

from newsrag.config import settings

_pool: ConnectionPool | None = None
_lock = threading.Lock()


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
            )
        return _pool


def ping(timeout_s: float = 2.0) -> bool:
    """True when ``select 1`` succeeds. Uses a direct connection so a down server fails fast."""
    try:
        connect_timeout = max(1, math.ceil(timeout_s))  # libpq takes whole seconds
        with psycopg.connect(settings.postgres_dsn, connect_timeout=connect_timeout) as conn:
            conn.execute(f"set statement_timeout = {int(timeout_s * 1000)}")
            return conn.execute("select 1").fetchone() == (1,)
    except Exception:  # noqa: BLE001 - ping() must never raise (FR-4)
        return False
