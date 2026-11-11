"""The warehouse rows of one print date (ticket 024 FR-2). Pure functions over plain data: the
stage loads the artifacts and the ledger, `write` stores the result."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
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


def parse_date(value: Any) -> date | None:
    """`YYYY-MM-DD` to a date; anything else (None, a partial or invalid date) to None."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _list(value: Any) -> list[str]:
    return [str(v) for v in value or []]


def page_row(page: Mapping[str, Any]) -> dict[str, Any]:
    """A `pages` row from a `ledger.pages` row joined with its `ledger.page_editions` row."""
    return {
        "page_id": page["page_id"],
        "pub_date": page["pub_date"],
        "edition_code": page["edition_code"],
        "section": page["section"],
        "page_num": page["page_num"],
        "page_end": page.get("page_end"),
        "edition_names": _list(page.get("edition_names")),
        "districts_current": _list(page.get("districts_current")),
        "states": _list(page.get("states")),
    }


def surface_forms(
    enriched: Mapping[str, Any] | None, name_to_entity: Mapping[str, str]
) -> dict[str, list[str]]:
    """entity id -> the spellings of it that the story used (people, then organisations)."""
    from newsrag.aliases import key

    out: dict[str, list[str]] = {}
    for kind, field_name in (("person", "people"), ("org", "organisations")):
        for entry in (enriched or {}).get(field_name) or []:
            name = (entry.get("name") or "").strip()
            entity = name_to_entity.get(key(kind, name)) if name else None
            if entity is None:
                continue
            forms = out.setdefault(entity, [])
            if name not in forms:
                forms.append(name)
    return out


def build_date_rows(
    pub_date: date,
    generation: str,
    stories: Iterable[Mapping[str, Any]],
    enriched: Mapping[str, Mapping[str, Any]],
    chunks: Sequence[Mapping[str, Any]],
    name_to_entity: Mapping[str, str],
    pages: Iterable[Mapping[str, Any]] = (),
) -> DateRows:
    """The rows of the date. Only stories that produced chunks are included (FR-2)."""
    from newsrag.chunking.build import canonical_page_id

    out = DateRows()
    by_story: dict[str, list[Mapping[str, Any]]] = {}
    for chunk in chunks:
        by_story.setdefault(chunk["story_id"], []).append(chunk)
    out.pages = [page_row(p) for p in pages]
    for chunk in chunks:
        out.chunks.append(
            {
                "generation": generation,
                "chunk_id": chunk["chunk_id"],
                "story_id": chunk["story_id"],
                "pub_date": pub_date,
                "seq": chunk["seq"],
                "kind": chunk["kind"],
                "headline": chunk["headline"],
                "prefix": chunk["prefix"],
                "text": chunk["text"],
                "n_tokens": chunk["n_tokens"],
            }
        )
    for story in stories:
        story_id = story["story_id"]
        story_chunks = by_story.get(story_id)
        if not story_chunks:
            continue
        # the first chunk is the story's own text (a story without text has no chunks), so its
        # reasons are the story's; a table chunk may add reasons of its own
        first = min(story_chunks, key=lambda c: c["seq"])
        record = enriched.get(story_id) or {}
        printings = story.get("printings") or []
        out.stories.append(
            {
                "story_id": story_id,
                "pub_date": pub_date,
                "event_date": parse_date(first.get("event_date")),
                "kind": story.get("kind") or "news",
                "category": first.get("category"),
                "headline": first["headline"],
                "text": story.get("text") or "",
                "canonical_page_id": first.get("canonical_page_id") or canonical_page_id(story),
                "n_printings": len(printings) or 1,
                "department": story.get("department"),
                "notice_number": story.get("notice_number"),
                "deadline": story.get("deadline"),
                "low_confidence": bool(first.get("low_confidence")),
                "low_confidence_reasons": _list(first.get("low_confidence_reasons")),
            }
        )
        for printing in printings:
            out.story_printings.append(
                {
                    "story_id": story_id,
                    "unit_id": printing["unit_id"],
                    "page_id": printing["page_id"],
                    "edition_code": printing["edition_code"],
                    "section": printing.get("section"),
                    "page_num": printing.get("page_num"),
                    "parts": list(printing.get("parts") or []),
                }
            )
        for entity_id, forms in surface_forms(record, name_to_entity).items():
            out.story_entities.append(
                {"story_id": story_id, "entity_id": entity_id, "surface_forms": forms}
            )
        for seq, place in enumerate(record.get("places") or [], start=1):
            out.story_places.append(
                {
                    "story_id": story_id,
                    "seq": seq,
                    "name": place.get("name") or "",
                    "llm_type": place.get("llm_type"),
                    "kind": place.get("kind"),
                    "state": place.get("state"),
                    "districts_current": _list(place.get("districts_current")),
                    "districts_at_print": _list(place.get("districts_at_print")),
                }
            )
        for fact in record.get("facts") or []:
            out.facts.append(
                {
                    "fact_id": fact["fact_id"],
                    "story_id": story_id,
                    "value_text": fact.get("value_text"),
                    "value": fact.get("value"),
                    "scale": fact.get("scale"),
                    "normalized_value": fact.get("normalized_value"),
                    "unit": fact.get("unit"),
                    "refers_to": fact.get("refers_to"),
                    "period_text": fact.get("period_text"),
                    "period_start": parse_date(fact.get("period_start")),
                    "period_end": parse_date(fact.get("period_end")),
                    "place": fact.get("place"),
                    "districts_current": _list(fact.get("districts_current")),
                    "districts_at_print": _list(fact.get("districts_at_print")),
                    "state": fact.get("state"),
                }
            )
    return out


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
