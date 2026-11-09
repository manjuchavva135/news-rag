"""Stage `ocr_split` (ticket 008): cut text blocks that span a column gap and re-OCR the pieces.

Reads the page's `ocr` artifact. Candidates and the gap test are in `newsrag.ocr.colsplit`.
All pieces of a page are re-read by indic-ocr's text model in one request. The model is started
(under `gpu_lock("indic-ocr")`) only when the first page with something to split arrives;
pages without candidates are copied without touching the GPU. The artifact has the `ocr`
artifact's schema; pieces also carry `split_from`.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.ocr import colsplit
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    StageUnavailable,
    artifact_path,
    versioned,
    write_json_atomic,
)

NOT_INSTALLED = "OCR models not installed: run uv run newsrag ocr install"
VERSION_SETTINGS = [
    "colsplit_width_factor",
    "colsplit_min_gap_px",
    "colsplit_min_piece_px",
    "text_labels",
]


def ocr_artifact(page_id: str) -> Path:
    """The page's done `ocr` artifact, from the ledger."""
    from newsrag.ledger import db

    with db.conn() as c:
        row = db.fetch_one(
            c,
            "select artifact_path from stage_runs where page_id = %s and stage = 'ocr' "
            "and status = 'done' and artifact_path is not null",
            (page_id,),
        )
    if row is None:
        raise FileNotFoundError(f"no done ocr artifact for {page_id}")
    path = Path(row["artifact_path"])
    if not path.is_file():
        raise FileNotFoundError(f"ocr artifact missing: {path}")
    return path


def make_text() -> Any:
    """indic-ocr's text model (a `TextOcr`, not started); tests replace this with a fake."""
    from newsrag.ocr.install import indic_snapshot
    from newsrag.ocr.text import TextOcr, indic_profile, tokenizer_dir

    repo = indic_snapshot()
    if repo is None:
        raise StageUnavailable(NOT_INSTALLED)
    return TextOcr(indic_profile(repo / "weights" / "ocr", tokenizer_dir()))


def load_gray(image: Path) -> Any:
    import numpy as np
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None  # 300 DPI newspaper pages exceed PIL's bomb guard
    with Image.open(image) as img:
        return np.asarray(img.convert("L"))


class OcrSplitStage(PageStage):
    name = "ocr_split"
    depends_on = ("ocr",)
    concurrency = 1

    def __init__(self) -> None:
        self._stack: contextlib.ExitStack | None = None
        self._text: Any = None

    @property
    def version(self) -> str:  # type: ignore[override]
        return versioned("colsplit-v1", VERSION_SETTINGS)

    def open(self) -> None:
        """Nothing: the GPU is taken only when a page has something to split."""

    def _start_text(self) -> Any:
        if self._text is None:
            from newsrag.models import gpu_lock

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
        source = ocr_artifact(page.page_id)
        data = json.loads(source.read_text(encoding="utf-8"))
        return split_page(data, self._start_text, self.version)


def split_page(data: dict[str, Any], start_text: Any, version: str) -> StageResult:
    """Split one page's `ocr` artifact (`data`); `start_text()` gives a started `TextOcr`."""
    s = config.settings
    page_id = data["page_id"]
    image = Path((data.get("image") or {}).get("path") or Path(s.images_dir) / f"{page_id}.png")
    blocks = data.get("blocks") or []
    params = colsplit.Params.from_settings()

    def reader() -> colsplit.Reader:
        text = start_text()
        return lambda request: text.read(image, request)

    if colsplit.candidates(blocks, params):
        if not image.is_file():
            raise FileNotFoundError(f"rendered page missing: {image}")
        outcome = colsplit.split_blocks(load_gray(image), blocks, params, reader)
    else:
        outcome = colsplit.apply_splits(blocks, {}, {})

    artifact = {
        "page_id": page_id,
        "image": data.get("image"),
        "model": data.get("model"),
        "blocks": outcome.blocks,
        "markdown": "\n\n".join(b["text"] for b in outcome.blocks if b["text"]),
    }
    path = write_json_atomic(artifact_path("ocr_split", version, page_id), artifact)
    detail = {
        "n_blocks": len(outcome.blocks),
        # text_labels blocks of the input `ocr` artifact: the split rate's denominator
        "n_text_blocks": sum(1 for b in blocks if b.get("label") in params.text_labels),
        "n_split_blocks": outcome.n_split_blocks,
        "n_pieces": outcome.n_pieces,
        "split_errors": outcome.split_errors,
    }
    return StageResult(str(path), detail)
