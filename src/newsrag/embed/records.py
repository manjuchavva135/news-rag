"""What is written to Qdrant and OpenSearch for a chunk (ticket 024 FR-2). Pure functions."""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

POINT_NAMESPACE = uuid.NAMESPACE_URL
NOT_IN_PAYLOAD = ("text", "prefix")  # the text lives in Postgres and OpenSearch

# Qdrant payload indexes (FR-2)
QDRANT_INT_INDEXES = ("pub_date_int", "event_date_int")
QDRANT_KEYWORD_INDEXES = (
    "category",
    "editions",
    "edition_names",
    "states",
    "districts_current",
    "districts_at_print",
    "edition_districts",
    "entity_ids",
    "story_id",
    "page_ids",
)
QDRANT_BOOL_INDEXES = ("low_confidence",)

# OpenSearch fields besides the four text fields
OS_KEYWORDS = (
    "chunk_id",
    "story_id",
    "kind",
    "category",
    "editions",
    "edition_names",
    "edition_districts",
    "edition_states",
    "states",
    "districts_current",
    "districts_at_print",
    "entity_ids",
    "facts_ids",
    "department",
    "notice_number",
    "deadline",
    "page_ids",
    "canonical_page_id",
    "low_confidence_reasons",
)


def point_id(chunk_id: str) -> str:
    """The Qdrant point id of a chunk: the UUID5 of its chunk_id."""
    return str(uuid.uuid5(POINT_NAMESPACE, chunk_id))


def date_int(value: str | date | None) -> int | None:
    """`2026-04-03` to 20260403 (None for a missing or invalid date)."""
    if value is None:
        return None
    try:
        day = value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        return None
    return day.year * 10000 + day.month * 100 + day.day


def qdrant_payload(chunk: Mapping[str, Any]) -> dict[str, Any]:
    """Every field of the chunk record except `text` and `prefix`, plus `pub_date_int` and
    `event_date_int`."""
    payload = {k: v for k, v in chunk.items() if k not in NOT_IN_PAYLOAD}
    payload["pub_date_int"] = date_int(chunk.get("pub_date"))
    payload["event_date_int"] = date_int(chunk.get("event_date") or chunk.get("pub_date"))
    return payload


def script_shares(text: str) -> tuple[float, float]:
    """(Telugu share, Latin share) of the letters of `text` (letters and their combining marks,
    so Telugu vowel signs count)."""
    telugu = latin = letters = 0
    for ch in text:
        if unicodedata.category(ch)[0] not in "LM":
            continue
        letters += 1
        code = ord(ch)
        if 0x0C00 <= code <= 0x0C7F:
            telugu += 1
        elif (ch.isascii() and ch.isalpha()) or 0x00C0 <= code <= 0x024F:
            latin += 1
    if not letters:
        return 0.0, 0.0
    return telugu / letters, latin / letters


def text_fields(text: str, share: float) -> list[str]:
    """The languages (`te`, `en`) a text is indexed under: each script with at least `share` of
    the letters. A text with neither (digits, symbols) goes to `te`, the newspaper's language."""
    telugu, latin = script_shares(text)
    langs = [lang for lang, s in (("te", telugu), ("en", latin)) if s >= share and s > 0]
    return langs or ["te"]


def os_document(chunk: Mapping[str, Any], share: float) -> dict[str, Any]:
    """The OpenSearch document of a chunk: `text_te`/`text_en`, `headline_te`/`headline_en` (a
    text goes to each language it is written in) and the filter fields."""
    doc: dict[str, Any] = {k: chunk.get(k) for k in OS_KEYWORDS}
    for key in ("seq", "n_tokens", "pub_date", "event_date", "low_confidence", "headline"):
        doc[key] = chunk.get(key)
    doc["pub_date_int"] = date_int(chunk.get("pub_date"))
    doc["event_date_int"] = date_int(chunk.get("event_date") or chunk.get("pub_date"))
    for name in ("text", "headline"):
        value = chunk.get(name) or ""
        for lang in text_fields(value, share):
            doc[f"{name}_{lang}"] = value
    return doc
