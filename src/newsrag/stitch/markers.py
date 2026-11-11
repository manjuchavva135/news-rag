"""Continuation markers: the regexes of `continuations.yaml` (ticket 016 FR-1) and their use.

`find_markers(text)` returns the markers in a text; `strip_markers(text)` removes the marker
phrases (and the arrow glyphs around them) from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONTINUES_ON = "a"  # "continues on page N": the article is a source part
CONTINUED_FROM = "b"  # "continued from page N / front page": the article is a continuation part

_TELUGU_DIGITS = str.maketrans("౦౧౨౩౪౫౬౭౮౯", "0123456789")
# glyphs the paper prints around a marker: "▶ మొదటి పేజీ తరువాయి", "(...9వ పేజీ తరువాయి)"
_BEFORE = re.compile(r"(?:[▶►▸▷]|\$\\blacktriangleright\$|\(\s*(?:\.{2,3}|…))\s*$")
_AFTER = re.compile(r"^\s*(?:[▶►▸▷]|\$\\blacktriangleright\$|\)|\.)")


@dataclass(frozen=True)
class Pattern:
    kind: str  # CONTINUES_ON or CONTINUED_FROM
    regex: re.Pattern[str]
    default_page: int | None = None  # used when the regex has no `page` group (or it is empty)


@dataclass(frozen=True)
class Marker:
    kind: str
    page: int | None
    text: str  # the matched phrase


def normalize(text: str) -> str:
    """Telugu digits to 0-9 (same length, so match positions stay valid)."""
    return text.translate(_TELUGU_DIGITS)


def _entry(kind: str, item: Any) -> Pattern:
    if isinstance(item, str):
        pattern, page = item, None
    else:
        pattern, page = item["pattern"], item.get("page")
    regex = re.compile(pattern)
    if page is not None and "page" in regex.groupindex:
        raise ValueError(f"continuations.yaml: {pattern!r} has a page group and a default page")
    return Pattern(kind, regex, int(page) if page is not None else None)


def load_patterns(path: str | Path) -> list[Pattern]:
    """The patterns of `continuations.yaml`: `continues_on` (a) then `continued_from` (b)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    unknown = set(data) - {"continues_on", "continued_from"}
    if unknown:
        raise ValueError(f"continuations.yaml: unknown keys {sorted(unknown)}")
    out: list[Pattern] = []
    for key, kind in (("continues_on", CONTINUES_ON), ("continued_from", CONTINUED_FROM)):
        out.extend(_entry(kind, item) for item in data.get(key) or [])
    return out


def _page_of(pattern: Pattern, match: re.Match[str]) -> int | None:
    if "page" in pattern.regex.groupindex and match.group("page"):
        return int(match.group("page"))
    return pattern.default_page


def _spans(text: str, patterns: list[Pattern]) -> list[tuple[int, int, Pattern, re.Match[str]]]:
    """Non-overlapping matches, in text order: the longest wins, then the one with a page."""
    found = []
    for pattern in patterns:
        for match in pattern.regex.finditer(text):
            has_page = _page_of(pattern, match) is not None
            found.append((match.end() - match.start(), has_page, match.start(), pattern, match))
    found.sort(key=lambda f: (-f[0], not f[1], f[2]))
    taken: list[tuple[int, int, Pattern, re.Match[str]]] = []
    for _, _, start, pattern, match in found:
        end = match.end()
        if all(end <= t[0] or start >= t[1] for t in taken):
            taken.append((start, end, pattern, match))
    return sorted(taken, key=lambda t: t[0])


def find_markers(text: str, patterns: list[Pattern]) -> list[Marker]:
    """The markers in `text`, in text order."""
    text = normalize(text or "")
    return [
        Marker(p.kind, _page_of(p, m), " ".join(m.group(0).split()))
        for _, _, p, m in _spans(text, patterns)
    ]


def strip_markers(text: str, patterns: list[Pattern]) -> str:
    """`text` without the marker phrases, the glyphs around them and the space they leave."""
    text = text or ""
    pieces: list[str] = []
    pos = 0
    for start, end, _, _ in _spans(normalize(text), patterns):
        before = _BEFORE.search(text[pos:start])
        if before:
            start = pos + before.start()
        after = _AFTER.match(text[end:])
        if after:
            end += after.end()
        pieces.append(text[pos:start])
        pos = end
    pieces.append(text[pos:])
    cleaned = "".join(pieces)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]*\n[ \t]*", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
