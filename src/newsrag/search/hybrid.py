"""Plain hybrid search over the stores (ticket 025 FR-1): dense (Qdrant) + BM25 (OpenSearch),
fused with reciprocal-rank fusion. No reranking, no query rewriting.

`search(store="new")` reads the active generation (or the one given); other stores are backends
registered with `register_backend` (ticket 027 adds `baseline`).

Importing this module touches neither the stores nor the GPU: the embed model is loaded on the
first query and kept until `close()`.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from newsrag import config, embed

FILTER_KEYS = ("page_ids",)


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    story_id: str
    score: float
    page_ids: list[str] = field(default_factory=list)
    text: str = ""
    headline: str = ""
    pub_date: str = ""


class StoreNotConfigured(RuntimeError):
    """`search(store=...)` for a store no backend is registered for."""


Backend = Callable[..., list[Hit]]
_BACKENDS: dict[str, Backend] = {}


def register_backend(store: str, backend: Backend) -> None:
    """Make `search(store=<store>)` call `backend(query, k=, filters=, generation=,
    qdrant_collection=, embed_profile=)`."""
    _BACKENDS[store] = backend


def unregister_backend(store: str) -> None:
    _BACKENDS.pop(store, None)


# --- fusion ---------------------------------------------------------------------------------


def rrf_fuse(rankings: Sequence[Sequence[str]], c: float = 60.0) -> list[tuple[str, float]]:
    """Reciprocal-rank fusion: score(d) = sum over rankings of 1 / (c + rank), rank from 1.

    Best first; ties keep the order in which the ids were first seen (dense list first)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (c + rank)
    order = {doc_id: i for i, doc_id in enumerate(scores)}
    return sorted(scores.items(), key=lambda item: (-item[1], order[item[0]]))


def check_filters(filters: Mapping[str, Any] | None) -> dict[str, Any]:
    filters = dict(filters or {})
    unknown = sorted(set(filters) - set(FILTER_KEYS))
    if unknown:
        raise ValueError(f"unknown search filter(s): {', '.join(unknown)}")
    if "page_ids" in filters:
        if isinstance(filters["page_ids"], str):
            raise ValueError("filter page_ids must be a list of page ids")
        filters["page_ids"] = [str(p) for p in filters["page_ids"]]
    return filters


# --- the query embedding ----------------------------------------------------------------------

_embed_lock = threading.Lock()
_embed_state: dict[str, Any] = {"profile": None, "stack": None, "embedder": None}


def _embedder(profile: str) -> Any:
    """The open Embedder of `profile`; another profile already open is closed first."""
    with _embed_lock:
        if _embed_state["profile"] != profile:
            _close_locked()
            from newsrag import models

            stack = contextlib.ExitStack()
            try:
                embedder = stack.enter_context(models.use(profile))
                embedder.embed(["warm-up"], "query", 1)  # CUDA set-up off the first query
            except BaseException:
                stack.close()
                raise
            _embed_state.update(profile=profile, stack=stack, embedder=embedder)
        return _embed_state["embedder"]


def _close_locked() -> None:
    stack = _embed_state["stack"]
    _embed_state.update(profile=None, stack=None, embedder=None)
    if stack is not None:
        stack.close()


def close() -> None:
    """Unload the query embed model (and free the GPU lock)."""
    with _embed_lock:
        _close_locked()


def embed_query(query: str, profile: str) -> list[float]:
    """The query vector, with the profile's `query_prefix` (tests replace this function)."""
    return [float(x) for x in _embedder(profile).embed([query], "query", 1)[0]]


# --- the two retrievers -----------------------------------------------------------------------


