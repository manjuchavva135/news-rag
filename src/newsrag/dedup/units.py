"""The units of a print date: stitched articles and standalone ticket-015 items (ticket 017 FR-1,
FR-7). Pure functions over plain data; reading the ledger and the work folder is in the stage."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from newsrag import config

NOTICE = "notice_tender"
COMMERCIAL = "commercial"
TABLE_ONLY = "table_only"
UNTRANSCRIBED = "low_confidence:notice_untranscribed"
TABLE_TYPES = ("table", "infographic")

# Excluded groups that are always listed in the artifact, even at 0.
EXCLUDED_KEYS = ("ad_commercial", "masthead", "other", "commercial")


@dataclass
class Unit:
    """One thing that may have several printings: an article or a standalone item."""

    unit_id: str
    kind: str
    headline: str
    text: str
    page_id: str
    edition_code: str
    section: str
    page_num: int
    parts: list[dict[str, Any]]
    flags: list[str] = field(default_factory=list)
    department: str | None = None
    notice_number: str | None = None
    deadline: str | None = None

    @property
    def pages(self) -> frozenset[str]:
        """Every page a part of this unit is on."""
        return frozenset(p["page_id"] for p in self.parts)


def union_flags(*groups: Iterable[str]) -> list[str]:
    out: list[str] = []
    for group in groups:
        for flag in group or ():
            if flag not in out:
                out.append(flag)
    return out


def _usable(item: Mapping[str, Any]) -> bool:
    return bool((item.get("markdown") or "").strip())


def _union_box(boxes: Sequence[Sequence[float]]) -> list[float]:
    if not boxes:
        return []
    return [
        float(min(b[0] for b in boxes)),
        float(min(b[1] for b in boxes)),
        float(max(b[2] for b in boxes)),
        float(max(b[3] for b in boxes)),
    ]


def _article_unit(article: Any, items: list[Mapping[str, Any]]) -> tuple[Unit | None, str]:
    """(unit, "") for an article that becomes a unit, else (None, excluded group)."""
    notices = [i for i in items if i.get("type") == NOTICE and _usable(i)]
    if any(i.get("type") == COMMERCIAL for i in items) and not notices:
        return None, COMMERCIAL
    base = {
        "unit_id": article.article_id,
        "headline": article.headline or "",
        "page_id": article.page_id,
        "edition_code": article.edition_code,
        "section": article.section,
        "page_num": article.page_num,
        "parts": [dict(p) for p in article.parts],
    }
    if notices:
        item = notices[0]
        return Unit(
            kind=NOTICE,
            text=item["markdown"],
            flags=union_flags(article.flags, item.get("flags") or []),
            department=item.get("department"),
            notice_number=item.get("notice_number"),
            deadline=item.get("deadline"),
            **base,
        ), ""
    if article.kind not in config.settings.dedup_kinds:
        return None, article.kind
    if article.kind == NOTICE:
        return Unit(
            kind=NOTICE,
            text=article.text or "",
            flags=union_flags(article.flags, [UNTRANSCRIBED]),
            **base,
        ), ""
    if article.kind == TABLE_ONLY:
        tables = [i for i in items if i.get("type") in TABLE_TYPES and _usable(i)]
        text = "\n\n".join(i["markdown"] for i in tables) if tables else article.text or ""
        flags = union_flags(article.flags, *(i.get("flags") or [] for i in tables))
        return Unit(kind=TABLE_ONLY, text=text, flags=flags, **base), ""
    return Unit(kind=article.kind, text=article.text or "", flags=list(article.flags), **base), ""


def build_units(
    articles: Sequence[Any],
    items_by_page: Mapping[str, Sequence[Mapping[str, Any]]],
    pages: Mapping[str, Any],
    boxes_of: Callable[[str], Mapping[str, Sequence[float]]],
) -> tuple[list[Unit], dict[str, int]]:
    """The date's units (sorted by unit_id) and the `excluded` counts.

    `articles` come from `newsrag.stitch.stitched_articles`; `items_by_page[page_id]` are the
    `items` of the page's `transcribe` artifact; `pages[page_id]` has `edition_code`, `section`
    and `page_num`; `boxes_of(page_id)` maps block ids to boxes (only asked for pages that hold
    a standalone item).
    """
    by_article: dict[str, list[Mapping[str, Any]]] = {}
    standalone: list[tuple[str, Mapping[str, Any]]] = []
    for page_id in sorted(items_by_page):
        for item in items_by_page[page_id]:
            if item.get("article_id"):
                by_article.setdefault(item["article_id"], []).append(item)
            else:
                standalone.append((page_id, item))

    excluded = dict.fromkeys(EXCLUDED_KEYS, 0)
    units: list[Unit] = []
    for article in articles:
        items: list[Mapping[str, Any]] = []
        for part in article.parts:
            items.extend(by_article.get(part["article_id"], []))
        unit, group = _article_unit(article, items)
        if unit is None:
            excluded[group] = excluded.get(group, 0) + 1
        else:
            units.append(unit)

    for page_id, item in standalone:
        if item.get("type") == COMMERCIAL:
            excluded[COMMERCIAL] += 1
            continue
        page = pages[page_id]
        boxes = boxes_of(page_id)
        found = [boxes[b] for b in item.get("block_ids") or [] if b in boxes]
        units.append(
            Unit(
                unit_id=item["item_id"],
                kind=item["type"],
                headline=item.get("title") or "",
                text=item.get("markdown") or "",
                page_id=page_id,
                edition_code=page.edition_code,
                section=page.section,
                page_num=page.page_num,
                parts=[{"article_id": None, "page_id": page_id, "bbox_union": _union_box(found)}],
                flags=list(item.get("flags") or []),
            )
        )
    units.sort(key=lambda u: u.unit_id)
    return units, excluded
