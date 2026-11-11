"""Finding the continuation of each article of one print date (ticket 016 FR-2 to FR-4, FR-7).

Pure functions on plain data (no ledger, no files), so the tests can feed synthetic articles:

- `collect_parts(...)` turns the date's `group_final` artifacts into `Part`s (the eligible
  articles, with the continuation markers found in their text and marker blocks);
- `match_parts(...)` links source parts to continuation parts;
- `stitch_date(...)` runs both and returns the per-date artifact.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from newsrag import config
from newsrag.stitch import markers as mk

AMBIGUOUS = "continuation_ambiguous"
UNMATCHED_FLAG = "low_confidence:continuation_unmatched"
REASONS = ("no_target_page", "no_candidate", "below_score", "taken", "no_source")
MARKER_ROLE = "continuation_marker"

_TOKEN = re.compile(r"[ఀ-౿‌‍]+")
_EPS = 1e-9


@dataclass(frozen=True)
class Params:
    """The settings the matching reads (all `stitch_*` settings, part of the stage version)."""

    kinds: tuple[str, ...] = ("news", "editorial", "brief_item")
    min_score: float = 0.15
    ambiguity_margin: float = 0.02
    page_ref_bonus: float = 0.2
    tail_chars: int = 300
    token_min_chars: int = 2
    attach_px: int = 100

    @classmethod
    def from_settings(cls) -> Params:
        s = config.settings
        return cls(
            kinds=tuple(s.stitch_kinds),
            min_score=s.stitch_min_score,
            ambiguity_margin=s.stitch_ambiguity_margin,
            page_ref_bonus=s.stitch_page_ref_bonus,
            tail_chars=s.stitch_tail_chars,
            token_min_chars=s.stitch_token_min_chars,
            attach_px=s.stitch_attach_px,
        )


@dataclass(frozen=True)
class PageInfo:
    page_id: str
    edition_code: str
    section: str
    page_num: int
    page_end: int | None = None

    def covers(self, n: int) -> bool:
        """Page `n` is this page (or, for a spread, one of its pages)."""
        if self.page_end is None:
            return self.page_num == n
        return self.page_num <= n <= self.page_end

    @property
    def last_page(self) -> int:
        return self.page_end if self.page_end is not None else self.page_num


@dataclass
class Part:
    """An eligible article: it may be a source part (has `a` markers) and/or a continuation part
    (has `b` markers)."""

    article_id: str
    page: PageInfo
    headline: str
    text: str  # marker phrases removed
    a: list[mk.Marker] = field(default_factory=list)
    b: list[mk.Marker] = field(default_factory=list)

    @property
    def a_targets(self) -> list[int | None]:
        return [m.page for m in self.a]

    @property
    def b_pages(self) -> list[int]:
        return [m.page for m in self.b if m.page is not None]


# --- word overlap -------------------------------------------------------------------------------


def tokens(text: str, min_chars: int = 2) -> set[str]:
    """Telugu words of at least `min_chars` characters."""
    out = set()
    for raw in _TOKEN.findall(text or ""):
        word = raw.replace("‌", "").replace("‍", "")
        if len(word) >= min_chars:
            out.add(word)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def source_words(part: Part, p: Params) -> set[str]:
    tail = part.text[-p.tail_chars :] if p.tail_chars > 0 else ""
    return tokens(f"{part.headline} {tail}", p.token_min_chars)


def continuation_words(part: Part, p: Params) -> set[str]:
    return tokens(f"{part.headline} {part.text[: p.tail_chars]}", p.token_min_chars)


# --- editions -----------------------------------------------------------------------------------


def _wild(code: str) -> bool:
    return code in ("ALL", "FH") or "-VK" in code or not code.isalpha()


def editions_overlap(a: str, b: str) -> bool:
    """Letter-only codes overlap when they share a letter; `ALL`, `FH`, `-VK` variants (and any
    code that is not made of letters only) overlap with every code, on either side."""
    if _wild(a) or _wild(b):
        return True
    return bool(set(a) & set(b))


# --- collecting the parts -----------------------------------------------------------------------


def box_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Edge-to-edge distance of two boxes (0 when they touch or overlap)."""
    dx = max(0.0, a[0] - b[2], b[0] - a[2])
    dy = max(0.0, a[1] - b[3], b[1] - a[3])
    return math.hypot(dx, dy)


def _dedupe(found: Sequence[mk.Marker]) -> list[mk.Marker]:
    seen: set[tuple[str, int | None]] = set()
    out = []
    for m in found:
        if (m.kind, m.page) not in seen:
            seen.add((m.kind, m.page))
            out.append(m)
    return out


