"""The local review web page (ticket 009 FR-2, FR-5, NFR-1): a stdlib WSGI app.

Routes::

    GET  /                          the pages and their verdicts
    GET  /page/<id>                 scan (JPEG + SVG boxes) beside the OCR blocks, verdict form
    POST /page/<id>                 save the verdict (303 back to the page)
    GET  /page/<id>/image.jpg       the page downscaled to a JPEG (cached on disk)
    GET  /page/<id>/full.png        the full-size page PNG
    GET  /signoff, POST /signoff    sign-off form
    GET  /static/<file>             CSS, JS, the Telugu webfont

``App`` takes the page list, the artifacts and the store as arguments, so tests drive it without
the ledger; ``serve()`` resolves them from the ledger and starts a threaded server.
"""

from __future__ import annotations

import html
import io
import json
import mimetypes
import os
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from socketserver import ThreadingMixIn
from string import Template
from typing import Any
from urllib.parse import parse_qs, quote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from newsrag.config import settings
from newsrag.ocr.check import verdicts as v

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
TEMPLATE_DIR = HERE / "templates"
MAX_BODY_BYTES = 1_000_000
MIN_JPEG_QUALITY = 40
JPEG_QUALITY_STEP = 10
TAG_FONT_FRACTION = 1 / 170  # block-number label height, as a fraction of the page's long side
N_EMPTY_ROWS = 1  # blank failing-page-type rows offered on the sign-off form

StartResponse = Callable[..., Any]

_templates: dict[str, Template] = {}


def render(template: str, /, **values: Any) -> str:
    if template not in _templates:
        _templates[template] = Template(
            (TEMPLATE_DIR / f"{template}.html").read_text(encoding="utf-8")
        )
    return _templates[template].substitute(**values)


def e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def page_url(page_id: str) -> str:
    return f"/page/{quote(page_id)}"


# --- images ---


