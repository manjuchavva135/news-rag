"""Kinds of registered pages, by edition code and page number (ticket 005 D-5): the strata of the
dev sample (`newsrag.sample`) and of the OCR check page (`newsrag.ocr.check`)."""

from __future__ import annotations

import re
from collections.abc import Callable

from newsrag.core import PageRow

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
