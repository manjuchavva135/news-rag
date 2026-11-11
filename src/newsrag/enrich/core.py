"""Enrichment of the stories of one date by a local text model (ticket 019 FR-1, FR-2, FR-3).

`enrich_stories` sends one request per story from a thread pool and validates every reply with
`newsrag.enrich.validate`. No database and no GPU here: the caller passes the model client.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.enrich import validate as v
from newsrag.models.client import parse_json_reply

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
PROMPT_FILE = PROMPTS / "enrich_core_v1.md"
JUDGE_PROMPT_FILE = PROMPTS / "enrich_core_judge_v1.md"

# Telugu weekday names, Monday = 0.
WEEKDAYS_TE = ("సోమవారం", "మంగళవారం", "బుధవారం", "గురువారం", "శుక్రవారం", "శనివారం", "ఆదివారం")
WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

RULES_START = "<!-- rules:start -->"
RULES_END = "<!-- rules:end -->"

ROLE_MAX_CHARS = 60  # a safety net against a looping role; roles are a few words


def reply_schema(max_items: int) -> dict[str, Any]:
    """The JSON schema vLLM constrains the reply to: valid JSON, a category of the list, at most
    `max_items` people and organisations (so a long list of officials cannot run to max_tokens)."""
    return {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": list(v.CATEGORIES)},
            "people": {
                "type": "array",
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": ["string", "null"], "maxLength": ROLE_MAX_CHARS},
                    },
                    "required": ["name", "role"],
                },
            },
            "organisations": {
                "type": "array",
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string", "enum": list(v.ORG_TYPES)},
                    },
                    "required": ["name", "type"],
                },
            },
            "event_date_expression": {"type": ["string", "null"]},
            "event_date": {"type": ["string", "null"]},
        },
        "required": ["category", "people", "organisations", "event_date_expression", "event_date"],
    }


# A retry after an unusable reply asks for shorter lists (the reply was probably cut off).
RETRY_LISTS = (4, 2)
RETRY_NOTE = (
    "\n\nYour previous reply was not valid JSON (it may have been too long). Answer again with "
    'at most <<N>> people and <<N>> organisations, each "role" at most two words or null.'
)


def category_rules() -> str:
    """The category rules block of the prompt (the judge prompt of NFR-1 uses the same text)."""
    text = PROMPT_FILE.read_text(encoding="utf-8")
    start, end = text.index(RULES_START) + len(RULES_START), text.index(RULES_END)
    return text[start:end].strip()


def date_table(print_date: date) -> str:
    """The relative expressions and weekdays of one issue, as prompt lines."""

    def day(offset: int) -> str:
        d = print_date + timedelta(days=offset)
        return f"{d.isoformat()} ({WEEKDAYS_EN[d.weekday()]})"

    lines = [
        f"- నేడు / ఈరోజు (today) = {day(0)}",
        f"- నిన్న (yesterday) = {day(-1)}",
        f"- మొన్న (the day before yesterday) = {day(-2)}",
        f"- రేపు (tomorrow) = {day(1)}",
        f"- ఎల్లుండి (the day after tomorrow) = {day(2)}",
        "- Most recent weekdays, on or before the print date: "
        + "; ".join(
            f"{WEEKDAYS_TE[(print_date + timedelta(days=-k)).weekday()]} = {day(-k)}"
            for k in range(7)
        ),
        "- Coming weekdays, after the print date: "
        + "; ".join(
            f"{WEEKDAYS_TE[(print_date + timedelta(days=k)).weekday()]} = {day(k)}"
            for k in range(1, 8)
        ),
    ]
    return "\n".join(lines)


def build_prompt(print_date: date) -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    return (
        text.replace("<<PRINT_DATE>>", print_date.isoformat())
        .replace("<<WEEKDAY>>", WEEKDAYS_EN[print_date.weekday()])
        .replace("<<MAX_LISTED>>", str(config.settings.enrich_max_listed))
        .replace("<<DATE_TABLE>>", date_table(print_date))
    )


def model_input(headline: str, text: str, limit: int | None = None) -> str:
    """What the model reads: headline, a new line, the text, cut to `limit` characters."""
    limit = config.settings.enrich_input_chars if limit is None else limit
    return f"{headline or ''}\n{text or ''}"[:limit]


def is_transport_error(exc: BaseException) -> bool:
    """Timeout, connection error or a 5xx answer: worth asking again."""
    import openai

    if isinstance(exc, (TimeoutError, ConnectionError, openai.APIConnectionError)):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500


class TransportFailed(RuntimeError):
    """The request still failed after the transport retries."""


def chat_once(client: Any, messages: list[dict[str, str]], **extra: Any) -> str:
    """One request, sent once: a `ModelClient` is used through its raw OpenAI client with the
    SDK's own retries off (the retries are ours); anything else only needs a `chat` method."""
    s = config.settings
    params = {
        "temperature": 0,
        "max_tokens": s.enrich_max_tokens,
        "timeout": s.enrich_request_timeout_s,
        **extra,
    }
    raw = getattr(client, "raw", None)
    if raw is None:
        return client.chat(messages, **params)
    resp = raw.with_options(max_retries=0).chat.completions.create(
        model=client.profile, messages=messages, **params
    )
    return resp.choices[0].message.content or ""


