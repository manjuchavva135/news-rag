"""Amounts, counts and percentages of the stories of one date as structured facts (ticket 021).

`enrich_facts` sends one request per story (the requests, retries and failure rules of
`newsrag.enrich.core`). The model only finds the number as printed (`value_text`); the code checks
that it is verbatim in the story, parses the value and the scale from it, derives the normalised
value, the period and the districts of the place, and drops what it cannot trust. No database and
no GPU here: the caller passes the model client.
"""

from __future__ import annotations

import calendar
import json
import random
import re
import sys
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from newsrag import config, geo
from newsrag.enrich import core, places
from newsrag.enrich import validate as v
from newsrag.models.client import parse_json_reply

PROMPT_FILE = core.PROMPTS / "enrich_facts_v1.md"
JUDGE_PROMPT_FILE = core.PROMPTS / "facts_judge_v1.md"

UNITS = ("INR", "percent", "count", "acres", "tonnes", "km", "other")
SCALES = ("none", "thousand", "lakh", "crore", "million", "billion")
SCALE_FACTOR = {
    "none": Decimal(1),
    "thousand": Decimal(10) ** 3,
    "lakh": Decimal(10) ** 5,
    "crore": Decimal(10) ** 7,
    "million": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
}

SCALE_FROM_MODEL_FLAG = "scale_from_model"  # informational, not `low_confidence:`
PERIOD_UNPARSED_FLAG = "period_unparsed"

REFERS_TO_MAX_CHARS = 200  # safety nets against a looping reply
VALUE_TEXT_MAX_CHARS = 80
PERIOD_MAX_CHARS = 80
PLACE_MAX_CHARS = 80


# --- text helpers ----------------------------------------------------------------------------------

_TELUGU_DIGITS = {0x0C66 + i: ord("0") + i for i in range(10)}  # ౦-౯ -> 0-9
_TELUGU = "ఀ-౿‌‍"
_SPACES = re.compile(r"[ \t\r\f\v ]+")


def ascii_digits(text: str) -> str:
    """`text` with Telugu digits written as ASCII digits."""
    return text.translate(_TELUGU_DIGITS)


def norm(text: str) -> str:
    """The form texts are compared in (FR-2): NFC, Telugu digits as ASCII, whitespace collapsed."""
    return v.normalise(ascii_digits(unicodedata.normalize("NFC", text)))


def norm_lines(text: str) -> str:
    """Like `norm` but keeps the line breaks (a Markdown table is read row by row)."""
    text = ascii_digits(unicodedata.normalize("NFC", text))
    return "\n".join(_SPACES.sub(" ", line).strip() for line in text.splitlines())


# --- value, scale and unit from value_text (FR-2, FR-5) ----------------------------------------------

