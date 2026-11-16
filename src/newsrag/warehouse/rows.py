"""The shape of the warehouse rows of one print date (ticket 024 FR-2): the column order of each
table and `DateRows`. `newsrag.embed.rows` builds them from the pipeline artifacts; `write` stores
them."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# column order of every table the date's rows go to
COLUMNS: dict[str, tuple[str, ...]] = {
    "pages": (
        "page_id",
        "pub_date",
        "edition_code",
        "section",
        "page_num",
        "page_end",
        "edition_names",
        "districts_current",
        "states",
    ),
    "stories": (
        "story_id",
        "pub_date",
        "event_date",
        "kind",
        "category",
        "headline",
        "text",
        "canonical_page_id",
        "n_printings",
        "department",
        "notice_number",
        "deadline",
        "low_confidence",
        "low_confidence_reasons",
    ),
    "story_printings": (
        "story_id",
        "unit_id",
        "page_id",
        "edition_code",
        "section",
        "page_num",
        "parts",
    ),
    "chunks": (
        "generation",
        "chunk_id",
        "story_id",
        "pub_date",
        "seq",
        "kind",
        "headline",
        "prefix",
        "text",
        "n_tokens",
    ),
    "story_entities": ("story_id", "entity_id", "surface_forms"),
    "story_places": (
        "story_id",
        "seq",
        "name",
        "llm_type",
        "kind",
        "state",
        "districts_current",
        "districts_at_print",
    ),
    "facts": (
        "fact_id",
        "story_id",
        "value_text",
        "value",
        "scale",
        "normalized_value",
        "unit",
        "refers_to",
        "period_text",
        "period_start",
        "period_end",
        "place",
        "districts_current",
        "districts_at_print",
        "state",
    ),
}
ENTITY_COLUMNS = (
    "entity_id",
    "type",
    "org_type",
    "canonical",
    "canonical_te",
    "canonical_en",
    "aliases",
)
JSON_COLUMNS = {"parts", "surface_forms", "aliases"}


@dataclass
class DateRows:
    """Rows by table name (dicts keyed by column), for the stories of one date that have chunks."""

    pages: list[dict[str, Any]] = field(default_factory=list)
    stories: list[dict[str, Any]] = field(default_factory=list)
    story_printings: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    story_entities: list[dict[str, Any]] = field(default_factory=list)
    story_places: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)

    def story_ids(self) -> list[str]:
        return [s["story_id"] for s in self.stories]


def entity_rows(entities: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """`warehouse.entities` rows from the `entities` list of the alias table."""
    return [
        {
            "entity_id": e["entity_id"],
            "type": e["type"],
            "org_type": e.get("org_type"),
            "canonical": e.get("canonical") or "",
            "canonical_te": e.get("canonical_te"),
            "canonical_en": e.get("canonical_en"),
            "aliases": list(e.get("aliases") or []),
        }
        for e in entities
    ]