def scaled_size(width: int, height: int, long_side: int) -> tuple[int, int]:
    scale = min(1.0, long_side / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def make_jpeg(source: Path, target: Path, long_side: int, max_bytes: int, quality: int) -> Path:
    """Downscale the page PNG to `long_side` and save a JPEG of at most `max_bytes` (atomic)."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None  # 300 DPI newspaper pages exceed PIL's bomb guard
    with Image.open(source) as img:
        img.draft("RGB", scaled_size(*img.size, long_side))
        page = img.convert("RGB")
    size = scaled_size(*page.size, long_side)
    if size != page.size:
        page = page.resize(size, Image.LANCZOS)
    data = b""
    while True:
        buf = io.BytesIO()
        page.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        data = buf.getvalue()
        if len(data) <= max_bytes or quality <= MIN_JPEG_QUALITY:
            break
        quality = max(MIN_JPEG_QUALITY, quality - JPEG_QUALITY_STEP)
    if len(data) > max_bytes:
        raise RuntimeError(f"{source.name}: JPEG is {len(data)} bytes at quality {quality}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


# --- the app ---


class HTTPError(Exception):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class App:
    """The WSGI app for one list of pages."""

    def __init__(
        self,
        page_ids: Sequence[str],
        artifacts: Mapping[str, Path],
        store: v.Store,
        cache_dir: Path,
        *,
        image_long_side: int | None = None,
        image_max_bytes: int | None = None,
        jpeg_quality: int | None = None,
    ) -> None:
        self.page_ids = list(page_ids)
        self.artifacts = {p: Path(artifacts[p]) for p in self.page_ids}
        self.store = store
        self.cache_dir = Path(cache_dir)
        self.long_side = image_long_side or settings.ocr_check_image_long_side
        self.max_bytes = image_max_bytes or settings.ocr_check_image_max_bytes
        self.quality = jpeg_quality or settings.ocr_check_jpeg_quality
        # One lock per page: a cached image never waits for another page's JPEG being made.
        self._image_locks = {p: threading.Lock() for p in self.page_ids}

    # -- WSGI --

    def __call__(self, environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO") or "/"
        try:
            status, headers, body = self.route(method, path, environ)
        except HTTPError as exc:
            status, headers, body = self.error_page(exc.status, exc.message)
        start_response(status, headers)
        return [body] if method != "HEAD" else [b""]

    def route(self, method: str, path: str, environ: dict[str, Any]) -> tuple:
        if method not in ("GET", "HEAD", "POST"):
            raise HTTPError("405 Method Not Allowed", "method not allowed")
        if path == "/":
            return self.get_only(method) or self.index()
        if path in ("/signoff", "/signoff/"):
            if method == "POST":
                return self.post_signoff(self.form(environ))
            return self.signoff_page()
        if path.startswith("/static/"):
            return self.get_only(method) or self.static(path[len("/static/") :])
        if path.startswith("/page/"):
            rest = path[len("/page/") :].rstrip("/")
            page_id, _, tail = rest.partition("/")
            if page_id not in self.page_ids:
                raise HTTPError("404 Not Found", f"{page_id} is not one of the check pages")
            if tail == "":
                if method == "POST":
                    return self.post_verdict(page_id, self.form(environ))
                return self.page(page_id, environ.get("QUERY_STRING", ""))
            if tail == "image.jpg":
                return self.get_only(method) or self.image(page_id)
            if tail == "full.png":
                return self.get_only(method) or self.full_png(page_id)
        raise HTTPError("404 Not Found", "not found")

    @staticmethod
    def get_only(method: str) -> None:
        if method == "POST":
            raise HTTPError("405 Method Not Allowed", "method not allowed")

    @staticmethod
    def form(environ: dict[str, Any]) -> dict[str, list[str]]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            raise HTTPError("413 Payload Too Large", "form too large")
        raw = environ["wsgi.input"].read(length) if length else b""
        return parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)

    # -- responses --

    @staticmethod
    def html(body: str, status: str = "200 OK") -> tuple:
        data = body.encode("utf-8")
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(data))),
            ("Cache-Control", "no-store"),
        ]
        return status, headers, data

    @staticmethod
    def redirect(location: str) -> tuple:
        return "303 See Other", [("Location", location), ("Content-Length", "0")], b""

    @staticmethod
    def file(path: Path, content_type: str, cache: str) -> tuple:
        data = path.read_bytes()
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(data))),
            ("Cache-Control", cache),
        ]
        return "200 OK", headers, data

    def error_page(self, status: str, message: str) -> tuple:
        body = f'<h1>{e(status)}</h1><p class="error">{e(message)}</p><p><a href="/">Pages</a></p>'
        return self.html(render("base", title=e(status), body=body), status)

    # -- pages --

    def index(self) -> tuple:
        state = self.store.read()
        rows = []
        for n, page_id in enumerate(self.page_ids, 1):
            entry = state["pages"].get(page_id) or {}
            verdict = entry.get("verdict")
            shown = (
                f'<span class="v v-{e(verdict)}">{e(verdict)}</span>'
                if verdict
                else '<span class="v v-none">not checked</span>'
            )
            rows.append(
                f'    <tr><td>{n}</td><td><a href="{page_url(page_id)}">{e(page_id)}</a></td>'
                f"<td>{shown}</td><td>{e(', '.join(entry.get('problems') or []))}</td>"
                f"<td>{e(entry.get('note') or '')}</td></tr>"
            )
        body = render(
            "index", total=len(self.page_ids), status=self.status_line(state), rows="\n".join(rows)
        )
        return self.html(render("base", title="Pages", body=body))

    def status_line(self, state: Mapping[str, Any]) -> str:
        signoff = state.get("signoff")
        if signoff:
            return (
                f'<p class="status">Signed off by <strong>{e(signoff["name"])}</strong> '
                f"at {e(signoff['signed_at'])}.</p>"
            )
        done = len(self.page_ids) - len(v.missing_verdicts(state, self.page_ids))
        return (
            f'<p class="status">Not signed off. {done} of {len(self.page_ids)} pages have a '
            "verdict.</p>"
        )

    def load_artifact(self, page_id: str) -> dict[str, Any]:
        return json.loads(self.artifacts[page_id].read_text(encoding="utf-8"))

    def image_path(self, page_id: str, artifact: Mapping[str, Any] | None = None) -> Path:
        artifact = artifact if artifact is not None else self.load_artifact(page_id)
        found = (artifact.get("image") or {}).get("path")
        return Path(found) if found else Path(settings.images_dir) / f"{page_id}.png"

    def page_size(self, page_id: str, artifact: Mapping[str, Any]) -> tuple[int, int]:
        image = artifact.get("image") or {}
        if image.get("width") and image.get("height"):
            return int(image["width"]), int(image["height"])
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with Image.open(self.image_path(page_id, artifact)) as img:
            return img.size

    def page(self, page_id: str, query: str) -> tuple:
        artifact = self.load_artifact(page_id)
        width, height = self.page_size(page_id, artifact)
        img_w, img_h = scaled_size(width, height, self.long_side)
        blocks = sorted(artifact.get("blocks") or [], key=lambda b: b.get("order") or 0)
        tag_px = max(width, height) * TAG_FONT_FRACTION

        boxes, items = [], []
        for block in blocks:
            order = e(block.get("order", ""))
            kind = e(block.get("type") or "other")
            x1, y1, x2, y2 = (float(c) for c in block.get("bbox") or (0, 0, 0, 0))
            tag_w = tag_px * (0.62 * len(order) + 0.6)
            boxes.append(
                f'<g class="box t-{kind}" data-order="{order}">'
                f"<title>{order} {e(block.get('label', ''))}</title>"
                f'<rect class="frame" x="{x1:.0f}" y="{y1:.0f}" width="{x2 - x1:.0f}" '
                f'height="{y2 - y1:.0f}"/>'
                f'<rect class="tag" x="{x1:.0f}" y="{y1:.0f}" width="{tag_w:.0f}" '
                f'height="{tag_px * 1.3:.0f}"/>'
                f'<text x="{x1 + tag_px * 0.3:.0f}" y="{y1 + tag_px * 0.15:.0f}">{order}</text></g>'
            )
            text = str(block.get("text") or "").strip()
            shown = (
                f'<div class="text" lang="te">{e(text)}</div>'
                if text
                else '<div class="text empty">(no text)</div>'
            )
            items.append(
                f'      <li class="block t-{kind}" data-order="{order}">'
                f'<span class="num">{order}</span><span class="label">{e(block.get("label", ""))}'
                f"</span>{shown}</li>"
            )

        state = self.store.read()
        entry = state["pages"].get(page_id) or {}
        params = parse_qs(query)
        flash = ""
        if "saved" in params:
            pos = self.page_ids.index(page_id)
            following = (
                f' <a href="{page_url(self.page_ids[pos + 1])}">Next page →</a>'
                if pos + 1 < len(self.page_ids)
                else ' <a href="/signoff">Go to sign-off →</a>'
            )
            cleared = (
                " The sign-off was cleared: sign again after your changes."
                if "cleared" in params
                else ""
            )
            flash = f'<p class="flash">Verdict saved.{cleared}{following}</p>'
        body = render(
            "page",
            page_id=e(page_id),
            pos=self.page_ids.index(page_id) + 1,
            total=len(self.page_ids),
            prev=self.neighbour(page_id, -1, "← Previous"),
            next=self.neighbour(page_id, 1, "Next →"),
            flash=flash,
            w=width,
            h=height,
            img_w=img_w,
            img_h=img_h,
            tag_px=f"{tag_px:.0f}px",
            boxes="".join(boxes),
            items="\n".join(items),
            n_blocks=len(blocks),
            verdicts=self.verdict_inputs(entry.get("verdict")),
            problems=self.problem_inputs(entry.get("problems") or []),
            note=e(entry.get("note") or ""),
        )
        return self.html(render("base", title=e(page_id), body=body))

    def neighbour(self, page_id: str, step: int, text: str) -> str:
        pos = self.page_ids.index(page_id) + step
        if 0 <= pos < len(self.page_ids):
            return f'<a href="{page_url(self.page_ids[pos])}">{e(text)}</a>'
        return ""

    @staticmethod
    def verdict_inputs(current: str | None) -> str:
        return "\n".join(
            f'    <label><input type="radio" name="verdict" value="{name}" required'
            f"{' checked' if name == current else ''}> {name}</label>"
            for name in v.VERDICTS
        )

    @staticmethod
    def problem_inputs(current: Sequence[str]) -> str:
        return "\n".join(
            f'    <label><input type="checkbox" name="problems" value="{e(name)}"'
            f"{' checked' if name in current else ''}> {e(name)}</label>"
            for name in v.PROBLEMS
        )

    def post_verdict(self, page_id: str, form: Mapping[str, list[str]]) -> tuple:
        before = self.store.read().get("signoff")
        try:
            state = self.store.set_verdict(
                page_id,
                (form.get("verdict") or [""])[0],
                form.get("problems") or [],
                (form.get("note") or [""])[0],
            )
        except v.FormError as exc:
            raise HTTPError("400 Bad Request", str(exc)) from None
        cleared = "&cleared=1" if before and not state.get("signoff") else ""
        return self.redirect(f"{page_url(page_id)}?saved=1{cleared}")

    # -- sign-off --

    @staticmethod
    def row_html(index: int | str, row: Mapping[str, str] | None = None) -> str:
        row = row or {}
        cells = "".join(
            f'<td><input type="text" name="row-{index}-{field}" value="{e(row.get(field, ""))}" '
            f'aria-label="{field}"></td>'
            for field in (*v.ROW_FIELDS, "note")
        )
        return f"      <tr>{cells}</tr>"

    @staticmethod
    def stored_row(row: Mapping[str, Any]) -> dict[str, str]:
        return {
            k: ", ".join(str(x) for x in val) if isinstance(val, list) else str(val)
            for k, val in row.items()
        }

    @staticmethod
    def form_rows(form: Mapping[str, list[str]]) -> list[dict[str, str]]:
        """The `row-<i>-<field>` inputs as dicts, in row order."""
        rows: dict[int, dict[str, str]] = {}
        for key, values in form.items():
            parts = key.split("-", 2)
            if len(parts) != 3 or parts[0] != "row" or not parts[1].isdigit():
                continue
            if parts[2] in (*v.ROW_FIELDS, "note"):
                rows.setdefault(int(parts[1]), {})[parts[2]] = values[0] if values else ""
        return [rows[i] for i in sorted(rows)]

    def signoff_page(
        self,
        error: str = "",
        name: str | None = None,
        no_checked: bool | None = None,
        rows: Sequence[Mapping[str, str]] | None = None,
        status: str = "200 OK",
    ) -> tuple:
        state = self.store.read()
        signoff = state.get("signoff") or {}
        if name is None:
            name = signoff.get("name", "")
        if no_checked is None:
            no_checked = bool(signoff) and not signoff.get("failing_page_types")
        if rows is None:
            rows = [self.stored_row(r) for r in signoff.get("failing_page_types") or []]
        rows = [r for r in rows if any(str(x).strip() for x in r.values())]
        rows = [*rows, *([{}] * N_EMPTY_ROWS)]
        missing = v.missing_verdicts(state, self.page_ids)
        hint = (
            f"{len(missing)} of {len(self.page_ids)} pages still need a verdict."
            if missing
            else ("Signing again replaces the sign-off above." if signoff else "")
        )
        body = render(
            "signoff",
            status=self.status_line(state),
            error=f'<p class="error" role="alert">{e(error)}</p>' if error else "",
            name=e(name),
            no_checked=" checked" if no_checked else "",
            rows="\n".join(self.row_html(i, r) for i, r in enumerate(rows)),
            row_template=self.row_html("__I__").strip(),
            disabled=" disabled" if missing else "",
            hint=e(hint),
        )
        return self.html(render("base", title="Sign-off", body=body), status)

    def post_signoff(self, form: Mapping[str, list[str]]) -> tuple:
        name = (form.get("name") or [""])[0]
        no_checked = bool((form.get("no_page_types") or [""])[0])
        rows = self.form_rows(form)
        try:
            self.store.sign_off(name, no_checked, rows)
        except v.FormError as exc:
            return self.signoff_page(str(exc), name, no_checked, rows, "400 Bad Request")
        return self.redirect("/signoff")

    # -- files --

    def image(self, page_id: str) -> tuple:
        source = self.image_path(page_id)
        if not source.is_file():
            raise HTTPError("404 Not Found", f"page image missing: {source}")
        target = self.ensure_jpeg(page_id, source)
        return self.file(target, "image/jpeg", "max-age=3600")

    def ensure_jpeg(self, page_id: str, source: Path | None = None) -> Path:
        source = source or self.image_path(page_id)
        target = self.cache_dir / f"{page_id}.jpg"

        def fresh() -> bool:
            return target.is_file() and target.stat().st_mtime >= source.stat().st_mtime

        if not fresh():
            with self._image_locks[page_id]:
                if not fresh():
                    make_jpeg(source, target, self.long_side, self.max_bytes, self.quality)
        return target

    def full_png(self, page_id: str) -> tuple:
        source = self.image_path(page_id)
        if not source.is_file():
            raise HTTPError("404 Not Found", f"page image missing: {source}")
        return self.file(source, "image/png", "max-age=3600")

    def static(self, name: str) -> tuple:
        path = (STATIC_DIR / name).resolve()
        if path.parent != STATIC_DIR.resolve() or not path.is_file():
            raise HTTPError("404 Not Found", "not found")
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".woff2":
            kind = "font/woff2"
        elif kind.startswith("text/") or kind == "application/javascript":
            kind += "; charset=utf-8"
        return self.file(path, kind, "no-cache")

    def warm_cache(self) -> None:
        """Make every page's JPEG now, so the first view of a page is fast."""
        for page_id in self.page_ids:
            try:
                self.ensure_jpeg(page_id)
            except (OSError, RuntimeError, ValueError) as exc:  # shows as a 404 on its page
                print(f"image cache: {page_id}: {exc}", flush=True)


# --- serving ---


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        if not str(args[1] if len(args) > 1 else "").startswith(("2", "3")):
            super().log_message(format, *args)


def build_app() -> App:
    """The app for data/ocr-check.txt, with its artifacts from the ledger."""
    from newsrag.ocr.check.pages import check_ids, require_artifacts

    page_ids = check_ids()
    artifacts = require_artifacts(page_ids)
    out_dir = Path(settings.ocr_check_dir)
    store = v.Store(out_dir / "verdicts.json", page_ids)
    return App(page_ids, artifacts, store, out_dir / "cache")


def serve(app: App, host: str, port: int) -> None:
    server = make_server(
        host, port, app, server_class=ThreadingWSGIServer, handler_class=QuietHandler
    )
    threading.Thread(target=app.warm_cache, name="image-cache", daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