_NUMBER = re.compile(r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?")  # Indian and Western grouping
_TAIL = f"(?![{_TELUGU}])"

# (scale, pattern) of the scale words, Telugu (with the usual case endings) and English.
_SCALE_WORDS: list[tuple[str, re.Pattern[str]]] = [
    (
        "crore",
        re.compile(
            r"కోటి(?:కి|లో|తో)?"
            + _TAIL
            + r"|కోట్(?:లు|ల(?:కు|లో|తో|ను)?|ల్లో)?"
            + _TAIL
            + r"|(?<![A-Za-z])(?:crores?|cr)(?![A-Za-z])",
            re.IGNORECASE,
        ),
    ),
    (
        "lakh",
        re.compile(
            r"లక్ష(?:లు|ల(?:కు|లో|తో|ను)?|ల్లో)?"
            + _TAIL
            + r"|(?<![A-Za-z])(?:lakhs?|lacs?)(?![A-Za-z])",
            re.IGNORECASE,
        ),
    ),
    (
        "thousand",
        re.compile(
            r"వేల(?:ు|కు|లో|తో|ను)?"
            + _TAIL
            + r"|వెయ్యి(?:కి)?"
            + _TAIL
            + r"|(?<![A-Za-z])thousands?(?![A-Za-z])",
            re.IGNORECASE,
        ),
    ),
    ("million", re.compile(r"(?<![A-Za-z])(?:million|mn)(?![A-Za-z])", re.IGNORECASE)),
    ("billion", re.compile(r"(?<![A-Za-z])(?:billion|bn)(?![A-Za-z])", re.IGNORECASE)),
]
_PERCENT = re.compile(r"%|శాతం|per\s?cent|పర్సెంట్", re.IGNORECASE)
_INR = re.compile(r"₹|రూ|(?<![A-Za-z])rs(?![A-Za-z])|(?<![A-Za-z])inr(?![A-Za-z])", re.IGNORECASE)


@dataclass(frozen=True)
class ScaleWord:
    scale: str
    start: int
    end: int


def scale_words(text: str) -> list[ScaleWord]:
    """The scale words of `text`, in order of position."""
    found = [
        ScaleWord(scale, m.start(), m.end())
        for scale, pattern in _SCALE_WORDS
        for m in pattern.finditer(text)
    ]
    return sorted(found, key=lambda w: w.start)


@dataclass
class TextValue:
    """What `value_text` itself says."""

    number: Decimal  # the printed number, with a "lakh crore" style compound already multiplied
    scale: str | None  # a scale word printed in value_text
    unit: str | None  # "percent" or "INR" when a marker is printed in value_text


def parse_value_text(value_text: str) -> TextValue | None:
    """Value, scale word and unit marker of the printed number; None when it cannot be parsed
    (no number, or more than one number: a range, a date, a quantity such as `2 లక్షల 50 వేల`)."""
    text = norm(value_text)
    numbers = _NUMBER.findall(text)
    if len(numbers) != 1:
        return None
    try:
        number = Decimal(numbers[0].replace(",", ""))
    except ArithmeticError:
        return None
    unit = "percent" if _PERCENT.search(text) else "INR" if _INR.search(text) else None
    scale = None
    words = scale_words(text)
    if words:
        scale = words[-1].scale
        if (  # "లక్ష కోట్లు" is a lakh of crore, "వేల కోట్లు" a thousand of crore
            len(words) >= 2
            and scale == "crore"
            and words[-2].scale in ("lakh", "thousand")
            and not text[words[-2].end : words[-1].start].strip(" ‌‍")
        ):
            number *= SCALE_FACTOR[words[-2].scale]
    return TextValue(number, scale, unit)


def _after_digit(text: str, pos: int) -> bool:
    """Is the character before `pos`, skipping spaces, a digit? (Then a scale word at `pos` is the
    scale of that number, not a scale phrase.)"""
    i = pos - 1
    while i >= 0 and text[i] in " ‌‍":
        i -= 1
    return i >= 0 and text[i].isdigit()


_IN_BEFORE = re.compile(r"(?:\bin|\brs\.?|₹|\binr|\()\s*$", re.IGNORECASE)


def _is_scale_phrase(text: str, word: ScaleWord) -> bool:
    """Does the scale word name the scale of a table or of the figures that follow ("కోట్లలో",
    "in crore", "Rs crore", "(lakh)"), rather than being an ordinary word ("కోట్ల రూపాయల విలువైన")?"""
    spelled = text[word.start : word.end]
    if re.search("[\u0c00-\u0c7f]", spelled):
        return spelled.endswith("లో")  # కోట్లలో, లక్షల్లో, వేలలో
    return bool(_IN_BEFORE.search(text[: word.start]))


def _free_scale(text: str, strict: bool = True) -> str | None:
    """The scale phrase of `text` nearest to its end that is not the scale of a number in it.
    `strict` (running text) accepts only phrases that announce a scale, not scale words."""
    for word in reversed(scale_words(text)):
        if _after_digit(text, word.start):
            continue
        if not strict or _is_scale_phrase(text, word):
            return word.scale
    return None


def _table_scale(lines: list[str], row: int, col: int) -> str | None:
    """The scale a Markdown table names for row `row`, column `col`: in the column's header cell,
    else in the line above the table (its title), else in the header cell of the label column."""
    top = row
    while top > 0 and lines[top - 1].lstrip().startswith("|"):
        top -= 1
    header = lines[top].split("|") if top != row else []  # a body row has a header row above it
    if col < len(header) and (found := _free_scale(header[col], strict=False)):
        return found
    above = top - 1
    while above >= 0 and not lines[above].strip():
        above -= 1
    if (
        above >= 0
        and len(lines[above]) <= 160
        and (found := _free_scale(lines[above], strict=False))
    ):
        return found
    if len(header) > 1:  # `| శాఖ (రూ. కోట్లలో) | 2023 | 2024 |`: the label column's header
        return _free_scale(header[1], strict=False)
    return None


def context_scale(given: str, value_text: str, window: int) -> str | None:
    """FR-5: the scale named around the number: in the title or header of the table it is in, or
    within `window` characters before it. Every place the number is printed is tried in turn."""
    hay = norm_lines(given)
    needle = norm(value_text)
    if not needle:
        return None
    lines = hay.split("\n")
    start = 0
    while (idx := hay.find(needle, start)) != -1:
        start = idx + 1
        row = hay.count("\n", 0, idx)
        line = lines[row]
        offset = idx - (hay.rfind("\n", 0, idx) + 1)
        if line.lstrip().startswith("|"):
            found = _table_scale(lines, row, line[:offset].count("|"))
        else:
            found = _free_scale(hay[max(0, idx - window) : idx].replace("\n", " "))
        if found:
            return found
    return None


def number_out(value: Decimal) -> int | float:
    """A JSON number: an int when the value is whole."""
    if value == value.to_integral_value() and abs(value) < Decimal(10) ** 15:
        return int(value)
    return float(value)


# --- periods (FR-2, FR-5) ------------------------------------------------------------------------------

_MONTHS_TE = {
    1: ("జనవరి",),
    2: ("ఫిబ్రవరి", "ఫిబ్రవరీ"),
    3: ("మార్చి", "మార్చ్"),
    4: ("ఏప్రిల్",),
    5: ("మే",),
    6: ("జూన్",),
    7: ("జూలై", "జులై"),
    8: ("ఆగస్టు", "ఆగస్ట్", "ఆగష్టు"),
    9: ("సెప్టెంబర్", "సెప్టెంబరు"),
    10: ("అక్టోబర్", "అక్టోబరు"),
    11: ("నవంబర్", "నవంబరు"),
    12: ("డిసెంబర్", "డిసెంబరు"),
}
_MONTH_TE_ENDINGS = "(?:లోని|లో|నెలలో|నెలకు|నెల|కు|నుంచి|నుండి|వరకు|లోపు|నాటికి)"
_MONTH_TE = re.compile(
    f"(?<![{_TELUGU}])(?P<name>"
    + "|".join(re.escape(n) for names in _MONTHS_TE.values() for n in names)
    + f")[‌‍]?{_MONTH_TE_ENDINGS}?(?![{_TELUGU}])"
)
_TE_MONTH_OF = {n: m for m, names in _MONTHS_TE.items() for n in names}
_MONTHS_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MONTH_EN = re.compile(
    r"(?<![A-Za-z])(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![A-Za-z])",
    re.IGNORECASE,
)
_DATE_ISO = re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})(?!\d)")
_DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})[-./](\d{1,2})[-./]((?:19|20)\d{2})(?!\d)")
_FIN_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*[-–—/]\s*(\d{4}|\d{2})(?!\d)")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def months_in(text: str) -> set[int]:
    """The month numbers named in `text` (Telugu and English month names)."""
    found = {_TE_MONTH_OF[m.group("name")] for m in _MONTH_TE.finditer(text)}
    found |= {_MONTHS_EN[m.group(1)[:3].lower()] for m in _MONTH_EN.finditer(text)}
    return found


