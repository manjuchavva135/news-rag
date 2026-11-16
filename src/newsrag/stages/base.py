"""Base classes and helpers for pipeline stages.

A stage is a :class:`PageStage` (one item = one page) or a :class:`DateStage` (one item = one
print date, run once every page of that date is through its dependencies). See CLAUDE.md,
"How to add a stage".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

from newsrag.config import settings
from newsrag.core import (  # re-exported: stage modules import them from here
    PageRow,
    StageUnavailable,
    config_hash,
    version_slug,
    write_json_atomic,
)

__all__ = [
    "PageRow",
    "StageUnavailable",
    "config_hash",
    "version_slug",
    "write_json_atomic",
]


@dataclass
class StageResult:
    """What ``handle`` returns: where its output went and a small JSON-able summary."""

    artifact_path: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class _StageBase:
    kind: ClassVar[str] = ""

    name: str = ""
    # `version` may be a plain attribute or a (cached) property computed lazily, e.g. when it
    # hashes installed files. It must end with `-<config_hash(...)>` (see `versioned`).
    version: str = ""
    depends_on: tuple[str, ...] = ()
    # Page stages only: dependencies that also count as satisfied when they failed for good
    # (failed at `max_attempts`, or cannot run because something they depend on did). The stage
    # then runs and finds the failure in the ledger itself.
    tolerate_failed: tuple[str, ...] = ()
    concurrency: int = 1
    models_needed: tuple[str, ...] = ()

    def version_for(self, item: Any) -> str:
        """The version this stage uses for one item (default: the stage's `version`)."""
        return self.version

    def preflight(self) -> None:
        """Before the DB is touched. Raise StageUnavailable to stop with exit 2."""

    def open(self) -> None:
        """Once, after the first item is claimed (load a model, start a worker...)."""

    def close(self) -> None:
        """Once at the end of a run in which `open` was called, also on error or Ctrl-C."""

    def on_final_failure(self, item: Any, error: str) -> None:
        """When an item fails its `max_attempts`-th attempt."""

    def on_reset(self, c: Any, page_id: str | None, pub_date: date | None, all_rows: bool) -> None:
        """Inside the `stage reset` transaction (`c`), after this stage's rows were deleted.

        Exactly one of `page_id`, `pub_date`, `all_rows` is set (e.g. to lift a quarantine the
        stage itself put on the page).
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"


class PageStage(_StageBase):
    """A stage that processes one page at a time."""

    kind: ClassVar[str] = "page"

    def handle(self, page: PageRow) -> StageResult:
        raise NotImplementedError


class DateStage(_StageBase):
    """A stage that processes one print date at a time, given all its non-quarantined pages."""

    kind: ClassVar[str] = "date"

    def handle(self, pub_date: date, pages: list[PageRow]) -> StageResult:
        raise NotImplementedError


Stage = PageStage | DateStage


def versioned(prefix: str, settings_names: Sequence[str], files: Iterable[str | Path] = ()) -> str:
    """``<prefix>-<config_hash>``: the full version string of a stage."""
    return f"{prefix}-{config_hash(settings_names, files)}"


def artifact_path(stage: str, version: str, key: str) -> Path:
    """``{work_dir}/{stage}/{version_slug}/{key}.json``."""
    return settings.work_dir / stage / version_slug(version) / f"{key}.json"
