"""Grouping names into entities, curated overrides and persistent entity ids (ticket 022 FR-4..6).

Pure functions plus two id stores (`LedgerIdStore` for the `entity_ids` table, `MemoryIdStore` for
tests). Name keys are `"<type>|<normalised name>"` (`newsrag.aliases.key`).
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import yaml

from newsrag.aliases import TYPES, iso, normalise, script, split_key
from newsrag.aliases import key as name_key
from newsrag.aliases.names import NameInfo

Warn = Callable[[str], None]


# --- overrides (FR-6) ---


@dataclass
class Overrides:
    """Curated pairs of name keys. `split` are cannot-link constraints, `merge` joins groups."""

    merge: list[tuple[str, str]] = field(default_factory=list)
    split: list[tuple[str, str]] = field(default_factory=list)


def load_overrides(path: Path | None, warn: Warn = print) -> Overrides:
    """Read `overrides.yaml` (a missing or empty file means no overrides). Entries are
    `[type, name_a, name_b]`; names match by their normalised form."""
    if path is None or not Path(path).is_file():
        return Overrides()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping with merge: and split:")  # noqa: TRY004
    out = Overrides()
    for which in ("merge", "split"):
        entries = data.get(which) or []
        if not isinstance(entries, list):
            raise ValueError(f"{path}: {which}: must be a list")  # noqa: TRY004
        for entry in entries:
            ok = (
                isinstance(entry, (list, tuple))
                and len(entry) == 3
                and entry[0] in TYPES
                and all(isinstance(x, str) and normalise(x) for x in entry[1:])
            )
            if not ok:
                warn(f"warning: ignored {which} entry {entry!r}: expected [person|org, name, name]")
                continue
            kind, a, b = entry
            getattr(out, which).append((name_key(kind, a), name_key(kind, b)))
    return out


# --- grouping (FR-4) ---


class Groups:
    """Union-find over name keys with cannot-link constraints."""

    def __init__(self, keys: Iterable[str], split: Iterable[tuple[str, str]] = ()) -> None:
        self.parent = {k: k for k in keys}
        self.members = {k: [k] for k in self.parent}
        self.cannot: dict[str, set[str]] = defaultdict(set)
        for a, b in split:
            self.cannot[a].add(b)
            self.cannot[b].add(a)

    def find(self, k: str) -> str:
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def blocked(self, a: str, b: str) -> bool:
        """True when a cannot-link pair sits between the groups of `a` and `b`."""
        ra, rb = self.find(a), self.find(b)
        small, large = sorted((self.members[ra], self.members[rb]), key=len)
        other = set(large)
        return any(self.cannot[x] & other for x in small if x in self.cannot)

    def union(self, a: str, b: str) -> bool:
        """Join the groups of `a` and `b` unless a cannot-link pair forbids it."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if self.blocked(a, b):
            return False
        if len(self.members[ra]) < len(self.members[rb]):
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.members[ra].extend(self.members.pop(rb))
        return True

    def same(self, a: str, b: str) -> bool:
        return self.find(a) == self.find(b)

    def groups(self, min_size: int = 1) -> list[list[str]]:
        out = [sorted(m) for m in self.members.values() if len(m) >= min_size]
        return sorted(out)


def group_names(
    keys: Iterable[str],
    applied: Sequence[tuple[float, str, str]],
    overrides: Overrides,
    warn: Warn = print,
) -> tuple[Groups, list[list[str]]]:
    """Union-find over the applied pairs `(confidence, key_a, key_b)` in descending confidence,
    skipping unions an override `split` forbids; then the override merges. Returns the final
    groups and the auto-merged groups (size 2 or more, before the merges)."""
    keys = sorted(keys)
    known = set(keys)
    groups = Groups(keys, [(a, b) for a, b in overrides.split if a in known and b in known])
    for _, a, b in sorted(applied, key=lambda t: (-t[0], t[1], t[2])):
        groups.union(a, b)
    auto_groups = groups.groups(min_size=2)
    for a, b in overrides.merge:
        label = f"merge {split_key(a)[0]} {split_key(a)[1]!r} + {split_key(b)[1]!r}"
        if a not in known or b not in known:
            warn(f"warning: ignored {label}: unknown name")
        elif not groups.union(a, b):
            warn(f"warning: ignored {label}: conflicts with a split")
    return groups, auto_groups


# --- entities (FR-5) ---


def canonical_of(names: Sequence[NameInfo]) -> str:
    """The most frequent spelling of a group; tie: Telugu script first, then the smallest."""
    best = min(
        ((i.count, i.display) for i in names),
        key=lambda t: (-t[0], 0 if script(t[1]) == "te" else 1, t[1]),
    )
    return best[1]


def _most_frequent(items: Iterable[tuple[int, str]]) -> str | None:
    items = list(items)
    return min(items, key=lambda t: (-t[0], t[1]))[1] if items else None


