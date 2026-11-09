"""Find text blocks that span a newspaper column gap and cut them into single-column pieces
(ticket 008 FR-1, FR-2). Pure image maths; the `ocr_split` stage does the re-OCR.

Gap test on a block's crop: binarise with Otsu's threshold (dark = ink); a pixel column is
*blank* when its dark ratio over the middle 90 % of the block height is <= BLANK_MAX_RATIO; a
run of <= RULE_MAX_PX columns with a dark ratio >= RULE_MIN_RATIO (a printed column rule) counts
as blank. A gap is a run of >= `colsplit_min_gap_px` blank columns that touches neither edge of
the crop, with >= `colsplit_min_piece_px` inked columns on each side of it (between the previous
accepted gap and this one, and between this one and the crop's right edge).
"""

from __future__ import annotations

import itertools
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from newsrag import config

BAND_TRIM = 0.05  # ratio of the block height left out at the top and at the bottom
BLANK_MAX_RATIO = 0.01
RULE_MIN_RATIO = 0.8
RULE_MAX_PX = 4
MIN_TEXT_BLOCKS = 3  # pages with fewer text_labels blocks have no candidates


@dataclass(frozen=True)
class Params:
    width_factor: float
    min_gap_px: int
    min_piece_px: int
    text_labels: tuple[str, ...]

    @classmethod
    def from_settings(cls) -> Params:
        s = config.settings
        return cls(
            width_factor=s.colsplit_width_factor,
            min_gap_px=s.colsplit_min_gap_px,
            min_piece_px=s.colsplit_min_piece_px,
            text_labels=tuple(s.text_labels),
        )


# --- the gap test ---


def otsu_threshold(gray: np.ndarray) -> int:
    """Otsu's threshold of an 8-bit image: pixels <= the result are dark."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 0
    levels = np.arange(256, dtype=np.float64)
    w0 = np.cumsum(hist)
    w1 = total - w0
    sum0 = np.cumsum(hist * levels)
    m0 = np.divide(sum0, w0, out=np.zeros(256), where=w0 > 0)
    m1 = np.divide(sum0[-1] - sum0, w1, out=np.zeros(256), where=w1 > 0)
    between = w0 * w1 * (m0 - m1) ** 2
    return int(np.argmax(between))


def column_ink(gray: np.ndarray) -> np.ndarray:
    """Dark-pixel ratio of every pixel column over the middle 90 % of the crop's height."""
    height = gray.shape[0]
    if gray.size == 0 or int(gray.max()) == int(gray.min()):
        return np.zeros(gray.shape[1])  # a flat crop has no ink to tell apart
    dark = gray <= otsu_threshold(gray)
    top = int(height * BAND_TRIM)
    bottom = height - top
    band = dark[top:bottom] if bottom > top else dark
    return band.mean(axis=0)


def blank_columns(ink: np.ndarray) -> np.ndarray:
    """Blank columns, with thin printed column rules counted as blank."""
    blank = ink <= BLANK_MAX_RATIO
    rule = ink >= RULE_MIN_RATIO
    for start, end in _runs(rule):
        if end - start <= RULE_MAX_PX:
            blank[start:end] = True
    return blank


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of every run of True in a 1-D mask."""
    padded = np.concatenate(([False], mask.astype(bool), [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def find_gaps(ink: np.ndarray, min_gap_px: int, min_piece_px: int) -> list[tuple[int, int]]:
    """Interior column gaps of a crop, as [start, end) column ranges, left to right."""
    blank = blank_columns(ink)
    width = len(blank)
    inked = np.concatenate(([0], np.cumsum(~blank)))  # inked[x] = inked columns left of x
    gaps: list[tuple[int, int]] = []
    piece_start = 0
    for start, end in _runs(blank):
        if end - start < min_gap_px or start == 0 or end == width:
            continue
        left = inked[start] - inked[piece_start]
        right = inked[width] - inked[end]
        if left >= min_piece_px and right >= min_piece_px:
            gaps.append((start, end))
            piece_start = end
    return gaps


def cut_columns(gray: np.ndarray, params: Params) -> list[int]:
    """Where to cut a crop (column offsets from its left edge): the middle of every gap."""
    gaps = find_gaps(column_ink(gray), params.min_gap_px, params.min_piece_px)
    return [(start + end) // 2 for start, end in gaps]


# --- candidates and pieces ---


def _width(block: dict[str, Any]) -> float:
    x1, _, x2, _ = block["bbox"]
    return float(x2) - float(x1)


def candidates(blocks: Sequence[dict[str, Any]], params: Params) -> list[dict[str, Any]]:
    """Text blocks wider than `width_factor` × the page's median text-block width."""
    text = [b for b in blocks if b.get("label") in params.text_labels]
    if len(text) < MIN_TEXT_BLOCKS:
        return []
    limit = params.width_factor * statistics.median(_width(b) for b in text)
    return [b for b in text if _width(b) > limit]


