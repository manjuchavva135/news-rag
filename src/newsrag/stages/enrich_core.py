"""Stage `enrich_core` (ticket 019): category, people/organisations and event date per story.

A date stage on top of `dedup`: it reads the date's canonical stories and asks a local text model
about each one (`enrich_concurrency` parallel requests inside `handle`, one client opened once per
`stage run`). The date fails when more than `enrich_fail_ratio` of its stories could not be
enriched, so that it is retried as a whole.
"""

from __future__ import annotations

import contextlib
import sys
from datetime import date

from newsrag import config
from newsrag.stages.base import (
    DateStage,
    PageRow,
    StageResult,
    StageUnavailable,
    artifact_path,
    versioned,
    write_json_atomic,
)

# every setting the stage reads that changes what it writes
VERSION_SETTINGS = [
    "enrich_model",
    "enrich_input_chars",
    "enrich_max_tokens",
    "enrich_max_listed",
    "enrich_format_retries",
    "enrich_event_past_years",
    "enrich_event_future_days",
]


class EnrichCoreStage(DateStage):
    name = "enrich_core"
    depends_on = ("dedup",)
    concurrency = 1  # one date at a time; the requests of a date run in parallel inside handle

    def __init__(self) -> None:
        self._stack: contextlib.ExitStack | None = None
        self._client = None

    @property
    def version(self) -> str:  # type: ignore[override]
        from newsrag.enrich.core import PROMPT_FILE

        return versioned(f"{config.settings.enrich_model}-core-p1", VERSION_SETTINGS, [PROMPT_FILE])

    @property
    def models_needed(self) -> tuple[str, ...]:  # type: ignore[override]
        return (config.settings.enrich_model,)

    def preflight(self) -> None:
        from newsrag.models import get_profile

        try:
            profile = get_profile(config.settings.enrich_model)
        except KeyError as exc:
            raise StageUnavailable(str(exc)) from None
        if not profile.is_vllm:
            raise StageUnavailable(f"enrich_model {profile.name} is not a text-model profile")

    def open(self) -> None:
        """Start (or reuse) the text model; the client is shared by all requests of the run."""
        from newsrag import models

        self.close()
        self._stack = contextlib.ExitStack()
        try:
            self._client = self._stack.enter_context(models.use(config.settings.enrich_model))
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            stack.close()

    def handle(self, pub_date: date, pages: list[PageRow]) -> StageResult:
        from newsrag.dedup import load
        from newsrag.enrich import core

        if self._client is None:
            raise RuntimeError("enrich_core: the model is not open (call open() first)")
        paths = load.done_dedup(pub_date)
        path = paths.get(pub_date.isoformat())
        if path is None:
            raise FileNotFoundError(f"no done dedup artifact for {pub_date.isoformat()}")
        stories = load.load_dedup(path)["stories"]
        records, summary = core.enrich_stories(self._client, stories, pub_date)
        out = write_json_atomic(
            artifact_path(self.name, self.version, pub_date.isoformat()),
            {"pub_date": pub_date.isoformat(), "stories": records},
        )
        print(
            f"enrich_core {pub_date.isoformat()}: {summary['stories']} stories in "
            f"{summary['request_seconds']:.1f} s ({summary['stories_per_s']} stories/s), "
            f"{summary['enrich_failed']} failed, {summary['dropped_names']} dropped names",
            file=sys.stderr,
            flush=True,
        )
        total, failed = summary["stories"], summary["enrich_failed"]
        if total and failed / total > config.settings.enrich_fail_ratio:
            raise RuntimeError(
                f"{failed} of {total} stories could not be enriched "
                f"(more than {config.settings.enrich_fail_ratio:.0%}); artifact kept at {out}"
            )
        return StageResult(str(out), summary)
