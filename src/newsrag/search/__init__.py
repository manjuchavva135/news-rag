"""Search over the stores (ticket 025): `search(query, k, store, ...) -> list[Hit]`.

- `hybrid`: dense + BM25 + reciprocal-rank fusion over the active generation; the backend
  registry (`register_backend("baseline", ...)` is ticket 027's).
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
)

__all__ = [
    "Hit",
    "StoreNotConfigured",
    "close",
    "register_backend",
    "rrf_fuse",
    "search",
    "unregister_backend",
]
