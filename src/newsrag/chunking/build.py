"""The chunks of one print date (ticket 023 FR-2, FR-3, FR-5, FR-7). Pure functions over plain
data: `newsrag.chunking.load` reads the artifacts and the ledger, the stage writes the result."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from newsrag.chunking.split import NO_HEADLINE, Chunker

EXCLUDED_KINDS = ("ad_commercial", "masthead", "other")  # FR-2: never chunked (grill A-8, D-9)
ROW_SPLIT_KINDS = ("table", "infographic", "table_only")  # FR-7: split by rows, not paragraphs
ATTACHED_TYPES = ("table", "infographic")
LOW_CONFIDENCE = "low_confidence:"


@dataclass
class DateInputs:
    """Everything `build_date` reads, loaded once per date."""

    pub_date: date
    stories: list[dict[str, Any]]  # the date's dedup stories
    enriched: Mapping[str, Mapping[str, Any]]  # story_id -> merged enrich record
    items: Mapping[str, Sequence[Mapping[str, Any]]]  # page_id -> ticket-015 items
    page_editions: Mapping[str, Mapping[str, Any]]  # page_id -> ledger.page_editions row
    page_flags: Mapping[str, Sequence[str]]  # page_id -> group_final + transcribe page flags
    name_to_entity: Mapping[str, str]
    editions: Mapping[str, Mapping[str, Any]]  # edition code -> editions.yaml entry of the year


@dataclass
class DateChunks:
    chunks: list[dict[str, Any]] = field(default_factory=list)
    stories: int = 0  # stories that produced chunks
    table_chunks: int = 0
    empty_stories: int = 0
    unmapped_names: int = 0
    lookup_hash: str = ""


@dataclass
class _Section:
    """The text of one story or table, cut into windows with one prefix."""

    kind: str
    headline: str
    text: str
    split: Callable[[str, str], list[str]]
    reasons: list[str]


def chunkable(story: Mapping[str, Any]) -> bool:
    return story.get("kind") not in EXCLUDED_KINDS


def story_page_ids(story: Mapping[str, Any]) -> list[str]:
    """Every page a printing or a part of the story is on, in printing order."""
    out: list[str] = []
    for printing in story.get("printings") or []:
        for page_id in [printing["page_id"], *(p["page_id"] for p in printing.get("parts") or [])]:
            if page_id not in out:
                out.append(page_id)
    return out


def part_article_ids(story: Mapping[str, Any]) -> set[str]:
    return {
        part["article_id"]
        for printing in story.get("printings") or []
        for part in printing.get("parts") or []
        if part.get("article_id")
    }


def canonical_page_id(story: Mapping[str, Any]) -> str | None:
    wanted = story.get("canonical_unit_id")
    for printing in story.get("printings") or []:
        if printing.get("unit_id") == wanted:
            return printing["page_id"]
    printings = story.get("printings") or []
    return printings[0]["page_id"] if printings else None


def attached_items(story: Mapping[str, Any], items: Mapping[str, Sequence[Mapping[str, Any]]]):
    """The ticket-015 tables and infographics of the story's articles, de-duplicated by their
    markdown (`inlined` items are already part of a story's text)."""
    articles = part_article_ids(story)
    seen: set[str] = set()
    out: list[Mapping[str, Any]] = []
    for page_id in story_page_ids(story):
        for item in items.get(page_id) or []:
            markdown = (item.get("markdown") or "").strip()
            if (
                item.get("article_id") not in articles
                or item.get("type") not in ATTACHED_TYPES
                or item.get("inlined")
                or not markdown
                or markdown in seen
            ):
                continue
            seen.add(markdown)
            out.append(item)
    return out


def printings_phrase(n: int) -> str:
    return "1 printing" if n == 1 else f"{n} printings"


def make_prefix(chunker: Chunker, headline: str, pub_date: date, n_printings: int) -> str:
    """`<headline> | DD-MM-YYYY | <n> printings` (FR-3); a headline too long for the budget is cut."""
    rest = f" | {pub_date.strftime('%d-%m-%Y')} | {printings_phrase(n_printings)}"
    return f"{chunker.fit_headline(headline, rest)}{rest}"


def low_confidence_reasons(*flag_groups: Iterable[str]) -> list[str]:
    """The `low_confidence:` flags of the groups, without the prefix, in order, once each."""
    out: list[str] = []
    for group in flag_groups:
        for flag in group or ():
            if flag.startswith(LOW_CONFIDENCE) and flag[len(LOW_CONFIDENCE) :] not in out:
                out.append(flag[len(LOW_CONFIDENCE) :])
    return out


def _unique(values: Iterable[Any]) -> list[Any]:
    return list(dict.fromkeys(v for v in values if v))


def story_flags(story: Mapping[str, Any], enriched: Mapping[str, Any] | None) -> list[str]:
    """The flags carried by the story itself: dedup (with its printings' and parts' flags) and
    enrichment, the printings' own flags and the parts' flags."""
    flags = list(story.get("flags") or [])
    flags += list((enriched or {}).get("flags") or [])
    for printing in story.get("printings") or []:
        flags += list(printing.get("flags") or [])
        for part in printing.get("parts") or []:
            flags += list(part.get("flags") or [])
    return flags


def entity_ids(
    enriched: Mapping[str, Any] | None,
    name_to_entity: Mapping[str, str],
    looked_up: dict[str, str | None],
) -> tuple[list[str], int]:
    """(entity ids of the story's people and organisations, names not in the alias table).

    Every key asked for is recorded in `looked_up` (for the lookup hash)."""
    from newsrag.aliases import key

    ids: list[str] = []
    unmapped = 0
    for kind, field_name in (("person", "people"), ("org", "organisations")):
        for entry in (enriched or {}).get(field_name) or []:
            name = (entry.get("name") or "").strip()
            if not name:
                continue
            name_key = key(kind, name)
            entity = name_to_entity.get(name_key)
            looked_up[name_key] = entity
            if entity is None:
                unmapped += 1
            elif entity not in ids:
                ids.append(entity)
    return ids, unmapped


def lookup_digest(
    looked_up: Mapping[str, str | None],
    editions: Mapping[str, Any],
    page_editions: Mapping[str, Any],
) -> str:
    """FR-7: sha1 of the alias entries, `editions.yaml` entries and page editions a date used."""
    payload = {"names": looked_up, "editions": editions, "pages": page_editions}
    text = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha1(text.encode()).hexdigest()


def edition_entry_key(pub_date: date, code: str) -> str:
    return f"{pub_date.year}|{code}"


def story_edition_codes(story: Mapping[str, Any]) -> list[str]:
    return sorted(_unique(p.get("edition_code") for p in story.get("printings") or []))


def compute_lookup_hash(inputs: DateInputs) -> str:
    """The `lookup_hash` of a date (FR-7): what its chunks take from the alias table, the edition
    map and the page editions. `stale_dates` recomputes it without chunking."""
    looked_up: dict[str, str | None] = {}
    editions: dict[str, Any] = {}
    pages: dict[str, Any] = {}
    for story in inputs.stories:
        if not chunkable(story) or not (story.get("text") or "").strip():
            continue
        entity_ids(inputs.enriched.get(story["story_id"]), inputs.name_to_entity, looked_up)
        for page_id in story_page_ids(story):
            if page_id in inputs.page_editions:
                pages[page_id] = dict(inputs.page_editions[page_id])
        for code in story_edition_codes(story):
            editions[edition_entry_key(inputs.pub_date, code)] = inputs.editions.get(code)
    return lookup_digest(looked_up, editions, pages)


def build_date(inputs: DateInputs, chunker: Chunker) -> DateChunks:
    """All chunks of the date, plus the counts and the lookup hash for the stage detail."""
    out = DateChunks()
    for story in inputs.stories:
        if not chunkable(story):
            continue
        text = (story.get("text") or "").strip()
        if not text:
            out.empty_stories += 1
            continue
        enriched = inputs.enriched.get(story["story_id"])
        ids, unmapped = entity_ids(enriched, inputs.name_to_entity, {})
        out.unmapped_names += unmapped
        page_ids = story_page_ids(story)
        canonical = canonical_page_id(story)
        rows = [inputs.page_editions[p] for p in page_ids if p in inputs.page_editions]
        codes = story_edition_codes(story)
        flags_story = story_flags(story, enriched)
        reasons = low_confidence_reasons(
            flags_story,
            inputs.page_flags.get(canonical, ()) if canonical else (),
            *(r.get("flags") or () for r in rows),
        )
        n_printings = len(story.get("printings") or []) or 1
        meta = {
            "pub_date": inputs.pub_date.isoformat(),
            "event_date": (enriched or {}).get("event_date") or inputs.pub_date.isoformat(),
            "category": (enriched or {}).get("category"),
            "editions": codes,
            "edition_names": _unique(n for r in rows for n in r.get("edition_names") or ()),
            "edition_districts": _unique(n for r in rows for n in r.get("districts_current") or ()),
            "edition_states": _unique(n for r in rows for n in r.get("states") or ()),
            "states": list((enriched or {}).get("states") or []),
            "districts_current": list((enriched or {}).get("districts_current") or []),
            "districts_at_print": list((enriched or {}).get("districts_at_print") or []),
            "entity_ids": ids,
            "facts_ids": [f["fact_id"] for f in (enriched or {}).get("facts") or []],
            "department": story.get("department"),
            "notice_number": story.get("notice_number"),
            "deadline": story.get("deadline"),
            "page_ids": page_ids,
            "canonical_page_id": canonical,
        }
        headline = (story.get("headline") or "").strip()
        story_kind = story.get("kind") or "news"
        split = chunker.split_table if story_kind in ROW_SPLIT_KINDS else chunker.split_text
        sections = [_Section(story_kind, headline or NO_HEADLINE, text, split, [])]
        for item in attached_items(story, inputs.items):
            title = (item.get("title") or "").strip()
            sections.append(
                _Section(
                    item["type"],
                    headline or title or NO_HEADLINE,
                    item["markdown"].strip(),
                    chunker.split_table,
                    low_confidence_reasons(item.get("flags") or ()),
                )
            )
        seq = 0
        for section in sections:
            prefix = make_prefix(chunker, section.headline, inputs.pub_date, n_printings)
            for window in section.split(prefix, section.text):
                seq += 1
                out.chunks.append(
                    {
                        "chunk_id": f"{story['story_id']}#c{seq:02d}",
                        "story_id": story["story_id"],
                        "kind": section.kind,
                        "seq": seq,
                        "headline": section.headline,
                        "prefix": prefix,
                        "text": window,
                        "n_tokens": chunker.n_tokens(prefix, window),
                        **meta,
                        "low_confidence": bool(reasons or section.reasons),
                        "low_confidence_reasons": _unique([*reasons, *section.reasons]),
                    }
                )
                if section is not sections[0]:
                    out.table_chunks += 1
        out.stories += 1
    out.lookup_hash = compute_lookup_hash(inputs)
    return out
