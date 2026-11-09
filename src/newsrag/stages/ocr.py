"""Stage `ocr` (ticket 007): Surya finds the blocks, indic-ocr reads their text.

For each rendered page: one `layout` request to Surya gives the regions in reading order; the
regions of the text-like groups (and tables, as `type: table`) go to indic-ocr in one `blocks`
request. Every Surya region is kept in the artifact; figures, charts, ads and `other` keep
`text: ""` (ticket 015 reads them). A page with no regions is `done` with `zero_blocks: true`.

Both models stay loaded for the whole run under one GPU lock (D-8): Surya as a worker, the
indic-ocr text model as a vLLM server. `ocr_pages_in_flight` pages are worked on at once so
vLLM batches the blocks of several pages.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.config import LABEL_SET_GROUPS
from newsrag.ocr import labels as labels_mod
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    StageUnavailable,
    artifact_path,
    versioned,
    write_json_atomic,
)
from newsrag.stages.render import png_size

NOT_INSTALLED = "OCR models not installed: run uv run newsrag ocr install"
VERSION_SETTINGS = [
    "surya_version",
    "indic_ocr_revision",
    "indic_ocr_repo",
    "surya_layout_checkpoint",
    "surya_order_checkpoint",
    "ocr_layout_threshold",
    "ocr_crop_min_px_side",
    "ocr_crop_max_px_side",
    "ocr_max_new_tokens",
    "ocr_tokens_per_mpx",
    "ocr_min_new_tokens",
    *LABEL_SET_GROUPS,
]


def make_client():
    """The OcrClient for a run (tests replace this with fake workers)."""
    from newsrag.ocr.client import OcrClient, surya_spec
    from newsrag.ocr.install import indic_snapshot
    from newsrag.ocr.text import TextOcr, indic_profile, tokenizer_dir

    repo = indic_snapshot()
    if repo is None:
        raise StageUnavailable(NOT_INSTALLED)
    text = TextOcr(indic_profile(repo / "weights" / "ocr", tokenizer_dir()))
    return OcrClient(surya_spec(), text)


def _missing() -> list[str]:
    """What `preflight` finds missing: the Surya venv, vLLM, the indic-ocr tokenizer folder,
    the pins, labels.yaml (the pinned snapshot is checked by `_snapshot_ok`)."""
    from newsrag.ocr.text import TOKENIZER_FILES, tokenizer_dir

    s = config.settings
    missing = []
    for path in (Path(s.surya_env_dir) / "bin" / "python", Path(s.vllm_env_dir) / "bin" / "vllm"):
        if not path.exists():
            missing.append(str(path))
    if s.indic_ocr_revision:
        missing += [
            str(p) for p in (tokenizer_dir() / n for n in TOKENIZER_FILES) if not p.exists()
        ]
    if not s.surya_version:
        missing.append("surya_version (pipeline.yaml)")
    if not s.indic_ocr_revision:
        missing.append("indic_ocr_revision (pipeline.yaml)")
    if not Path(s.labels_file).is_file():
        missing.append(str(s.labels_file))
    return missing


def _snapshot_ok() -> bool:
    from newsrag.ocr.install import indic_snapshot

    return indic_snapshot() is not None


class OcrStage(PageStage):
    name = "ocr"
    depends_on = ("render",)

    def __init__(self) -> None:
        self._stack: contextlib.ExitStack | None = None
        self._client: Any = None
        self._pages = 0
        self._first: float | None = None  # monotonic start of the first page, end of the last
        self._last: float | None = None
        self._stats = threading.Lock()
        self._version_cache: tuple[tuple[int, int | None], str] | None = None

    @property
    def concurrency(self) -> int:  # type: ignore[override]
        return config.settings.ocr_pages_in_flight

    @property
    def version(self) -> str:  # type: ignore[override]
        """Computed lazily; cached per (settings object, labels.yaml mtime)."""
        s = config.settings
        labels = Path(s.labels_file)
        try:
            mtime: int | None = labels.stat().st_mtime_ns
        except FileNotFoundError:
            mtime = None
        key = (id(s), mtime)
        if self._version_cache is None or self._version_cache[0] != key:
            revision = (s.indic_ocr_revision or "none")[:8]
            prefix = f"surya@{s.surya_version or 'none'}+indic-ocr@{revision}-v1"
            files = [labels] if mtime is not None else []
            self._version_cache = (key, versioned(prefix, VERSION_SETTINGS, files))
        return self._version_cache[1]

    def preflight(self) -> None:
        missing = _missing()
        if missing or not _snapshot_ok():
            raise StageUnavailable(NOT_INSTALLED)

    def open(self) -> None:
        from newsrag.models import gpu_lock

        stack = contextlib.ExitStack()
        try:
            lock = stack.enter_context(gpu_lock("ocr"))
            client = make_client()
            stack.callback(client.stop)
            client.start(lock=lock)
        except BaseException:
            stack.close()
            raise
        self._stack, self._client = stack, client
        self._pages, self._first, self._last = 0, None, None

    def close(self) -> None:
        stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            stack.close()
        if self._pages and self._first is not None and self._last is not None:
            seconds = self._last - self._first  # wall clock: pages overlap
            rate = self._pages / max(seconds, 1e-9)
            print(
                f"ocr: {self._pages} pages in {seconds:.1f} s (excl. model load) "
                f"= {rate:.2f} pages/s",
                file=sys.stdout,
                flush=True,
            )

    def handle(self, page: PageRow) -> StageResult:
        if self._client is None:
            raise RuntimeError("ocr stage used without open()")
        started = time.monotonic()
        with self._stats:
            if self._first is None:
                self._first = started
        result = ocr_page(self._client, page, self.version)
        with self._stats:
            self._pages += 1
            self._last = time.monotonic()
        return result


def ocr_page(client: Any, page: PageRow, version: str) -> StageResult:
    """OCR one rendered page with `client` (an OcrClient); write the artifact."""
    s = config.settings
    image = Path(s.images_dir) / f"{page.page_id}.png"
    if not image.is_file():
        raise FileNotFoundError(f"rendered page missing: {image}")
    width, height = png_size(image)

    layout = client.layout(image)
    regions = layout.get("regions") or []
    # Boxes in pixels of the 300 DPI PNG, scaled if Surya worked on a resized image.
    sx = width / float(layout.get("width") or width)
    sy = height / float(layout.get("height") or height)
    mapping = labels_mod.load()

    blocks: list[dict[str, Any]] = []
    unknown = 0
    for order, region in enumerate(sorted(regions, key=lambda r: r["position"]), 1):
        label = str(region["label"])
        if label not in mapping:
            unknown += 1
        x1, y1, x2, y2 = (float(v) for v in region["bbox"])
        bbox = [
            max(0, min(width, round(x1 * sx))),
            max(0, min(height, round(y1 * sy))),
            max(0, min(width, round(x2 * sx))),
            max(0, min(height, round(y2 * sy))),
        ]
        blocks.append(
            {
                "block_id": f"b{order:03d}",
                "order": order,
                "label": label,
                "type": labels_mod.group_of(label, mapping),
                "bbox": bbox,
                "conf": round(float(region.get("confidence") or 0.0), 3),
                "text": "",
            }
        )

    send = [b for b in blocks if b["type"] in labels_mod.SENT_AS]
    texts = client.blocks(
        image, [{"bbox": b["bbox"], "type": labels_mod.SENT_AS[b["type"]]} for b in send]
    )
    for block, text in zip(send, texts, strict=True):
        block["text"] = text.strip()

    revision = s.indic_ocr_revision
    artifact = {
        "page_id": page.page_id,
        "image": {"path": str(image), "width": width, "height": height},
        "model": {
            "layout": f"surya@{s.surya_version}",
            "ocr": {"repo": s.indic_ocr_repo, "revision": revision},
        },
        "blocks": blocks,
        "markdown": "\n\n".join(b["text"] for b in blocks if b["text"]),
    }
    path = write_json_atomic(artifact_path("ocr", version, page.page_id), artifact)

    types = Counter(b["type"] for b in blocks)
    detail = {
        "n_blocks": len(blocks),
        "n_text_blocks": types.get("text", 0),
        "n_title_blocks": types.get("title", 0),
        "label_counts": dict(sorted(Counter(b["label"] for b in blocks).items())),
        "chars": sum(len(b["text"]) for b in blocks),
        "zero_blocks": not blocks,
        "unknown_labels": unknown,
    }
    return StageResult(str(path), detail)
