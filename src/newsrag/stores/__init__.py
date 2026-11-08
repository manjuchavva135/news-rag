"""The three backend stores: Postgres, Qdrant and OpenSearch (ICU)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from newsrag.stores import os_store, pg_store, qdrant_store

pg = pg_store.client
qdrant = qdrant_store.client
opensearch = os_store.client

_PINGS = {
    "postgres": pg_store.ping,
    "qdrant": qdrant_store.ping,
    "opensearch": os_store.ping,
}


def ping_all(timeout_s: float = 2.0) -> dict[str, bool]:
    """Ping every store at once; keys ``postgres``, ``qdrant``, ``opensearch``."""
    with ThreadPoolExecutor(max_workers=len(_PINGS)) as pool:
        futures = {name: pool.submit(ping, timeout_s) for name, ping in _PINGS.items()}
        return {name: future.result() for name, future in futures.items()}


__all__ = ["opensearch", "pg", "ping_all", "qdrant"]
