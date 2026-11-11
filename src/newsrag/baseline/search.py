"""`search(store="baseline")` (ticket 027 FR-3): the same plain hybrid search over the old
stores. Dense on Qdrant `chunks_old` (queries embedded with BGE-M3, the old vectors' model),
BM25 on OpenSearch `chunks_old`, RRF as for `new`. Each hit gets its text from `old.chunks`,
its headline and print date from `old.articles`, and as `page_ids` the new page ids its old page
maps to (empty when unmapped). A page filter never applies here.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.baseline import pages
from newsrag.search import hybrid
from newsrag.search.hybrid import Hit, StoreNotConfigured

_map_lock = threading.Lock()
_map_cache: dict[str, Any] = {"key": None, "map": {}}


def page_map() -> dict[str, list[str]]:
    """The page map CSV, re-read when it changes."""
    path = Path(config.settings.baseline_page_map_file)
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except FileNotFoundError:
        raise StoreNotConfigured(
            f"no baseline page map ({path}): run 'newsrag baseline map-pages' first"
        ) from None
    with _map_lock:
        if _map_cache["key"] != key:
            _map_cache.update(key=key, map=pages.read_page_map(path))
        return _map_cache["map"]


def dense_search(vector: Sequence[float], limit: int) -> list[dict[str, Any]]:
    from newsrag.baseline import stores

    response = stores.qdrant().query_points(
        config.settings.baseline_collection,
        query=list(vector),
        limit=limit,
        with_payload=["chunk_id", "story_id", "page_id"],
    )
    return [dict(point.payload or {}) for point in response.points]


def bm25_search(query: str, limit: int) -> list[dict[str, Any]]:
    from newsrag.baseline import stores

    body = hybrid.bm25_body(query, limit, {})
    body["_source"] = ["chunk_id", "story_id", "page_ids"]
    hits = stores.opensearch().search(index=config.settings.baseline_collection, body=body)
    out = []
    for h in hits["hits"]["hits"]:
        src = h.get("_source") or {}
        old_pages = src.get("page_ids") or []
        out.append(
            {
                "chunk_id": h["_id"],
                "story_id": src.get("story_id"),
                "page_id": old_pages[0] if old_pages else None,
            }
        )
    return out


def chunk_rows(chunk_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """chunk_id -> {story_id, page_id, text, headline, pub_date} from `old.chunks`/`old.articles`."""
    from psycopg import sql
    from psycopg.rows import dict_row

    from newsrag.baseline import stores

    if not chunk_ids:
        return {}
    query = sql.SQL(
        "select c.chunk_id, c.story_id, c.page_id, c.text, a.headline, a.pub_date "
        "from {s}.chunks c left join {s}.articles a on a.article_id = c.article_id "
        "where c.chunk_id = any(%s)"
    ).format(s=sql.Identifier(config.settings.baseline_schema))
    with stores.pg_connect() as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute(query, (list(chunk_ids),))
        return {r["chunk_id"]: r for r in cur.fetchall()}


def _unreachable(e: Exception) -> StoreNotConfigured:
    return StoreNotConfigured(
        f"baseline stores not reachable ({type(e).__name__}): run 'newsrag baseline up' "
        "and 'newsrag baseline load <bundle>'"
    )


def search_baseline(
    query: str,
    k: int = 10,
    filters: Mapping[str, Any] | None = None,
    generation: str | None = None,
    qdrant_collection: str | None = None,
    embed_profile: str | None = None,
) -> list[Hit]:
    """FR-3 for store `baseline`. `generation` and `qdrant_collection` don't apply."""
    s = config.settings
    if hybrid.check_filters(filters).get("page_ids") is not None:
        raise ValueError("--restrict-pages applies to store new only")
    mapping = page_map()
    limit = max(k, s.search_candidates)
    vector = hybrid.embed_query(query, embed_profile or s.baseline_embed_profile)
    try:
        dense = dense_search(vector, limit)
        sparse = bm25_search(query, limit)
    except Exception as e:
        if _is_connection_error(e):
            raise _unreachable(e) from e
        raise

    meta: dict[str, dict[str, Any]] = {}
    for item in [*dense, *sparse]:
        if item.get("chunk_id"):
            meta.setdefault(str(item["chunk_id"]), item)
    fused = hybrid.rrf_fuse(
        [[str(d["chunk_id"]) for d in dense], [str(d["chunk_id"]) for d in sparse]],
        s.search_rrf_c,
    )[:k]
    rows = chunk_rows([cid for cid, _ in fused])
    hits = []
    for chunk_id, score in fused:
        m, row = meta.get(chunk_id, {}), rows.get(chunk_id, {})
        old_page = row.get("page_id") or m.get("page_id")
        pub = row.get("pub_date") or ""
        hits.append(
            Hit(
                chunk_id=chunk_id,
                story_id=str(row.get("story_id") or m.get("story_id") or ""),
                score=score,
                page_ids=list(mapping.get(str(old_page), [])) if old_page else [],
                text=str(row.get("text") or ""),
                headline=str(row.get("headline") or ""),
                pub_date=pub.isoformat() if isinstance(pub, date) else str(pub),
            )
        )
    return hits


def _is_connection_error(e: Exception) -> bool:
    import httpx
    from opensearchpy.exceptions import ConnectionError as OSConnectionError
    from qdrant_client.http.exceptions import ResponseHandlingException

    errors = (ConnectionError, httpx.TransportError, OSConnectionError, ResponseHandlingException)
    return isinstance(e, errors)
