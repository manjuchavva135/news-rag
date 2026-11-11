"""Places of the stories of one date and their districts (ticket 020 FR-1 to FR-4).

`enrich_places` sends one request per story (the requests, retries and failure rules of
`newsrag.enrich.core`), keeps the place names that are verbatim in the story and resolves the
district / mandal / city ones with `newsrag.geo.resolve`, once for the districts of today and once
for the districts in force on the print date. No database and no GPU here: the caller passes the
model client.
"""

from __future__ import annotations

import sys
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Any

from newsrag import config, geo
from newsrag.enrich import core
from newsrag.enrich import validate as v
from newsrag.models.client import parse_json_reply

PROMPT_FILE = core.PROMPTS / "enrich_places_v1.md"

PLACE_TYPES = ("district", "mandal", "city", "village", "constituency", "state", "other")
RESOLVED_TYPES = ("district", "mandal", "city")  # the types that are looked up in the geo list
KIND_OF_TYPE = {"district": "district", "mandal": "mandal", "city": "town"}  # LLM type -> geo kind

AMBIGUOUS_FLAG = "place_ambiguous"  # informational, not `low_confidence:`
UNRESOLVED_FLAG = "place_unresolved"

PLACE_NAME_MAX_CHARS = 80  # a safety net against a looping name

# Telugu case suffixes stripped from a name that was not found as written (longest first).
CASE_SUFFIXES = tuple(
    sorted(
        ("లోని", "లో", "కు", "కి", "ను", "నుంచి", "నుండి", "పై", "తో", "వద్ద", "గా"),
        key=len,
        reverse=True,
    )
)
_JOINERS = "‌‍"  # ZWNJ, ZWJ

# Type "state": the two states of the newspaper (key = `geo.normalise` of the name).
STATE_IDS = {
    geo.normalise(name): state
    for state, names in {
        "andhra_pradesh": ("Andhra Pradesh", "ఆంధ్రప్రదేశ్"),
        "telangana": ("Telangana", "తెలంగాణ"),
    }.items()
    for name in names
}

# A retry after an unusable reply asks for a shorter list (the reply was probably cut off).
RETRY_PLACES = (8, 4)
RETRY_NOTE = (
    "\n\nYour previous reply was not valid JSON (it may have been too long). Answer again with "
    "at most <<N>> places."
)


def reply_schema(max_items: int) -> dict[str, Any]:
    """The JSON schema vLLM constrains the reply to: valid JSON, a type of the list, at most
    `max_items` places."""
    return {
        "type": "object",
        "properties": {
            "places": {
                "type": "array",
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "maxLength": PLACE_NAME_MAX_CHARS},
                        "type": {"type": "string", "enum": list(PLACE_TYPES)},
                    },
                    "required": ["name", "type"],
                },
            }
        },
        "required": ["places"],
    }


def build_prompt() -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    return text.replace("<<MAX_PLACES>>", str(config.settings.enrich_max_places))


# --- validation (FR-1) ---------------------------------------------------------------------------


def parse_places(
    data: Any, model_input: str, max_places: int | None = None
) -> tuple[list[tuple[str, str]], int, bool]:
    """(places, dropped, capped) of one decoded reply: `places` are (name, type) pairs, in reply
    order, de-duplicated by (normalised name, type); `dropped` counts the names that are not a
    substring of the input (NFC / whitespace normalised); `capped` says that the list reached the
    cap, so places may be missing. Raises `v.SchemaError` when the reply has the wrong shape."""
    if not isinstance(data, dict):
        raise v.SchemaError(f"expected a JSON object, got {type(data).__name__}")
    raw = data.get("places")
    if not isinstance(raw, list):
        raise v.SchemaError(f"places: expected a list, got {type(raw).__name__}")
    haystack = v.normalise(model_input)
    kept: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    dropped = 0
    for item in raw:
        raw_name = item.get("name") if isinstance(item, dict) else None
        name = v.normalise(raw_name) if isinstance(raw_name, str) else ""
        if not name or name not in haystack:
            dropped += 1
            continue
        kind = item.get("type")
        kind = kind.strip().lower() if isinstance(kind, str) else kind
        if kind not in PLACE_TYPES:
            kind = "other"
        if (name, kind) in seen:
            continue
        seen.add((name, kind))
        kept.append((name, kind))
    capped = max_places is not None and len(raw) >= max_places
    return kept, dropped, capped


# --- resolution (FR-2) ---------------------------------------------------------------------------


def strip_case_suffix(name: str) -> str | None:
    """`name` without one trailing Telugu case suffix and without the ZWJ/ZWNJ and spaces left at
    its end; None when no suffix is there (or nothing would be left)."""
    name = unicodedata.normalize("NFC", name).strip()
    for suffix in CASE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            stripped = name[: -len(suffix)].rstrip(_JOINERS + " \t\n")
            return stripped or None
    return None


