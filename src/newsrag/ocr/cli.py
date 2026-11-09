"""`newsrag ocr`: install the OCR models, (re)generate labels.yaml, show a page's blocks, draw
them as a numbered overlay, and report the column-split rate (ticket 008)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from newsrag import config

app = typer.Typer(help="OCR: Surya layout + indic-ocr text.", no_args_is_help=True)

# The OCR block stages, most processed first (`ocr show` picks the first one that is done).
SHOW_STAGES = ("ocr_final", "ocr_split", "ocr")
SHOW_TEXT_CHARS = 60


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


@app.command()
def install() -> None:
    """Set up envs/indic-ocr and envs/surya and download both models (idempotent)."""
    from newsrag.ocr.install import InstallError, LicenceError, install_indic, install_surya

    try:
        for step in (install_indic, install_surya):
            typer.echo(step().line())
    except LicenceError as exc:
        raise _fail(str(exc), 2) from None
    except InstallError as exc:
        raise _fail(str(exc), 1) from None


def surya_labels() -> list[str]:
    """Surya's layout labels, from the worker script run in envs/surya."""
    from newsrag.ocr.client import SURYA_WORKER

    py = Path(config.settings.surya_env_dir) / "bin" / "python"
    if not py.exists():
        raise FileNotFoundError(f"{py} missing: run uv run newsrag ocr install")
    result = subprocess.run(
        [str(py), "-I", str(SURYA_WORKER), "--labels"],
        capture_output=True,
        text=True,
        env=config.settings.subprocess_env(),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"surya_worker.py --labels failed:\n{result.stderr[-2000:]}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@app.command()
def labels() -> None:
    """Write labels.yaml from Surya's label list (existing mappings are kept)."""
    from newsrag.ocr import labels as labels_mod

    try:
        found = surya_labels()
    except (FileNotFoundError, RuntimeError) as exc:
        raise _fail(str(exc), 2) from None
    existing = labels_mod.load()
    mapping = labels_mod.merged(found, existing)
    path = labels_mod.write(mapping)
    for label, group in mapping.items():
        note = "" if label in existing else "  (new: review)"
        note = "  (not a Surya label)" if label not in found else note
        typer.echo(f"{label}\t{group}{note}")
    typer.echo(f"wrote {path} ({len(mapping)} labels)")


def _artifact(page_id: str, stage: str | None) -> tuple[str, Path] | None:
    from newsrag.ledger import db

    wanted = [stage] if stage else list(SHOW_STAGES)
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            "select stage, artifact_path from stage_runs where page_id = %s "
            "and stage = any(%s) and status = 'done' and artifact_path is not null",
            (page_id, wanted),
        )
    found = {r["stage"]: r["artifact_path"] for r in rows}
    for name in wanted:
        if name in found and Path(found[name]).is_file():
            return name, Path(found[name])
    return None


def block_line(block: dict) -> str:
    bbox = ",".join(str(round(float(v))) for v in block.get("bbox", []))
    text = " ".join(str(block.get("text") or "").split())[:SHOW_TEXT_CHARS]
    return f"{block.get('order')}\t{block.get('label')}\t{bbox}\t{text}"


@app.command()
def show(
    page_id: str,
    stage: Annotated[
        str | None,
        typer.Option("--stage", help="ocr | ocr_split | ocr_final (default: newest done)."),
    ] = None,
) -> None:
    """Print one line per block: order, label, x1,y1,x2,y2, first 60 chars of text."""
    if stage is not None and stage not in SHOW_STAGES:
        raise _fail(f"--stage must be one of {', '.join(SHOW_STAGES)}", 2)
    found = _artifact(page_id, stage)
    if found is None:
        raise _fail(f"no {stage or 'ocr'} artifact for {page_id}", 1)
    _, path = found
    data = json.loads(path.read_text(encoding="utf-8"))
    for block in data.get("blocks", []):
        typer.echo(block_line(block))


@app.command()
def overlay(
    page_id: str,
    stage: Annotated[
        str | None,
        typer.Option("--stage", help="ocr | ocr_split | ocr_final (default: newest done)."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="PNG to write (default <reports_dir>/overlays/<page_id>.png)."),
    ] = None,
) -> None:
    """Draw the page's blocks as numbered boxes on its image and write a PNG."""
    from newsrag.ocr.overlay import draw_blocks

    if stage is not None and stage not in SHOW_STAGES:
        raise _fail(f"--stage must be one of {', '.join(SHOW_STAGES)}", 2)
    found = _artifact(page_id, stage)
    if found is None:
        raise _fail(f"no {stage or 'ocr'} artifact for {page_id}", 1)
    _, path = found
    data = json.loads(path.read_text(encoding="utf-8"))
    image = (data.get("image") or {}).get("path") or (
        Path(config.settings.images_dir) / f"{page_id}.png"
    )
    target = out or Path(config.settings.reports_dir) / "overlays" / f"{page_id}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    draw_blocks(image, data.get("blocks") or []).save(target, format="PNG")
    typer.echo(str(target))


def _done_artifacts(stage: str, page_ids: list[str]) -> dict[str, Path]:
    from newsrag.ledger import db

    with db.conn() as c:
        rows = db.fetch_all(
            c,
            "select page_id, artifact_path from stage_runs where stage = %s "
            "and page_id = any(%s) and status = 'done' and artifact_path is not null",
            (stage, page_ids),
        )
    return {r["page_id"]: Path(r["artifact_path"]) for r in rows}


def _blocks(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("blocks") or []


def split_counts(ocr_blocks: list[dict], split_blocks: list[dict]) -> tuple[int, int, int]:
    """(split blocks, text_labels blocks of the ocr artifact, pieces) of one page."""
    text_labels = set(config.settings.text_labels)
    pieces = [b for b in split_blocks if b.get("split_from")]
    texts = sum(1 for b in ocr_blocks if b.get("label") in text_labels)
    return len({b["split_from"] for b in pieces}), texts, len(pieces)


@app.command("split-stats")
def split_stats(
    pages_file: Annotated[
        Path, typer.Option("--pages-file", help="Page ids, one per line ('#' comments).")
    ],
) -> None:
    """Split rate of `ocr_split` over the listed pages that have both artifacts."""
    from newsrag.stages.runner import read_pages_file

    page_ids = read_pages_file(pages_file)
    ocr = _done_artifacts("ocr", page_ids)
    split = _done_artifacts("ocr_split", page_ids)
    missing = [p for p in page_ids if p not in ocr or p not in split]
    if missing:
        typer.echo(f"{len(missing)} pages without ocr/ocr_split artifacts skipped", err=True)
    a = b = c = 0
    for page_id in page_ids:
        if page_id in missing:
            continue
        n_split, n_text, n_pieces = split_counts(_blocks(ocr[page_id]), _blocks(split[page_id]))
        a, b, c = a + n_split, b + n_text, c + n_pieces
    pct = a / b * 100 if b else 0.0
    typer.echo(f"split_blocks={a} text_blocks={b} pct={pct:.1f} pieces={c}")
