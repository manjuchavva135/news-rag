"""Numbers of `newsrag aliases stats` (ticket 022) as a dict, for the command and the quality
report (ticket 030)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def stats_path() -> Path:
    from newsrag.aliases import version_dir

    return version_dir() / "stats.json"


def stats() -> dict[str, Any] | None:
    """The statistics of the last alias build, None when there is no build yet."""
    path = stats_path()
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
