"""The Qdrant collection and the OpenSearch index of a generation (ticket 024 FR-2 to FR-5)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from newsrag import config, embed
from newsrag.embed import records
from newsrag.stores import opensearch, qdrant

# --- Qdrant ----------------------------------------------------------------------------------


def _date_filter(day: date) -> Any:
    from qdrant_client import models as qm

    return qm.Filter(
        must=[
            qm.FieldCondition(key="pub_date_int", match=qm.MatchValue(value=records.date_int(day)))
        ]
    )


def ensure_collection(generation: str, dim: int) -> str:
    """Create the Qdrant collection of the generation (cosine, `dim`) with its payload indexes;
    an existing one is kept. Returns its name."""
    return ensure_named_collection(embed.collection_name(generation), dim)


def ensure_named_collection(name: str, dim: int) -> str:
    """`ensure_collection` for any collection name (ticket 026's `bakeoff__<candidate>`)."""
    from qdrant_client import models as qm

    client = qdrant()
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )
    else:
        existing = client.get_collection(name).config.params.vectors
        size = getattr(existing, "size", None)
        if size is not None and size != dim:
            raise ValueError(f"collection {name} has dimension {size}, the model gives {dim}")
    schema = qm.PayloadSchemaType
    for fields, kind in (
        (records.QDRANT_INT_INDEXES, schema.INTEGER),
        (records.QDRANT_KEYWORD_INDEXES, schema.KEYWORD),
        (records.QDRANT_BOOL_INDEXES, schema.BOOL),
    ):
        for field in fields:
            client.create_payload_index(name, field_name=field, field_schema=kind, wait=True)
    return name


def qdrant_replace_date(
    generation: str,
    pub_date: date,
    chunks: Sequence[dict[str, Any]],
    vectors: Sequence[Sequence[float]],
) -> None:
    """FR-3: delete the date's points of the generation (by `pub_date_int`), write the new ones."""
    from qdrant_client import models as qm

    name = embed.collection_name(generation)
    client = qdrant()
    client.delete(name, points_selector=qm.FilterSelector(filter=_date_filter(pub_date)), wait=True)
    size = max(1, config.settings.index_write_batch)
    for start in range(0, len(chunks), size):
        points = [
            qm.PointStruct(
                id=records.point_id(chunk["chunk_id"]),
                vector=[float(x) for x in vector],
                payload=records.qdrant_payload(chunk),
            )
            for chunk, vector in zip(
                chunks[start : start + size], vectors[start : start + size], strict=True
            )
        ]
        client.upsert(name, points=points, wait=True)


def qdrant_counts(generation: str) -> dict[date, int]:
    """Points per print date of the generation (0 for a missing collection)."""
    name = embed.collection_name(generation)
    client = qdrant()
    if not client.collection_exists(name):
        return {}
    response = client.facet(name, key="pub_date_int", limit=1_000_000, exact=True)
    return {_int_date(hit.value): hit.count for hit in response.hits if isinstance(hit.value, int)}


