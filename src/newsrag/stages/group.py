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

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.grouping.page import (
    INVALID_BLOCK_REFS,
    NO_BLOCKS,
    PROMPT_VERSION,
    SPLIT_HALVES,
    VERSION_SETTINGS,
    Ask,
    group_page,
    make_create,
    send,
    split_halves,
)
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    StageUnavailable,
    artifact_path,
    versioned,
    write_json_atomic,
)

# The grouping logic lives in `newsrag.grouping.page`; these names stay importable from here for
# the stage modules and tests that use them through this module.
__all__ = [
    "INVALID_BLOCK_REFS",
    "NO_BLOCKS",
    "PROMPT_VERSION",
    "SPLIT_HALVES",
    "VERSION_SETTINGS",
    "GroupStage",
    "group_page",
    "make_create",
    "send",
    "split_halves",
]

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
