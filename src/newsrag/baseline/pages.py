"""Old page id -> new page id(s) (ticket 027 FR-3, FR-5).

An old page id is `<state>-<edition>-<YYYY-MM-DD>#p<NN>` (main) or `#<section><NN>` (e.g.
`#fea02`). It maps to every `ledger.pages` row of that date, edition (upper-cased) and section
with `page_num = NN`, whatever its `page_end`: old `#p08` of a spread maps to `..._main_08-09`,
and to both `..._main_08` and `..._main_08-09` when both exist.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from newsrag import config

OLD_PAGE_ID = re.compile(
    r"^(?P<state>[a-z]+)-(?P<edition>[a-z0-9]+)-(?P<date>\d{4}-\d{2}-\d{2})"
    r"#(?P<section>[a-z]+?)(?P<num>\d+)$"
)
OLD_SECTIONS = {"p": "main"}  # any other old section letter is the new section name as is
CSV_FIELDS = ("old_page_id", "new_page_id", "found_in_new", "new_page_ids")
LIST_SEP = ";"


@dataclass(frozen=True)
class OldPage:
    date: date
    edition: str  # upper-cased, as the new edition codes
    section: str  # new section name: main | fea | ...
    num: int


def parse_old_page_id(page_id: str) -> OldPage | None:
    m = OLD_PAGE_ID.match(page_id.strip())
    if not m:
        return None
    try:
        day = date.fromisoformat(m["date"])
    except ValueError:
        return None
    section = OLD_SECTIONS.get(m["section"], m["section"])
    return OldPage(day, m["edition"].upper(), section, int(m["num"]))


def map_pages(
    old_ids: Iterable[str], new_pages: Iterable[Mapping[str, Any]]
) -> dict[str, list[str]]:
    """{old page id: sorted new page ids} (empty list when unmapped).

    `new_pages` rows have `page_id`, `pub_date`, `edition_code`, `section`, `page_num`."""
    index: dict[tuple[date, str, str, int], list[str]] = {}
    for row in new_pages:
        day = row["pub_date"]
        if not isinstance(day, date):
            day = date.fromisoformat(str(day))
        key = (day, str(row["edition_code"]).upper(), str(row["section"]), int(row["page_num"]))
        index.setdefault(key, []).append(str(row["page_id"]))
    out: dict[str, list[str]] = {}
    for old in old_ids:
        page = parse_old_page_id(old)
        found = [] if page is None else index.get((page.date, page.edition, page.section, page.num))
        out[old] = sorted(found or [])
    return out


def new_pages_for(dates: Iterable[date]) -> list[dict[str, Any]]:
    """The `ledger.pages` rows of these print dates."""
    from newsrag.ledger import db as ldb

    days = sorted(set(dates))
    if not days:
        return []
    with ldb.conn() as c:
        return ldb.fetch_all(
            c,
            "select page_id, pub_date, edition_code, section, page_num from pages "
            "where pub_date = any(%s)",
            (days,),
        )


def build_page_map(old_ids: Iterable[str], out: Path | None = None) -> dict[str, list[str]]:
    """Map the old page ids against the ledger and write the CSV."""
    old_ids = list(old_ids)
    dates = [p.date for p in map(parse_old_page_id, old_ids) if p is not None]
    mapping = map_pages(old_ids, new_pages_for(dates))
    write_page_map(mapping, out)
    return mapping


def write_page_map(mapping: Mapping[str, list[str]], out: Path | None = None) -> Path:
    out = Path(out or config.settings.baseline_page_map_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        for old in sorted(mapping):
            new = mapping[old]
            writer.writerow([old, new[0] if new else "", bool(new), LIST_SEP.join(new)])
    tmp.replace(out)
    return out


def read_page_map(path: Path | None = None) -> dict[str, list[str]]:
    """{old page id: new page ids} from the CSV (FileNotFoundError when it was never written)."""
    path = Path(path or config.settings.baseline_page_map_file)
    with path.open(encoding="utf-8", newline="") as f:
        return {
            row["old_page_id"]: [p for p in (row.get("new_page_ids") or "").split(LIST_SEP) if p]
            for row in csv.DictReader(f)
        }


def mapped_new_pages(mapping: Mapping[str, list[str]]) -> set[str]:
    """Every new page id some old page maps to."""
    return {new for news in mapping.values() for new in news}


def reverse(mapping: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """{new page id: old page ids}."""
    out: dict[str, list[str]] = {}
    for old, news in mapping.items():
        for new in news:
            out.setdefault(new, []).append(old)
    return out


def summary(mapping: Mapping[str, list[str]]) -> str:
    found = sum(1 for news in mapping.values() if news)
    total = len(mapping)
    pct = 100 * found / total if total else 0.0
    return f"old pages={total} found_in_new={found} ({pct:.1f}%)"
