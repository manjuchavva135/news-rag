"""Stage `group` (ticket 012): a vision-language model groups a page's blocks into articles.

The model sees the page with every `ocr_final` block boxed and numbered, plus a text list of the
blocks, and returns the articles and each block's role (`newsrag.grouping.parse`). Geometry then
checks the answer, orders the blocks inside each article by columns, drops duplicate boxes and
flags what looks wrong (`newsrag.grouping.geometry`).

The model is loaded once per `stage run` (`open`) and shared by all threads.
"""

from __future__ import annotations

import contextlib
import functools
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from newsrag import config
from newsrag.grouping import geometry, parse
from newsrag.grouping import input as gin
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    StageUnavailable,
    artifact_path,
    versioned,
    write_json_atomic,
)

PROMPT_VERSION = gin.PROMPT_VERSION
NO_BLOCKS = "low_confidence:no_blocks"
SPLIT_HALVES = "split_halves"
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
    """
    import openai

    s = config.settings

    def ask(messages: list[dict[str, Any]], max_items: int | None = None) -> str:
        attempt = 0
        while True:
            try:
                response = create(messages, max_items)
            except openai.BadRequestError as exc:
                if is_context_error(str(exc)):
                    raise parse.TooLong(str(exc)) from None
                raise
            except (openai.APITimeoutError, openai.APIConnectionError) as exc:
                failure: Exception = exc
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
) -> dict[str, Any]:
    """Raw grouping of `blocks`, with the invalid-reply retries."""
    messages = gin.messages_for(data, blocks)
    valid = {b["order"] for b in blocks}
    return parse.request_grouping(
        lambda sent: ask(sent, len(blocks)), messages, valid, config.settings.group_parse_retries
    )


def group_page(data: dict[str, Any], ask: Ask, model: str) -> dict[str, Any]:
    """The `group` artifact of one page from its `ocr_final` artifact (`data`).

    `ask(messages, max_items)` is the model call (see `send`). A page that does not fit the model is
    grouped in two halves and the answers are merged; a half that still fails fails the page.
    """
    page_id = data["page_id"]
    blocks = list(data.get("blocks") or [])
    page_flags = list(data.get("page_flags") or [])
    params = geometry.Params.from_settings()

    def artifact(articles: list, unassigned: list, extra: Sequence[str]) -> dict[str, Any]:
        return {
            "page_id": page_id,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "articles": articles,
            "unassigned": unassigned,
            "page_flags": [*dict.fromkeys([*page_flags, *extra])],
        }

    if not blocks:
        return artifact([], [], [NO_BLOCKS])

    extra: list[str] = []
    try:
        raw = ask_blocks(ask, data, blocks)
    except parse.TooLong:
        width, _ = gin.page_size(data)
        left, right = split_halves(blocks, width)
        if not left or not right:
            raise parse.GroupingError("page does not fit the model and cannot be split") from None
        raw = parse.merge_raw([ask_blocks(ask, data, left), ask_blocks(ask, data, right)])
        extra.append(SPLIT_HALVES)

    built = geometry.build_grouping(raw["articles"], raw["unassigned"], blocks, page_id, params)
    return artifact(built.articles, built.unassigned, [*extra, *built.page_flags])


# --- the stage ---------------------------------------------------------------------------------


class GroupStage(PageStage):
    name = "group"
    depends_on = ("ocr_final",)
    concurrency = config.settings.group_concurrency

    def __init__(self) -> None:
        self._stack: contextlib.ExitStack | None = None
        self._ask: Ask | None = None
        self._lock = threading.Lock()
        self._pages = 0
        self._t_first: float | None = None
        self._t_last = 0.0
        self._tokens = [0, 0, 0]  # requests, prompt tokens, completion tokens

    def _add_usage(self, prompt: int, completion: int) -> None:
        with self._lock:
            self._tokens[0] += 1
            self._tokens[1] += prompt
            self._tokens[2] += completion

    @functools.cached_property
    def version(self) -> str:  # type: ignore[override]
        prefix = f"{config.settings.group_model}-group-p{PROMPT_VERSION}"
        return versioned(prefix, VERSION_SETTINGS, [gin.PROMPT_FILE])

    @property
    def models_needed(self) -> tuple[str, ...]:  # type: ignore[override]
        return (config.settings.group_model,)

    def preflight(self) -> None:
        from newsrag.models import get_profile

        try:
            profile = get_profile(config.settings.group_model)
        except KeyError as exc:
            raise StageUnavailable(str(exc)) from None
        if profile.kind != "vlm":
            raise StageUnavailable(f"group_model {profile.name} is not a vlm profile")

    def open(self) -> None:
        """Load the model once; every thread shares the client."""
        from newsrag.models import use

        stack = contextlib.ExitStack()
        try:
            client = stack.enter_context(use(config.settings.group_model))
            self._ask = send(make_create(client), self._add_usage)
        except BaseException:
            stack.close()
            raise
        self._stack = stack
        self._pages, self._t_first, self._t_last = 0, None, 0.0
        self._tokens = [0, 0, 0]

    def close(self) -> None:
        stack, self._stack, self._ask = self._stack, None, None
        if stack is not None:
            stack.close()
        if self._pages:
            seconds = max((self._t_last - (self._t_first or self._t_last)), 1e-9)
            print(
                f"group: {self._pages} pages in {seconds:.1f} s (excl. model load) = "
                f"{self._pages / seconds:.2f} pages/s",
                flush=True,
            )
            n, prompt, completion = self._tokens
            print(
                f"group: {n} requests, {prompt} prompt tokens, {completion} completion tokens",
                flush=True,
            )

    def handle(self, page: PageRow) -> StageResult:
        started = time.monotonic()
        with self._lock:
            if self._t_first is None:
                self._t_first = started
        if self._ask is None:
            raise RuntimeError("group stage used before open()")
        data = gin.load_ocr_final(page.page_id)
        artifact = group_page(data, self._ask, config.settings.group_model)
        path = write_json_atomic(artifact_path(self.name, self.version, page.page_id), artifact)
        with self._lock:
            self._pages += 1
            self._t_last = max(self._t_last, time.monotonic())
        return StageResult(
            str(path),
            {
                "n_blocks": len(data.get("blocks") or []),
                "n_articles": len(artifact["articles"]),
                "n_unassigned": len(artifact["unassigned"]),
                "page_flags": artifact["page_flags"],
            },
        )