def derive_period(period_text: str, event_date: date) -> tuple[date, date] | None:
    """`(period_start, period_end)` of a period phrase, or None when it is relative or cannot be
    read: a full date (that day), a financial year `2023-24` / `2023-2024` (1 April to 31 March), a month with its year
    (a month alone takes the latest such month on or before the event date's month), a year."""
    text = norm(period_text)
    if day := (_DATE_ISO.search(text) or _DATE_DMY.search(text)):
        try:
            if day.re is _DATE_ISO:
                one = date(int(day.group(1)), int(day.group(2)), int(day.group(3)))
            else:
                one = date(int(day.group(3)), int(day.group(2)), int(day.group(1)))
        except ValueError:
            return None
        return one, one
    if fy := _FIN_YEAR.search(text):
        first, last = int(fy.group(1)), fy.group(2)
        end_year = int(last) if len(last) == 4 else first // 100 * 100 + int(last)
        if len(last) == 2 and end_year < first:
            end_year += 100
        if end_year != first + 1:
            return None
        return date(first, 4, 1), date(end_year, 3, 31)
    years = set(_YEAR.findall(text))
    months = months_in(text)
    if len(months) > 1 or len(years) > 1:
        return None
    if months:
        month = months.pop()
        if years:
            year = int(years.pop())
        else:  # no year: the latest such month that is not after the event date's month
            year = event_date.year if month <= event_date.month else event_date.year - 1
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    if years:
        year = int(years.pop())
        return date(year, 1, 1), date(year, 12, 31)
    return None


