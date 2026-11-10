"""Parse the folio/header line printed on a page: place, state, date and page number (011 FR-2).

Pure functions over strings: `parse_folio(folio_text, pub_date)` and the block selection
`folio_blocks(blocks, height, folio_labels, top_fraction)`. The `folio` stage
(`newsrag.stages.folio`) does the I/O.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import Any

from newsrag import geo

STATE_NAMES = {
    "andhra_pradesh": ("ఆంధ్రప్రదేశ్", "ఆంధ్ర ప్రదేశ్", "andhra pradesh", "andhrapradesh"),
    "telangana": ("తెలంగాణ", "telangana"),
}
TELUGU_MONTHS = {
    "జనవరి": 1,
    "ఫిబ్రవరి": 2,
    "మార్చి": 3,
    "ఏప్రిల్": 4,
    "ఎప్రిల్": 4,
    "మే": 5,
    "జూన్": 6,
    "జూలై": 7,
    "జులై": 7,
    "ఆగస్టు": 8,
    "ఆగష్టు": 8,
    "సెప్టెంబర్": 9,
    "సెప్టెంబరు": 9,
    "అక్టోబర్": 10,
    "అక్టోబరు": 10,
    "నవంబర్": 11,
    "నవంబరు": 11,
    "డిసెంబర్": 12,
    "డిసెంబరు": 12,
}
TELUGU_DIGITS = str.maketrans("౦౧౨౩౪౫౬౭౮౯", "0123456789")
# Tokens are split on whitespace and `। | , -` (FR-2); "/" and "॥" are folio separators too.
_TOKEN_SPLIT = re.compile(r"[\s।|,\-/॥]+")
_NUMERIC_DATE = re.compile(
    r"(?<!\d)(\d{1,2})\s*([-./।|])\s*(\d{1,2})\s*\2\s*((?:19|20)\d{2})(?!\d)"
)
_LATEX_COMMAND = re.compile(r"\\[a-zA-Z]+")
_WEEKDAYS = ("ఆదివారం", "సోమవారం", "మంగళవారం", "బుధవారం", "గురువారం", "శుక్రవారం", "శనివారం")


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").translate(TELUGU_DIGITS)
    return text


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(_clean(text)) if t]


# --- block selection ----------------------------------------------------------------------------


def is_folio_block(block: dict[str, Any], height: float, labels: Iterable[str], top: float) -> bool:
    """A block whose label is a folio label, or whose box lies in the top `top` of the page."""
    if block.get("label") in set(labels):
        return True
    bbox = block.get("bbox") or [0, 0, 0, height + 1]
    return height > 0 and float(bbox[3]) <= top * height


# --- date and page number -----------------------------------------------------------------------


def find_date(text: str) -> tuple[dt.date | None, tuple[int, int] | None]:
    """The first date printed in `text` (digits or a Telugu month name) and its span."""
    text = _clean(text)
    best: tuple[int, dt.date, tuple[int, int]] | None = None
    for m in _NUMERIC_DATE.finditer(text):
        try:
            d = dt.date(int(m.group(4)), int(m.group(3)), int(m.group(1)))
        except ValueError:
            continue
        best = (m.start(), d, m.span())
        break
    months = "|".join(sorted(map(re.escape, TELUGU_MONTHS), key=len, reverse=True))
    named = re.compile(
        rf"(?<!\d)(\d{{1,2}})[\s।|,.\-/]*({months})[\s।|,.\-/]*((?:19|20)\d{{2}})(?!\d)"
    )
    # day-month-year, and month-day-year as some folios print it ("మే । 4 । 2025")
    month_first = re.compile(
        rf"({months})[\s।|,.\-/]*(\d{{1,2}})[\s।|,.\-/]*((?:19|20)\d{{2}})(?!\d)"
    )
    for pattern, day_group, month_group in ((named, 1, 2), (month_first, 2, 1)):
        m = pattern.search(text)
        if not m:
            continue
        try:
            d = dt.date(
                int(m.group(3)), TELUGU_MONTHS[m.group(month_group)], int(m.group(day_group))
            )
        except ValueError:
            continue
        if best is None or m.start() < best[0]:
            best = (m.start(), d, m.span())
    return (best[1], best[2]) if best else (None, None)


def find_page_no(text: str, date_span: tuple[int, int] | None) -> int | None:
    """A 1-3 digit number standing alone in the folio, outside the date."""
    text = _clean(text)
    if date_span:
        text = text[: date_span[0]] + " " + text[date_span[1] :]
    text = re.sub(r"సంపుటి\s*:?\s*\d+|సంచిక\s*:?\s*\d+|పేజీలు\s*:?\s*\d+", " ", text)
    text = re.sub(r"www\.\S+|\S+\.com", " ", text)
    for m in re.finditer(r"(?<![\w.,:])(\d{1,3})(?![\d,:/-]|\.\d|[^\W\d])", text):
        n = int(m.group(1))
        if 1 <= n <= 200:
            return n
    return None


def page_no_from_segments(segments: list[str]) -> int | None:
    """The page number: in the dated folio line, else a block that is only a number."""
    for seg in segments:
        printed, span = find_date(seg)
        if printed is not None:
            n = find_page_no(seg, span)
            if n is not None:
                return n
    for seg in segments:
        if re.fullmatch(r"\s*\d{1,3}\s*", _clean(seg)):
            n = int(_clean(seg))
            if 1 <= n <= 200:
                return n
    return None


# --- place --------------------------------------------------------------------------------------


Resolver = Callable[[str, dt.date | None], list[geo.Place]]


def _place_fields(p: geo.Place, run: str) -> dict[str, Any]:
    return {
        "place_te": run,
        "place_kind": p.kind,
        "place_districts_current": list(p.districts_current),
        "place_districts_at_print": list(p.districts_at_date),
        "place_state": p.state,
    }


def choose_place(matches: list[geo.Place]) -> geo.Place | None:
    """FR-2: one state + one district list → it; else town, then district, then mandal."""
    if not matches:
        return None
    keys = {(m.state, tuple(m.districts_current)) for m in matches}
    if len(keys) == 1:
        for kind in ("town", "district", "mandal"):
            for m in matches:
                if m.kind == kind:
                    return m
        return matches[0]
    for kind in ("town", "district", "mandal"):
        of_kind = [m for m in matches if m.kind == kind]
        if len(of_kind) == 1:
            return of_kind[0]
        if len(of_kind) > 1:
            return None
    return None


def find_place(
    toks: list[str], pub_date: dt.date | None, resolver: Resolver
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    """First run of 1-3 tokens (left to right, longest first) the resolver knows.

    Returns (place fields, None), or (None, candidates) when that run stays ambiguous, or
    (None, None) when no run resolves.
    """
    for i in range(len(toks)):
        for n in (3, 2, 1):
            if i + n > len(toks):
                continue
            run = " ".join(toks[i : i + n])
            if re.fullmatch(r"[\d\s]+", run):
                continue
            matches = resolver(run, pub_date)
            if not matches:
                continue
            chosen = choose_place(matches)
            if chosen is not None:
                return _place_fields(chosen, run), None
            return None, [
                {
                    "place_te": run,
                    "kind": m.kind,
                    "id": m.id,
                    "state": m.state,
                    "districts_current": list(m.districts_current),
                }
                for m in matches
            ]
    return None, None


def find_state(text: str) -> str | None:
    low = _clean(text).lower()
    for state, names in STATE_NAMES.items():
        if any(n.lower() in low for n in names):
            return state
    return None


EMPTY_PLACE = {
    "place_te": None,
    "place_kind": None,
    "place_districts_current": None,
    "place_districts_at_print": None,
    "place_state": None,
}


def strip_noise(text: str) -> str:
    """Drop a block read as LaTeX markup (decorative footers, logos): it never holds a place or
    a date. A bare math-wrapped number (`$$3$$`, a page number) keeps its digits."""
    text = text or ""
    if _LATEX_COMMAND.search(text):
        return ""
    return text.replace("$", " ")


def parse_segments(
    segments: list[str], pub_date: dt.date | None, resolver: Resolver | None = None
) -> dict[str, Any]:
    """Parse the folio from its blocks' texts (reading order).

    The place is looked for first in the segments that carry the printed date (the folio line
    itself), then in all of them in order: a headline that happens to sit in the top band must
    not win over the place printed next to the date.
    """
    resolver = resolver or geo.resolve
    segments = [strip_noise(s) for s in segments]
    text = " | ".join(s for s in segments if s.strip())
    printed_date, _span = find_date(text)
    page_no = page_no_from_segments(segments)
    dated = [s for s in segments if find_date(s)[0] is not None]
    out: dict[str, Any] = dict(EMPTY_PLACE)
    candidates = None
    for group in ([" | ".join(dated)] if dated else []) + [text]:
        place, cands = find_place(tokens(group), pub_date, resolver)
        if place:
            out.update(place)
            candidates = None
            break
        if cands and candidates is None:
            candidates = cands
            break
    if out["place_te"] is None:
        state = find_state(text)
        if state:
            out["place_state"] = state
    if candidates:
        out["place_candidates"] = candidates
    out["printed_date"] = printed_date.isoformat() if printed_date else None
    out["printed_page_no"] = page_no
    return out


def parse_folio(
    folio_text: str, pub_date: dt.date | None, resolver: Resolver | None = None
) -> dict[str, Any]:
    """Parse one folio string as a single block."""
    return parse_segments([folio_text], pub_date, resolver)