def dense_search(
    vector: Sequence[float], collection: str, limit: int, filters: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Top `limit` payloads (chunk_id, story_id, page_ids, pub_date, headline) from Qdrant."""
    from qdrant_client import models as qm

    from newsrag.stores import qdrant

    query_filter = None
    if filters.get("page_ids") is not None:
        query_filter = qm.Filter(
            must=[qm.FieldCondition(key="page_ids", match=qm.MatchAny(any=filters["page_ids"]))]
        )
    response = qdrant().query_points(
        collection,
        query=list(vector),
        limit=limit,
        query_filter=query_filter,
        with_payload=["chunk_id", "story_id", "page_ids", "pub_date", "headline"],
    )
    return [dict(point.payload or {}) for point in response.points]


def bm25_body(query: str, limit: int, filters: Mapping[str, Any]) -> dict[str, Any]:
    """The OpenSearch request: `multi_match` over the four text fields, optional filter."""
    match: dict[str, Any] = {
        "multi_match": {"query": query, "fields": list(config.settings.search_bm25_fields)}
    }
    query_part: dict[str, Any] = match
    if filters.get("page_ids") is not None:
        query_part = {
            "bool": {"must": [match], "filter": [{"terms": {"page_ids": filters["page_ids"]}}]}
        }
    return {
        "size": limit,
        "query": query_part,
        "_source": ["chunk_id", "story_id", "page_ids", "pub_date", "headline"],
    }


def bm25_search(
    query: str, index: str, limit: int, filters: Mapping[str, Any]
) -> list[dict[str, Any]]:
    from newsrag.stores import opensearch

    hits = opensearch().search(index=index, body=bm25_body(query, limit, filters))["hits"]["hits"]
    return [{"chunk_id": h["_id"], **(h.get("_source") or {})} for h in hits]


def chunk_rows(generation: str, chunk_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """chunk_id -> {story_id, headline, text, pub_date} from `warehouse.chunks`."""
    from newsrag.warehouse import db as wdb

    if not chunk_ids:
        return {}
    with wdb.conn() as c:
        rows = wdb.fetch_all(
            c,
            "select chunk_id, story_id, headline, text, pub_date from chunks "
            "where generation = %s and chunk_id = any(%s)",
            (generation, list(chunk_ids)),
        )
    return {r["chunk_id"]: r for r in rows}


def profile_of(generation: str) -> str:
    """The embed profile a generation `<embed_model>__<tag>` was built with."""
    return generation.partition("__")[0]


def search_new(
    query: str,
    k: int = 10,
    filters: Mapping[str, Any] | None = None,
    generation: str | None = None,
    qdrant_collection: str | None = None,
    embed_profile: str | None = None,
) -> list[Hit]:
    """FR-1 for store `new`."""
    from newsrag.warehouse import db as wdb

    s = config.settings
    filters = check_filters(filters)
    generation = generation or wdb.active_generation()
    if generation is None:
        raise StoreNotConfigured("no active generation: run 'newsrag stage run index' first")
    profile = embed_profile or profile_of(generation)
    collection = embed.collection_name(generation)
    limit = max(k, s.search_candidates)

    vector = embed_query(query, profile)
    dense = dense_search(vector, qdrant_collection or collection, limit, filters)
    sparse = bm25_search(query, collection, limit, filters)

    meta: dict[str, dict[str, Any]] = {}
    for item in [*dense, *sparse]:
        if item.get("chunk_id"):
            meta.setdefault(str(item["chunk_id"]), item)
    fused = rrf_fuse(
        [[str(d["chunk_id"]) for d in dense], [str(d["chunk_id"]) for d in sparse]],
        s.search_rrf_c,
    )[:k]
    rows = chunk_rows(generation, [cid for cid, _ in fused])
    hits = []
    for chunk_id, score in fused:
        m, row = meta.get(chunk_id, {}), rows.get(chunk_id, {})
        pub = row.get("pub_date") or m.get("pub_date") or ""
        hits.append(
            Hit(
                chunk_id=chunk_id,
                story_id=str(row.get("story_id") or m.get("story_id") or ""),
                score=score,
                page_ids=list(m.get("page_ids") or []),
                text=str(row.get("text") or ""),
                headline=str(row.get("headline") or m.get("headline") or ""),
                pub_date=pub.isoformat() if isinstance(pub, date) else str(pub),
            )
        )
    return hits


register_backend("new", search_new)


def search(
    query: str,
    k: int = 10,
    store: str = "new",
    filters: Mapping[str, Any] | None = None,
    generation: str | None = None,
    qdrant_collection: str | None = None,
    embed_profile: str | None = None,
) -> list[Hit]:
    """The top `k` chunks for `query` from `store` (FR-1)."""
    backend = _BACKENDS.get(store)
    if backend is None:
        raise StoreNotConfigured(f"{store} stores not configured")
    return backend(
        query,
        k=k,
        filters=filters,
        generation=generation,
        qdrant_collection=qdrant_collection,
        embed_profile=embed_profile,
    )
