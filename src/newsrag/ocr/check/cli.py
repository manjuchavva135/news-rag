"""`newsrag ocr check`: build the page list, serve the review page, print the verdicts."""

from __future__ import annotations

import socket
from typing import Annotated

import typer

from newsrag.config import settings

app = typer.Typer(help="OCR sanity check: a reader reviews 10 pages and signs off.")


def _fail(message: str) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(1)


@app.command()
def build(
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing list.")] = False,
) -> None:
    """Choose the 10 check pages from the dev sample and write data/ocr-check.txt."""
    from newsrag.ocr.check.pages import CheckError
    from newsrag.ocr.check.pages import build as do_build

    try:
        path, ids, short = do_build(force)
    except CheckError as exc:
        raise _fail(str(exc)) from None
    for line in short:
        typer.echo(line)
    typer.echo(f"wrote {len(ids)} page ids to {path}")


@app.command()
def serve(
    port: Annotated[int | None, typer.Option("--port", help="Default: ocr_check_port.")] = None,
    host: Annotated[str | None, typer.Option("--host", help="Default: ocr_check_host.")] = None,
) -> None:
    """Start the review web page (Ctrl-C stops it)."""
    from newsrag.ocr.check.pages import CheckError
    from newsrag.ocr.check.server import build_app
    from newsrag.ocr.check.server import serve as do_serve

    try:
        check_app = build_app()
    except CheckError as exc:
        raise _fail(str(exc)) from None
    host = host or settings.ocr_check_host
    port = port or settings.ocr_check_port
    shown = socket.gethostname() if host in ("0.0.0.0", "") else host
    typer.echo(f"OCR check: http://{shown}:{port}/ (also http://localhost:{port}/)")
    typer.echo(f"verdicts: {check_app.store.path}")
    try:
        do_serve(check_app, host, port)
    except OSError as exc:
        raise _fail(f"cannot listen on {host}:{port}: {exc}") from None
    except KeyboardInterrupt:
        pass


@app.command()
def summary() -> None:
    """One line per page (page_id verdict problems), the sign-off, the failing page types."""
    from pathlib import Path

    from newsrag.ocr.check import verdicts
    from newsrag.ocr.check.pages import CheckError, check_ids

    try:
        page_ids = check_ids()
    except CheckError as exc:
        raise _fail(str(exc)) from None
    state = verdicts.load(Path(settings.ocr_check_dir) / "verdicts.json")
    for line in verdicts.summary_lines(state, page_ids):
        typer.echo(line)
