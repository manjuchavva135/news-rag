"""Reading the model's answer (ticket 012 FR-3, FR-4).

`parse_reply` checks one reply against the schema and returns a raw grouping
`{"articles": [{"kind", "blocks": [{"n", "role"}]}], "unassigned": [n...]}`; `request_grouping`
asks the model, appending the error and asking again when the reply is invalid.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from typing import Any

from newsrag.models.client import parse_json_reply

KINDS = (
    "news",
    "editorial",
    "brief_item",
    "notice_tender",
    "table_only",
    "ad_commercial",
    "masthead",
    "other",
)
ROLES = (
    "headline",
    "kicker",
    "subheadline",
    "byline",
    "dateline",
    "body",
    "caption",
    "photo",
    "table",
    "infographic",
    "pull_quote",
    "continuation_marker",
)


def response_format(max_items: int) -> dict[str, Any]:
    """The reply's JSON schema, sent as `response_format`: the server only produces known kinds
    and roles, no empty article, and no list longer than `max_items` (the page's block count),
    so a model that loops cannot run until `max_tokens`. `parse_reply` still checks everything
    (block numbers need the page)."""
    cap = max(1, max_items)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "grouping",
            "schema": {
                "type": "object",
                "properties": {
                    "articles": {
                        "type": "array",
                        "maxItems": cap,
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"enum": list(KINDS)},
                                "blocks": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": cap,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "n": {"type": "integer"},
                                            "role": {"enum": list(ROLES)},
                                        },
                                        "required": ["n", "role"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["kind", "blocks"],
                            "additionalProperties": False,
                        },
                    },
                    "unassigned": {
                        "type": "array",
                        "maxItems": cap,
                        "items": {"type": "integer"},
                    },
                },
                "required": ["articles"],
                "additionalProperties": False,
            },
        },
    }


class SchemaError(ValueError):
    """The reply is not JSON, or does not follow the schema."""


class GroupingError(RuntimeError):
    """The model never gave a valid grouping."""


class TooLong(Exception):
    """The request does not fit the model (HTTP 400 for context length, or the reply was cut)."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_reply(text: str, valid: Collection[int]) -> dict[str, Any]:
    """The raw grouping of one reply. `valid` are the block numbers that exist on the page.

    Raises SchemaError for invalid JSON, a missing or mistyped field, an unknown kind or role,
    or a block number that is not in `valid`. An article with an empty `blocks` list is dropped.
    """
    try:
        data = parse_json_reply(text)
    except ValueError as exc:
        raise SchemaError(str(exc)) from None
    if not isinstance(data, dict):
        raise SchemaError("the reply must be a JSON object with an `articles` list")
    articles = data.get("articles")
    if not isinstance(articles, list):
        raise SchemaError("`articles` must be a list")
    out_articles = []
    for i, article in enumerate(articles):
        where = f"articles[{i}]"
        if not isinstance(article, dict):
            raise SchemaError(f"{where} must be an object")
        kind = article.get("kind")
        if kind not in KINDS:
            raise SchemaError(f"{where}.kind {kind!r} is not one of {', '.join(KINDS)}")
        blocks = article.get("blocks")
        if not isinstance(blocks, list):
            raise SchemaError(f"{where}.blocks must be a list")
        entries = []
        for j, entry in enumerate(blocks):
            at = f"{where}.blocks[{j}]"
            if not isinstance(entry, dict):
                raise SchemaError(f'{at} must be an object like {{"n": 12, "role": "body"}}')
            n, role = entry.get("n"), entry.get("role")
            if not _is_int(n):
                raise SchemaError(f"{at}.n must be an integer block number, got {n!r}")
            if n not in valid:
                raise SchemaError(f"{at}.n {n} is not a block number on this page")
            if role not in ROLES:
                raise SchemaError(f"{at}.role {role!r} is not one of {', '.join(ROLES)}")
            entries.append({"n": n, "role": role})
        if entries:
            out_articles.append({"kind": kind, "blocks": entries})
    unassigned = data.get("unassigned", [])
    if not isinstance(unassigned, list):
        raise SchemaError("`unassigned` must be a list of block numbers")
    for n in unassigned:
        if not _is_int(n) or n not in valid:
            raise SchemaError(f"unassigned entry {n!r} is not a block number on this page")
    return {"articles": out_articles, "unassigned": list(unassigned)}


def merge_raw(parts: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Several raw groupings (the two halves of a page) as one."""
    return {
        "articles": [a for part in parts for a in part["articles"]],
        "unassigned": [n for part in parts for n in part["unassigned"]],
    }


def request_grouping(
    ask: Callable[[list[dict[str, Any]]], str],
    messages: list[dict[str, Any]],
    valid: Collection[int],
    retries: int = 2,
) -> dict[str, Any]:
    """`ask(messages)` returns the model's reply text (and raises TooLong when it does not
    fit). An invalid reply is answered with the error appended; after `retries` more tries the
    page fails with GroupingError."""
    sent = list(messages)
    error = ""
    for _ in range(retries + 1):
        reply = ask(sent)
        try:
            return parse_reply(reply, valid)
        except SchemaError as exc:
            error = str(exc)
            sent = [
                *messages,
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": (
                        f"Your reply was not valid: {error}\n"
                        "Answer again with only the corrected JSON object."
                    ),
                },
            ]
    raise GroupingError(f"no valid grouping after {retries + 1} replies: {error}")
