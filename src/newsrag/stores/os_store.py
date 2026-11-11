"""OpenSearch client: a lazy ``OpenSearch`` on ``settings.opensearch_url``."""

from __future__ import annotations

import threading

import httpx
from opensearchpy import OpenSearch

from newsrag.config import settings

_client: OpenSearch | None = None
_lock = threading.Lock()


def client() -> OpenSearch:
    """The shared client, created on first use."""
    global _client
    with _lock:
        if _client is None:
            _client = OpenSearch(hosts=[settings.opensearch_url])
        return _client


def ping(timeout_s: float = 2.0, url: str | None = None) -> bool:
    """True when ``GET /_cluster/health`` answers with a status other than ``red`` (`url`
    defaults to ``settings.opensearch_url``)."""
    try:
        url = f"{(url or settings.opensearch_url).rstrip('/')}/_cluster/health"
        response = httpx.get(url, timeout=timeout_s)
        response.raise_for_status()
        status = response.json().get("status")
        return status is not None and status != "red"
    except Exception:  # noqa: BLE001 - ping() must never raise (FR-4)
        return False