def collect_parts(
    pages: Sequence[PageInfo],
    groups: Mapping[str, Mapping[str, Any]],
    blocks: Mapping[str, Mapping[str, Mapping[str, Any]]],
    patterns: list[mk.Pattern],
    params: Params,
) -> tuple[list[Part], int]:
    """(the date's eligible articles as parts, number of orphan marker blocks).

    `groups[page_id]` is the page's `group_final` artifact, `blocks[page_id][block_id]` its
    `ocr_final` block (`text`, `bbox`). A `continuation_marker` block of an eligible article is
    searched with the article; one the grouping left unassigned (any unassigned block whose
    text holds a marker) or put in a non-eligible article joins the eligible article whose union
    box is nearest, within `attach_px`, else it is an orphan.
    """
    parts: list[Part] = []
    orphans = 0
    for page in pages:
        group = groups.get(page.page_id)
        if group is None:
            continue
        eligible = [a for a in group.get("articles") or [] if a.get("kind") in params.kinds]
        articles = group.get("articles") or []
        page_blocks: Mapping[str, Mapping[str, Any]] = {}
        if group.get("unassigned") or any(
            b.get("role") == MARKER_ROLE for a in articles for b in a.get("blocks") or []
        ):
            page_blocks = blocks.get(page.page_id) or {}
        pieces: dict[str, list[str]] = {}
        for a in eligible:
            texts = [a.get("text") or ""]
            for b in a.get("blocks") or []:
                if b.get("role") == MARKER_ROLE and not b.get("duplicate_of"):
                    texts.append((page_blocks.get(b["block_id"]) or {}).get("text") or "")
            pieces[a["article_id"]] = texts
        loose: list[str] = []
        for a in group.get("articles") or []:
            if a.get("kind") not in params.kinds:
                loose.extend(
                    b["block_id"] for b in a.get("blocks") or [] if b.get("role") == MARKER_ROLE
                )
        loose.extend(group.get("unassigned") or [])
        for block_id in loose:
            block = page_blocks.get(block_id) or {}
            text = block.get("text") or ""
            if not mk.find_markers(text, patterns):
                continue
            best, best_d = None, None
            for a in eligible:
                if not block.get("bbox") or not a.get("bbox_union"):
                    continue
                d = box_distance(block["bbox"], a["bbox_union"])
                if d <= params.attach_px and (best_d is None or d < best_d):
                    best, best_d = a, d
            if best is None:
                orphans += 1
            else:
                pieces[best["article_id"]].append(text)
        for a in eligible:
            found: list[mk.Marker] = []
            for text in pieces[a["article_id"]]:
                found.extend(mk.find_markers(text, patterns))
            found = _dedupe(found)
            parts.append(
                Part(
                    article_id=a["article_id"],
                    page=page,
                    headline=a.get("headline") or "",
                    text=mk.strip_markers(a.get("text") or "", patterns),
                    a=[m for m in found if m.kind == mk.CONTINUES_ON],
                    b=[m for m in found if m.kind == mk.CONTINUED_FROM],
                )
            )
    return parts, orphans


# --- matching -----------------------------------------------------------------------------------


@dataclass
class _Pair:
    source: Part
    cont: Part
    score: float
    method: str


def _targets_page(source: Part, cont: Part) -> bool:
    """The source's marker(s) point at the continuation's page ("every later page" if no number)."""
    for target in source.a_targets:
        if target is None:
            if cont.page.page_num > source.page.last_page:
                return True
        elif cont.page.covers(target):
            return True
    return False


def _names_page(cont: Part, source: Part) -> bool:
    """The continuation's (b) marker names the source's page."""
    return any(source.page.covers(n) for n in cont.b_pages)


def _reaches(nxt: Mapping[str, str], start: str, target: str) -> bool:
    cur = start
    while True:
        if cur == target:
            return True
        if cur not in nxt:
            return False
        cur = nxt[cur]


def _flags(score: float, others: Sequence[float], p: Params) -> list[str]:
    if any(abs(o - score) <= p.ambiguity_margin + _EPS for o in others):
        return [AMBIGUOUS]
    return []


