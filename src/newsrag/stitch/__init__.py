"""Continuation stitching (ticket 016): stories that continue on another page become one article.

`stitched_articles(pub_date)` is the helper later stages read; the matching is in `match`, the
merging in `merge`, the stage in `newsrag.stages.stitch`, the CLI in `newsrag.stitch.cli`.
Importing this package touches neither the database nor the GPU.
"""

from __future__ import annotations

from datetime import date

from newsrag.stitch.model import Article

__all__ = ["Article", "stitched_articles"]


def stitched_articles(pub_date: date | str) -> list[Article]:
    """All articles of the date (every kind) with each linked chain merged into one article.

    Needs a done `stitch` row for the date (`FileNotFoundError` otherwise).
    """
    from newsrag.stitch import load
    from newsrag.stitch.merge import merge_articles

    day = date.fromisoformat(pub_date) if isinstance(pub_date, str) else pub_date
    done = load.done_stitch(day)
    if day.isoformat() not in done:
        raise FileNotFoundError(f"no done stitch artifact for {day.isoformat()}")
    pages = load.date_pages(day)
    return merge_articles(
        pages,
        load.group_final_artifacts(pages),
        load.load_stitch(done[day.isoformat()]),
        load.patterns(),
    )