def lookup(name: str, print_date: date) -> list[geo.Place]:
    """The geo matches of a name: as written first; only when that finds nothing, without its
    case suffix."""
    found = geo.resolve(name, print_date)
    if found:
        return found
    stripped = strip_case_suffix(name)
    if stripped and stripped != name:
        return geo.resolve(stripped, print_date)
    return []


def _districts(place: geo.Place) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return tuple(place.districts_current), tuple(place.districts_at_date)


def _candidate(place: geo.Place) -> dict[str, Any]:
    return {
        "kind": place.kind,
        "id": place.id,
        "name_en": place.name_en,
        "state": place.state,
        "districts_current": list(place.districts_current),
        "districts_at_print": list(place.districts_at_date),
    }


def place_record(name: str, llm_type: str, print_date: date) -> dict[str, Any]:
    """One place of the artifact (FR-2): `kind`/`id`/`state`/districts are filled when the name
    resolved, `candidates` and the flag `place_ambiguous` when it did not decide between
    several, the flag `place_unresolved` when nothing matched."""
    rec: dict[str, Any] = {
        "name": name,
        "llm_type": llm_type,
        "kind": None,
        "id": None,
        "state": None,
        "districts_current": [],
        "districts_at_print": [],
        "flags": [],
    }
    if llm_type == "state":
        rec["state"] = STATE_IDS.get(geo.normalise(name))
        if rec["state"] is None and (stripped := strip_case_suffix(name)):
            rec["state"] = STATE_IDS.get(geo.normalise(stripped))
        return rec
    if llm_type not in RESOLVED_TYPES:  # village, constituency, other: stored as they are
        return rec
    matches = lookup(name, print_date)
    if not matches:
        rec["flags"].append(UNRESOLVED_FLAG)
        return rec
    fitting = [m for m in matches if m.kind == KIND_OF_TYPE[llm_type]] or matches
    if len({_districts(m) for m in fitting}) > 1:
        rec["candidates"] = [_candidate(m) for m in fitting]
        rec["flags"].append(AMBIGUOUS_FLAG)
        return rec
    chosen = fitting[0]  # one match, or several that agree on both district lists
    rec.update(
        kind=chosen.kind,
        id=chosen.id,
        state=chosen.state,
        districts_current=list(chosen.districts_current),
        districts_at_print=list(chosen.districts_at_date),
    )
    return rec


def story_record(
    places: Sequence[Mapping[str, Any]], extra_flags: Sequence[str] = ()
) -> dict[str, Any]:
    """The story's entry in the artifact (FR-3, FR-4). Ambiguous places add no district; they add
    a state only when all their candidates share one."""
    current: set[str] = set()
    at_print: set[str] = set()
    states: set[str] = set()
    flags: list[str] = list(extra_flags)
    for p in places:
        for flag in p["flags"]:
            if flag not in flags:
                flags.append(flag)
        if AMBIGUOUS_FLAG in p["flags"]:
            candidate_states = {c["state"] for c in p.get("candidates") or []}
            if len(candidate_states) == 1:
                states |= candidate_states
            continue
        current.update(p["districts_current"])
        at_print.update(p["districts_at_print"])
        if p["state"]:
            states.add(p["state"])
    return {
        "places": [dict(p) for p in places],
        "districts_current": sorted(current),
        "districts_at_print": sorted(at_print),
        "states": sorted(states),
        "flags": flags,
    }


def failed_record() -> dict[str, Any]:
    """A story the model could not be asked: no places, the failure flag."""
    return story_record([], [v.FAILED_FLAG])


# --- the requests (FR-1) -------------------------------------------------------------------------


@dataclass
class PlacesOutcome:
    places: list[tuple[str, str]]
    dropped: int = 0
    capped: bool = False
    failed: bool = False
    empty: bool = False
    error: str | None = None  # why the story fell back, for the log