def describe(names: Sequence[NameInfo]) -> dict[str, Any]:
    """The entity fields of a group, without the id."""
    kind = names[0].type
    canonical = canonical_of(names)
    te = _most_frequent((i.count, i.display) for i in names if script(i.display) == "te")
    en = _most_frequent((i.count, i.display) for i in names if script(i.display) == "en")
    if en is None and te is not None:
        en = iso(te)
    org_types: Counter[str] = Counter()
    stories: set[str] = set()
    for info in names:
        org_types.update(info.org_types)
        stories |= info.stories
    org_type = _most_frequent((n, t) for t, n in org_types.items()) if kind == "org" else None
    return {
        "type": kind,
        "org_type": org_type,
        "canonical": canonical,
        "canonical_te": te,
        "canonical_en": en,
        "aliases": sorted({i.display for i in names}),
        "story_count": len(stories),
    }


def mint_id(kind: str, canonical: str, used: set[str]) -> str:
    """`e_<first 12 hex of sha1(type + canonical)>`; `#k` is added to the hash input until the id
    is unused."""
    k = 0
    while True:
        text = kind + canonical + (f"#{k}" if k else "")
        new = "e_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        if new not in used:
            return new
        k += 1


def assign_ids(
    groups: Sequence[Sequence[str]],
    canonicals: Sequence[str],
    rows: Mapping[str, tuple[str, datetime]],
) -> list[str]:
    """The entity id of every group (same order as `groups`).

    `rows` is the stored table `name_key -> (entity_id, created_at)`. A group whose names already
    have ids keeps the oldest one (smallest created_at, then smallest id); when an id would end up
    in two groups, the group holding the oldest name keeps it and the other takes its next id or
    a fresh one. A group with no stored name gets `mint_id`.
    """
    taken: set[str] = set()
    all_ids = {entity_id for entity_id, _ in rows.values()}
    oldest: list[tuple | None] = []
    preferred: list[list[str]] = []
    for group in groups:
        stored = [(rows[k][1], k, rows[k][0]) for k in group if k in rows]
        oldest.append(min((t[0], t[1]) for t in stored) if stored else None)
        first_seen: dict[str, datetime] = {}
        for created, _, entity_id in stored:
            if entity_id not in first_seen or created < first_seen[entity_id]:
                first_seen[entity_id] = created
        preferred.append(sorted(first_seen, key=lambda i: (first_seen[i], i)))
    order = sorted(
        range(len(groups)),
        key=lambda i: (oldest[i] is None, oldest[i] or (), canonicals[i], tuple(groups[i])),
    )
    ids: list[str] = [""] * len(groups)
    for i in order:
        pick = next((x for x in preferred[i] if x not in taken), None)
        if pick is None:
            kind = split_key(groups[i][0])[0]
            pick = mint_id(kind, canonicals[i], taken | all_ids)
        taken.add(pick)
        ids[i] = pick
    return ids


class IdStore(Protocol):
    def load(self) -> dict[str, tuple[str, datetime]]: ...

    def save(self, assigned: Mapping[str, str]) -> None: ...


class MemoryIdStore:
    """`entity_ids` in memory (unit tests)."""

    def __init__(self, rows: Mapping[str, tuple[str, datetime]] | None = None) -> None:
        self.rows: dict[str, tuple[str, datetime]] = dict(rows or {})
        self._tick = 0

    def load(self) -> dict[str, tuple[str, datetime]]:
        return dict(self.rows)

    def save(self, assigned: Mapping[str, str]) -> None:
        self._tick += 1
        now = datetime(2030, 1, 1, tzinfo=UTC) + timedelta(seconds=self._tick)
        for nk, entity_id in assigned.items():
            old = self.rows.get(nk)
            self.rows[nk] = (entity_id, old[1] if old else now)


class LedgerIdStore:
    """`ledger.entity_ids` (migration `004_entity_ids.sql`)."""

    def load(self) -> dict[str, tuple[str, datetime]]:
        from newsrag.ledger import db

        with db.conn() as c:
            rows = db.fetch_all(c, "select name_key, entity_id, created_at from entity_ids")
        return {r["name_key"]: (r["entity_id"], r["created_at"]) for r in rows}

    def save(self, assigned: Mapping[str, str]) -> None:
        from newsrag.ledger import db

        rows = list(assigned.items())
        with db.conn() as c, c.cursor() as cur:
            for i in range(0, len(rows), 2000):
                cur.executemany(
                    "insert into entity_ids (name_key, entity_id) values (%s, %s) "
                    "on conflict (name_key) do update set entity_id = excluded.entity_id "
                    "where entity_ids.entity_id is distinct from excluded.entity_id",
                    rows[i : i + 2000],
                )


def make_entities(
    final: Groups, names: Mapping[str, NameInfo], store: IdStore
) -> tuple[dict[str, Any], dict[str, str]]:
    """The `entities.json` content and the `name_key -> entity_id` assignment (saved in `store`)."""
    members = final.groups()
    described = [describe([names[k] for k in group]) for group in members]
    ids = assign_ids(members, [d["canonical"] for d in described], store.load())
    entities = []
    assigned: dict[str, str] = {}
    for group, entity_id, fields in zip(members, ids, described, strict=True):
        entities.append({"entity_id": entity_id, **fields})
        for nk in group:
            assigned[nk] = entity_id
    entities.sort(key=lambda e: e["entity_id"])
    store.save(assigned)
    return {
        "entities": entities,
        "name_to_entity": dict(sorted(assigned.items())),
    }, assigned
