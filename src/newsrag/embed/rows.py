"""The warehouse rows of one print date (ticket 024 FR-2), built from the date's stories, chunks
and enrichments. Pure functions over plain data: the `index` stage loads the artifacts and the
ledger, `newsrag.warehouse.write` stores the result."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from newsrag.warehouse.rows import DateRows


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
