"""indic-ocr's text model (IndicBlockOCR, Qwen3.5-0.8B) served by vLLM, read block by block.

The server is started from `envs/vllm` (ticket 006) with `newsrag.models.serve.run_server`, on
the weights in the pinned indic-ocr snapshot (`weights/ocr`) and with `--tokenizer` pointing at
a folder holding only indic-ocr's own tokenizer files: given the model folder, transformers maps
`model_type: qwen3_5` to its byte-level Qwen tokenizer, which mis-encodes the prompts and leaves
U+2581 in the output. `newsrag ocr install` writes and checks that folder.

Crops and prompts follow indic-ocr's own recipe (`idp_crops.py`, `idp_contract.py` at the pinned
revision): the block box clamped to the page, its area scaled into
[ocr_crop_min_px_side^2, ocr_crop_max_px_side^2], the transcription prompt for text and the
HTML prompt for tables. Each text block gets a token budget from its crop's area; a table gets
`ocr_max_new_tokens`. vLLM batches the blocks of all pages in flight.
"""

from __future__ import annotations

import contextlib
import math
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.models.profiles import Profile

PROFILE_NAME = "indic-ocr"
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja")

# indic-ocr's prompts (idp_contract.py at the pinned revision; `ocr install` checks they match).
TEXT_PROMPT = (
    "Transcribe the text in this image. Write any mathematical expressions in LaTeX, "
    "using $...$ for inline math and $$...$$ for display equations."
)
TABLE_PROMPT = (
    "Convert this table image to HTML. Preserve the structure exactly, using colspan and "
    "rowspan for merged cells and <br/> for line breaks within a cell. "
    "Output only the HTML table."
)
PROMPTS = {"text": TEXT_PROMPT, "table": TABLE_PROMPT}


class TextServerDied(RuntimeError):
    """The vLLM server exited during a request; it was restarted."""


def tokenizer_dir(revision: str | None = None) -> Path:
    revision = revision or config.settings.indic_ocr_revision or "unpinned"
    return Path(config.settings.ocr_tokenizer_root) / revision


def indic_profile(weights: Path, tokenizer: Path) -> Profile:
    """The vLLM profile for indic-ocr's text model (built here: its weights are a subfolder of
    the pinned snapshot, not a Hub repo of their own)."""
    s = config.settings
    return Profile(
        name=PROFILE_NAME,
        hf_id=str(weights),
        family="qwen",
        kind="vlm",
        dtype="bfloat16",
        max_model_len=s.ocr_vllm_max_model_len,
        gpu_memory_utilization=s.ocr_vllm_gpu_memory_utilization,
        extra_args=(
            "--tokenizer",
            str(tokenizer),
            "--limit-mm-per-prompt",
            '{"image": 1}',
            "--max-num-seqs",
            str(s.ocr_vllm_max_num_seqs),
        ),
    )


def crop_block(page: Any, bbox: list[float]) -> Any | None:
    """The image indic-ocr would send for one block, or None for a box with no area."""
    from PIL import Image

    s = config.settings
    width, height = page.size
    x0, y0, x1, y1 = (round(float(v)) for v in bbox)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    crop = page.crop((x0, y0, x1, y1))
    pixels = crop.width * crop.height
    lo, hi = s.ocr_crop_min_px_side**2, s.ocr_crop_max_px_side**2
    if lo <= pixels <= hi:
        return crop
    scale = math.sqrt((lo if pixels < lo else hi) / pixels)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    return crop.resize(size, Image.LANCZOS)


def token_budget(crop: Any, kind: str) -> int:
    s = config.settings
    if kind == "table":
        return s.ocr_max_new_tokens
    need = math.ceil(crop.width * crop.height * s.ocr_tokens_per_mpx / 1_000_000)
    return min(s.ocr_max_new_tokens, max(s.ocr_min_new_tokens, need))


class TextOcr:
    """The vLLM server for the text model, and block reads against it. Thread-safe."""

    def __init__(self, profile: Profile):
        self.profile = profile
        self.server: Any = None
        self._stack: contextlib.ExitStack | None = None
        self._lock_handle: Any = None
        self._openai: Any = None
        self._pool: ThreadPoolExecutor | None = None
        self._mutex = threading.RLock()
        self.starts = 0

    # --- lifecycle ---

    def start(self, lock: Any = None) -> None:
        """Start vLLM; the caller holds the GPU lock (`lock` gets the server recorded)."""
        from openai import OpenAI

        from newsrag.models.serve import run_server

        with self._mutex:
            self._close_server()
            self._lock_handle = lock
            stack = contextlib.ExitStack()
            try:
                self.server = stack.enter_context(run_server(self.profile, lock=lock))
            except BaseException:
                stack.close()
                raise
            self._stack = stack
            self._openai = OpenAI(
                base_url=self.server.base_url,
                api_key="EMPTY",
                timeout=config.settings.ocr_request_timeout_s,
                max_retries=0,
            )
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=config.settings.ocr_text_concurrency,
                    thread_name_prefix="ocr-text",
                )
            self.starts += 1

    def _close_server(self) -> None:
        stack, self._stack, self.server = self._stack, None, None
        if stack is not None:
            stack.close()

    def stop(self) -> None:
        with self._mutex:
            self._close_server()
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def alive(self) -> bool:
        server = self.server
        return server is not None and server.proc.poll() is None

    def _ensure_server(self) -> None:
        """Restart a dead server once, however many page threads notice it."""
        with self._mutex:
            if not self.alive():
                self.start(self._lock_handle)

    # --- reads ---

    def _read_one(self, crop: Any, kind: str) -> str:
        from newsrag.models.client import build_messages

        reply = self._openai.chat.completions.create(
            model=self.profile.name,
            messages=build_messages(PROMPTS[kind], [crop]),
            temperature=0,
            max_tokens=token_budget(crop, kind),
        )
        return reply.choices[0].message.content or ""

    def read(self, image: str | Path, blocks: list[dict[str, Any]]) -> list[str]:
        """Text for each block ({"bbox", "type": "text"|"table"}), same order, NFC."""
        from PIL import Image

        if not blocks:
            return []
        Image.MAX_IMAGE_PIXELS = None  # 300 DPI newspaper pages exceed PIL's bomb guard
        with Image.open(image) as img:
            page = img.convert("RGB")
        self._ensure_server()
        pool = self._pool
        assert pool is not None
        futures = {}
        for i, block in enumerate(blocks):
            kind = block.get("type", "text")
            if kind not in PROMPTS:
                raise ValueError(f"block {i}: unknown type {kind!r}")
            crop = crop_block(page, block["bbox"])
            if crop is not None:
                futures[i] = pool.submit(self._read_one, crop, kind)
        texts = [""] * len(blocks)
        try:
            for i, future in futures.items():
                texts[i] = future.result()
        except Exception:
            for future in futures.values():
                future.cancel()
            if not self.alive():
                with contextlib.suppress(Exception):
                    self._ensure_server()
                raise TextServerDied("vLLM text server died; restarted") from None
            raise
        return [unicodedata.normalize("NFC", t) for t in texts]
