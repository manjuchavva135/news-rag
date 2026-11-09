"""Numbered-box overlay of a page's OCR blocks (ticket 008 FR-4).

Every block box is drawn with a 3 px outline, and its `order` number in a filled label at the
box's top-left corner. The label font is 32 px high on the 300 DPI page and scales with the
image when `max_long_side` shrinks it. The sanity-check page (009) and the grouping prompt (012)
use this image.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

OUTLINE_PX = 3
FONT_PX_300DPI = 32
LABEL_PAD_PX = 4

# Outline / label colour per block type (types from labels.yaml groups).
COLOURS = {
    "text": (0, 102, 204),
    "title": (204, 0, 0),
    "header_footer": (128, 128, 128),
    "page_number": (128, 128, 128),
    "caption": (0, 153, 76),
    "table": (153, 51, 204),
    "figure": (230, 126, 0),
    "chart": (230, 126, 0),
    "advertisement": (153, 102, 51),
}
DEFAULT_COLOUR = (64, 64, 64)


def draw_blocks(
    image_path: str | Path, blocks: Sequence[dict[str, Any]], max_long_side: int | None = None
) -> Any:
    """The page image with every block's box and order number drawn on it (a PIL.Image)."""
    from PIL import Image, ImageDraw, ImageFont

    Image.MAX_IMAGE_PIXELS = None  # 300 DPI newspaper pages exceed PIL's bomb guard
    with Image.open(image_path) as img:
        page = img.convert("RGB")
    scale = 1.0
    long_side = max(page.size)
    if max_long_side and long_side > max_long_side:
        scale = max_long_side / long_side
        size = (max(1, round(page.width * scale)), max(1, round(page.height * scale)))
        page = page.resize(size, Image.LANCZOS)

    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=max(8, round(FONT_PX_300DPI * scale)))
    pad = max(1, round(LABEL_PAD_PX * scale))
    for block in blocks:
        colour = COLOURS.get(str(block.get("type")), DEFAULT_COLOUR)
        x1, y1, x2, y2 = (float(v) * scale for v in block["bbox"])
        draw.rectangle((x1, y1, x2, y2), outline=colour, width=OUTLINE_PX)
        label = str(block.get("order", ""))
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        box = (x1, y1, x1 + (right - left) + 2 * pad, y1 + (bottom - top) + 2 * pad)
        draw.rectangle(box, fill=colour)
        draw.text((x1 + pad - left, y1 + pad - top), label, fill=(255, 255, 255), font=font)
    return page
