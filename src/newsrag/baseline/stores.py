"""Clients of the baseline stores (ticket 027 FR-1): lazy, on the `baseline_*` settings."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psycopg

from newsrag import config
from newsrag.stores import os_store, pg_store, qdrant_store

_lock = threading.Lock()
_clients: dict[str, Any] = {}


def pg_connect(**kwargs: Any) -> psycopg.Connection:
    """A new connection to the baseline Postgres."""
    return psycopg.connect(config.settings.baseline_postgres_dsn, **kwargs)


def qdrant() -> Any:
    from qdrant_client import QdrantClient

    with _lock:
        if "qdrant" not in _clients:
            _clients["qdrant"] = QdrantClient(url=config.settings.baseline_qdrant_url, timeout=120)
        return _clients["qdrant"]


def opensearch() -> Any:
    from opensearchpy import OpenSearch

    with _lock:
        if "opensearch" not in _clients:
            _clients["opensearch"] = OpenSearch(
                hosts=[config.settings.baseline_opensearch_url], timeout=120
            )
        return _clients["opensearch"]


def ping_all(timeout_s: float = 2.0) -> dict[str, bool]:
    """Ping the three baseline stores at once; keys `postgres`, `qdrant`, `opensearch`."""
    s = config.settings
    pings = {
        "postgres": lambda: pg_store.ping(timeout_s, dsn=s.baseline_postgres_dsn),
        "qdrant": lambda: qdrant_store.ping(timeout_s, url=s.baseline_qdrant_url),
        "opensearch": lambda: os_store.ping(timeout_s, url=s.baseline_opensearch_url),
    }
    with ThreadPoolExecutor(max_workers=len(pings)) as pool:
        futures = {name: pool.submit(ping) for name, ping in pings.items()}
        return {name: future.result() for name, future in futures.items()}
