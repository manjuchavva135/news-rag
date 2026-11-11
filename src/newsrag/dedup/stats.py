"""Numbers of `newsrag dedup stats` (ticket 017 FR-6) as a dict, plus the printings-per-story
distribution the quality report shows (ticket 030)."""

from __future__ import annotations

from datetime import date
from typing import Any

BUCKETS_KEY = "printings_buckets"


def buckets(sizes: list[int]) -> dict[str, int]:
    """Stories with 1, 2, 3-4 and >= 5 printings."""
    out = {"1": 0, "2": 0, "3-4": 0, ">=5": 0}
    for n in sizes:
        key = "1" if n <= 1 else "2" if n == 2 else "3-4" if n <= 4 else ">=5"
        out[key] += 1
    return out


def summarize(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    from newsrag.dedup.cli import totals

    sizes = [len(s["printings"]) for art in artifacts for s in art["stories"]]
    return {**totals(artifacts), BUCKETS_KEY: buckets(sizes)}


def stats(pub_date: date | None = None) -> dict[str, Any] | None:
    """`summarize` over the done `dedup` artifacts (of one date, or of every date); None when
    there is none."""
    from newsrag.dedup import load

    done = load.done_dedup(pub_date)
    if not done:
        return None
    return summarize([load.load_dedup(p) for p in done.values()])
