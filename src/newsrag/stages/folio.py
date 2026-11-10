"""Stage `folio` (ticket 011): read the folio line of each page - place, state, date, page no.

Takes the page's `ocr_final` blocks whose label is in `folio_labels` plus every block lying in
the top `folio_top_fraction` of the page. Blocks with text are used as they are; the others are
re-read by indic-ocr's text model in one request per page. When those blocks give no place,
the top band itself is read in `folio_band_pieces` pieces (the layout sometimes misses the
folio strip). The model is started (under
`gpu_lock("indic-ocr")`) only when the first page with such a block arrives. Parsing is in
`newsrag.editions.folio`.
"""

from __future__ import annotations

import contextlib
import functools
import json
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.editions import folio as parse
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    artifact_path,
    versioned,
    write_json_atomic,
)

VERSION_SETTINGS = ["folio_labels", "folio_top_fraction", "folio_band_pieces"]


def geo_files() -> list[Path]:
    """The reference-list files the place lookup reads (part of the version)."""
    from newsrag.geo.resolve import PACKAGED_ALIASES

    s = config.settings
    geo_dir = Path(s.geo_dir)
    files = sorted(geo_dir.glob("*.yaml"))
    files.append(Path(s.geo_aliases_file) if s.geo_aliases_file else PACKAGED_ALIASES)
    return files


def final_artifact(page_id: str) -> Path:
    """The page's done `ocr_final` artifact, from the ledger."""
    from newsrag.ledger import db

    with db.conn() as c:
        row = db.fetch_one(
            c,
            "select artifact_path from stage_runs where page_id = %s and stage = 'ocr_final' "
            "and status = 'done' and artifact_path is not null",
            (page_id,),
        )
    if row is None:
        raise FileNotFoundError(f"no done ocr_final artifact for {page_id}")
    path = Path(row["artifact_path"])
    if not path.is_file():
        raise FileNotFoundError(f"ocr_final artifact missing: {path}")
    return path


class FolioStage(PageStage):
    name = "folio"
    depends_on = ("ocr_final",)
    concurrency = 1

    def __init__(self) -> None:
        self._stack: contextlib.ExitStack | None = None
        self._text: Any = None

    @functools.cached_property
    def version(self) -> str:  # type: ignore[override]
        return versioned("folio-v1", VERSION_SETTINGS, geo_files())

    def _start_text(self) -> Any:
        if self._text is None:
            from newsrag.models import gpu_lock
            from newsrag.stages.ocr_split import make_text

            stack = contextlib.ExitStack()
            try:
                lock = stack.enter_context(gpu_lock("indic-ocr"))
                text = make_text()
                stack.callback(text.stop)
                text.start(lock)
            except BaseException:
                stack.close()
                raise
            self._stack, self._text = stack, text
        return self._text

    def close(self) -> None:
        stack, self._stack, self._text = self._stack, None, None
        if stack is not None:
            stack.close()

    def handle(self, page: PageRow) -> StageResult:
        data = json.loads(final_artifact(page.page_id).read_text(encoding="utf-8"))
        return folio_page(data, page, self._start_text, self.version)


def folio_page(data: dict[str, Any], page: PageRow, start_text: Any, version: str) -> StageResult:
    """One page's folio artifact from its `ocr_final` artifact (`data`).

    `start_text()` gives a started `TextOcr`; it is called only when a block needs reading.
    """
    s = config.settings
    page_id = data["page_id"]
    info = data.get("image") or {}
    image = Path(info.get("path") or Path(s.images_dir) / f"{page_id}.png")
    height = float(info.get("height") or 0)
    if not height:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with Image.open(image) as img:
            height = float(img.size[1])
    blocks = sorted(data.get("blocks") or [], key=lambda b: b.get("order") or 0)
    chosen = [
        b for b in blocks if parse.is_folio_block(b, height, s.folio_labels, s.folio_top_fraction)
    ]
    texts = [(b.get("text") or "").strip() for b in chosen]
    todo = [i for i, t in enumerate(texts) if not t]
    if todo:
        if not image.is_file():
            raise FileNotFoundError(f"rendered page missing: {image}")
        text = start_text()
        read = text.read(image, [{"bbox": chosen[i]["bbox"], "type": "text"} for i in todo])
        for i, t in zip(todo, read, strict=True):
            texts[i] = (t or "").strip()
    folio_blocks = [
        {"block_id": b.get("block_id"), "bbox": b.get("bbox"), "text": t}
        for b, t in zip(chosen, texts, strict=True)
    ]
    segments = [parse.strip_noise(t).strip() for t in texts]
    segments = [seg for seg in segments if seg]
    parsed = parse.parse_segments(segments, page.pub_date)
    n_band = 0
    width = float(info.get("width") or 0)
    if parsed["place_te"] is None and s.folio_band_pieces > 0 and width > 0:
        # The layout may have missed the folio strip: read the top band itself.
        if not image.is_file():
            raise FileNotFoundError(f"rendered page missing: {image}")
        n = s.folio_band_pieces
        bottom = round(s.folio_top_fraction * height)
        boxes = [[round(width * k / n), 0, round(width * (k + 1) / n), bottom] for k in range(n)]
        band = start_text().read(image, [{"bbox": box, "type": "text"} for box in boxes])
        n_band = len(boxes)
        for k, (box, t) in enumerate(zip(boxes, band, strict=True)):
            folio_blocks.append(
                {"block_id": f"band-{k + 1}", "bbox": box, "text": (t or "").strip()}
            )
            seg = parse.strip_noise(t or "").strip()
            if seg:
                segments.append(seg)
        parsed = parse.parse_segments(segments, page.pub_date)
    artifact = {
        "page_id": page_id,
        "folio_blocks": folio_blocks,
        "folio_text": " | ".join(segments),
        **parsed,
    }
    path = write_json_atomic(artifact_path("folio", version, page_id), artifact)
    detail = {
        "n_folio_blocks": len(chosen),
        "n_reread": len(todo),
        "n_band_pieces": n_band,
        "place_te": parsed["place_te"],
        "printed_date": parsed["printed_date"],
    }
    return StageResult(str(path), detail)
