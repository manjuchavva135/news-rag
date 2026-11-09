"""Parse an archive PDF path into a page (ticket 004, FR-3 and FR-5).

``parse(path)`` returns a :class:`ParsedPage` or a :class:`Rejected` with the first reason that
applies, in this order: ``no_date``, ``test_or_dummy``, ``bad_filename``, ``bad_date``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}

DATE_DIR = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
MONTH_DIR = re.compile(r"([A-Za-z]+)-?(\d{4})")
WEEK_DIR = re.compile(r"(\d{2})-(\d{2})")
FILENAME = re.compile(
    r"^(?P<day>\d{2})\s*(?P<edition>[A-Za-z]+)\s*[-_]\s*(?P<section>MAIN|MAI|FEA)\s*_?\s*"
    r"(?P<page>\d{1,2})(?:-(?P<page_end>\d{2}))?(?P<rest>.*)\.pdf$",
    re.IGNORECASE,
)
TEST_OR_DUMMY = re.compile(r"dummy|test", re.IGNORECASE)
EPAPER = re.compile(r"e[\s_-]*p[ae]p[ae]r", re.IGNORECASE)
WEB = re.compile(r"for\s*net", re.IGNORECASE)
VK = re.compile(r"\(\s*vk\s*\)", re.IGNORECASE)
SPLUS = re.compile(r"splus", re.IGNORECASE)
REVISION_NOISE = re.compile(r"vk|splus|[._\-()\s]", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedPage:
    date: date
    edition_code: str
    section: str  # main | fea | splus
    page: int
    page_end: int | None
    revision: int
    copy_kind: str  # print | epaper | web
    path: str
    day_mismatch: bool = False  # the date folder and the filename's day disagree (folder wins)

    @property
    def page_id(self) -> str:
        return page_id(self.date, self.edition_code, self.section, self.page, self.page_end)

    @property
    def group_key(self) -> tuple:
        return (self.date, self.edition_code, self.section, self.page, self.page_end)


@dataclass(frozen=True)
class Rejected:
    reason: str  # no_date | test_or_dummy | bad_filename | bad_date
    path: str


PARSE_REASONS = ("no_date", "test_or_dummy", "bad_filename", "bad_date")


def page_id(d: date, edition_code: str, section: str, page: int, page_end: int | None) -> str:
    """``{YYYY-MM-DD}_{EDITION}_{section}_{NN}`` plus ``-{MM}`` for a spread."""
    pid = f"{d.isoformat()}_{edition_code}_{section}_{page:02d}"
    if page_end is not None:
        pid += f"-{page_end:02d}"
    return pid


def clean_name(name: str) -> str:
    """Drop control/format characters and odd spaces (U+00AD, U+2008, ...), strip the ends."""
    kept = []
    for ch in name:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf"):
            continue
        if ch != " " and (cat.startswith("Z") or ch.isspace()):
            continue
        kept.append(ch)
    return "".join(kept).strip()


def revision_of(rest: str) -> int:
    """Length of the leading run of upper-case R once tags and separators are removed."""
    stripped = REVISION_NOISE.sub("", rest)
    return len(stripped) - len(stripped.lstrip("R"))


def copy_kind_of(rest: str) -> str:
    if EPAPER.search(rest):
        return "epaper"
    if WEB.search(rest):
        return "web"
    return "print"


def _last_match(pattern: re.Pattern[str], segments: tuple[str, ...]) -> re.Match[str] | None:
    for seg in reversed(segments):
        m = pattern.fullmatch(seg)
        if m:
            if pattern is MONTH_DIR and m.group(1).lower() not in MONTHS:
                continue
            return m
    return None


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse(path: str | Path) -> ParsedPage | Rejected:
    path = Path(path)
    text = str(path)
    folders = path.parent.parts
    date_dir = _last_match(DATE_DIR, folders)
    month_dir = _last_match(MONTH_DIR, folders) if date_dir is None else None
    if date_dir is None and month_dir is None:
        return Rejected("no_date", text)

    name = clean_name(path.name)
    if TEST_OR_DUMMY.search(name):
        return Rejected("test_or_dummy", text)
    m = FILENAME.match(name)
    if m is None:
        return Rejected("bad_filename", text)

    file_day = int(m["day"])
    day_mismatch = False
    if date_dir is not None:
        day, month, year = (int(g) for g in date_dir.groups())
        day_mismatch = file_day != day
    else:
        day, month, year = file_day, MONTHS[month_dir.group(1).lower()], int(month_dir.group(2))
        week = _last_match(WEEK_DIR, folders)
        if week is not None and day < int(week.group(1)):
            month += 1
            if month == 13:
                month, year = 1, year + 1
    pub_date = _make_date(year, month, day)
    if pub_date is None:
        return Rejected("bad_date", text)

    rest = m["rest"]
    edition = m["edition"].upper()
    if VK.search(rest):
        edition += "-VK"
    section = m["section"].upper()
    section = "fea" if section == "FEA" else "main"
    if SPLUS.search(rest):
        section = "splus"
    return ParsedPage(
        date=pub_date,
        edition_code=edition,
        section=section,
        page=int(m["page"]),
        page_end=int(m["page_end"]) if m["page_end"] else None,
        revision=revision_of(rest),
        copy_kind=copy_kind_of(rest),
        path=text,
        day_mismatch=day_mismatch,
    )
