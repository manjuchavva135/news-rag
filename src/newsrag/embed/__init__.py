"""Embedding and indexing (ticket 024): the chunks of a date in Qdrant, OpenSearch and Postgres.

- `records`: Qdrant payloads, OpenSearch documents, script detection, point ids (pure).
- `index`: creating, filling, counting and switching the Qdrant collection and OpenSearch index of
  a generation.
- `cli`: `newsrag index activate | collections | validate`.

A *generation* is `<embed_model>__<index_generation_tag>`: everything that depends on the embed
model (the Qdrant collection `chunks__G`, the OpenSearch index `chunks__G` and the rows of
`warehouse.chunks`) is stored per generation and switched together (grill A-30, D-20).

Importing this package touches neither the stores nor the GPU.
"""

from __future__ import annotations

from newsrag import config

BASE_NAME = "chunks"


def generation_name(embed_model: str | None = None, tag: str | None = None) -> str:
    """`<embed_model>__<tag>`: the generation the stage writes to."""
    s = config.settings
    return f"{embed_model or s.embed_model}__{tag or s.index_generation_tag}"


def alias_name() -> str:
    """The alias the search reads: `chunks`, with `store_alias_prefix` in front."""
    return f"{config.settings.store_alias_prefix}{BASE_NAME}"


def collection_prefix() -> str:
    """What every collection / index name of a generation starts with: `<prefix>chunks__`."""
    return f"{alias_name()}__"


def collection_name(generation: str) -> str:
    """The Qdrant collection and the OpenSearch index of a generation."""
    return f"{collection_prefix()}{generation}"


def generation_of(collection: str) -> str | None:
    """The generation a collection / index name belongs to, None when it is not one of ours."""
    prefix = collection_prefix()
    return collection[len(prefix) :] if collection.startswith(prefix) else None
