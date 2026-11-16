"""Small helpers shared by the stages and the domain packages.

This module sits at the bottom of the package layering (`tests/test_layers.py`), next to
`config`: it imports nothing else from `newsrag`, so any package may use it without importing the
stage framework. `newsrag.stages.base` re-exports these names for the stage modules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime
from pathlib import Path
from typing import Any

from newsrag import config


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


def _canonical(value: Any) -> Any:
    if hasattr(value, "get_secret_value"):
        raise ValueError("config_hash: secrets must not be part of a stage version")
    if isinstance(value, Path):
        return str(value)
    return value


def config_hash(settings_names: Sequence[str], files: Iterable[str | Path] = ()) -> str:
    """First 8 hex of sha1 over the canonical JSON of the named settings plus the files' bytes."""
    settings = config.settings
    values = {name: _canonical(getattr(settings, name)) for name in settings_names}
    digest = hashlib.sha1(
        json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()
    )
    for path in files:
        digest.update(b"\0")
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()[:8]


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


def version_slug(version: str) -> str:
    return _SLUG_RE.sub("_", version)


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


def read_pages_file(path: str | Path) -> list[str]:
    """One page_id per line; blank lines and `#` lines are skipped."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out
