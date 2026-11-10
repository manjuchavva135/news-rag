"""Stage `group_final` (ticket 013 FR-5, FR-7): the grouping every later stage reads.

Picks the better of the original grouping and the regrouping by judge score (a tie keeps the
original), adds the judge's verdicts and the doubt flags, and writes the ticket-012 artifact plus
`judge_score`, `retried` and per article `judge_verdict`. CPU only.

If `judge` failed for good the original grouping passes through with `judge_score: null`; if
`group_retry` or `judge_retry` failed for good the original is kept. Both are flagged.
"""

from __future__ import annotations

import copy
import functools
from typing import Any

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    artifact_path,
    versioned,
    write_json_atomic,
)

JUDGE_LOW = "low_confidence:judge_low"
JUDGE_FAILED = "low_confidence:judge_failed"
RETRY_FAILED = "low_confidence:retry_failed"
GEOMETRY = "low_confidence:geometry"
# ticket-012 article flags that mean the geometry doubts the article (the others are informational)
GEOMETRY_ARTICLE_FLAGS = ("disconnected", "overlaps_other", "no_headline", "headline_below_body")
GEOMETRY_PAGE_FLAGS = ("blocks_omitted", "block_duplicated")
VERSION_SETTINGS = ["judge_threshold", "judge_skip_kinds"]


def add_flag(flags: list[str], flag: str) -> None:
    if flag not in flags:
        flags.append(flag)


def better(original: float | None, retry: float | None) -> bool:
    """The regrouping wins only with a strictly higher score; a null score never wins."""
    return retry is not None and original is not None and retry > original


def finalize(
    grouping: dict[str, Any],
    score: float | None,
    verdicts: dict[str, str] | None,
    retried: bool,
    extra_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The `group_final` artifact for the kept grouping.

    `verdicts` maps article ids to the judge's verdicts (None when the judge failed).
    `extra_flags` are page flags that are also put on every article.
    """
    s = config.settings
    out = copy.deepcopy(grouping)
    out["judge_score"] = score
    out["retried"] = retried
    page_flags: list[str] = list(out.get("page_flags") or [])
    every_article: list[str] = list(extra_flags)
    if score is not None and score < s.judge_threshold:
        every_article.append(JUDGE_LOW)
    if any(f in page_flags for f in GEOMETRY_PAGE_FLAGS):
        every_article.append(GEOMETRY)
    for flag in every_article:
        add_flag(page_flags, flag)
    skip = set(s.judge_skip_kinds)
    for article in out["articles"]:
        flags: list[str] = list(article.get("flags") or [])
        if article["kind"] in skip or verdicts is None:
            verdict = None
        else:
            verdict = verdicts.get(article["article_id"], "missing")
        article["judge_verdict"] = verdict
        if verdict not in (None, "correct"):
            add_flag(flags, f"judge:{verdict}")
        if any(f in flags for f in GEOMETRY_ARTICLE_FLAGS):
            add_flag(flags, GEOMETRY)
        for flag in every_article:
            add_flag(flags, flag)
        article["flags"] = flags
    out["page_flags"] = page_flags
    return out


class GroupFinalStage(PageStage):
    name = "group_final"
    depends_on = ("group", "judge", "group_retry", "judge_retry")
    # a failed judge or retry must not block the page: group_final records it and passes the
    # original grouping through
    tolerate_failed = ("judge", "group_retry", "judge_retry")
    concurrency = config.settings.group_concurrency

    @functools.cached_property
    def version(self) -> str:  # type: ignore[override]
        return versioned(f"final-t{config.settings.judge_threshold}", VERSION_SETTINGS)

    @staticmethod
    def _done(stage: str, page_id: str) -> dict[str, Any] | None:
        row = gin.stage_row(stage, page_id)
        return row if row is not None and row["status"] == "done" else None

    def handle(self, page: PageRow) -> StageResult:
        pid = page.page_id
        group_path = gin.done_artifact("group", pid)
        if group_path is None:
            raise FileNotFoundError(f"no done group artifact for {pid}")
        original = gin.load_artifact(group_path)

        judge_row = self._done("judge", pid)
        if judge_row is None or not judge_row["artifact_path"]:
            kept = finalize(original, None, None, False, (JUDGE_FAILED,))
            return self._write(pid, kept, {"kept": "group", "judge_failed": True})
        judged = gin.load_artifact(judge_row["artifact_path"])
        score: float | None = judged["score"]
        verdicts: dict[str, str] = judged["verdicts"]

        retry_row = self._done("group_retry", pid)
        if retry_row is None:
            kept = finalize(original, score, verdicts, False, (RETRY_FAILED,))
            return self._write(pid, kept, {"kept": "group", "retry_failed": True})
        if not (retry_row["detail"] or {}).get("retried"):
            kept = finalize(original, score, verdicts, False)
            return self._write(pid, kept, {"kept": "group"})
        retry_judge_row = self._done("judge_retry", pid)
        if retry_judge_row is None or not retry_judge_row["artifact_path"]:
            kept = finalize(original, score, verdicts, False, (RETRY_FAILED,))
            return self._write(pid, kept, {"kept": "group", "retry_failed": True})
        retry_judged = gin.load_artifact(retry_judge_row["artifact_path"])
        retry_score: float | None = retry_judged["score"]
        if better(score, retry_score):
            grouping = gin.load_artifact(retry_row["artifact_path"])
            kept = finalize(grouping, retry_score, retry_judged["verdicts"], True)
            where = "group_retry"
        else:
            kept = finalize(original, score, verdicts, True)
            where = "group"
        return self._write(
            pid, kept, {"kept": where, "original_score": score, "retry_score": retry_score}
        )

    def _write(self, page_id: str, artifact: dict[str, Any], detail: dict[str, Any]) -> StageResult:
        path = write_json_atomic(artifact_path(self.name, self.version, page_id), artifact)
        detail = {
            **detail,
            "judge_score": artifact["judge_score"],
            "retried": artifact["retried"],
            "page_flags": artifact["page_flags"],
        }
        return StageResult(str(path), detail)
