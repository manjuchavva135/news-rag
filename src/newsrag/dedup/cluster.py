"""Duplicate pairs, clusters and the story record (ticket 017 FR-2, FR-4)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from newsrag import config
from newsrag.dedup import minhash
from newsrag.dedup.units import NOTICE, Unit, union_flags

Embed = Callable[[Sequence[str]], np.ndarray]


@dataclass(frozen=True)
class Params:
    """The similarity knobs (defaults from `config.settings`)."""

    jaccard: float
    cosine: float
    num_perm: int
    shingle_size: int
    lsh_threshold: float

    @classmethod
    def from_settings(cls) -> Params:
        s = config.settings
        return cls(
            jaccard=s.dedup_jaccard,
            cosine=s.dedup_cosine,
            num_perm=s.dedup_num_perm,
            shingle_size=s.dedup_shingle_size,
            lsh_threshold=min(s.dedup_lsh_threshold, s.dedup_jaccard),
        )


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    jaccard: float
    cosine: float


def embed_text(unit: Unit) -> str:
    return f"{unit.headline}\n{unit.text}"


def duplicate_pairs(units: Sequence[Unit], embed: Embed, params: Params) -> list[Pair]:
    """Pairs with estimated Jaccard >= `jaccard` **and** embedding cosine >= `cosine`.

    Only units that have a MinHash partner are embedded.
    """
    sigs = {}
    for unit in units:
        sig = minhash.signature(
            minhash.normalise(unit.headline, unit.text), params.num_perm, params.shingle_size
        )
        if sig is not None:
            sigs[unit.unit_id] = sig
    close = []
    for a, b in minhash.candidate_pairs(sigs, params.num_perm, params.lsh_threshold):
        jac = minhash.jaccard(sigs[a], sigs[b])
        if jac >= params.jaccard:
            close.append((a, b, jac))
    if not close:
        return []
    by_id = {u.unit_id: u for u in units}
    needed = sorted({x for a, b, _ in close for x in (a, b)})
    vectors = np.asarray(embed([embed_text(by_id[i]) for i in needed]), dtype=np.float32)
    row = {unit_id: n for n, unit_id in enumerate(needed)}
    out = []
    for a, b, jac in close:
        cos = float(np.dot(vectors[row[a]], vectors[row[b]]))
        if cos >= params.cosine:
            out.append(Pair(a, b, jac, cos))
    return out


def cluster_units(units: Sequence[Unit], pairs: Sequence[Pair]) -> list[list[Unit]]:
    """Transitive clusters: pairs in descending cosine order, union-find, and a union is skipped
    when the two clusters would share a page. Members are sorted by unit_id, clusters by their
    smallest unit_id."""
    by_id = {u.unit_id: u for u in units}
    parent = {u.unit_id: u.unit_id for u in units}
    pages = {u.unit_id: set(u.pages) for u in units}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in sorted(pairs, key=lambda p: (-p.cosine, p.a, p.b)):
        ra, rb = find(pair.a), find(pair.b)
        if ra == rb or pages[ra] & pages[rb]:
            continue
        if rb < ra:
            ra, rb = rb, ra
        parent[rb] = ra
        pages[ra] |= pages.pop(rb)

    groups: dict[str, list[Unit]] = {}
    for unit_id in sorted(by_id):
        groups.setdefault(find(unit_id), []).append(by_id[unit_id])
    return sorted(groups.values(), key=lambda members: members[0].unit_id)


def canonical(members: Sequence[Unit]) -> Unit:
    """The member with the longest text (tie: smallest unit_id)."""
    return min(members, key=lambda u: (-len(u.text), u.unit_id))


def printing(unit: Unit) -> dict[str, Any]:
    return {
        "unit_id": unit.unit_id,
        "page_id": unit.page_id,
        "edition_code": unit.edition_code,
        "section": unit.section,
        "page_num": unit.page_num,
        "parts": [dict(p) for p in unit.parts],
        "flags": list(unit.flags),
    }


def story_record(story_id: str, members: Sequence[Unit]) -> dict[str, Any]:
    """One story of the artifact; the canonical member's text, headline, kind and flags."""
    best = canonical(members)
    is_notice = best.kind == NOTICE
    return {
        "story_id": story_id,
        "kind": best.kind,
        "canonical_unit_id": best.unit_id,
        "headline": best.headline,
        "text": best.text,
        "department": best.department if is_notice else None,
        "notice_number": best.notice_number if is_notice else None,
        "deadline": best.deadline if is_notice else None,
        "printings": [printing(u) for u in sorted(members, key=lambda u: u.unit_id)],
        "flags": union_flags(best.flags),
    }


def build_artifact(
    pub_date: str,
    clusters: Sequence[Sequence[Unit]],
    story_ids: Mapping[str, str],
    excluded: Mapping[str, int],
) -> dict[str, Any]:
    """`{pub_date, stories, excluded}`; `story_ids` maps the smallest unit_id of each cluster to
    its story id."""
    return {
        "pub_date": pub_date,
        "stories": [story_record(story_ids[c[0].unit_id], c) for c in clusters],
        "excluded": dict(excluded),
    }
