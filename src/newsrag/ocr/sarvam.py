"""Sarvam Document Intelligence for block crops (ticket 010, grill D-17 / A-38).

`reread_blocks` crops the chosen blocks of a page, packs at most `sarvam_crops_per_job` crops into
one PDF (one crop per PDF page), runs one Sarvam job per PDF and puts the returned text back into
the blocks, keeping our block ids. The job pattern (create -> upload -> start -> poll ->
download a ZIP with `metadata/page_NNN.json`) follows the old system's
`pipeline/stage03_ocr/run_hybrid.py`.

- Text of a crop = every Sarvam block on its PDF page except `layout_tag == "image"` (those are
  invented scene descriptions), in reading order, joined with "\\n", NFC.
- Results are cached by sha1 of the crop PNG in `sarvam_cache_dir`, so a retry never pays twice.
- At most `sarvam_max_jobs` jobs run at once in the process. Every SDK call is retried on HTTP
  429/5xx and connection errors (`sarvam_retry_backoff_s`). A job that still fails, times out or
  ends in another state than `Completed` leaves its blocks with the indic-ocr text.
- A rejected API key (HTTP 401/403) is raised, so the page fails instead of being flagged.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

from newsrag import config

TERMINAL_STATES = frozenset({"Completed", "PartiallyCompleted", "Failed"})
AUTH_STATUSES = frozenset({401, 403})
META_RE = re.compile(r"(?:^|/)metadata/page_(\d+)\.json$")

# Patched by tests.
_sleep: Callable[[float], None] = time.sleep
_monotonic: Callable[[], float] = time.monotonic


class SarvamApi(Protocol):
    """The SDK calls a job needs; each one is retried on its own."""

    def create(self) -> str: ...
    def upload(self, job_id: str, pdf: Path) -> None: ...
    def start(self, job_id: str) -> None: ...
    def status(self, job_id: str) -> str: ...
    def download(self, job_id: str, out: Path) -> None: ...


class SarvamAuthError(RuntimeError):
    """Sarvam rejected the API key: the page fails (and is retried by the runner)."""


class JobFailed(Exception):
    def __init__(self, job_id: str | None, reason: str):
        super().__init__(reason)
        self.job_id = job_id
        self.reason = reason


class SdkApi:
    """`SarvamApi` on the `sarvamai` SDK, with the SDK's own retries off (we retry ourselves)."""

    NO_RETRY: ClassVar[dict[str, Any]] = {"max_retries": 0}

    def __init__(self, api_key: str):
        from sarvamai import SarvamAI

        s = config.settings
        self._timeout = s.sarvam_http_timeout_s
        self._language = s.sarvam_language
        self._output_format = s.sarvam_output_format
        self._di = SarvamAI(
            api_subscription_key=api_key, timeout=self._timeout
        ).document_intelligence

    def create(self) -> str:
        params = {"language": self._language, "output_format": self._output_format}
        response = self._di.initialise(job_parameters=params, request_options=self.NO_RETRY)
        return response.job_id

    def upload(self, job_id: str, pdf: Path) -> None:
        import httpx

        links = self._di.get_upload_links(
            job_id=job_id, files=[pdf.name], request_options=self.NO_RETRY
        )
        details = (links.upload_urls or {}).get(pdf.name)
        if details is None:
            raise RuntimeError(f"Sarvam job {job_id}: no upload URL for {pdf.name}")
        response = httpx.put(
            details.file_url,
            content=pdf.read_bytes(),
            headers={"Content-Type": "application/pdf", "x-ms-blob-type": "BlockBlob"},
            timeout=self._timeout,
        )
        response.raise_for_status()

    def start(self, job_id: str) -> None:
        self._di.start(job_id, request_options=self.NO_RETRY)

    def status(self, job_id: str) -> str:
        return str(self._di.get_status(job_id, request_options=self.NO_RETRY).job_state)

    def download(self, job_id: str, out: Path) -> None:
        import httpx

        links = self._di.get_download_links(job_id, request_options=self.NO_RETRY)
        urls = links.download_urls or {}
        if not urls:
            raise RuntimeError(f"Sarvam job {job_id}: no download URL")
        details = urls[next(iter(urls))]
        response = httpx.get(details.file_url, timeout=self._timeout)
        response.raise_for_status()
        out.write_bytes(response.content)


# --- retries ---


