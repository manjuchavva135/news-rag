"""Stage `judge` (ticket 013 FR-1, FR-2) and the model holder shared with the retry stages.

A vision-language model of another family than the grouping model scores each page's `group`
artifact (`newsrag.grouping.judge`). The model is loaded once per `stage run` (`open`).
"""

from __future__ import annotations

import contextlib
import functools
import threading
from collections.abc import Callable
from typing import Any

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.grouping import judge as jg
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    artifact_path,
    versioned,
    write_json_atomic,
)


class LazyModel:
    """A model opened by the first caller that needs it and shared by all threads.

    `ask(messages, max_items=None)` is the reply text of the chat call; it opens the model on its
    first use. `close()` releases the model if it was opened.
    """

    def __init__(self, profile: Callable[[], str], make_ask: Callable[[Any], Callable[..., str]]):
        self._profile = profile
        self._make_ask = make_ask
        self._lock = threading.Lock()
        self._stack: contextlib.ExitStack | None = None
        self._ask: Callable[..., str] | None = None
        self._error: BaseException | None = None  # the first failure to open: not tried again
        self._closed = False

    def reset(self) -> None:
        """Forget an earlier close or failure: a new `stage run` starts (the stage objects live
        as long as the process)."""
        with self._lock:
            self._closed = False
            self._error = None

    def open(self) -> Callable[..., str]:
        """Open the model if it is not open yet; returns the ask function."""
        with self._lock:
            if self._closed:
                raise RuntimeError("the model was released: the run is over")
            if self._error is not None:
                raise self._error
            if self._ask is None:
                from newsrag import models

                stack = contextlib.ExitStack()
                try:
                    client = stack.enter_context(models.use(self._profile()))
                    self._ask = self._make_ask(client)
                except BaseException as exc:
                    stack.close()
                    self._error = exc  # every page would only start vLLM again
                    raise
                self._stack = stack
            return self._ask

    def ask(self, messages: list[dict[str, Any]], max_items: int | None = None) -> str:
        return self.open()(messages, max_items)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            stack, self._stack, self._ask = self._stack, None, None
        if stack is not None:
            stack.close()


def judge_ask_factory(client: Any) -> Callable[..., str]:
    from newsrag.stages.group import send

    return send(jg.make_create(client))


def judge_artifact(page_id: str, source: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "model": config.settings.judge_model,
        "prompt_version": jg.PROMPT_VERSION,
        "source": source,
        **result,
    }


def judge_detail(result: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for verdict in result["verdicts"].values():
        counts[verdict] = counts.get(verdict, 0) + 1
    return {
        "score": result["score"],
        "n_judged": result["n_judged"],
        "missed_regions": result["missed_regions"],
        "judge_skipped": result["score"] is None,
        "text_chars": result.get("text_chars"),
        "verdicts": counts,
        "story_merged": sum(1 for v in (result.get("stories") or {}).values() if v == 2),
    }


class JudgeStage(PageStage):
    name = "judge"
    depends_on = ("group",)
    concurrency = config.settings.judge_concurrency
    source_stage = "group"  # the stage whose grouping is judged
    lazy = False  # JudgeRetryStage opens the model at its first request instead

    def __init__(self) -> None:
        self._model = LazyModel(lambda: config.settings.judge_model, judge_ask_factory)

    @functools.cached_property
    def version(self) -> str:  # type: ignore[override]
        return versioned(
            f"{config.settings.judge_model}-judge-p{jg.PROMPT_VERSION}",
            jg.JUDGE_SETTINGS,
            jg.PROMPT_FILES,
        )

    @property
    def models_needed(self) -> tuple[str, ...]:  # type: ignore[override]
        return (config.settings.judge_model,)

    def preflight(self) -> None:
        jg.check_families()

    def open(self) -> None:
        """Load the judge once; every thread shares it."""
        self._model.reset()
        if not self.lazy:
            self._model.open()

    def close(self) -> None:
        self._model.close()

    def grouping_path(self, page_id: str) -> str | None:
        """The grouping artifact to judge; None when there is nothing to judge."""
        path = gin.done_artifact(self.source_stage, page_id)
        if path is None:
            raise FileNotFoundError(f"no done {self.source_stage} artifact for {page_id}")
        return str(path)

    def handle(self, page: PageRow) -> StageResult:
        path = self.grouping_path(page.page_id)
        if path is None:
            return StageResult(None, {"retried": False})
        grouping = gin.load_artifact(path)
        data = gin.load_ocr_final(page.page_id)
        result = jg.judge_grouping(data, grouping, ask=self._model.ask)
        artifact = judge_artifact(page.page_id, self.source_stage, result)
        out = write_json_atomic(artifact_path(self.name, self.version, page.page_id), artifact)
        return StageResult(str(out), self.detail(result))

    def detail(self, result: dict[str, Any]) -> dict[str, Any]:
        return judge_detail(result)


class JudgeRetryStage(JudgeStage):
    """Stage `judge_retry` (FR-4): judges the regrouping of `group_retry`; a pass-through when
    the page was not regrouped. The model opens at the first page that needs it."""

    name = "judge_retry"
    depends_on = ("group_retry",)
    concurrency = config.settings.group_concurrency
    source_stage = "group_retry"
    lazy = True

    @functools.cached_property
    def version(self) -> str:  # type: ignore[override]
        return versioned(
            f"{config.settings.judge_model}-judge-p{jg.PROMPT_VERSION}"
            f"-t{config.settings.judge_threshold}",
            jg.JUDGE_SETTINGS,
            jg.PROMPT_FILES,
        )

    def grouping_path(self, page_id: str) -> str | None:
        row = gin.stage_row("group_retry", page_id)
        if row is None or row["status"] != "done":
            raise FileNotFoundError(f"no done group_retry row for {page_id}")
        if not (row["detail"] or {}).get("retried"):
            return None
        if not row["artifact_path"]:
            raise FileNotFoundError(f"group_retry of {page_id} has no artifact")
        return row["artifact_path"]

    def detail(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"retried": True, **judge_detail(result)}
