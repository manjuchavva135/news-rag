"""Chunking (ticket 023): canonical stories, tables and notices as retrieval chunks.

- `split`: the token-budget splitting rules (paragraphs with one-paragraph overlap, sentences,
  table rows) and the exact embedded string.
- `build`: the chunk records of one date (pure); `load`: reading the inputs.
- `cli`: `newsrag chunk stats`.
- `stale_dates()`: dates whose chunks used alias / edition lookups that have changed since.

Importing this package touches neither the database, the GPU nor the network.
"""

from __future__ import annotations

from datetime import date


def stale_dates() -> list[date]:
    """Dates with a done `chunk` run whose `lookup_hash` no longer matches the current alias
    table, `editions.yaml` and page editions (FR-7). Ticket 029 resets `chunk` for just these
    dates after `aliases build` / `editions apply`."""
    from newsrag.chunking import build, load

    stale = []
    for day, detail in load.done_dates().items():
        current = build.compute_lookup_hash(load.load_inputs(day))
        if current != detail.get("lookup_hash"):
            stale.append(day)
    return stale
