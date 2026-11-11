"""Validation of one model reply for `enrich_core` (ticket 019 FR-3): pure functions, no model."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

CATEGORIES = ("sports", "politics", "health", "cinema", "family", "education", "other")
ORG_TYPES = ("party", "government_department", "company", "court", "other")

FAILED_FLAG = "low_confidence:enrich_failed"
REJECTED_FLAG = "event_date_rejected"
CAPPED_FLAG = "enrich_list_capped"  # informational: a list reached the cap, names may be missing

_SPACES = re.compile(r"\s+")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SchemaError(ValueError):
    """The reply is JSON but does not have the required shape."""


def normalise(text: str) -> str:
    """NFC, runs of whitespace collapsed to one space, trimmed."""
    return _SPACES.sub(" ", unicodedata.normalize("NFC", text)).strip()


@dataclass
class Parsed:
    """A validated reply."""

    category: str
    people: list[dict[str, Any]]
    organisations: list[dict[str, Any]]
    event_date: str | None
    event_date_basis: str
    event_date_expression: str | None
    flags: list[str] = field(default_factory=list)
    dropped_names: int = 0

    def record(self) -> dict[str, Any]:
        """The story's entry in the artifact (FR-4)."""
        return {
            "category": self.category,
            "people": self.people,
            "organisations": self.organisations,
            "event_date": self.event_date,
            "event_date_basis": self.event_date_basis,
            "event_date_expression": self.event_date_expression,
            "flags": list(self.flags),
        }


def fallback(print_date: date, flags: list[str] | None = None) -> Parsed:
    """Category other, no names, the print date: a story the model could not enrich."""
    return Parsed(
        category="other",
        people=[],
        organisations=[],
        event_date=print_date.isoformat(),
        event_date_basis="print_date_fallback",
        event_date_expression=None,
        flags=[FAILED_FLAG, *(flags or [])],
    )


def _clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = normalise(value)
    return name or None


def _entities(
    raw: Any, haystack: str, extra_key: str, extra_ok: tuple[str, ...] | None
) -> tuple[list[dict[str, Any]], int]:
    """The kept entities of `raw` (a list of dicts with `name` and `extra_key`) and the number
    dropped (name not verbatim in the input, no name, or a duplicate is not counted)."""
    if not isinstance(raw, list):
        raise SchemaError(f"expected a list, got {type(raw).__name__}")
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for item in raw:
        name = _clean_name(item.get("name")) if isinstance(item, dict) else None
        if name is None or name not in haystack:
            dropped += 1
            continue
        if name in seen:
            continue
        seen.add(name)
        value = item.get(extra_key)
        if extra_ok is None:  # a free-text role
            value = normalise(value) or None if isinstance(value, str) else None
        elif value not in extra_ok:
            value = "other"
        kept.append({"name": name, extra_key: value})
    return kept, dropped


def _subtract_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def resolve_event_date(
    raw_date: Any,
    expression: str | None,
    print_date: date,
    past_years: int,
    future_days: int,
) -> tuple[str, str, list[str]]:
    """(event_date, basis, flags). Never returns a null date (FR-3, D-5)."""
    flags: list[str] = []
    if raw_date not in (None, ""):
        parsed: date | None = None
        if isinstance(raw_date, str) and _ISO_DATE.match(raw_date.strip()):
            try:
                parsed = date.fromisoformat(raw_date.strip())
            except ValueError:
                parsed = None
        low = _subtract_years(print_date, past_years)
        high = print_date + timedelta(days=future_days)
        if parsed is not None and low <= parsed <= high:
            basis = "model_future" if parsed > print_date else "model"
            return parsed.isoformat(), basis, flags
        flags.append(REJECTED_FLAG)
        return print_date.isoformat(), "print_date_fallback", flags
    basis = "print_date_fallback" if expression else "print_date"
    return print_date.isoformat(), basis, flags


def parse_reply(
    data: Any,
    model_input: str,
    print_date: date,
    past_years: int = 3,
    future_days: int = 366,
    max_listed: int | None = None,
) -> Parsed:
    """Validate the decoded JSON reply for the story whose model input was `model_input`.

    Raises :class:`SchemaError` when the reply has the wrong shape (the caller asks again).
    """
    if not isinstance(data, dict):
        raise SchemaError(f"expected a JSON object, got {type(data).__name__}")
    category = data.get("category")
    category = category.strip().lower() if isinstance(category, str) else category
    if category not in CATEGORIES:
        raise SchemaError(f"category {data.get('category')!r} is not one of {list(CATEGORIES)}")
    haystack = normalise(model_input)
    people, dropped_people = _entities(data.get("people"), haystack, "role", None)
    orgs, dropped_orgs = _entities(data.get("organisations"), haystack, "type", ORG_TYPES)
    expression = data.get("event_date_expression")
    expression = normalise(expression) or None if isinstance(expression, str) else None
    event_date, basis, flags = resolve_event_date(
        data.get("event_date"), expression, print_date, past_years, future_days
    )
    if max_listed is not None and any(
        isinstance(data.get(k), list) and len(data[k]) >= max_listed
        for k in ("people", "organisations")
    ):
        flags.append(CAPPED_FLAG)
    return Parsed(
        category=category,
        people=people,
        organisations=orgs,
        event_date=event_date,
        event_date_basis=basis,
        event_date_expression=expression,
        flags=flags,
        dropped_names=dropped_people + dropped_orgs,
    )
