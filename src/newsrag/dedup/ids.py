"""Stable story ids in `ledger.story_ids` (ticket 017 FR-3, FR-7)."""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping, Sequence
from datetime import date
from typing import Any

Existing = Mapping[str, tuple[str, date]]  # unit_id -> (story_id, first_seen)


def new_story_id(smallest_unit_id: str, pub_date: str, *taken: Collection[str]) -> str:
    """`s_<first 16 hex of sha1(smallest unit_id + pub_date + k)>`, the smallest k >= 0 whose id
    is in none of the `taken` collections."""
    k = 0
    while True:
        digest = hashlib.sha1(f"{smallest_unit_id}{pub_date}{k}".encode()).hexdigest()[:16]
        story_id = f"s_{digest}"
        if not any(story_id in t for t in taken):
            return story_id
        k += 1


def _best_candidate(members: Sequence[str], existing: Existing) -> tuple[str | None, int]:
    """The id held by most members (tie: oldest first_seen, then smallest id) and its count."""
    held: dict[str, list[date]] = {}
    for unit_id in members:
        if unit_id in existing:
            story_id, first_seen = existing[unit_id]
            held.setdefault(story_id, []).append(first_seen)
    if not held:
        return None, 0
    best = min(held, key=lambda sid: (-len(held[sid]), min(held[sid]), sid))
    return best, len(held[best])


def plan_ids(
    pub_date: str,
    clusters: Sequence[Sequence[str]],
    existing: Existing,
    used: Collection[str],
) -> list[str]:
    """The story id of each cluster (same order as `clusters`, which lists unit ids).

    Clusters are handled by the number of members that already hold their preferred id (most
    first), then by smallest unit_id. A cluster reuses its preferred id unless a cluster handled
    before it took it; otherwise it gets a new id. `used` holds every id in the table (so a new id
    never collides with an old one); `existing` has the rows of this date's current units.
    """
    smallest = [min(c) for c in clusters]
    prefs = [_best_candidate(c, existing) for c in clusters]
    order = sorted(range(len(clusters)), key=lambda i: (-prefs[i][1], smallest[i]))
    taken: set[str] = set()
    out = [""] * len(clusters)
    for i in order:
        candidate = prefs[i][0]
        if candidate is not None and candidate not in taken:
            out[i] = candidate
        else:
            out[i] = new_story_id(smallest[i], pub_date, used, taken)
        taken.add(out[i])
    return out


class _InTable:
    """`story_id in _InTable(c)`: is the id held by any row of `story_ids`? (cached)"""

    def __init__(self, c: Any) -> None:
        self.c = c
        self.cache: dict[str, bool] = {}

    def __contains__(self, story_id: object) -> bool:
        key = str(story_id)
        if key not in self.cache:
            row = self.c.execute(
                "select 1 from story_ids where story_id = %s limit 1", (key,)
            ).fetchone()
            self.cache[key] = row is not None
        return self.cache[key]


def sync(
    pub_date: date,
    clusters: Sequence[Sequence[str]],
    today: date | None = None,
) -> list[str]:
    """Assign ids to the date's clusters and bring `story_ids` in line, in one transaction.

    Rows of units of this date that no longer exist are deleted first. Returns the story id of
    each cluster, in order.
    """
    from newsrag.ledger import db

    day = pub_date.isoformat()
    today = today or date.today()
    current = sorted({u for c in clusters for u in c})
    with db.conn() as c:
        c.execute(
            "delete from story_ids where left(unit_id, 10) = %s and unit_id <> all(%s)",
            (day, current),
        )
        rows = db.fetch_all(
            c,
            "select unit_id, story_id, first_seen from story_ids where unit_id = any(%s)",
            (current,),
        )
        existing = {r["unit_id"]: (r["story_id"], r["first_seen"]) for r in rows}
        ids = plan_ids(day, clusters, existing, _InTable(c))
        records = []
        for members, story_id in zip(clusters, ids, strict=True):
            for unit_id in members:
                old = existing.get(unit_id)
                first_seen = old[1] if old and old[0] == story_id else today
                records.append((unit_id, story_id, first_seen))
        with c.cursor() as cur:
            cur.executemany(
                "insert into story_ids (unit_id, story_id, first_seen) values (%s, %s, %s) "
                "on conflict (unit_id) do update set story_id = excluded.story_id, "
                "first_seen = excluded.first_seen",
                records,
            )
    return ids