# --- places (FR-2, FR-5) -----------------------------------------------------------------------------------


def place_fields(name: str | None, print_date: date) -> dict[str, Any]:
    """`districts_current`, `districts_at_print` and `state` of a fact's place, resolved like the
    places of ticket 020 (suffix stripping, the districts of the print date). Empty lists when the
    name is unresolved or ambiguous; an ambiguous name still gives a state all candidates share; a
    state name gives that state only."""
    out: dict[str, Any] = {"districts_current": [], "districts_at_print": [], "state": None}
    if not name:
        return out
    state = places.STATE_IDS.get(geo.normalise(name))
    if state is None and (stripped := places.strip_case_suffix(name)):
        state = places.STATE_IDS.get(geo.normalise(stripped))
    if state:
        out["state"] = state
        return out
    matches = places.lookup(name, print_date)
    if not matches:
        return out
    # the model gives no place type: a name written with "జిల్లా" / "మండలం" is a district / mandal,
    # a bare name is taken as the town when the geo list has one (like ticket 020's kind filter)
    low = name.lower()
    kind = (
        "district"
        if "జిల్లా" in name or "district" in low
        else "mandal"
        if "మండల" in name or "mandal" in low
        else "town"
    )
    matches = [m for m in matches if m.kind == kind] or matches
    if len({places._districts(m) for m in matches}) > 1:  # ambiguous: no district
        states = {m.state for m in matches}
        out["state"] = states.pop() if len(states) == 1 else None
        return out
    chosen = matches[0]
    out.update(
        districts_current=list(chosen.districts_current),
        districts_at_print=list(chosen.districts_at_date),
        state=chosen.state,
    )
    return out


# --- validation of one reply (FR-1, FR-2) ---------------------------------------------------------------------


@dataclass
class Parsed:
    """The validated facts of one reply (without their ids) and what was dropped."""

    facts: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    dropped_non_verbatim: int = 0
    dropped_unparsed: int = 0
    dropped_invalid: int = 0  # not an object, no refers_to
    dropped_period: int = 0  # a period_text that is not verbatim (the fact is kept without it)
    dropped_place: int = 0  # a place that is not verbatim (the fact is kept without it)
    capped: bool = False


