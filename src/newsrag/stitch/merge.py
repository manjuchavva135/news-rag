"""Joining the linked parts of a date into one article each (ticket 016 FR-5, FR-4, FR-7)."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from newsrag.stitch import markers as mk
from newsrag.stitch.match import UNMATCHED_FLAG, PageInfo
from newsrag.stitch.model import Article

_KNOWN = {
    "article_id",
    "kind",
    "blocks",
    "headline",
    "text",
    "bbox_union",
    "flags",
    "judge_verdict",
}


def _add(flags: list[str], new: Sequence[str]) -> None:
    for flag in new:
        if flag not in flags:
            flags.append(flag)


def _article(page: PageInfo, record: Mapping[str, Any]) -> Article:
    return Article(
        article_id=record["article_id"],
        kind=record.get("kind") or "other",
        blocks=copy.deepcopy(list(record.get("blocks") or [])),
        headline=record.get("headline") or "",
        text=record.get("text") or "",
        bbox_union=list(record.get("bbox_union") or []),
        flags=list(record.get("flags") or []),
        judge_verdict=record.get("judge_verdict"),
        page_id=page.page_id,
        edition_code=page.edition_code,
        section=page.section,
        page_num=page.page_num,
        parts=[
            {
                "article_id": record["article_id"],
                "page_id": page.page_id,
                "bbox_union": list(record.get("bbox_union") or []),
            }
        ],
        extra={k: copy.deepcopy(v) for k, v in record.items() if k not in _KNOWN},
    )


def merge_articles(
    pages: Sequence[PageInfo],
    groups: Mapping[str, Mapping[str, Any]],
    artifact: Mapping[str, Any],
    patterns: list[mk.Pattern],
) -> list[Article]:
    """Every article of the date (all kinds), in page order, each linked chain as one article.

    Parts of the `unmatched` list get `low_confidence:continuation_unmatched` here, at read
    time; the `group_final` artifacts are not changed.
    """
    flagged = {u["article_id"] for u in artifact.get("unmatched") or []}
    singles: dict[str, Article] = {}
    order: list[str] = []
    for page in pages:
        group = groups.get(page.page_id)
        if group is None:
            continue
        for record in group.get("articles") or []:
            art = _article(page, record)
            if art.article_id in flagged:
                _add(art.flags, [UNMATCHED_FLAG])
            singles[art.article_id] = art
            order.append(art.article_id)

    nxt: dict[str, str] = {}
    link_flags: dict[str, list[str]] = {}
    for link in artifact.get("links") or []:
        s, c = link["source_article_id"], link["continuation_article_id"]
        if s in singles and c in singles:
            nxt[s] = c
            link_flags[s] = list(link.get("flags") or [])
    is_continuation = set(nxt.values())

    out: list[Article] = []
    for article_id in order:
        if article_id in is_continuation:
            continue
        head = singles[article_id]
        if article_id not in nxt:
            out.append(head)
            continue
        chain = [head]
        flags = list(head.flags)
        while chain[-1].article_id in nxt:
            _add(flags, link_flags[chain[-1].article_id])
            nxt_part = singles[nxt[chain[-1].article_id]]
            _add(flags, nxt_part.flags)
            chain.append(nxt_part)
        texts = [mk.strip_markers(part.text, patterns) for part in chain]
        merged = Article(
            article_id=head.article_id,
            kind=head.kind,
            blocks=[{**block, "page_id": part.page_id} for part in chain for block in part.blocks],
            headline=head.headline,
            text="\n\n".join(t for t in texts if t),
            bbox_union=head.bbox_union,
            flags=flags,
            judge_verdict=head.judge_verdict,
            page_id=head.page_id,
            edition_code=head.edition_code,
            section=head.section,
            page_num=head.page_num,
            parts=[p for part in chain for p in part.parts],
            extra=head.extra,
        )
        out.append(merged)
    return out


def link_pairs(
    pages: Sequence[PageInfo],
    groups: Mapping[str, Mapping[str, Any]],
    artifact: Mapping[str, Any],
    patterns: list[mk.Pattern],
) -> list[dict[str, Any]]:
    """For `stitch verify`: each link with the source's headline and text and the
    continuation's text, marker phrases removed."""
    records: dict[str, Mapping[str, Any]] = {}
    for page in pages:
        for record in (groups.get(page.page_id) or {}).get("articles") or []:
            records[record["article_id"]] = record
    out = []
    for link in artifact.get("links") or []:
        s = records.get(link["source_article_id"])
        c = records.get(link["continuation_article_id"])
        if s is None or c is None:
            continue
        out.append(
            {
                **link,
                "pub_date": artifact.get("pub_date"),
                "source_headline": s.get("headline") or "",
                "source_text": mk.strip_markers(s.get("text") or "", patterns),
                "continuation_text": mk.strip_markers(c.get("text") or "", patterns),
            }
        )
    return out