def http_status(exc: BaseException) -> int | None:
    """The HTTP status of an SDK (`ApiError.status_code`) or httpx error, else None."""
    response = getattr(exc, "response", None)
    if response is not None and isinstance(getattr(response, "status_code", None), int):
        return response.status_code
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def retryable(exc: BaseException) -> bool:
    status = http_status(exc)
    if status is not None:
        return status == 429 or status >= 500
    import httpx

    return isinstance(exc, httpx.TransportError | ConnectionError)


def with_retry(fn: Callable[..., Any], *args: Any) -> Any:
    """Call `fn(*args)`; on a retryable error wait 2 s, 8 s, 30 s (by default) and try again."""
    waits = tuple(config.settings.sarvam_retry_backoff_s)
    for attempt in range(len(waits) + 1):
        try:
            return fn(*args)
        except Exception as exc:
            if attempt >= len(waits) or not retryable(exc):
                raise
            _sleep(waits[attempt])
    raise AssertionError("unreachable")


# --- one job ---

_slots_lock = threading.Lock()
_slots: threading.BoundedSemaphore | None = None


def job_slots() -> threading.BoundedSemaphore:
    """The process-wide limit on running Sarvam jobs (`sarvam_max_jobs`)."""
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(max(1, config.settings.sarvam_max_jobs))
        return _slots


@dataclass
class JobOutput:
    job_id: str
    pages: dict[int, dict[str, Any]]  # PDF page number (1-based) -> its metadata JSON


def write_pdf(images: Sequence[Any], path: Path) -> None:
    first, *rest = images
    first.save(path, format="PDF", save_all=True, append_images=rest, resolution=300.0)


def read_output(zip_path: Path) -> dict[int, dict[str, Any]]:
    """`metadata/page_NNN.json` of a job's output ZIP, by page number (nothing is extracted)."""
    pages: dict[int, dict[str, Any]] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            m = META_RE.search(name)
            if m:
                pages[int(m.group(1))] = json.loads(zf.read(name).decode("utf-8"))
    return pages


def run_job(api: SarvamApi, images: Sequence[Any], workdir: Path) -> JobOutput:
    """One Sarvam job over up to `sarvam_crops_per_job` crops. Raises JobFailed."""
    s = config.settings
    job_id: str | None = None
    with job_slots():
        try:
            pdf = workdir / "input.pdf"
            write_pdf(images, pdf)
            job_id = with_retry(api.create)
            with_retry(api.upload, job_id, pdf)
            with_retry(api.start, job_id)
            deadline = _monotonic() + s.sarvam_job_timeout_s
            while True:
                state = str(with_retry(api.status, job_id))
                if state in TERMINAL_STATES:
                    break
                if _monotonic() >= deadline:
                    raise JobFailed(job_id, f"timeout after {s.sarvam_job_timeout_s:g} s")
                _sleep(s.sarvam_poll_interval_s)
            if state != "Completed":
                raise JobFailed(job_id, f"job ended {state}")
            out = workdir / "output.zip"
            with_retry(api.download, job_id, out)
            return JobOutput(job_id, read_output(out))
        except JobFailed:
            raise
        except Exception as exc:
            status = http_status(exc)
            if status in AUTH_STATUSES:
                raise SarvamAuthError(f"Sarvam rejected the API key (HTTP {status})") from None
            # Only the type and status: SDK/httpx messages can hold presigned URLs.
            reason = type(exc).__name__ + (f" HTTP {status}" if status is not None else "")
            raise JobFailed(job_id, reason) from exc


def page_text(meta: dict[str, Any]) -> str:
    """A crop's text: its non-image Sarvam blocks in reading order, joined with "\\n", NFC."""
    blocks = [b for b in meta.get("blocks") or [] if isinstance(b, dict)]
    blocks = [b for b in blocks if b.get("layout_tag") != "image"]
    indexed = sorted(enumerate(blocks), key=lambda ib: (ib[1].get("reading_order", ib[0]), ib[0]))
    texts = [str(b.get("text") or "").strip() for _, b in indexed]
    return unicodedata.normalize("NFC", "\n".join(t for t in texts if t))


# --- the cache ---


def cache_path(digest: str) -> Path:
    return Path(config.settings.sarvam_cache_dir) / digest[:2] / f"{digest}.json"


def cache_get(digest: str) -> dict[str, Any] | None:
    path = cache_path(digest)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def cache_put(digest: str, text: str, job_id: str) -> None:
    from newsrag.stages.base import write_json_atomic

    write_json_atomic(cache_path(digest), {"text": text, "job_id": job_id})