def _text_or_none(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", v.normalise(value))
    return text[:limit] or None


def parse_reply(
    data: Any,
    model_input: str,
    print_date: date,
    event_date: date,
    max_facts: int | None = None,
) -> Parsed:
    """Validate the decoded JSON reply for the story whose model input was `model_input`.

    Raises :class:`newsrag.enrich.validate.SchemaError` when the reply has the wrong shape (the
    caller asks again). Facts come back in order of appearance in the story.
    """
    if not isinstance(data, dict):
        raise v.SchemaError(f"expected a JSON object, got {type(data).__name__}")
    raw = data.get("facts")
    if not isinstance(raw, list):
        raise v.SchemaError(f"facts: expected a list, got {type(raw).__name__}")
    s = config.settings
    haystack = norm(model_input)
    out = Parsed(capped=max_facts is not None and len(raw) >= max_facts)
    flags: list[str] = []
    seen: set[tuple[str, str, str | None]] = set()
    kept: list[tuple[int, dict[str, Any]]] = []
    for item in raw:
        if not isinstance(item, dict):
            out.dropped_invalid += 1
            continue
        vt = _text_or_none(item.get("value_text"), VALUE_TEXT_MAX_CHARS)
        if vt is None:
            out.dropped_unparsed += 1
            continue
        position = haystack.find(norm(vt))
        if position < 0:  # D-3: a number that is not in the story is invented
            out.dropped_non_verbatim += 1
            continue
        text_value = parse_value_text(vt)
        if text_value is None or months_in(vt):  # D-4: the code reads the number, the model only
            # found the span; a span with a month name ("మే 15 నుంచి") is a date, not an amount
            out.dropped_unparsed += 1
            continue
        refers_to = _text_or_none(item.get("refers_to"), REFERS_TO_MAX_CHARS)
        if refers_to is None:
            out.dropped_invalid += 1
            continue

        unit = text_value.unit
        if unit is None:
            unit = item.get("unit") if item.get("unit") in UNITS else "other"
        scale = text_value.scale
        if unit == "percent":
            scale = "none"
        elif scale is None:
            scale = context_scale(model_input, vt, s.enrich_facts_scale_window)
        if scale is None:  # nothing in the story says it: the model's reading, flagged
            scale = item.get("scale") if item.get("scale") in SCALES else "none"
            if scale != "none" and SCALE_FROM_MODEL_FLAG not in flags:
                flags.append(SCALE_FROM_MODEL_FLAG)
        value = text_value.number

        period_text = _text_or_none(item.get("period_text"), PERIOD_MAX_CHARS)
        if period_text is not None and norm(period_text) not in haystack:
            period_text = None
            out.dropped_period += 1
        period = derive_period(period_text, event_date) if period_text else None
        if period_text is not None and period is None and PERIOD_UNPARSED_FLAG not in flags:
            flags.append(PERIOD_UNPARSED_FLAG)
        start, end = period if period else (event_date, event_date)

        place = _text_or_none(item.get("place"), PLACE_MAX_CHARS)
        if place is not None and norm(place) not in haystack:
            place = None
            out.dropped_place += 1

        key = (norm(vt), norm(refers_to), period_text)
        if key in seen:
            continue
        seen.add(key)
        kept.append(
            (
                position,
                {
                    "value_text": vt,
                    "value": number_out(value),
                    "scale": scale,
                    "normalized_value": number_out(value * SCALE_FACTOR[scale]),
                    "unit": unit,
                    "refers_to": refers_to,
                    "period_text": period_text,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "place": place,
                    **place_fields(place, print_date),
                },
            )
        )
    kept.sort(key=lambda pair: pair[0])  # order of appearance; the sort is stable
    out.facts = [fact for _, fact in kept]
    out.flags = flags
    return out


def story_record(
    story_id: str, parsed: Parsed | None, extra_flags: Sequence[str] = ()
) -> dict[str, Any]:
    """The story's entry in the artifact (FR-3): the facts with their ids, and the flags."""
    flags = list(extra_flags)
    facts: list[dict[str, Any]] = []
    if parsed is not None:
        flags.extend(f for f in parsed.flags if f not in flags)
        if parsed.capped and v.CAPPED_FLAG not in flags:
            flags.append(v.CAPPED_FLAG)
        for number, fact in enumerate(parsed.facts, start=1):
            order = [
                "value_text", "value", "scale", "normalized_value", "unit", "refers_to",
                "period_text", "period_start", "period_end", "place", "districts_current",
                "districts_at_print", "state",
            ]  # fmt: skip
            facts.append({"fact_id": f"{story_id}#f{number:03d}", **{k: fact[k] for k in order}})
    return {"facts": facts, "flags": flags}


# --- the requests (FR-1) ---------------------------------------------------------------------------------------

# A retry after an unusable reply asks for a shorter list (the reply was probably cut off).
RETRY_FACTS = (8, 4)
RETRY_NOTE = (
    "\n\nYour previous reply was not valid JSON (it may have been too long). Answer again with "
    'at most <<N>> facts, each "refers_to" at most six words.'
)


def reply_schema(max_items: int) -> dict[str, Any]:
    """The JSON schema vLLM constrains the reply to: valid JSON, units and scales of the lists, at
    most `max_items` facts, bounded strings."""
    return {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "properties": {
                        "value_text": {"type": "string", "maxLength": VALUE_TEXT_MAX_CHARS},
                        "value": {"type": "number"},
                        "unit": {"type": "string", "enum": list(UNITS)},
                        "scale": {"type": "string", "enum": list(SCALES)},
                        "refers_to": {"type": "string", "maxLength": REFERS_TO_MAX_CHARS},
                        "period_text": {"type": ["string", "null"], "maxLength": PERIOD_MAX_CHARS},
                        "place": {"type": ["string", "null"], "maxLength": PLACE_MAX_CHARS},
                    },
                    "required": [
                        "value_text",
                        "value",
                        "unit",
                        "scale",
                        "refers_to",
                        "period_text",
                        "place",
                    ],
                },
            }
        },
        "required": ["facts"],
    }


