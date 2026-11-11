"""Numbers of `newsrag stitch stats` (ticket 016 FR-6) as a dict, for the command and the quality
report (ticket 030)."""

from __future__ import annotations

from datetime import date
from typing import Any


def stats(pub_date: date | None = None) -> dict[str, Any] | None:
    """Totals over the done `stitch` artifacts (of one date, or of every date); None when there
    is none."""
    from newsrag.stitch import load
    from newsrag.stitch.cli import totals

    done = load.done_stitch(pub_date)
    if not done:
        return None
    return totals([load.load_stitch(p) for p in done.values()])
