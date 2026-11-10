"""What the model sees for one page (ticket 012 FR-2) and where the artifacts live."""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from newsrag import config

PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "group_v1.md"
PROMPT_VERSION = 1
JPEG_QUALITY = 92


def block_line(block: dict[str, Any], max_chars: int) -> str:
    """``#<order> [<label>] (x1,y1,x2,y2) <first max_chars chars of text>``."""
    x1, y1, x2, y2 = (round(float(v)) for v in block["bbox"])
    text = " ".join(str(block.get("text") or "").split())[:max_chars]
    return f"#{block['order']} [{block.get('label')}] ({x1},{y1},{x2},{y2}) {text}".rstrip()


def block_list(blocks: Sequence[dict[str, Any]]) -> str:
    """The text list; on a page with more than `group_long_page_blocks` blocks the texts are
    cut shorter."""
    s = config.settings
    long_page = len(blocks) > s.group_long_page_blocks
    chars = s.group_long_page_chars if long_page else s.group_text_chars
    return "\n".join(block_line(b, chars) for b in blocks)


def page_size(data: dict[str, Any]) -> tuple[int, int]:
    """(width, height) in 300-DPI pixels from the artifact's image record, else from the boxes."""
    image = data.get("image") or {}
    if image.get("width") and image.get("height"):
        return int(image["width"]), int(image["height"])
    blocks = data.get("blocks") or []
    return (
        round(max((b["bbox"][2] for b in blocks), default=0)),
        round(max((b["bbox"][3] for b in blocks), default=0)),
    )


def prompt_text(width: int, height: int, blocks: Sequence[dict[str, Any]]) -> str:
    template = PROMPT_FILE.read_text(encoding="utf-8")
    return (
        template.replace("{width}", str(width))
        .replace("{height}", str(height))
        .replace("{count}", str(len(blocks)))
        .replace("{blocks}", block_list(blocks))
    )


def page_image(
    data: dict[str, Any], blocks: Sequence[dict[str, Any]], image_long_side: int | None = None
) -> bytes:
    """JPEG of the page with the `blocks` boxed and numbered, long side `image_long_side`
    (default `group_image_max_px`)."""
    from newsrag.ocr.overlay import draw_blocks

    s = config.settings
    image = (data.get("image") or {}).get("path") or Path(s.images_dir) / f"{data['page_id']}.png"
    if not Path(image).is_file():
        raise FileNotFoundError(f"rendered page missing: {image}")
    picture = draw_blocks(image, blocks, max_long_side=image_long_side or s.group_image_max_px)
    buf = io.BytesIO()
    picture.save(buf, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
    return buf.getvalue()


def messages_for(
    data: dict[str, Any], blocks: Sequence[dict[str, Any]], image_long_side: int | None = None
) -> list[dict[str, Any]]:
    """The chat messages for grouping `blocks` of the page: one user message, image then text.
    `image_long_side` overrides `group_image_max_px` (ticket 013's regroup pass)."""
    from newsrag.models.client import build_messages

    width, height = page_size(data)
    return build_messages(
        prompt_text(width, height, blocks), [page_image(data, blocks, image_long_side)]
    )


# --- artifacts of other stages --------------------------------------------------------------


def done_artifact(stage: str, page_id: str) -> Path | None:
    """The page's done artifact of `stage` according to the ledger, if the file exists."""
    from newsrag.ledger import db

    with db.conn() as c:
        row = db.fetch_one(
            c,
            "select artifact_path from stage_runs where page_id = %s and stage = %s "
            "and status = 'done' and artifact_path is not null",
            (page_id, stage),
        )
    if row is None or not Path(row["artifact_path"]).is_file():
        return None
    return Path(row["artifact_path"])


def load_ocr_final(page_id: str) -> dict[str, Any]:
    path = done_artifact("ocr_final", page_id)
    if path is None:
        raise FileNotFoundError(f"no done ocr_final artifact for {page_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def stage_row(stage: str, page_id: str) -> dict[str, Any] | None:
    """The page's ledger row of `stage` (status, attempt, detail, artifact_path, error), if any."""
    from newsrag.ledger import db

    with db.conn() as c:
        return db.fetch_one(
            c,
            "select status, attempt, detail, artifact_path, error from stage_runs "
            "where page_id = %s and stage = %s",
            (page_id, stage),
        )


def load_artifact(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
