"""Geometry on top of the model's grouping (ticket 012 FR-5, FR-6, FR-9, FR-10).

All sizes are 300-DPI page pixels. A block is a dict with `block_id`, `order`, `bbox`
([x1, y1, x2, y2]) and `text`. The model's answer arrives as a "raw grouping" (see
`newsrag.grouping.parse`): articles as `{"kind", "blocks": [{"n", "role"}]}` plus `unassigned`
numbers. `build_grouping` turns it into the artifact's articles: every block accounted for,
duplicates marked, reading order set by columns, headline and text assembled, flags set.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from newsrag import config

# FR-6: the order roles are written in `blocks`; the first six feed headline/text.
ROLE_SEQUENCE = (
    "headline",
    "kicker",
    "subheadline",
    "byline",
    "dateline",
    "body",
    "caption",
    "photo",
    "table",
    "infographic",
    "pull_quote",
    "continuation_marker",
)
TEXT_ROLES = ("headline", "kicker", "subheadline", "byline", "dateline", "body")
TEXT_BODY_ROLES = ("kicker", "subheadline", "byline", "dateline", "body")  # `text`, in this order
HEADLINE_KINDS = ("news", "editorial")

# Article flag order in the artifact.
FLAG_SEQUENCE = (
    "disconnected",
    "overlaps_other",
    "no_headline",
    "headline_below_body",
    "duplicate_block",
    "order_from_model",
    "order_changed",
)


@dataclass(frozen=True)
class Params:
    max_gap_px: float = 60
    overlap_frac: float = 0.2
    headline_slack_px: float = 50
    same_column_frac: float = 0.5
    min_overlap_px: float = 30
    dup_overlap_frac: float = 0.8
    dup_text_frac: float = 0.8

    @classmethod
    def from_settings(cls) -> Params:
        s = config.settings
        return cls(
            max_gap_px=s.group_max_gap_px,
            overlap_frac=s.group_overlap_frac,
            headline_slack_px=s.group_headline_slack_px,
            same_column_frac=s.group_order_same_column_frac,
            min_overlap_px=s.group_order_min_overlap_px,
            dup_overlap_frac=s.group_dup_overlap_frac,
            dup_text_frac=s.group_dup_text_frac,
        )


# --- box arithmetic --------------------------------------------------------------------------


def area(box: Sequence[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def intersection_area(a: Sequence[float], b: Sequence[float]) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def gap(a: Sequence[float], b: Sequence[float]) -> float:
    """Edge-to-edge distance between two rectangles (0 if they touch or overlap)."""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return (dx * dx + dy * dy) ** 0.5


def union_box(boxes: Sequence[Sequence[float]]) -> list[float]:
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def x_overlap(a: Sequence[float], b: Sequence[float]) -> float:
    return min(a[2], b[2]) - max(a[0], b[0])


def _same_column(a: Sequence[float], b: Sequence[float], frac: float) -> bool:
    narrower = min(a[2] - a[0], b[2] - b[0])
    overlap = x_overlap(a, b)
    return narrower > 0 and overlap > 0 and overlap >= frac * narrower


# --- reading order (FR-9) --------------------------------------------------------------------


def _components(
    boxes: Sequence[Sequence[float]], idx: Sequence[int], frac: float
) -> dict[int, int]:
    """Union-find over the blocks `idx`: index -> its column's root index."""
    parent = {i: i for i in idx}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in combinations(idx, 2):
        if _same_column(boxes[i], boxes[j], frac):
            parent[find(i)] = find(j)
    return {i: find(i) for i in idx}


def _wide_blocks(boxes: Sequence[Sequence[float]], p: Params) -> set[int]:
    """Blocks whose x-range overlaps two or more blocks that are not in one column.

    First the blocks that clearly span: they overlap (by more than `min_overlap_px`) two blocks
    that do not overlap each other. Then, with those left out, any block overlapping blocks of
    two different columns.
    """
    n = len(boxes)
    thr = p.min_overlap_px

    def touching(w: int, among: Sequence[int]) -> list[int]:
        return [j for j in among if j != w and x_overlap(boxes[w], boxes[j]) > thr]

    wide = {
        w
        for w in range(n)
        if any(
            x_overlap(boxes[a], boxes[b]) <= thr for a, b in combinations(touching(w, range(n)), 2)
        )
    }
    rest = [i for i in range(n) if i not in wide]
    for w in rest:
        others = [i for i in rest if i != w]
        cols = _components(boxes, others, p.same_column_frac)
        if len({cols[j] for j in touching(w, others)}) >= 2:
            wide.add(w)
    return wide


