"""Grouping one page with the VLM: the request helper (`send`, `make_create`) and `group_page`,
which turns a page's `ocr_final` artifact into its `group` artifact.

The `group`, `judge`, `group_retry` and `transcribe` stages and the grouping tools (judge,
bake-off, spot-check) all use these; `newsrag.stages.group` is the stage adapter around them.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from newsrag import config
from newsrag.grouping import geometry, parse
from newsrag.grouping import input as gin

PROMPT_VERSION = gin.PROMPT_VERSION
NO_BLOCKS = "low_confidence:no_blocks"
SPLIT_HALVES = "split_halves"
# the model's last reply named block numbers that are not on the page; they were dropped
INVALID_BLOCK_REFS = "low_confidence:invalid_block_refs"
VERSION_SETTINGS = [
    "group_model",
    "group_image_max_px",
    "group_text_chars",
    "group_long_page_blocks",
    "group_long_page_chars",
    "group_max_tokens",
    "group_max_gap_px",
    "group_overlap_frac",
    "group_headline_slack_px",
    "group_order_same_column_frac",
    "group_order_min_overlap_px",
    "group_dup_overlap_frac",
    "group_dup_text_frac",
]
_TOO_LONG_HINTS = ("context length", "maximum context", "too long", "max_model_len")


# --- talking to the model ----------------------------------------------------------------------


def is_context_error(message: str) -> bool:
    low = message.lower()
    return any(hint in low for hint in _TOO_LONG_HINTS)


Ask = Callable[..., str]  # ask(messages, max_items=None) -> the model's reply text


def send(
    create: Callable[..., Any],
    on_usage: Callable[[int, int], None] | None = None,
) -> Ask:
    """A function `ask(messages, max_items=None) -> reply text` around `create(messages,
    max_items)` (one chat completion; `max_items` is the page's block count, the cap on the
    schema's lists).

    Timeouts, connection errors and HTTP 5xx are retried `group_transport_retries` times with
    `group_retry_backoff_s` waits; HTTP 400 for context length and a reply cut by
    `max_tokens` raise `parse.TooLong`. `on_usage(prompt_tokens, completion_tokens)` is called
    after every answered request.

    `ask.once(messages, max_items)` is the same call without the retries after a timeout (the
    timeout is raised at once), for a caller that would rather send a smaller request than wait
    for the same one again; connection errors and HTTP 5xx are still retried.
    """
    import openai

    s = config.settings

    def call(messages: list[dict[str, Any]], max_items: int | None, retry_timeouts: bool) -> str:
        attempt = 0
        while True:
            try:
                response = create(messages, max_items)
            except openai.BadRequestError as exc:
                if is_context_error(str(exc)):
                    raise parse.TooLong(str(exc)) from None
                raise
            except openai.APITimeoutError as exc:
                if not retry_timeouts:
                    raise
                failure: Exception = exc
            except openai.APIConnectionError as exc:
                failure = exc
            except openai.APIStatusError as exc:
                if exc.status_code < 500:
                    raise
                failure = exc
            else:
                usage = getattr(response, "usage", None)
                if on_usage is not None and usage is not None:
                    on_usage(usage.prompt_tokens or 0, usage.completion_tokens or 0)
                choice = response.choices[0]
                if choice.finish_reason == "length":
                    raise parse.TooLong("the reply was cut at max_tokens")
                return choice.message.content or ""
            if attempt >= s.group_transport_retries:
                raise failure
            waits = s.group_retry_backoff_s
            time.sleep(waits[min(attempt, len(waits) - 1)] if waits else 0.0)
            attempt += 1

    def ask(messages: list[dict[str, Any]], max_items: int | None = None) -> str:
        return call(messages, max_items, True)

    def once(messages: list[dict[str, Any]], max_items: int | None = None) -> str:
        return call(messages, max_items, False)

    ask.once = once  # type: ignore[attr-defined]
    return ask


def make_create(client: Any) -> Callable[..., Any]:
    """`create(messages, max_items)` on a `ModelClient`: temperature 0, group_max_tokens, the
    reply schema, own timeout."""
    s = config.settings
    raw = client.raw.with_options(timeout=s.group_request_timeout_s, max_retries=0)

    def create(messages: list[dict[str, Any]], max_items: int | None = None) -> Any:
        return raw.chat.completions.create(
            model=client.profile,
            messages=messages,
            temperature=0,
            max_tokens=s.group_max_tokens,
            response_format=parse.response_format(max_items or 1000),
            # the reply is one compact line; a run of blank lines is the model looping on
            # whitespace, which the JSON grammar allows: stop it there (the reply is then invalid)
            stop=["\n\n"],
        )

    return create


# --- one page ----------------------------------------------------------------------------------


def split_halves(
    blocks: Sequence[dict[str, Any]], width: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Blocks left and right of the page's vertical centre line (by box centre)."""
    mid = width / 2
    left = [b for b in blocks if (b["bbox"][0] + b["bbox"][2]) / 2 < mid]
    right = [b for b in blocks if (b["bbox"][0] + b["bbox"][2]) / 2 >= mid]
    return left, right


def ask_blocks(
    ask: Ask,
    data: dict[str, Any],
    blocks: Sequence[dict[str, Any]],
    image_long_side: int | None = None,
) -> dict[str, Any]:
    """Raw grouping of `blocks`, with the invalid-reply retries."""
    messages = gin.messages_for(data, blocks, image_long_side)
    valid = {b["order"] for b in blocks}
    return parse.request_grouping(
        lambda sent: ask(sent, len(blocks)), messages, valid, config.settings.group_parse_retries
    )


def group_page(
    data: dict[str, Any], ask: Ask, model: str, image_long_side: int | None = None
) -> dict[str, Any]:
    """The `group` artifact of one page from its `ocr_final` artifact (`data`).
    `image_long_side` overrides `group_image_max_px` (ticket 013's regroup pass).

    `ask(messages, max_items)` is the model call (see `send`). A page that does not fit the model is
    grouped in two halves and the answers are merged; a half that still fails fails the page.
    """
    page_id = data["page_id"]
    blocks = list(data.get("blocks") or [])
    page_flags = list(data.get("page_flags") or [])
    params = geometry.Params.from_settings()

    def artifact(
        articles: list, unassigned: list, extra: Sequence[str], dropped: Sequence[int] = ()
    ) -> dict[str, Any]:
        out = {
            "page_id": page_id,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "articles": articles,
            "unassigned": unassigned,
            "page_flags": [*dict.fromkeys([*page_flags, *extra])],
        }
        if dropped:
            out["dropped_block_refs"] = list(dropped)
        return out

    if not blocks:
        return artifact([], [], [NO_BLOCKS])

    extra: list[str] = []
    try:
        raw = ask_blocks(ask, data, blocks, image_long_side)
    except parse.TooLong:
        width, _ = gin.page_size(data)
        left, right = split_halves(blocks, width)
        if not left or not right:
            raise parse.GroupingError("page does not fit the model and cannot be split") from None
        raw = parse.merge_raw(
            [
                ask_blocks(ask, data, left, image_long_side),
                ask_blocks(ask, data, right, image_long_side),
            ]
        )
        extra.append(SPLIT_HALVES)
    dropped = raw.get("dropped_refs", [])
    if dropped:
        extra.append(INVALID_BLOCK_REFS)

    built = geometry.build_grouping(raw["articles"], raw["unassigned"], blocks, page_id, params)
    return artifact(built.articles, built.unassigned, [*extra, *built.page_flags], dropped)
