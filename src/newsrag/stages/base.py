"""Base classes and helpers for pipeline stages.

A stage is a :class:`PageStage` (one item = one page) or a :class:`DateStage` (one item = one
print date, run once every page of that date is through its dependencies). See CLAUDE.md,
"How to add a stage".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar

from newsrag.config import settings


class StageUnavailable(Exception):
    """A stage cannot run right now (missing model, file, GPU...). `stage run` exits 2."""


@dataclass(frozen=True)
class PageRow:
    """One row of ``ledger.pages``."""

    page_id: str
    pub_date: date
    edition_code: str
    section: str
    page_num: int
    page_end: int | None
    source_path: str
    source_archive: str
    revision: int
    quarantine_reason: str | None = None
    registered_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> PageRow:
        return cls(**{f.name: row.get(f.name) for f in fields(cls)})


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


def _canonical(value: Any) -> Any:
    if hasattr(value, "get_secret_value"):
        raise ValueError("config_hash: secrets must not be part of a stage version")
    if isinstance(value, Path):
        return str(value)
    return value


def config_hash(settings_names: Sequence[str], files: Iterable[str | Path] = ()) -> str:
    """First 8 hex of sha1 over the canonical JSON of the named settings plus the files' bytes."""
    values = {name: _canonical(getattr(settings, name)) for name in settings_names}
    digest = hashlib.sha1(
        json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()
    )
    for path in files:
        digest.update(b"\0")
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()[:8]


def versioned(prefix: str, settings_names: Sequence[str], files: Iterable[str | Path] = ()) -> str:
    """``<prefix>-<config_hash>``: the full version string of a stage."""
    return f"{prefix}-{config_hash(settings_names, files)}"


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


def version_slug(version: str) -> str:
    return _SLUG_RE.sub("_", version)


def artifact_path(stage: str, version: str, key: str) -> Path:
    """``{work_dir}/{stage}/{version_slug}/{key}.json``."""
    return settings.work_dir / stage / version_slug(version) / f"{key}.json"


def write_json_atomic(path: Path, data: Any) -> Path:
    """Write JSON to ``path`` atomically (temp file in the same folder + rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