def plan_splits(
    page_gray: np.ndarray, blocks: Sequence[dict[str, Any]], params: Params
) -> dict[str, list[list[int]]]:
    """{block_id: piece bboxes, left to right} for every candidate with an interior gap.

    `page_gray`: the page as an 8-bit grey array (height × width).
    """
    height, width = page_gray.shape
    plan: dict[str, list[list[int]]] = {}
    for block in candidates(blocks, params):
        x1, y1, x2, y2 = (round(float(v)) for v in block["bbox"])
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        cuts = [x1 + c for c in cut_columns(page_gray[y1:y2, x1:x2], params)]
        if not cuts:
            continue
        edges = [x1, *cuts, x2]
        plan[block["block_id"]] = [[a, y1, b, y2] for a, b in itertools.pairwise(edges)]
    return plan


def piece_id(block_id: str, index: int) -> str:
    """`<block_id>a`, `<block_id>b`, … (then `aa`, `ab`, … past 26 pieces)."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("a") + rest) + letters
    return f"{block_id}{letters}"


@dataclass
class SplitOutcome:
    blocks: list[dict[str, Any]]
    n_split_blocks: int
    n_pieces: int
    split_errors: int


def apply_splits(
    blocks: Sequence[dict[str, Any]],
    plan: dict[str, list[list[int]]],
    texts: dict[str, list[str]] | None,
) -> SplitOutcome:
    """Replace every planned block by its pieces, in the block's reading-order position.

    `texts`: {block_id: one text per piece}, or None when the re-OCR request failed. A block
    whose request failed or that has an empty piece stays unsplit and counts as an error.
    """
    out: list[dict[str, Any]] = []
    n_split = n_pieces = errors = 0
    for block in sorted(blocks, key=lambda b: b["order"]):
        pieces = plan.get(block["block_id"])
        if pieces is None:
            out.append(dict(block))
            continue
        got = None if texts is None else texts.get(block["block_id"])
        if got is None or len(got) != len(pieces) or any(not t.strip() for t in got):
            errors += 1
            out.append(dict(block))
            continue
        n_split += 1
        n_pieces += len(pieces)
        for i, (bbox, text) in enumerate(zip(pieces, got, strict=True)):
            out.append(
                {
                    "block_id": piece_id(block["block_id"], i),
                    "order": 0,
                    "label": block["label"],
                    "type": block["type"],
                    "bbox": bbox,
                    "conf": block["conf"],
                    "text": text.strip(),
                    "split_from": block["block_id"],
                }
            )
    for order, block in enumerate(out, 1):
        block["order"] = order
    return SplitOutcome(out, n_split, n_pieces, errors)


Reader = Callable[[list[dict[str, Any]]], list[str]]


def split_blocks(
    page_gray: np.ndarray,
    blocks: Sequence[dict[str, Any]],
    params: Params,
    read: Callable[[], Reader],
) -> SplitOutcome:
    """Plan the splits and re-OCR every piece of the page in one request.

    `read()` is called only when the page has something to split; it returns a function that
    takes [{"bbox", "type": "text"}, ...] and returns one NFC text per block. If that function
    raises, every planned block stays unsplit (one error each).
    """
    import unicodedata

    plan = plan_splits(page_gray, blocks, params)
    if not plan:
        return apply_splits(blocks, {}, {})
    reader = read()
    request = [{"bbox": bbox, "type": "text"} for pieces in plan.values() for bbox in pieces]
    try:
        flat = reader(request)
    except Exception:  # noqa: BLE001 - FR-2: keep the blocks unsplit, count the error
        return apply_splits(blocks, plan, None)
    if len(flat) != len(request):
        return apply_splits(blocks, plan, None)
    texts: dict[str, list[str]] = {}
    i = 0
    for block_id, pieces in plan.items():
        texts[block_id] = [unicodedata.normalize("NFC", t) for t in flat[i : i + len(pieces)]]
        i += len(pieces)
    return apply_splits(blocks, plan, texts)
