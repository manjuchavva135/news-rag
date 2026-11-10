"""Articles drawn on the page image (ticket 012 FR-7, FR-8).

Each article's blocks are filled in one translucent colour; its number and kind are written at
the top-left of its box, and (with `mark_flags`) an article with flags gets a red outline.
Blocks left unassigned get a thin grey outline.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

FILL_ALPHA = 90
FONT_PX_300DPI = 40
FLAG_COLOUR = (220, 0, 0)
UNASSIGNED_COLOUR = (110, 110, 110)
PALETTE = (
    (31, 119, 180),
    (255, 127, 14),
    (44, 160, 44),
    (148, 103, 189),
    (140, 86, 75),
    (227, 119, 194),
    (23, 190, 207),
    (188, 189, 34),
    (0, 100, 0),
    (128, 0, 128),
)


def article_number(article: dict[str, Any]) -> str:
    """``03`` from ``<page_id>#a03``."""
    return str(article["article_id"]).rsplit("#a", 1)[-1]


def draw_articles(
    image_path: str | Path,
    artifact: dict[str, Any],
    max_long_side: int | None = None,
    mark_flags: bool = True,
    kinds: Collection[str] | None = None,
    blocks: Sequence[dict[str, Any]] | None = None,
) -> Any:
    """The page image with the articles of a `group` artifact drawn on it (a PIL.Image).

    `kinds` limits the drawing to articles of those kinds. The blocks' boxes come from `blocks`
    (the page's `ocr_final` blocks) or, when not given, from the page's `ocr_final` artifact.
    """
    from PIL import Image, ImageDraw, ImageFont

    if blocks is None:
        from newsrag.grouping.input import load_ocr_final

        blocks = load_ocr_final(artifact["page_id"])["blocks"]
    boxes = {b["block_id"]: b["bbox"] for b in blocks}

    Image.MAX_IMAGE_PIXELS = None  # 300 DPI newspaper pages exceed PIL's bomb guard
    with Image.open(image_path) as img:
        page = img.convert("RGB")
    scale = 1.0
    if max_long_side and max(page.size) > max_long_side:
        scale = max_long_side / max(page.size)
        page = page.resize(
            (max(1, round(page.width * scale)), max(1, round(page.height * scale))),
            Image.LANCZOS,
        )
    layer = Image.new("RGBA", page.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = ImageFont.load_default(size=max(10, round(FONT_PX_300DPI * scale)))
    outline = max(2, round(6 * scale))

    def scaled(box: Sequence[float]) -> tuple[float, ...]:
        return tuple(float(v) * scale for v in box)

    if kinds is None:
        for block_id in artifact.get("unassigned") or []:
            if block_id in boxes:
                draw.rectangle(scaled(boxes[block_id]), outline=(*UNASSIGNED_COLOUR, 255), width=1)

    labels = []
    for i, article in enumerate(artifact.get("articles") or []):
        if kinds is not None and article["kind"] not in kinds:
            continue
        colour = PALETTE[i % len(PALETTE)]
        for entry in article["blocks"]:
            if entry["block_id"] in boxes:
                draw.rectangle(
                    scaled(boxes[entry["block_id"]]),
                    fill=(*colour, FILL_ALPHA),
                    outline=(*colour, 255),
                    width=1,
                )
        x1, y1, x2, y2 = scaled(article["bbox_union"])
        if mark_flags and article.get("flags"):
            draw.rectangle((x1, y1, x2, y2), outline=(*FLAG_COLOUR, 255), width=outline)
        labels.append((x1, y1, f"{article_number(article)} {article['kind']}", colour))

    page = Image.alpha_composite(page.convert("RGBA"), layer)
    draw = ImageDraw.Draw(page)
    for x1, y1, text, colour in labels:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        pad = 3
        draw.rectangle(
            (x1, y1, x1 + (right - left) + 2 * pad, y1 + (bottom - top) + 2 * pad),
            fill=(*colour, 255),
        )
        draw.text((x1 + pad - left, y1 + pad - top), text, fill=(255, 255, 255, 255), font=font)
    return page.convert("RGB")
