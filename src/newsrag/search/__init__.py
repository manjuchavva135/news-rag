"""Search over the stores (ticket 025): `search(query, k, store, ...) -> list[Hit]`.

- `hybrid`: dense + BM25 + reciprocal-rank fusion over the active generation; the backend
  registry (`newsrag.baseline.search` registers `baseline` when it is imported, ticket 027).
- `cli`: `newsrag search "<query>"`.

Importing this package touches neither the stores nor the GPU.
"""

from newsrag.search.hybrid import (
    Hit,
    StoreNotConfigured,
    close,
    register_backend,
    rrf_fuse,
    search,
    unregister_backend,
    use_embedder,
)

__all__ = [
    "Hit",
    "StoreNotConfigured",
    "close",
    "register_backend",
    "rrf_fuse",
    "search",
    "unregister_backend",
    "use_embedder",
]