def column_order(blocks: Sequence[dict[str, Any]], p: Params | None = None) -> list[dict[str, Any]]:
    """The blocks in reading order: wide blocks cut the set into bands (top to bottom); inside a
    band the columns run left to right, each column top to bottom. Ties go to the lower `order`."""
    p = p or Params.from_settings()
    items = list(blocks)
    if len(items) < 2:
        return items
    boxes = [b["bbox"] for b in items]
    wide = _wide_blocks(boxes, p)
    narrow = [i for i in range(len(items)) if i not in wide]
    cols = _components(boxes, narrow, p.same_column_frac)

    def key(i: int) -> tuple[float, Any]:
        return (boxes[i][1], items[i].get("order", 0))

    wide_sorted = sorted(wide, key=key)
    bands: list[list[int]] = [[] for _ in range(len(wide_sorted) + 1)]
    for i in narrow:
        k = sum(1 for w in wide_sorted if boxes[w][1] <= boxes[i][1])
        bands[k].append(i)

    out: list[int] = []
    for k, band in enumerate(bands):
        if k > 0:
            out.append(wide_sorted[k - 1])
        columns: dict[int, list[int]] = {}
        for i in band:
            columns.setdefault(cols[i], []).append(i)
        for members in sorted(
            columns.values(),
            key=lambda m: (min(boxes[i][0] for i in m), min(items[i].get("order", 0) for i in m)),
        ):
            out.extend(sorted(members, key=key))
    return [items[i] for i in out]


def order_is_unsure(blocks: Sequence[dict[str, Any]], p: Params | None = None) -> bool:
    """Two blocks overlap in x by more than `min_overlap_px` but less than the same-column share
    of the narrower: the rule cannot tell their columns apart."""
    p = p or Params.from_settings()
    for a, b in combinations(blocks, 2):
        overlap = x_overlap(a["bbox"], b["bbox"])
        if overlap > p.min_overlap_px and not _same_column(
            a["bbox"], b["bbox"], p.same_column_frac
        ):
            return True
    return False


# --- duplicates (FR-10) ----------------------------------------------------------------------


def _squash(text: Any) -> str:
    return " ".join(str(text or "").split())


def text_match_frac(small: str, big: str) -> float:
    """Share of `small`'s characters matched in `big` (whitespace collapsed)."""
    a, b = _squash(small), _squash(big)
    if not a:
        return 0.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(m.size for m in matcher.get_matching_blocks()) / len(a)


def find_duplicates(blocks: Sequence[dict[str, Any]], p: Params | None = None) -> dict[str, str]:
    """{duplicate block_id: block_id of the bigger block it repeats} among `blocks` (the
    headline..body blocks of one article)."""
    p = p or Params.from_settings()
    ranked = sorted(blocks, key=lambda b: (-area(b["bbox"]), b.get("order", 0)))
    dup: dict[str, str] = {}
    for i, small in enumerate(ranked):
        box = small["bbox"]
        if area(box) <= 0 or not _squash(small.get("text")):
            continue
        for big in ranked[:i]:
            inside = intersection_area(box, big["bbox"]) / area(box)
            if inside < p.dup_overlap_frac:
                continue
            if text_match_frac(small.get("text", ""), big.get("text", "")) >= p.dup_text_frac:
                # point at the original, not at another duplicate
                target = big["block_id"]
                while target in dup:
                    target = dup[target]
                dup[small["block_id"]] = target
                break
    return dup


# --- one article -----------------------------------------------------------------------------


def _is_connected(boxes: Sequence[Sequence[float]], max_gap: float) -> bool:
    if len(boxes) < 2:
        return True
    seen = {0}
    todo = [0]
    while todo:
        i = todo.pop()
        for j in range(len(boxes)):
            if j not in seen and gap(boxes[i], boxes[j]) <= max_gap:
                seen.add(j)
                todo.append(j)
    return len(seen) == len(boxes)


def _join(texts: Sequence[Any], sep: str) -> str:
    return sep.join(t for t in (str(x or "").strip() for x in texts) if t)


