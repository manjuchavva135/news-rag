"""Qdrant client: a lazy ``QdrantClient`` on ``settings.qdrant_url``."""

from __future__ import annotations

import threading

import httpx
from qdrant_client import QdrantClient

from newsrag.config import settings

_client: QdrantClient | None = None
_lock = threading.Lock()


def client() -> QdrantClient:
    """The shared client, created on first use."""
    global _client
    with _lock:
        if _client is None:
            _client = QdrantClient(url=settings.qdrant_url)
        return _client


def ping(timeout_s: float = 2.0, url: str | None = None) -> bool:
    """True when ``GET /healthz`` answers 200 (`url` defaults to ``settings.qdrant_url``)."""
    try:
        base = (url or settings.qdrant_url).rstrip("/")
        response = httpx.get(f"{base}/healthz", timeout=timeout_s)
        return response.status_code == 200
    except Exception:  # noqa: BLE001 - ping() must never raise (FR-4)
        return False