# --- a page ---


@dataclass
class Reread:
    blocks: list[dict[str, Any]]
    n_sent: int = 0  # blocks whose crop went to Sarvam (or came from the cache)
    n_cached: int = 0
    n_replaced: int = 0
    failed_blocks: int = 0
    job_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class _Crop:
    digest: str
    image: Any
    indexes: list[int]  # blocks with this exact crop


def crop_box(bbox: Sequence[float], size: tuple[int, int], pad: int) -> tuple[int, int, int, int]:
    width, height = size
    x1, y1, x2, y2 = (round(float(v)) for v in bbox)
    return max(0, x1 - pad), max(0, y1 - pad), min(width, x2 + pad), min(height, y2 + pad)


def png_bytes(image: Any) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def load_rgb(path: Path) -> Any:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None  # 300 DPI newspaper pages exceed PIL's bomb guard
    with Image.open(path) as img:
        return img.convert("RGB")


def reread_blocks(
    image_path: Path, blocks: Sequence[dict[str, Any]], labels: frozenset[str], api: SarvamApi
) -> Reread:
    """Re-read the `labels` blocks of a page with Sarvam; every block gets `ocr_source`."""
    s = config.settings
    out = [dict(b) for b in blocks]
    result = Reread(out)
    for b in out:
        b["ocr_source"] = "indic"

    wanted: list[int] = []
    for i, b in enumerate(out):
        if b.get("label") not in labels:
            continue
        x1, y1, x2, y2 = b["bbox"]
        if x2 - x1 < s.sarvam_min_block_px or y2 - y1 < s.sarvam_min_block_px:
            b["ocr_source"] = "indic_kept"
            continue
        wanted.append(i)
    if not wanted:
        return result

    page = load_rgb(image_path)
    crops: dict[str, _Crop] = {}
    for i in wanted:
        image = page.crop(crop_box(out[i]["bbox"], page.size, s.sarvam_crop_pad_px))
        digest = hashlib.sha1(png_bytes(image)).hexdigest()
        crops.setdefault(digest, _Crop(digest, image, [])).indexes.append(i)
    result.n_sent = len(wanted)

    texts: dict[str, str] = {}
    job_ids: set[str] = set()
    for digest, crop in crops.items():
        hit = cache_get(digest)
        if hit is not None:
            texts[digest] = str(hit.get("text") or "")
            result.n_cached += len(crop.indexes)
            if hit.get("job_id"):
                job_ids.add(str(hit["job_id"]))

    todo = [c for c in crops.values() if c.digest not in texts]
    size = max(1, s.sarvam_crops_per_job)
    batches = [todo[i : i + size] for i in range(0, len(todo), size)]
    failed: set[str] = set()
    if batches:
        jobs_dir = Path(s.sarvam_cache_dir) / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        def one(batch: list[_Crop]) -> tuple[list[_Crop], JobOutput | JobFailed]:
            with tempfile.TemporaryDirectory(dir=jobs_dir, prefix="job-") as tmp:
                try:
                    return batch, run_job(api, [c.image for c in batch], Path(tmp))
                except JobFailed as exc:
                    return batch, exc

        with ThreadPoolExecutor(max_workers=max(1, min(s.sarvam_max_jobs, len(batches)))) as pool:
            outcomes = list(pool.map(one, batches))
        for batch, outcome in outcomes:
            if isinstance(outcome, JobFailed):
                if outcome.job_id:
                    job_ids.add(outcome.job_id)
                result.errors.append(f"job {outcome.job_id or '-'}: {outcome.reason}")
                failed.update(c.digest for c in batch)
                continue
            job_ids.add(outcome.job_id)
            for n, crop in enumerate(batch, start=1):
                meta = outcome.pages.get(n)
                if meta is None:
                    result.errors.append(f"job {outcome.job_id}: no metadata/page_{n:03d}.json")
                    failed.add(crop.digest)
                    continue
                text = page_text(meta)
                cache_put(crop.digest, text, outcome.job_id)
                texts[crop.digest] = text

    for crop in crops.values():
        for i in crop.indexes:
            b = out[i]
            if crop.digest in failed:
                b["ocr_source"] = "indic_kept"
                result.failed_blocks += 1
            elif texts.get(crop.digest):
                b["text"] = texts[crop.digest]
                b["ocr_source"] = "sarvam"
                result.n_replaced += 1
            else:
                b["ocr_source"] = "indic_kept"
    result.job_ids = sorted(job_ids)
    return result
