"""Per-year numbers of `newsrag editions report` (ticket 011) as a dict, for the quality report
(ticket 030)."""

from __future__ import annotations

from pathlib import Path


def stats(path: Path | None = None) -> dict[int, dict[str, int]] | None:
    """Per year: codes, ok, ambiguous, unknown_edition, date_mismatch, state_pages. None when
    neither editions.yaml nor page_editions has anything."""
    from newsrag.editions import mapping, store
    from newsrag.ledger import db

    try:
        editions = store.as_plain(store.load_editions(path))
    except FileNotFoundError:
        editions = {}
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            "select p.pub_date, p.edition_code, e.flags "
            "from page_editions e join pages p using (page_id)",
        )
    pages = [(r["pub_date"], r["edition_code"], list(r["flags"] or [])) for r in rows]
    counts = mapping.year_counts(editions, pages)
    return counts or None