def ask(client: Any, messages: list[dict[str, str]], **extra: Any) -> str:
    """One chat request with the stage's sampling settings and the transport retries (FR-1)."""
    s = config.settings
    waits = list(s.enrich_retry_backoff_s) or [0.0]
    for attempt in range(s.enrich_transport_retries + 1):
        try:
            return chat_once(client, messages, **extra)
        except Exception as exc:
            if not is_transport_error(exc):
                raise
            if attempt >= s.enrich_transport_retries:
                raise TransportFailed(f"{type(exc).__name__}: {exc}") from exc
            time.sleep(waits[min(attempt, len(waits) - 1)])
    raise AssertionError("unreachable")


@dataclass
class StoryOutcome:
    parsed: v.Parsed
    error: str | None = None  # why the story fell back, for the log


def enrich_story(
    client: Any, prompt: str, headline: str, text: str, print_date: date
) -> StoryOutcome:
    """Enrich one story: ask, validate, ask again (with the reply schema) on a bad reply."""
    s = config.settings
    given = model_input(headline, text)
    if not given.strip():
        parsed = v.Parsed(
            "other", [], [], print_date.isoformat(), "print_date", None, ["empty_story"]
        )
        return StoryOutcome(parsed)
    error = ""
    for attempt in range(s.enrich_format_retries + 1):
        system = prompt
        limit = s.enrich_max_listed
        if attempt:  # the reply was unusable (often cut off at max_tokens): ask for less
            limit = RETRY_LISTS[min(attempt, len(RETRY_LISTS)) - 1]
            system = prompt + RETRY_NOTE.replace("<<N>>", str(limit))
        extra: dict[str, Any] = {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": reply_schema(limit)},
            }
        }
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": given},
        ]
        try:
            reply = ask(client, messages, **extra)
        except TransportFailed as exc:
            return StoryOutcome(v.fallback(print_date), str(exc))
        except Exception as exc:  # noqa: BLE001 - e.g. a 400: asking again would not help
            return StoryOutcome(v.fallback(print_date), f"{type(exc).__name__}: {exc}")
        try:
            data = parse_json_reply(reply)
            return StoryOutcome(
                v.parse_reply(
                    data,
                    given,
                    print_date,
                    s.enrich_event_past_years,
                    s.enrich_event_future_days,
                    limit,
                )
            )
        except ValueError as exc:  # not JSON, or v.SchemaError
            error = str(exc)[:200]
    return StoryOutcome(v.fallback(print_date), error)


def warm_up(client: Any, prompt: str) -> None:
    """Let the server cache the system prompt before the parallel requests start: requests that
    begin together would each compute it again. Best effort, the answer is not used."""
    with contextlib.suppress(Exception):  # the real requests report real problems
        client.chat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": "."}],
            temperature=0,
            max_tokens=1,
            timeout=config.settings.enrich_request_timeout_s,
        )


def enrich_stories(
    client: Any,
    stories: Sequence[Mapping[str, Any]],
    print_date: date,
    workers: int | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Enrich every story of a date. Returns ({story_id: record}, summary).

    `stories` are the dedup story records (`story_id`, `headline`, `text`). The summary holds the
    counters of the stage detail; `request_seconds` is the wall time of the requests only.
    """
    s = config.settings
    prompt = build_prompt(print_date)
    workers = max(1, workers if workers is not None else s.enrich_concurrency)
    errors: list[str] = []
    lock = threading.Lock()

    def one(story: Mapping[str, Any]) -> StoryOutcome:
        outcome = enrich_story(
            client, prompt, story.get("headline") or "", story.get("text") or "", print_date
        )
        if outcome.error:
            with lock:
                errors.append(f"{story.get('story_id')}: {outcome.error}")
        return outcome

    warm_up(client, prompt)
    # Longest first: the long stories are the slow requests, so they must not start last.
    order = sorted(range(len(stories)), key=lambda i: -len(stories[i].get("text") or ""))
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        done = list(pool.map(one, [stories[i] for i in order]))
    seconds = time.monotonic() - started
    outcomes: list[StoryOutcome] = [None] * len(stories)  # type: ignore[list-item]
    for i, outcome in zip(order, done, strict=True):
        outcomes[i] = outcome

    records: dict[str, dict[str, Any]] = {}
    categories = dict.fromkeys(v.CATEGORIES, 0)
    people = orgs = dropped = differs = failed = 0
    for story, outcome in zip(stories, outcomes, strict=True):
        parsed = outcome.parsed
        records[story["story_id"]] = parsed.record()
        categories[parsed.category] += 1
        people += len(parsed.people)
        orgs += len(parsed.organisations)
        dropped += parsed.dropped_names
        differs += parsed.event_date != print_date.isoformat()
        failed += v.FAILED_FLAG in parsed.flags
    for line in errors[:5]:
        print(f"enrich_core {print_date.isoformat()}: fallback for {line}", file=sys.stderr)
    summary = {
        "stories": len(stories),
        "request_seconds": round(seconds, 3),
        "stories_per_s": round(len(stories) / seconds, 2) if seconds > 0 else 0.0,
        "categories": categories,
        "people": people,
        "organisations": orgs,
        "dropped_names": dropped,
        "event_date_differs": differs,
        "enrich_failed": failed,
    }
    return records, summary