def build_prompt() -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    return text.replace("<<MAX_FACTS>>", str(config.settings.enrich_max_facts))


@dataclass
class FactsOutcome:
    parsed: Parsed | None  # None when the story could not be asked or the reply stayed unusable
    failed: bool = False
    empty: bool = False
    error: str | None = None  # why the story fell back, for the log


def enrich_story(
    client: Any, prompt: str, headline: str, text: str, print_date: date, event_date: date
) -> FactsOutcome:
    """Ask for the facts of one story; validate; ask again (shorter) on a bad reply."""
    s = config.settings
    given = core.model_input(headline, text)
    if not given.strip():
        return FactsOutcome(Parsed(), empty=True)
    error = ""
    for attempt in range(s.enrich_format_retries + 1):
        system = prompt
        limit = s.enrich_max_facts
        if attempt:  # the reply was unusable (often cut off at max_tokens): ask for less
            limit = RETRY_FACTS[min(attempt, len(RETRY_FACTS)) - 1]
            system = prompt + RETRY_NOTE.replace("<<N>>", str(limit))
        extra: dict[str, Any] = {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": reply_schema(limit)},
            }
        }
        messages = [{"role": "system", "content": system}, {"role": "user", "content": given}]
        try:
            reply = core.ask(client, messages, **extra)
        except core.TransportFailed as exc:
            return FactsOutcome(None, failed=True, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - e.g. a 400: asking again would not help
            return FactsOutcome(None, failed=True, error=f"{type(exc).__name__}: {exc}")
        try:
            parsed = parse_reply(parse_json_reply(reply), given, print_date, event_date, limit)
            return FactsOutcome(parsed)
        except ValueError as exc:  # not JSON, or v.SchemaError
            error = str(exc)[:200]
    return FactsOutcome(None, failed=True, error=error)


def _event_date(raw: Any, print_date: date) -> date:
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return print_date


def enrich_facts(
    client: Any,
    stories: Sequence[Mapping[str, Any]],
    print_date: date,
    event_dates: Mapping[str, str] | None = None,
    workers: int | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Facts of every story of a date. Returns ({story_id: record}, summary).

    `stories` are the dedup story records (`story_id`, `headline`, `text`); `event_dates` maps a
    story id to its `enrich_core` event date (the print date when missing). The summary holds the
    counters of the stage detail; `request_seconds` is the wall time of the requests only.
    """
    s = config.settings
    prompt = build_prompt()
    workers = max(1, workers if workers is not None else s.enrich_concurrency)
    event_dates = event_dates or {}
    errors: list[str] = []
    lock = threading.Lock()

    def one(story: Mapping[str, Any]) -> FactsOutcome:
        event = _event_date(event_dates.get(story["story_id"]), print_date)
        outcome = enrich_story(
            client, prompt, story.get("headline") or "", story.get("text") or "", print_date, event
        )
        if outcome.error:
            with lock:
                errors.append(f"{story.get('story_id')}: {outcome.error}")
        return outcome

    geo.resolve("")  # build the geo index once, before the pool resolves places in threads
    core.warm_up(client, prompt)
    # Longest first: the long stories are the slow requests, so they must not start last.
    order = sorted(range(len(stories)), key=lambda i: -len(stories[i].get("text") or ""))
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        done = list(pool.map(one, [stories[i] for i in order]))
    seconds = time.monotonic() - started
    outcomes: list[FactsOutcome] = [None] * len(stories)  # type: ignore[list-item]
    for i, outcome in zip(order, done, strict=True):
        outcomes[i] = outcome

    records: dict[str, dict[str, Any]] = {}
    dropped: Counter[str] = Counter()
    units: Counter[str] = Counter()
    failed = n_facts = 0
    for story, outcome in zip(stories, outcomes, strict=True):
        extra: list[str] = []
        if outcome.failed:
            extra.append(v.FAILED_FLAG)
            failed += 1
        elif outcome.empty:
            extra.append("empty_story")
        parsed = outcome.parsed
        record = story_record(story["story_id"], parsed, extra)
        records[story["story_id"]] = record
        if parsed is not None:
            dropped["dropped_non_verbatim"] += parsed.dropped_non_verbatim
            dropped["dropped_unparsed"] += parsed.dropped_unparsed
            dropped["dropped_invalid"] += parsed.dropped_invalid
            dropped["dropped_period"] += parsed.dropped_period
            dropped["dropped_place"] += parsed.dropped_place
        for fact in record["facts"]:
            units[fact["unit"]] += 1
            n_facts += 1
    for line in errors[:5]:
        print(f"enrich_facts {print_date.isoformat()}: fallback for {line}", file=sys.stderr)
    summary = {
        "stories": len(stories),
        "request_seconds": round(seconds, 3),
        "stories_per_s": round(len(stories) / seconds, 2) if seconds > 0 else 0.0,
        "facts": n_facts,
        "units": {u: units[u] for u in UNITS},
        "dropped_non_verbatim": dropped["dropped_non_verbatim"],
        "dropped_unparsed": dropped["dropped_unparsed"],
        "dropped_invalid": dropped["dropped_invalid"],
        "dropped_period": dropped["dropped_period"],
        "dropped_place": dropped["dropped_place"],
        "enrich_failed": failed,
    }
    return records, summary


# --- statistics (FR-4) ---------------------------------------------------------------------------------------------


def fact_totals(
    artifacts: Sequence[Mapping[str, Any]], details: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The FR-4 numbers over `enrich_facts` artifacts and the details of their runs:
    `facts_per_story` (mean over all stories, zero-fact stories included), `unit_<unit>` counts
    and the two dropped counters of the runs."""
    records = [r for art in artifacts for r in (art.get("stories") or {}).values()]
    facts = [f for r in records for f in r["facts"]]
    out: dict[str, Any] = {
        "facts_per_story": round(len(facts) / len(records), 2) if records else 0.0
    }
    for unit in UNITS:
        out[f"unit_{unit}"] = sum(1 for f in facts if f["unit"] == unit)
    out["dropped_non_verbatim"] = sum(int(d.get("dropped_non_verbatim") or 0) for d in details)
    out["dropped_unparsed"] = sum(int(d.get("dropped_unparsed") or 0) for d in details)
    return out


# --- quality check (NFR-1) ----------------------------------------------------------------------------------------

Ask = Callable[[str], str]


def collect_facts(pub_date: str | None = None) -> list[dict[str, Any]]:
    """Every fact of the done `enrich_facts` artifacts (of one date, or all), with its story from
    the date's `dedup` artifact, sorted by (date, fact id)."""
    from newsrag import enrich
    from newsrag.dedup import load

    out: list[dict[str, Any]] = []
    for day, path in enrich.done_artifacts("enrich_facts", pub_date).items():
        records = enrich.read_artifact(path).get("stories") or {}
        dedup_path = load.done_dedup(date.fromisoformat(day)).get(day)
        if dedup_path is None:
            continue
        texts = {s["story_id"]: s for s in load.load_dedup(dedup_path)["stories"]}
        for story_id, record in sorted(records.items()):
            story = texts.get(story_id)
            if story is None:  # the dedup run changed after the enrichment
                continue
            for fact in record["facts"]:
                out.append(
                    {
                        "pub_date": day,
                        "story_id": story_id,
                        "headline": story.get("headline") or "",
                        "text": story.get("text") or "",
                        "fact": fact,
                    }
                )
    return out


def sample(items: Sequence[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """`n` facts drawn with `seed` (all of them when there are fewer)."""
    if len(items) <= n:
        return list(items)
    return random.Random(seed).sample(list(items), n)


def judge_prompt(item: dict[str, Any]) -> str:
    """The judge's input (FR-5): the story (6,000 characters) and the fact's value_text, value,
    scale, unit, refers_to, period and place."""
    fact = item["fact"]
    if fact["period_text"]:
        period = f"{fact['period_text']} ({fact['period_start']} to {fact['period_end']})"
    else:
        period = "(none)"
    text = JUDGE_PROMPT_FILE.read_text(encoding="utf-8")
    return (
        text.replace("<<STORY>>", core.model_input(item["headline"], item["text"]))
        .replace("<<VALUE_TEXT>>", str(fact["value_text"]))
        .replace("<<VALUE>>", json.dumps(fact["value"]))
        .replace("<<SCALE>>", str(fact["scale"]))
        .replace("<<UNIT>>", str(fact["unit"]))
        .replace("<<REFERS_TO>>", str(fact["refers_to"]))
        .replace("<<PERIOD>>", period)
        .replace("<<PLACE>>", str(fact["place"]) if fact["place"] else "(none)")
    )


def parse_verdict(reply: str) -> bool:
    """The judge's `correct`; an unparseable reply is not correct."""
    try:
        data = parse_json_reply(reply)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("correct") is True


def run_verify(
    items: Sequence[dict[str, Any]], n: int, seed: int, ask: Ask | None = None
) -> dict[str, Any]:
    """The report. `ask(prompt) -> reply` defaults to `enrich_verify_model` via `models.use`."""
    s = config.settings
    chosen = sample(items, n, seed)
    if chosen and ask is None:
        from newsrag import models

        with models.use(s.enrich_verify_model) as client:

            def ask_model(prompt: str) -> str:
                return client.chat(prompt, temperature=0, max_tokens=s.enrich_verify_max_tokens)

            return run_verify(items, n, seed, ask_model)

    def one(item: dict[str, Any]) -> dict[str, Any]:
        assert ask is not None
        reply = ask(judge_prompt(item))
        return {
            "pub_date": item["pub_date"],
            "story_id": item["story_id"],
            "fact": item["fact"],
            "correct": parse_verdict(reply),
            "reply": reply.strip()[:120],
        }

    with ThreadPoolExecutor(max_workers=max(1, s.enrich_verify_workers)) as pool:
        results = list(pool.map(one, chosen))
    return {
        "model": s.enrich_verify_model,
        "n_requested": n,
        "seed": seed,
        "n_facts_available": len(items),
        "n": len(results),
        "correct": sum(1 for x in results if x["correct"]),
        "items": results,
    }
