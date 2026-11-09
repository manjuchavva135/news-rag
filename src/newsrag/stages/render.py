"""Stage `render` (ticket 005): page 1 of every registered PDF to a PNG at `render_dpi`.

The PNG is the input of OCR, the article-box overlays, the judge and every visual check. It is
written as ``<images_dir>/.tmp-<page_id>.png`` and renamed to ``<images_dir>/<page_id>.png`` only
once the render succeeded, so a half-written file never sits under the final name.

A PDF that poppler itself rejects (non-zero exit with a message on stderr, or a timeout) is
quarantined after its last attempt with reason ``render_failed: <first stderr line | timeout>``.
Environment problems (``images_dir`` not writable, disk full, source file gone) only fail the
attempt.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from newsrag.config import settings
from newsrag.ledger import db
from newsrag.stages.base import PageRow, PageStage, StageResult, StageUnavailable, versioned

POPPLER_MISSING = "poppler not found: run  sudo apt install poppler-utils"
QUARANTINE_PREFIX = "render_failed:"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# stderr lines that mean "could not write the output", not "bad PDF".
_ENV_STDERR = re.compile(
    r"could not write|no space left|disk quota|read-only file system", re.IGNORECASE
)
_PAGES_RE = re.compile(r"^Pages:\s+(\d+)\s*$", re.MULTILINE)


class PopplerRejected(RuntimeError):
    """Poppler refused this PDF (or timed out on it). The message is the quarantine reason."""


_REJECTED = f"{PopplerRejected.__name__}: "  # how the runner prefixes the error text


def poppler_missing() -> list[str]:
    """The poppler tools (pdftoppm, pdfinfo) not found on PATH."""
    return [b for b in (settings.pdftoppm_bin, settings.pdfinfo_bin) if shutil.which(b) is None]


def png_size(path: Path) -> tuple[int, int]:
    """(width, height) from a PNG's IHDR chunk."""
    with open(path, "rb") as fh:
        head = fh.read(24)
    if len(head) < 24 or head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _env_problem(images_dir: Path, stderr: str) -> str | None:
    """Why a failed render is the machine's fault rather than the PDF's, or None."""
    if not os.access(images_dir, os.W_OK | os.X_OK):
        return f"{images_dir} is not writable"
    free = shutil.disk_usage(images_dir).free
    if free < settings.render_min_free_bytes:
        return f"only {free} bytes free in {images_dir}"
    if _ENV_STDERR.search(stderr):
        return _first_line(stderr)
    return None


def _run(cmd: list[str], timeout_s: float, images_dir: Path) -> str:
    """Run one poppler tool; return its stdout. Raise PopplerRejected or RuntimeError."""
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        env=settings.subprocess_env(),
        start_new_session=True,  # so a timeout kills the whole process group
    ) as proc:
        try:
            out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise PopplerRejected(f"{QUARANTINE_PREFIX} timeout") from None
    if proc.returncode != 0:
        tool = Path(cmd[0]).name
        why = _env_problem(images_dir, err)
        if err.strip() and why is None:
            raise PopplerRejected(f"{QUARANTINE_PREFIX} {_first_line(err)}")
        detail = why or _first_line(err) or "(no stderr)"
        raise RuntimeError(f"{tool} exited {proc.returncode}: {detail}")
    return out


class RenderStage(PageStage):
    name = "render"
    depends_on = ()

    @property
    def concurrency(self) -> int:  # type: ignore[override]
        return settings.render_workers

    @property
    def version(self) -> str:  # type: ignore[override]
        return versioned(f"pdftoppm-{settings.render_dpi}dpi-v1", ["render_dpi"])

    def preflight(self) -> None:
        if poppler_missing():
            raise StageUnavailable(POPPLER_MISSING)

    def handle(self, page: PageRow) -> StageResult:
        images_dir = Path(settings.images_dir)
        images_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(images_dir, os.W_OK | os.X_OK):
            raise PermissionError(f"{images_dir} is not writable")
        source = Path(page.source_path)
        if not source.is_file():
            raise FileNotFoundError(f"source PDF missing: {source}")

        prefix = images_dir / f".tmp-{page.page_id}"
        tmp_png = images_dir / f".tmp-{page.page_id}.png"
        final = images_dir / f"{page.page_id}.png"
        try:
            dpi = str(settings.render_dpi)
            _run(
                [settings.pdftoppm_bin, "-r", dpi, "-png", "-f", "1", "-l", "1", "-singlefile"]
                + [str(source), str(prefix)],
                settings.render_timeout_s,
                images_dir,
            )
            if not tmp_png.is_file() or tmp_png.stat().st_size == 0:
                raise RuntimeError("pdftoppm exited 0 but wrote no PNG")
            info = _run([settings.pdfinfo_bin, str(source)], settings.pdfinfo_timeout_s, images_dir)
            match = _PAGES_RE.search(info)
            if match is None:
                raise RuntimeError("pdfinfo printed no 'Pages:' line")
            pdf_pages = int(match.group(1))
            width, height = png_size(tmp_png)
            size = tmp_png.stat().st_size
            os.replace(tmp_png, final)
        finally:
            tmp_png.unlink(missing_ok=True)

        detail = {
            "width": width,
            "height": height,
            "bytes": size,
            "pdf_pages": pdf_pages,
            "multi_page": pdf_pages > 1,
            "small_render": width < settings.render_small_width_px,
        }
        return StageResult(str(final), detail)

    def on_final_failure(self, item: Any, error: str) -> None:
        if error.startswith(_REJECTED + QUARANTINE_PREFIX):
            db.quarantine_page(item.page_id, error[len(_REJECTED) :])

    def on_reset(self, c: Any, page_id: str | None, pub_date: date | None, all_rows: bool) -> None:
        """`stage reset render` also lifts the `render_failed:` quarantines in its scope."""
        clauses, params = ["quarantine_reason like %s"], [QUARANTINE_PREFIX + "%"]
        if page_id is not None:
            clauses.append("page_id = %s")
            params.append(page_id)
        elif pub_date is not None:
            clauses.append("pub_date = %s")
            params.append(pub_date)
        rows = db.fetch_all(
            c,
            "update pages set quarantine_reason = null, updated_at = now() where "
            + " and ".join(clauses)
            + " returning source_path",
            params,
        )
        if rows:
            c.execute(
                "delete from quarantine where source_path = any(%s) and reason like %s",
                ([r["source_path"] for r in rows], QUARANTINE_PREFIX + "%"),
            )


def stats() -> dict[str, int]:
    """Render counts for the quality report: rendered, multi_page, small_render, quarantined."""
    with db.conn() as c:
        row = db.fetch_one(
            c,
            "select count(*) filter (where r.status = 'done') as rendered, "
            "count(*) filter (where r.status = 'done' and (r.detail->>'multi_page')::boolean) "
            "as multi_page, "
            "count(*) filter (where r.status = 'done' and (r.detail->>'small_render')::boolean) "
            "as small_render "
            "from stage_runs r join pages p using (page_id) "
            "where r.stage = 'render' and p.quarantine_reason is null",
        )
        quarantined = c.execute(
            "select count(*) from pages where quarantine_reason like %s",
            (QUARANTINE_PREFIX + "%",),
        ).fetchone()[0]
    assert row is not None
    return {
        "rendered": int(row["rendered"]),
        "multi_page": int(row["multi_page"]),
        "small_render": int(row["small_render"]),
        "quarantined": int(quarantined),
    }