def enrich_story(client: Any, prompt: str, headline: str, text: str) -> PlacesOutcome:
    """Ask for the places of one story; validate; ask again (shorter) on a bad reply."""
    s = config.settings
    given = core.model_input(headline, text)
    if not given.strip():
        return PlacesOutcome([], empty=True)
    error = ""
    for attempt in range(s.enrich_format_retries + 1):
        system = prompt
        limit = s.enrich_max_places
        if attempt:  # the reply was unusable (often cut off at max_tokens): ask for less
            limit = RETRY_PLACES[min(attempt, len(RETRY_PLACES)) - 1]
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
            return PlacesOutcome([], failed=True, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - e.g. a 400: asking again would not help
            return PlacesOutcome([], failed=True, error=f"{type(exc).__name__}: {exc}")
        try:
            places, dropped, capped = parse_places(parse_json_reply(reply), given, limit)
            return PlacesOutcome(places, dropped, capped)
        except ValueError as exc:  # not JSON, or v.SchemaError
            error = str(exc)[:200]
    return PlacesOutcome([], failed=True, error=error)


def enrich_places(
    client: Any,
    stories: Sequence[Mapping[str, Any]],
    print_date: date,
    workers: int | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Places of every story of a date. Returns ({story_id: record}, summary).

    `stories` are the dedup story records (`story_id`, `headline`, `text`). The summary holds the
    counters of the stage detail; `request_seconds` is the wall time of the requests only.
    """
    s = config.settings
    prompt = build_prompt()
    workers = max(1, workers if workers is not None else s.enrich_concurrency)
    errors: list[str] = []
    lock = threading.Lock()

    def one(story: Mapping[str, Any]) -> PlacesOutcome:
        outcome = enrich_story(client, prompt, story.get("headline") or "", story.get("text") or "")
        if outcome.error:
            with lock:
                errors.append(f"{story.get('story_id')}: {outcome.error}")
        return outcome

    core.warm_up(client, prompt)
    # Longest first: the long stories are the slow requests, so they must not start last.
    order = sorted(range(len(stories)), key=lambda i: -len(stories[i].get("text") or ""))
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        done = list(pool.map(one, [stories[i] for i in order]))
    seconds = time.monotonic() - started
    outcomes: list[PlacesOutcome] = [None] * len(stories)  # type: ignore[list-item]
    for i, outcome in zip(order, done, strict=True):
        outcomes[i] = outcome

    geo.resolve("")  # build the geo index once, here, not inside the pool of a later caller
    records: dict[str, dict[str, Any]] = {}
    dropped = failed = 0
    types: Counter[str] = Counter()
    shares: Counter[str] = Counter()
    for story, outcome in zip(stories, outcomes, strict=True):
        if outcome.failed:
            record = failed_record()
        else:
            places = [place_record(n, t, print_date) for n, t in outcome.places]
            extra = ["empty_story"] if outcome.empty else []
            if outcome.capped:
                extra.append(v.CAPPED_FLAG)
            record = story_record(places, extra)
        records[story["story_id"]] = record
        dropped += outcome.dropped
        failed += outcome.failed
        for p in record["places"]:
            if p["llm_type"] in RESOLVED_TYPES:
                shares[outcome_of(p)] += 1
            types[p["llm_type"]] += 1
    for line in errors[:5]:
        print(f"enrich_places {print_date.isoformat()}: fallback for {line}", file=sys.stderr)
    summary = {
        "stories": len(stories),
        "request_seconds": round(seconds, 3),
        "stories_per_s": round(len(stories) / seconds, 2) if seconds > 0 else 0.0,
        "places": sum(types.values()),
        "place_types": dict(types),
        "resolved": shares["resolved"],
        "ambiguous": shares["ambiguous"],
        "unresolved": shares["unresolved"],
        "dropped_places": dropped,
        "enrich_failed": failed,
    }
    return records, summary


# --- statistics (FR-5) ---------------------------------------------------------------------------


def outcome_of(place: Mapping[str, Any]) -> str:
    """`resolved`, `ambiguous` or `unresolved` for a place of a resolved type."""
    if AMBIGUOUS_FLAG in place["flags"]:
        return "ambiguous"
    if UNRESOLVED_FLAG in place["flags"]:
        return "unresolved"
    return "resolved"


def place_totals(artifacts: Sequence[Mapping[str, Any]], top: int = 10) -> dict[str, Any]:
    """The FR-5 numbers over `enrich_places` artifacts: `places_per_story`, per type of
    district / mandal / city the percentages of resolved / ambiguous / unresolved places
    (ambiguous does not count as resolved), `combined_resolved` (over the three types, weighted by
    count) and the `top` most frequent unresolved names of those types as (name, count)."""
    records = [r for art in artifacts for r in (art.get("stories") or {}).values()]
    places = [p for r in records for p in r["places"]]
    counts = {t: Counter() for t in RESOLVED_TYPES}
    unresolved: Counter[str] = Counter()
    for p in places:
        if p["llm_type"] in RESOLVED_TYPES:
            result = outcome_of(p)
            counts[p["llm_type"]][result] += 1
            if result == "unresolved":
                unresolved[p["name"]] += 1

    def pct(part: int, whole: int) -> float:
        return round(100 * part / whole, 1) if whole else 0.0

    out: dict[str, Any] = {
        "places_per_story": round(len(places) / len(records), 2) if records else 0.0
    }
    for t in RESOLVED_TYPES:
        n = sum(counts[t].values())
        out[t] = {k: pct(counts[t][k], n) for k in ("resolved", "ambiguous", "unresolved")}
        out[t]["n"] = n
    total = sum(out[t]["n"] for t in RESOLVED_TYPES)
    out["combined_resolved"] = pct(sum(counts[t]["resolved"] for t in RESOLVED_TYPES), total)
    out["combined_n"] = total
    out["top_unresolved"] = unresolved.most_common(top)
    return out
