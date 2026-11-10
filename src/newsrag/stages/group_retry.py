"""Stage `group_retry` (ticket 013 FR-3): regroup the pages the judge scored below the threshold.

A page whose `judge` score is at or above `judge_threshold` (or null) passes through without a
model call. Any other page is grouped again by ticket 012's code with the page image
`judge_retry_scale` times larger. The grouping model is loaded at the first page that needs it.
"""

from __future__ import annotations

import functools
from typing import Any

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.stages import group as group_mod
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    StageUnavailable,
    artifact_path,
    versioned,
    write_json_atomic,
)
from newsrag.stages.judge import LazyModel

VERSION_SETTINGS = [*group_mod.VERSION_SETTINGS, "judge_threshold", "judge_retry_scale"]


def retry_long_side() -> int:
    s = config.settings
    return round(s.group_image_max_px * s.judge_retry_scale)


def needs_retry(score: float | None) -> bool:
    return score is not None and score < config.settings.judge_threshold


class GroupRetryStage(PageStage):
    name = "group_retry"
    depends_on = ("group", "judge")
    concurrency = config.settings.group_concurrency

    def __init__(self) -> None:
        self._model = LazyModel(
            lambda: config.settings.group_model,
            lambda client: group_mod.send(group_mod.make_create(client)),
        )

    @functools.cached_property
    def version(self) -> str:  # type: ignore[override]
        s = config.settings
        prefix = f"{s.group_model}-regroup-p{gin.PROMPT_VERSION}-t{s.judge_threshold}"
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
        """Nothing is loaded yet: the model opens at the first page that needs a retry."""
        self._model.reset()

    def close(self) -> None:
        self._model.close()

    def judge_score(self, page_id: str) -> float | None:
        row = gin.stage_row("judge", page_id)
        if row is None or row["status"] != "done" or not row["artifact_path"]:
            raise FileNotFoundError(f"no done judge artifact for {page_id}")
        return gin.load_artifact(row["artifact_path"]).get("score")

    def handle(self, page: PageRow) -> StageResult:
        score = self.judge_score(page.page_id)
        if not needs_retry(score):
            return StageResult(None, {"retried": False})
        side = retry_long_side()
        data = gin.load_ocr_final(page.page_id)
        artifact = group_mod.group_page(
            data, self._model.ask, config.settings.group_model, image_long_side=side
        )
        path = write_json_atomic(artifact_path(self.name, self.version, page.page_id), artifact)
        detail: dict[str, Any] = {
            "retried": True,
            "image_long_side": side,
            "judge_score": score,
            "n_articles": len(artifact["articles"]),
            "page_flags": artifact["page_flags"],
        }
        return StageResult(str(path), detail)