def _int_date(value: int) -> date:
    return date(value // 10000, value // 100 % 100, value % 100)


# --- OpenSearch -------------------------------------------------------------------------------


def os_settings() -> dict[str, Any]:
    """Index settings: one shard, no replica (single node), an ICU analyzer for Telugu."""
    return {
        "index": {"number_of_shards": 1, "number_of_replicas": 0},
        "analysis": {
            "analyzer": {
                "telugu_icu": {
                    "type": "custom",
                    "tokenizer": "icu_tokenizer",
                    "filter": ["icu_folding"],
                }
            }
        },
    }


def os_mappings() -> dict[str, Any]:
    props: dict[str, Any] = {
        "text_te": {"type": "text", "analyzer": "telugu_icu"},
        "headline_te": {"type": "text", "analyzer": "telugu_icu"},
        "text_en": {"type": "text", "analyzer": "english"},
        "headline_en": {"type": "text", "analyzer": "english"},
        "headline": {"type": "text", "index": False},
        "seq": {"type": "integer"},
        "n_tokens": {"type": "integer"},
        "pub_date": {"type": "date", "format": "yyyy-MM-dd"},
        "event_date": {"type": "date", "format": "yyyy-MM-dd"},
        "pub_date_int": {"type": "integer"},
        "event_date_int": {"type": "integer"},
        "low_confidence": {"type": "boolean"},
    }
    props.update({name: {"type": "keyword"} for name in records.OS_KEYWORDS})
    return {"dynamic": False, "properties": props}


def ensure_index(generation: str) -> str:
    """Create the OpenSearch index of the generation when missing. Returns its name."""
    name = embed.collection_name(generation)
    client = opensearch()
    if not client.indices.exists(index=name):
        client.indices.create(
            index=name, body={"settings": os_settings(), "mappings": os_mappings()}
        )
    return name


def os_replace_date(generation: str, pub_date: date, chunks: Sequence[dict[str, Any]]) -> None:
    """FR-3: delete the date's documents of the generation (by `pub_date_int`), write the new
    ones (document id = chunk_id) and refresh the index once."""
    from opensearchpy import helpers

    name = embed.collection_name(generation)
    client = opensearch()
    client.delete_by_query(
        index=name,
        body={"query": {"term": {"pub_date_int": records.date_int(pub_date)}}},
        conflicts="proceed",
        wait_for_completion=True,
    )
    share = config.settings.index_script_share
    actions = (
        {
            "_op_type": "index",
            "_index": name,
            "_id": chunk["chunk_id"],
            "_source": records.os_document(chunk, share),
        }
        for chunk in chunks
    )
    helpers.bulk(client, actions, chunk_size=max(1, config.settings.index_write_batch))
    client.indices.refresh(index=name)


def os_counts(generation: str) -> dict[date, int]:
    """Documents per print date of the generation (empty for a missing index)."""
    name = embed.collection_name(generation)
    client = opensearch()
    if not client.indices.exists(index=name):
        return {}
    client.indices.refresh(index=name)
    body = {
        "size": 0,
        "aggs": {"days": {"terms": {"field": "pub_date_int", "size": 100_000}}},
    }
    buckets = client.search(index=name, body=body)["aggregations"]["days"]["buckets"]
    return {_int_date(int(b["key"])): int(b["doc_count"]) for b in buckets}


# --- generations and the active alias -------------------------------------------------------


def qdrant_generations() -> list[str]:
    names = (c.name for c in qdrant().get_collections().collections)
    return sorted(g for n in names if (g := embed.generation_of(n)) is not None)


def os_generations() -> list[str]:
    client = opensearch()
    names = client.indices.get(index=f"{embed.collection_prefix()}*").keys()
    return sorted(g for n in names if (g := embed.generation_of(n)) is not None)


def qdrant_alias_target() -> str | None:
    """The generation the Qdrant alias `chunks` points at."""
    alias = embed.alias_name()
    for item in qdrant().get_aliases().aliases:
        if item.alias_name == alias:
            return embed.generation_of(item.collection_name)
    return None


def os_alias_targets() -> list[str]:
    """The generations the OpenSearch alias `chunks` points at (one when healthy)."""
    from opensearchpy import NotFoundError

    try:
        found = opensearch().indices.get_alias(name=embed.alias_name())
    except NotFoundError:
        return []
    return sorted(g for n in found if (g := embed.generation_of(n)) is not None)


def switch_aliases(generation: str) -> None:
    """Point the Qdrant alias and the OpenSearch alias `chunks` at the generation."""
    from qdrant_client import models as qm

    alias, target = embed.alias_name(), embed.collection_name(generation)
    ops: list[Any] = []
    if qdrant_alias_target() is not None:
        ops.append(qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=alias)))
    ops.append(
        qm.CreateAliasOperation(
            create_alias=qm.CreateAlias(collection_name=target, alias_name=alias)
        )
    )
    qdrant().update_collection_aliases(change_aliases_operations=ops)

    actions = [
        {"remove": {"index": embed.collection_name(g), "alias": alias}}
        for g in os_alias_targets()
        if g != generation
    ]
    actions.append({"add": {"index": target, "alias": alias}})
    opensearch().indices.update_aliases(body={"actions": actions})


def drop_generation(generation: str) -> None:
    """Delete the collection and the index of a generation (tests, tidy-up of an old model)."""
    name = embed.collection_name(generation)
    if qdrant().collection_exists(name):
        qdrant().delete_collection(name)
    if opensearch().indices.exists(index=name):
        opensearch().indices.delete(index=name)


def drop_all(generations: Iterable[str] = ()) -> None:
    """Delete every generation under the current alias prefix (and the aliases with them).

    Only for a throw-away `store_alias_prefix` (tests): with the real, empty prefix it refuses.
    """
    if not config.settings.store_alias_prefix:
        raise RuntimeError("drop_all: refusing to drop the real generations (empty alias prefix)")
    for generation in {*qdrant_generations(), *os_generations(), *generations}:
        drop_generation(generation)