def assemble_article(kind: str, members: Sequence[dict[str, Any]], p: Params) -> dict[str, Any]:
    """One article from its members: `{"block": <ocr block>, "role": str}` in the model's order.

    Returns kind, blocks (final order, with `duplicate_of` where it applies), headline, text,
    bbox_union and the per-article flags that need only this article (everything but
    `overlaps_other`).
    """
    flags: set[str] = set()
    text_members = [m for m in members if m["role"] in TEXT_ROLES]
    dups = find_duplicates([m["block"] for m in text_members], p)
    if dups:
        flags.add("duplicate_block")

    ordered: list[dict[str, Any]] = []  # members in the final order
    body_final: list[dict[str, Any]] = []
    for role in ROLE_SEQUENCE:
        of_role = [m for m in members if m["role"] == role]
        keep = [m for m in of_role if m["block"]["block_id"] not in dups]
        repeats = [m for m in of_role if m["block"]["block_id"] in dups]
        if len(keep) > 1 and order_is_unsure([m["block"] for m in keep], p):
            flags.add("order_from_model")
            seq = keep
        else:
            by_block = {id(m["block"]): m for m in keep}
            seq = [by_block[id(b)] for b in column_order([m["block"] for m in keep], p)]
        ordered.extend(seq)
        ordered.extend(repeats)
        if role == "body":
            body_final = seq
    if [m["block"]["order"] for m in body_final] != sorted(m["block"]["order"] for m in body_final):
        flags.add("order_changed")

    def texts(role: str) -> list[Any]:
        return [m["block"].get("text") for m in ordered if m["role"] == role and not dup_of(m)]

    def dup_of(m: dict[str, Any]) -> str | None:
        return dups.get(m["block"]["block_id"])

    headline = _join(texts("headline"), " ")
    text = _join([t for role in TEXT_BODY_ROLES for t in texts(role)], "\n\n")

    blocks_out = []
    for m in ordered:
        entry: dict[str, Any] = {"block_id": m["block"]["block_id"], "role": m["role"]}
        if dup_of(m):
            entry["duplicate_of"] = dup_of(m)
        blocks_out.append(entry)

    boxes = [m["block"]["bbox"] for m in members]
    if not _is_connected(boxes, p.max_gap_px):
        flags.add("disconnected")
    headlines = [m for m in members if m["role"] == "headline" and not dup_of(m)]
    bodies = [m for m in members if m["role"] == "body" and not dup_of(m)]
    if kind in HEADLINE_KINDS and not any(m["role"] == "headline" for m in members):
        flags.add("no_headline")
    if headlines and bodies:
        top_body = min(m["block"]["bbox"][1] for m in bodies)
        if any(m["block"]["bbox"][1] > top_body + p.headline_slack_px for m in headlines):
            flags.add("headline_below_body")
    return {
        "kind": kind,
        "blocks": blocks_out,
        "headline": headline,
        "text": text,
        "bbox_union": union_box(boxes),
        "flags": flags,
        "first_order": min(m["block"]["order"] for m in members),
    }


# --- the page --------------------------------------------------------------------------------


@dataclass
class Built:
    articles: list[dict[str, Any]]
    unassigned: list[str]
    page_flags: list[str]


def build_grouping(
    raw_articles: Sequence[dict[str, Any]],
    raw_unassigned: Sequence[int],
    blocks: Sequence[dict[str, Any]],
    page_id: str,
    p: Params | None = None,
) -> Built:
    """Account for every block, then build, check, flag and number the articles (FR-5, FR-6).

    `raw_articles` come from `parse.parse_reply` (block numbers are the blocks' `order`).
    """
    p = p or Params.from_settings()
    by_order = {b["order"]: b for b in blocks}
    page_flags: list[str] = []
    used: set[int] = set()

    def flag(name: str) -> None:
        if name not in page_flags:
            page_flags.append(name)

    articles_in: list[tuple[str, list[dict[str, Any]]]] = []
    for raw in raw_articles:
        members = []
        for entry in raw["blocks"]:
            n = entry["n"]
            if n in used:
                flag("block_duplicated")  # first use kept
                continue
            used.add(n)
            members.append({"block": by_order[n], "role": entry["role"]})
        if members:  # an article without blocks is dropped
            articles_in.append((raw["kind"], members))

    unassigned: set[int] = set()
    for n in raw_unassigned:
        if n in used or n in unassigned:
            flag("block_duplicated")
            continue
        unassigned.add(n)
    omitted = set(by_order) - used - unassigned
    if omitted:
        flag("blocks_omitted")
        unassigned |= omitted

    built = [assemble_article(kind, members, p) for kind, members in articles_in]
    built.sort(key=lambda a: a["first_order"])  # numbering follows the lowest block order

    boxes = {b["block_id"]: b["bbox"] for b in blocks}
    for a in built:
        own = {entry["block_id"] for entry in a["blocks"]}
        for other in built:
            if other is a:
                continue
            for entry in other["blocks"]:
                box = boxes[entry["block_id"]]
                if entry["block_id"] in own or area(box) <= 0:
                    continue
                if intersection_area(a["bbox_union"], box) > p.overlap_frac * area(box):
                    a["flags"].add("overlaps_other")
                    break
            if "overlaps_other" in a["flags"]:
                break

    articles = [
        {
            "article_id": f"{page_id}#a{i:02d}",
            "kind": a["kind"],
            "blocks": a["blocks"],
            "headline": a["headline"],
            "text": a["text"],
            "bbox_union": a["bbox_union"],
            "flags": [f for f in FLAG_SEQUENCE if f in a["flags"]],
        }
        for i, a in enumerate(built, start=1)
    ]
    left = sorted((b for b in blocks if b["order"] in unassigned), key=lambda b: b["order"])
    return Built(articles, [b["block_id"] for b in left], page_flags)