def match_parts(
    parts: Sequence[Part], pages: Sequence[PageInfo], p: Params
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(links, unmatched) of FR-4 for the date's parts."""
    sources = [x for x in parts if x.a]
    conts = [x for x in parts if x.b]
    existing = {n for pg in pages for n in range(pg.page_num, pg.last_page + 1)}

    pairs: dict[str, list[_Pair]] = {}  # source article_id -> its candidates
    for s in sources:
        for c in conts:
            if (
                c.article_id == s.article_id
                or c.page.page_id == s.page.page_id
                or c.page.section != s.page.section
                or not editions_overlap(s.page.edition_code, c.page.edition_code)
                or not _targets_page(s, c)
            ):
                continue
            score = jaccard(source_words(s, p), continuation_words(c, p))
            method = "similarity"
            if _names_page(c, s):
                score += p.page_ref_bonus
                method = "page_ref"
            pairs.setdefault(s.article_id, []).append(_Pair(s, c, score, method))

    nxt: dict[str, str] = {}  # source -> continuation
    prev: dict[str, str] = {}  # continuation -> source
    link_info: dict[str, _Pair] = {}
    ordered = sorted(
        (x for lst in pairs.values() for x in lst if x.score + _EPS >= p.min_score),
        key=lambda x: (-x.score, x.source.article_id, x.cont.article_id),
    )
    for pair in ordered:
        s, c = pair.source.article_id, pair.cont.article_id
        if s in nxt or c in prev or _reaches(nxt, c, s):
            continue
        nxt[s], prev[c] = c, s
        link_info[s] = pair

    links: list[dict[str, Any]] = []

    def add_link(pair: _Pair, others: Sequence[float]) -> None:
        links.append(
            {
                "source_article_id": pair.source.article_id,
                "continuation_article_id": pair.cont.article_id,
                "score": round(pair.score, 4),
                "method": pair.method,
                "flags": _flags(pair.score, others, p),
            }
        )

    for s_id, pair in link_info.items():
        others = [
            x.score
            for x in pairs[s_id]
            if x.cont.article_id != pair.cont.article_id and x.score + _EPS >= p.min_score
        ]
        add_link(pair, others)

    # reverse match: a continuation whose marker names page P, no source there with a marker
    rev: list[_Pair] = []
    for c in conts:
        if c.article_id in prev or not c.b_pages:
            continue
        for x in parts:
            if (
                x.article_id == c.article_id
                or x.article_id in nxt
                or x.page.page_id == c.page.page_id
                or x.page.section != c.page.section
                or not editions_overlap(x.page.edition_code, c.page.edition_code)
                or not any(x.page.covers(n) for n in c.b_pages)
                or any(t is not None and not c.page.covers(t) for t in x.a_targets)
            ):
                continue
            score = jaccard(source_words(x, p), continuation_words(c, p))
            if score + _EPS >= p.min_score:
                rev.append(_Pair(x, c, score, "reverse"))
    rev_sorted = sorted(rev, key=lambda x: (-x.score, x.source.article_id, x.cont.article_id))
    for pair in rev_sorted:
        s, c = pair.source.article_id, pair.cont.article_id
        if s in nxt or c in prev or _reaches(nxt, c, s):
            continue
        nxt[s], prev[c] = c, s
        others = [x.score for x in rev if x.cont.article_id == c and x.source.article_id != s]
        add_link(pair, others)

    unmatched: list[dict[str, Any]] = []
    for s in sources:
        if s.article_id in nxt:
            continue
        pairs_s = pairs.get(s.article_id, [])
        later = any(n > s.page.last_page for n in existing)
        reachable = any(
            (t is None and later) or (t is not None and t in existing) for t in s.a_targets
        )
        if not reachable:
            reason = "no_target_page"
        elif not pairs_s:
            reason = "no_candidate"
        elif all(x.score + _EPS < p.min_score for x in pairs_s):
            reason = "below_score"
        else:
            reason = "taken"
        unmatched.append({"article_id": s.article_id, "marker": s.a[0].text, "reason": reason})
    for c in conts:
        if c.article_id not in prev:
            unmatched.append(
                {"article_id": c.article_id, "marker": c.b[0].text, "reason": "no_source"}
            )
    links.sort(key=lambda x: (x["source_article_id"], x["continuation_article_id"]))
    return links, unmatched


def stitch_date(
    pub_date: Any,
    pages: Sequence[PageInfo],
    groups: Mapping[str, Mapping[str, Any]],
    blocks: Mapping[str, Mapping[str, Mapping[str, Any]]],
    patterns: list[mk.Pattern],
    params: Params,
) -> dict[str, Any]:
    """The per-date `stitch` artifact (FR-4) plus `stats`."""
    parts, orphans = collect_parts(pages, groups, blocks, patterns, params)
    links, unmatched = match_parts(parts, pages, params)
    return {
        "pub_date": str(pub_date),
        "links": links,
        "unmatched": unmatched,
        "stats": {
            "source_markers": sum(1 for x in parts if x.a),
            "continuation_markers": sum(1 for x in parts if x.b),
            "orphan_markers": orphans,
        },
    }
