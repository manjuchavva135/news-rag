"""The `Article` that `stitched_articles` returns (ticket 016 FR-5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Article:
    """One article of a print date, with continuation parts already joined.

    The first group of fields is the ticket-012/013 article record of the `group_final`
    artifact; `page_id`, `edition_code`, `section`, `page_num` and `parts` are added here. For
    a merged article (several parts) `article_id`, `kind`, `headline`, `bbox_union`,
    `judge_verdict`, `page_id`, `edition_code`, `section` and `page_num` are the first part's;
    `blocks` are all the parts' blocks, each with a `page_id` key (block ids repeat across
    pages); `text` joins the parts' texts; `flags` is the union of the parts' flags plus the
    link flags. `parts` lists `{article_id, page_id, bbox_union}` in chain order (one entry for
    an article that is not stitched).
    """

    article_id: str
    kind: str
    blocks: list[dict[str, Any]]
    headline: str
    text: str
    bbox_union: list[float]
    flags: list[str]
    page_id: str
    edition_code: str
    section: str
    page_num: int
    parts: list[dict[str, Any]]
    judge_verdict: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # any other key of the artifact record

    @property
    def stitched(self) -> bool:
        return len(self.parts) > 1
