"""Cutting a text into windows that fit a token budget (ticket 023 FR-3, FR-4).

Pure functions. `Chunker` knows how to count the tokens of the *embedded string* of a chunk,
`doc_prefix + prefix + "\\n" + text` (grill A-42), so that every window is measured exactly as
ticket 024 will embed it. The tokenizer is injected as a plain `count(string) -> int`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

NO_HEADLINE = "(శీర్షిక లేదు)"
PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n\s*")
# `?`, `!`, `।` and a `.` that does not sit between two digits end a sentence (with closers).
SENTENCE_END = re.compile(r"""(?:[?!।]|(?<!\d)\.|\.(?!\d))+[\"'”’)\]]*""")
# Telugu and Latin text both allow `|` rows; a separator row holds only these characters.
_SEPARATOR_CHARS = set("|-: \t")


def split_sentences(text: str) -> list[str]:
    """The sentences of `text` (end mark kept), stripped, never empty."""
    out: list[str] = []
    start = 0
    for match in SENTENCE_END.finditer(text):
        sentence = text[start : match.end()].strip()
        if sentence:
            out.append(sentence)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in PARAGRAPH_BREAK.split(text.strip()) if p.strip()]


def is_separator_row(line: str) -> bool:
    line = line.strip()
    return "-" in line and "|" in line and set(line) <= _SEPARATOR_CHARS


@dataclass(frozen=True)
class Piece:
    """A paragraph, or a sentence of a paragraph that is too long; `sep` joins it to the piece
    before it in a window."""

    text: str
    sep: str


def join(pieces: list[Piece]) -> str:
    out = pieces[0].text
    for piece in pieces[1:]:
        out += piece.sep + piece.text
    return out


class Chunker:
    """`count(embedded_string)` is the token count of one string, special tokens included."""

    def __init__(self, count: Callable[[str], int], doc_prefix: str = "", max_tokens: int = 512):
        self.count = count
        self.doc_prefix = doc_prefix
        self.max_tokens = max_tokens

    # --- the embedded string ------------------------------------------------------------------

    def embedded(self, prefix: str, text: str) -> str:
        return f"{self.doc_prefix}{prefix}\n{text}"

    def n_tokens(self, prefix: str, text: str) -> int:
        return self.count(self.embedded(prefix, text))

    def fit_headline(self, headline: str, rest: str) -> str:
        """The headline cut so that the prefix alone takes at most half of the budget.

        `rest` is what follows the headline in the prefix (` | 03-04-2026 | 2 printings`)."""
        half = self.max_tokens // 2

        def fits(h: str) -> bool:
            return self.n_tokens(f"{h}{rest}", "") <= half

        if fits(headline):
            return headline
        cut = len(_longest_prefix(headline, fits))
        while 1 < cut < len(headline) and unicodedata.category(headline[cut]).startswith("M"):
            cut -= 1  # not between a letter and its vowel sign
        return headline[:cut].rstrip() or headline[:1]

    # --- splitting -----------------------------------------------------------------------------

    def split_text(self, prefix: str, text: str) -> list[str]:
        """Windows of paragraph text (FR-4): one string if it fits, else overlapping windows."""
        text = text.strip()
        if self.n_tokens(prefix, text) <= self.max_tokens:
            return [text]

        def fits(t: str) -> bool:
            return self.n_tokens(prefix, t) <= self.max_tokens

        pieces = self._pieces(text, fits)
        windows: list[list[Piece]] = []
        i = 0
        last: Piece | None = None  # the last piece of the previous window
        while i < len(pieces):
            window: list[Piece] = []
            if last is not None:
                for overlap in self._overlaps(last):
                    if fits(join([overlap, pieces[i]])):
                        window.append(overlap)
                        break
            window.append(pieces[i])
            i += 1
            while i < len(pieces) and fits(join([*window, pieces[i]])):
                window.append(pieces[i])
                i += 1
            windows.append(window)
            last = window[-1]
        return [join(w) for w in windows]

    def split_table(self, prefix: str, text: str) -> list[str]:
        """Windows of table markdown by rows, the header and separator row in every window."""
        text = text.strip()
        if self.n_tokens(prefix, text) <= self.max_tokens:
            return [text]
        # several tables (a `table_only` story) are separated by blank lines: each keeps its header
        windows: list[str] = []
        for block in PARAGRAPH_BREAK.split(text):
            if block.strip():
                windows.extend(self._split_rows(prefix, block))
        return windows

    def _split_rows(self, prefix: str, block: str) -> list[str]:
        lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
        header: list[str] = []
        if len(lines) >= 2 and is_separator_row(lines[1]):
            header, lines = lines[:2], lines[2:]

        def fits(t: str) -> bool:
            return self.n_tokens(prefix, t) <= self.max_tokens

        head = "\n".join(header)
        if header and not fits(head + "\nx"):
            header, head = [], ""  # a header that leaves no room is not repeated

        def with_head(body: str) -> str:
            return f"{head}\n{body}" if header else body

        rows: list[str] = []
        for line in lines:
            if fits(with_head(line)):
                rows.append(line)
            else:
                rows.extend(hard_split(line, lambda t: fits(with_head(t))))
        windows: list[str] = []
        current: list[str] = []
        for row in rows:
            if current and not fits(with_head("\n".join([*current, row]))):
                windows.append(with_head("\n".join(current)))
                current = []
            current.append(row)
        if current:
            windows.append(with_head("\n".join(current)))
        return windows

    # --- helpers -------------------------------------------------------------------------------

    @staticmethod
    def _overlaps(last: Piece) -> list[Piece]:
        """What a new window may start with: the last piece, then its last sentence."""
        out = [Piece(last.text, "\n\n")]
        sentences = split_sentences(last.text)
        if len(sentences) > 1:
            out.append(Piece(sentences[-1], "\n\n"))
        return out

    @staticmethod
    def _pieces(text: str, fits: Callable[[str], bool]) -> list[Piece]:
        pieces: list[Piece] = []
        for paragraph in split_paragraphs(text):
            if fits(paragraph):
                pieces.append(Piece(paragraph, "\n\n"))
                continue
            first = True
            for sentence in split_sentences(paragraph):
                parts = [sentence] if fits(sentence) else hard_split(sentence, fits)
                for part in parts:
                    pieces.append(Piece(part, "\n\n" if first else " "))
                    first = False
        return pieces


def _longest_prefix(text: str, fits: Callable[[str], bool]) -> str:
    """The longest prefix of `text` that fits (binary search; `fits` is monotone enough)."""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(text[:mid]):
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def hard_split(text: str, fits: Callable[[str], bool]) -> list[str]:
    """Split a text that is too long on its own into parts that each fit, preferably at a space,
    never between a letter and its vowel sign."""
    parts: list[str] = []
    rest = text.strip()
    while rest:
        if fits(rest):
            parts.append(rest)
            break
        cut = len(_longest_prefix(rest, fits))
        if cut < 1:
            raise ValueError("a single character does not fit the token budget")
        space = max(rest.rfind(" ", 0, cut), rest.rfind("\t", 0, cut))
        if space > 0:
            cut = space
        else:
            while 1 < cut < len(rest) and unicodedata.category(rest[cut]).startswith("M"):
                cut -= 1
        part = rest[:cut].strip()
        if part:
            parts.append(part)
        rest = rest[cut:].strip()
    return parts
