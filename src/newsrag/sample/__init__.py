"""The dev sample (ticket 005 FR-4, FR-5): 20 fixed, varied pages for quick checks.

``data/dev-sample.txt`` (``settings.dev_sample_file``, committed) holds one page_id per line,
sorted, after optional ``# short: <year> <stratum>`` lines. The strata follow ticket 005 D-5.
"""

from __future__ import annotations

import os
import random
import re
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from newsrag.config import settings
from newsrag.ledger import db
from newsrag.stages.base import PageRow

_SINGLE = re.compile(r"[A-Za-z]")
_LETTERS = re.compile(r"[A-Za-z]{2,}")


def _front(p: PageRow) -> bool:
    return p.page_num == 1


def _single(p: PageRow) -> bool:
    return bool(_SINGLE.fullmatch(p.edition_code)) and p.page_num != 1


def _combined(p: PageRow) -> bool:
    return (
        bool(_LETTERS.fullmatch(p.edition_code))
        and p.edition_code.upper() != "ALL"
        and p.page_num != 1
    )


def _all(p: PageRow) -> bool:
    return p.edition_code.upper() == "ALL"


def _spread(p: PageRow) -> bool:
    return p.page_end is not None


PREDICATES: dict[str, Callable[[PageRow], bool]] = {
    "front": _front,
    "single": _single,
    "combined": _combined,
    "all": _all,
    "spread": _spread,
}

# year -> [(stratum, count)], filled in this order (ticket 005 FR-4 / D-5).
STRATA: dict[int, list[tuple[str, int]]] = {
    2024: [("front", 3), ("single", 3), ("combined", 2)],
    2025: [("front", 4), ("single", 3), ("combined", 3), ("all", 1), ("spread", 1)],
}


class SampleError(Exception):
    """`sample build` / `sample show` refuses; the message is printed and the exit code is 1."""


def choose(pages: Sequence[PageRow], seed: int | None = None) -> tuple[list[str], list[str]]:
    """(sorted page_ids, `# short:` lines) for the given candidate pages."""
    rng = random.Random(settings.dev_sample_seed if seed is None else seed)
    chosen: set[str] = set()
    short: list[str] = []
    for year, strata in STRATA.items():
        pool = sorted((p for p in pages if p.pub_date.year == year), key=lambda p: p.page_id)
        for stratum, count in strata:
            match = PREDICATES[stratum]
            candidates = [p.page_id for p in pool if p.page_id not in chosen and match(p)]
            picked = rng.sample(candidates, min(count, len(candidates)))
            if len(picked) < count:
                short.append(f"# short: {year} {stratum}")
                taken = chosen | set(picked)
                rest = [p.page_id for p in pool if p.page_id not in taken]
                picked += rng.sample(rest, min(count - len(picked), len(rest)))
            chosen.update(picked)
    return sorted(chosen), short


def _shown(path: Path) -> str:
    try:
        return str(path.relative_to(settings.repo_root))
    except ValueError:
        return str(path)


def _candidates() -> list[PageRow]:
    from newsrag.stages.registry import REGISTRY
    from newsrag.stages.runner import list_eligible

    render = REGISTRY["render"]
    with db.conn() as c:
        running = c.execute(
            "select count(*) from stage_runs r join pages p using (page_id) "
            "where r.stage = 'render' and r.status = 'running' and p.quarantine_reason is null"
        ).fetchone()[0]
    if running or list_eligible(render)[0]:
        raise SampleError("render not finished")
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            "select p.* from pages p join stage_runs r using (page_id) "
            "where r.stage = 'render' and r.status = 'done' and p.quarantine_reason is null "
            "order by p.page_id",
        )
    return [PageRow.from_row(r) for r in rows]


def build(force: bool = False) -> tuple[Path, list[str], list[str]]:
    """Write the dev sample file; return (path, page_ids, short lines)."""
    path = Path(settings.dev_sample_file)
    if path.exists() and not force:
        raise SampleError(f"{_shown(path)} exists (use --force)")
    ids, short = choose(_candidates())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in [*short, *ids]))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path, ids, short


def show_lines() -> list[str]:
    """`<page_id>\\t<image path>\\tok|missing` for every sample page."""
    from newsrag.stages.runner import read_pages_file

    path = Path(settings.dev_sample_file)
    if not path.exists():
        raise SampleError(f"{_shown(path)} not found: run  uv run newsrag sample build")
    lines = []
    for page_id in read_pages_file(path):
        image = Path(settings.images_dir) / f"{page_id}.png"
        lines.append(f"{page_id}\t{image}\t{'ok' if image.is_file() else 'missing'}")
    return lines
