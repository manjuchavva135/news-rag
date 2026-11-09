"""The 10 sanity-check pages (ticket 009 FR-1, D-5) and the file that lists them.

``data/ocr-check.txt`` (``settings.ocr_check_file``, committed) holds optional
``# short: <year> <slot>`` lines, then one page_id per line, sorted. The pages come from the dev
sample: each slot takes the first not-yet-chosen sorted dev-sample page of its year that matches
it, judged by the ``ledger.pages`` columns (never the id string).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from newsrag.config import settings
from newsrag.sample import PREDICATES as SAMPLE_PREDICATES
from newsrag.stages.base import PageRow


def _front(p: PageRow) -> bool:
    return p.section == "main" and p.page_num == 1


# Slot name -> test. "front" is a `main_01` page; the rest are the dev-sample strata (005 D-5).
PREDICATES: dict[str, Callable[[PageRow], bool]] = {
    "front": _front,
    "single": SAMPLE_PREDICATES["single"],  # single-letter edition, page != 1
    "combined": SAMPLE_PREDICATES["combined"],  # 2+ letter edition (not ALL), page != 1
    "all": SAMPLE_PREDICATES["all"],
    "spread": SAMPLE_PREDICATES["spread"],  # page_end set
}

# year -> slots, filled in this order (ticket 009 D-5: the corpus is Dec 2024 + 2025).
SLOTS: dict[int, list[str]] = {
    2024: ["front", "front", "single", "combined"],
    2025: ["front", "front", "all", "spread", "single", "combined"],
}


class CheckError(Exception):
    """An `ocr check` command refuses; the message is printed and the exit code is 1."""


def choose(dev_ids: Sequence[str], rows: Mapping[str, PageRow]) -> tuple[list[str], list[str]]:
    """(sorted page_ids, `# short:` lines) for the dev-sample ids and their ledger rows."""
    unknown = [i for i in dev_ids if i not in rows]
    if unknown:
        raise CheckError(f"not in ledger.pages: {', '.join(unknown)}")
    chosen: list[str] = []
    short: list[str] = []
    for year, slots in SLOTS.items():
        pool = sorted(i for i in dev_ids if rows[i].pub_date.year == year)
        for slot in slots:
            match = PREDICATES[slot]
            pick = next((i for i in pool if i not in chosen and match(rows[i])), None)
            if pick is None:
                short.append(f"# short: {year} {slot}")
                pick = next((i for i in pool if i not in chosen), None)
            if pick is not None:
                chosen.append(pick)
    return sorted(chosen), short


def read_ids(path: str | Path) -> list[str]:
    """One page_id per line; blank lines and `#` lines are skipped."""
    from newsrag.stages.runner import read_pages_file

    return read_pages_file(path)


def check_ids() -> list[str]:
    """The page ids of ``data/ocr-check.txt``; CheckError when it is missing."""
    path = Path(settings.ocr_check_file)
    if not path.is_file():
        raise CheckError(f"{_shown(path)} missing: run `newsrag ocr check build` first")
    return read_ids(path)


def _page_rows(page_ids: Iterable[str]) -> dict[str, PageRow]:
    from newsrag.ledger import db

    with db.conn() as c:
        rows = db.fetch_all(c, "select * from pages where page_id = any(%s)", (list(page_ids),))
    return {r["page_id"]: PageRow.from_row(r) for r in rows}


def artifact_paths(page_ids: Sequence[str]) -> dict[str, Path]:
    """page_id -> its done `ocr_check_stage` artifact, for the pages that have one on disk."""
    from newsrag.ledger import db

    with db.conn() as c:
        rows = db.fetch_all(
            c,
            "select page_id, artifact_path from stage_runs where stage = %s "
            "and page_id = any(%s) and status = 'done' and artifact_path is not null",
            (settings.ocr_check_stage, list(page_ids)),
        )
    found = {r["page_id"]: Path(r["artifact_path"]) for r in rows}
    return {p: path for p, path in found.items() if path.is_file()}


def require_artifacts(page_ids: Sequence[str]) -> dict[str, Path]:
    """artifact_paths(), or CheckError naming every page without one."""
    found = artifact_paths(page_ids)
    missing = [p for p in page_ids if p not in found]
    if missing:
        raise CheckError(f"missing {settings.ocr_check_stage} for: {', '.join(missing)}")
    return found


def _shown(path: Path) -> str:
    try:
        return str(Path(path).relative_to(settings.repo_root))
    except ValueError:
        return str(path)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def build(force: bool = False) -> tuple[Path, list[str], list[str]]:
    """Write data/ocr-check.txt; return (path, page_ids, short lines). Nothing is written when
    a chosen page has no artifact."""
    path = Path(settings.ocr_check_file)
    if path.exists() and not force:
        raise CheckError(f"{_shown(path)} exists (use --force)")
    dev_ids = read_ids(settings.dev_sample_file)
    ids, short = choose(dev_ids, _page_rows(dev_ids))
    require_artifacts(ids)
    _write_atomic(path, "".join(f"{line}\n" for line in [*short, *ids]))
    return path, ids, short
