"""Ticket 008: splitting column-spanning blocks, and the block overlay. Synthetic images and a
fake text model, no GPU; the `gpu`+`data` test re-reads one real fused block."""

from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

import newsrag.stages.base as base_mod
from newsrag import config
from newsrag.ocr import colsplit
from newsrag.ocr.colsplit import Params, apply_splits, find_gaps, piece_id, plan_splits
from newsrag.ocr.overlay import draw_blocks
from newsrag.stages import ocr_split as split_mod
from newsrag.stages.base import PageRow
from newsrag.stages.ocr_split import OcrSplitStage, split_page

# A fused block on a dev-sample page: two columns of body text (found by viewing crops and the
# overlay; see results.md).
REAL_FUSED_PAGE = "2025-10-15_B_main_10"
REAL_FUSED_BLOCK = "b017"

TEXT, TITLE = "Text", "SectionHeader"
PARAMS = Params(width_factor=1.6, min_gap_px=40, min_piece_px=120, text_labels=(TEXT,))
COL_W, GUTTER = 300, 60


# --- synthetic pages ---


def ink_column(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, seed: int) -> None:
    """Fake printed text: lines of word-sized dark boxes, word gaps 5-9 px, filling [x, x+w)."""
    rng = random.Random(seed)
    for top in range(y + 6, y + h - 18, 26):
        cx = x
        while cx < x + w:
            word = rng.randint(18, 60)
            draw.rectangle((cx, top, min(x + w, cx + word) - 1, top + 15), fill=(20, 20, 20))
            cx += word + rng.randint(5, 9)


def page_image(path: Path, size=(1400, 1200)) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


def block(bid: str, order: int, bbox, label=TEXT, text="old", conf=0.9) -> dict:
    gtype = "title" if label == TITLE else "text"
    return {
        "block_id": bid,
        "order": order,
        "label": label,
        "type": gtype,
        "bbox": list(bbox),
        "conf": conf,
        "text": text,
    }


def gray(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        return np.asarray(img.convert("L"))


def narrow_blocks(draw: ImageDraw.ImageDraw, y: int = 700) -> list[dict]:
    """Three ordinary one-column text blocks, so the page's median width is one column."""
    out = []
    for i in range(3):
        x = 20 + i * (COL_W + GUTTER)
        ink_column(draw, x, y, COL_W, 200, seed=100 + i)
        out.append(block(f"b10{i}", 10 + i, (x, y, x + COL_W, y + 200)))
    return out


def fused_page(tmp: Path, columns: int = 2, label=TEXT, rule=False) -> tuple[Path, list[dict]]:
    """A page with one block covering `columns` printed columns, then three narrow blocks."""
    path = tmp / "page.png"
    img = Image.new("RGB", (1400, 1200), "white")
    draw = ImageDraw.Draw(img)
    x0, y0, h = 20, 50, 400
    for c in range(columns):
        ink_column(draw, x0 + c * (COL_W + GUTTER), y0, COL_W, h, seed=c)
        if rule and c:
            mid = x0 + c * (COL_W + GUTTER) - GUTTER // 2
            draw.rectangle((mid - 1, y0, mid, y0 + h), fill=(0, 0, 0))  # a 2 px column rule
    width = columns * COL_W + (columns - 1) * GUTTER
    blocks = [block("b001", 1, (x0, y0, x0 + width, y0 + h), label=label), *narrow_blocks(draw)]
    for i, b in enumerate(blocks, 1):
        b["order"] = i
    img.save(path)
    return path, blocks


class FakeText:
    """Stands in for TextOcr: records requests, answers each piece with `piece-<n>`."""

    def __init__(self, answer=None, fail=False):
        self.requests: list[list[dict]] = []
        self.answer = answer
        self.fail = fail
        self.started = 0
        self.stopped = 0

    def start(self, lock=None) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def read(self, image, blocks):
        self.requests.append(blocks)
        if self.fail:
            raise RuntimeError("vLLM said no")
        if self.answer is not None:
            return list(self.answer)
        return [f"piece-{i}" for i in range(len(blocks))]


@pytest.fixture
def env(tmp_path, monkeypatch):
    patched = config.settings.model_copy(
        update={
            "work_dir": tmp_path / "work",
            "images_dir": tmp_path,
            "reports_dir": tmp_path / "reports",
            "text_labels": (TEXT,),
            "title_labels": (TITLE,),
            "colsplit_width_factor": 1.6,
            "colsplit_min_gap_px": 40,
            "colsplit_min_piece_px": 120,
        }
    )
    for mod in (config, base_mod):
        monkeypatch.setattr(mod, "settings", patched)
    return tmp_path


def artifact(image: Path, blocks: list[dict]) -> dict:
    return {
        "page_id": "2026-01-02_B_main_01",
        "image": {"path": str(image), "width": 1400, "height": 1200},
        "model": {"layout": "surya@test", "ocr": {"repo": "x/y", "revision": "abc"}},
        "blocks": blocks,
        "markdown": "",
    }


# --- E-1 ---


def test_two_columns_in_one_block_split_into_two(tmp_path):
    image, blocks = fused_page(tmp_path, columns=2)
    plan = plan_splits(gray(image), blocks, PARAMS)
    assert list(plan) == ["b001"]
    left, right = plan["b001"]
    cut = 20 + COL_W + GUTTER // 2
    assert left[0] == 20 and right[2] == 20 + 2 * COL_W + GUTTER
    assert abs(left[2] - cut) <= 1 and left[2] == right[0]
    assert left[1] == right[1] == 50 and left[3] == right[3] == 450


def test_single_column_not_split(tmp_path):
    # A wide block of continuous text (no gutter) is a candidate but has no gap.
    img = Image.new("RGB", (1400, 1200), "white")
    draw = ImageDraw.Draw(img)
    ink_column(draw, 20, 50, 2 * COL_W + GUTTER, 400, seed=7)
    blocks = [block("b001", 1, (20, 50, 20 + 2 * COL_W + GUTTER, 450)), *narrow_blocks(draw)]
    img.save(tmp_path / "p.png")
    assert colsplit.candidates(blocks, PARAMS)[0]["block_id"] == "b001"
    assert plan_splits(gray(tmp_path / "p.png"), blocks, PARAMS) == {}
    # A two-column block that is not wider than 1.6 × the median is not even a candidate.
    image, fused = fused_page(tmp_path, columns=2)
    wide = Params(3.0, 40, 120, (TEXT,))
    assert colsplit.candidates(fused, wide) == []
    assert plan_splits(gray(image), fused, wide) == {}


def test_display_type_word_spaces_not_split(tmp_path):
    # One line of large display type: its 25-35 px word spaces are blank columns, not gutters.
    img = Image.new("RGB", (1400, 1200), "white")
    draw = ImageDraw.Draw(img)
    rng = random.Random(5)
    x = 30
    while x < 1100:
        word = rng.randint(120, 220)
        draw.rectangle((x, 60, min(1100, x + word), 150), fill=(20, 20, 20))
        x += word + rng.randint(25, 35)
    blocks = [block("b001", 1, (20, 50, 1110, 160)), *narrow_blocks(draw)]
    img.save(tmp_path / "p.png")
    assert colsplit.candidates(blocks, PARAMS)
    assert plan_splits(gray(tmp_path / "p.png"), blocks, PARAMS) == {}


def test_too_few_text_blocks_no_candidates(tmp_path):
    image, blocks = fused_page(tmp_path, columns=2)
    assert colsplit.candidates(blocks[:3], PARAMS) == [blocks[0]]  # fused + 2 narrow: 3 blocks
    assert colsplit.candidates(blocks[:2], PARAMS) == []
    assert plan_splits(gray(image), blocks[:2], PARAMS) == {}


def test_headline_never_split(tmp_path):
    image, blocks = fused_page(tmp_path, columns=2, label=TITLE)
    assert plan_splits(gray(image), blocks, PARAMS) == {}


def test_piece_ids_and_order(env):
    image, blocks = fused_page(env, columns=3)
    first = block("b000", 0, (20, 5, 400, 40), label=TITLE, text="headline")
    blocks = [first, *blocks]
    for i, b in enumerate(blocks, 1):
        b["order"] = i
    blocks[1]["block_id"] = "b002"
    text = FakeText()
    result = split_page(artifact(image, blocks), lambda: text, "colsplit-test")
    data = json.loads(Path(result.artifact_path).read_text())
    ids = [b["block_id"] for b in data["blocks"]]
    assert ids == ["b000", "b002a", "b002b", "b002c", "b100", "b101", "b102"]
    assert [b["order"] for b in data["blocks"]] == list(range(1, 8))
    pieces = data["blocks"][1:4]
    assert all(p["split_from"] == "b002" and p["label"] == TEXT for p in pieces)
    assert all(p["conf"] == 0.9 and p["type"] == "text" for p in pieces)
    assert [p["text"] for p in pieces] == ["piece-0", "piece-1", "piece-2"]
    xs = [p["bbox"][0] for p in pieces]
    assert xs == sorted(xs) and pieces[0]["bbox"][2] == pieces[1]["bbox"][0]
    assert "split_from" not in data["blocks"][0]
    assert len(text.requests) == 1 and all(r["type"] == "text" for r in text.requests[0])
    assert data["markdown"].split("\n\n")[:4] == ["headline", "piece-0", "piece-1", "piece-2"]
    assert set(data) == {"page_id", "image", "model", "blocks", "markdown"}
    assert data["model"] == {"layout": "surya@test", "ocr": {"repo": "x/y", "revision": "abc"}}
    assert result.detail == {
        "n_blocks": 7,
        "n_text_blocks": 4,
        "n_split_blocks": 1,
        "n_pieces": 3,
        "split_errors": 0,
    }


def test_piece_texts_nfc(env):
    image, blocks = fused_page(env, columns=2)
    decomposed = "ె"[0] + "ై"  # TELUGU VOWEL SIGN E + AI LENGTH MARK -> U+0C48
    text = FakeText(answer=[decomposed, "two"])
    result = split_page(artifact(image, blocks), lambda: text, "colsplit-test")
    data = json.loads(Path(result.artifact_path).read_text())
    import unicodedata

    assert data["blocks"][0]["text"] == unicodedata.normalize("NFC", decomposed)


def test_edge_margins_not_gaps(tmp_path):
    img = Image.new("RGB", (1400, 1200), "white")
    draw = ImageDraw.Draw(img)
    # One column of text inside a wide box with 200 px of white on both sides.
    ink_column(draw, 220, 50, 400, 400, seed=3)
    blocks = [block("b001", 1, (20, 50, 820, 450)), *narrow_blocks(draw)]
    img.save(tmp_path / "p.png")
    assert colsplit.candidates(blocks, PARAMS)
    assert plan_splits(gray(tmp_path / "p.png"), blocks, PARAMS) == {}


def test_gap_rules_on_profiles():
    ink = np.full(700, 0.3)
    ink[300:340] = 0.0
    assert find_gaps(ink, 15, 120) == [(300, 340)]
    narrow = ink.copy()
    narrow[300:340] = 0.3
    narrow[300:310] = 0.0  # 10 blank columns < 15
    assert find_gaps(narrow, 15, 120) == []
    thin_side = np.full(700, 0.3)
    thin_side[100:140] = 0.0  # only 100 inked columns on the left
    assert find_gaps(thin_side, 15, 120) == []
    edge = np.full(700, 0.3)
    edge[:40] = 0.0
    edge[-40:] = 0.0
    assert find_gaps(edge, 15, 120) == []
    thick_rule = np.full(700, 0.3)
    thick_rule[300:340] = 0.0
    thick_rule[318:324] = 0.9  # 6 columns: too thick for a rule, splits the run in two
    # 300-318 is a gap; 324-340 has only the 6 rule columns of ink on its left
    assert find_gaps(thick_rule, 15, 120) == [(300, 318)]
    thin_rule = np.full(700, 0.3)
    thin_rule[300:340] = 0.0
    thin_rule[318:321] = 0.9  # 3 columns: a printed rule, counted as blank
    assert find_gaps(thin_rule, 15, 120) == [(300, 340)]


def test_empty_piece_keeps_original(env):
    image, blocks = fused_page(env, columns=2)
    text = FakeText(answer=["left text", "  "])
    result = split_page(artifact(image, blocks), lambda: text, "colsplit-test")
    data = json.loads(Path(result.artifact_path).read_text())
    assert [b["block_id"] for b in data["blocks"]] == ["b001", "b100", "b101", "b102"]
    assert data["blocks"][0]["text"] == "old" and "split_from" not in data["blocks"][0]
    assert result.detail["split_errors"] == 1 and result.detail["n_split_blocks"] == 0
    failing = FakeText(fail=True)
    result = split_page(artifact(image, blocks), lambda: failing, "colsplit-test")
    assert result.detail["split_errors"] == 1 and result.detail["n_pieces"] == 0


def page_row(page_id: str) -> PageRow:
    return PageRow(
        page_id=page_id,
        pub_date=date(2026, 1, 2),
        edition_code="B",
        section="main",
        page_num=1,
        page_end=None,
        source_path="/x.pdf",
        source_archive="2026.zip",
        revision=1,
    )


def stage_env(env, monkeypatch, pages: dict[str, dict]):
    """Point the stage at artifacts in tmp and count GPU lock / text model starts."""
    sources = {}
    for page_id, data in pages.items():
        data = {**data, "page_id": page_id}
        sources[page_id] = env / f"{page_id}.ocr.json"
        sources[page_id].write_text(json.dumps(data))
    monkeypatch.setattr(split_mod, "ocr_artifact", lambda page_id: sources[page_id])
    calls = {"lock": 0, "released": 0, "texts": []}

    import contextlib

    @contextlib.contextmanager
    def fake_lock(holder, **kw):
        assert holder == "indic-ocr"
        calls["lock"] += 1
        try:
            yield object()
        finally:
            calls["released"] += 1

    def fake_make_text():
        text = FakeText()
        calls["texts"].append(text)
        return text

    import newsrag.models as models_pkg

    monkeypatch.setattr(models_pkg, "gpu_lock", fake_lock)
    monkeypatch.setattr(split_mod, "make_text", fake_make_text)
    return calls


def test_no_candidates_no_gpu(env, monkeypatch):
    img = Image.new("RGB", (1400, 1200), "white")
    blocks = narrow_blocks(ImageDraw.Draw(img))
    img.save(env / "p.png")
    calls = stage_env(env, monkeypatch, {"2026-01-02_B_main_01": artifact(env / "p.png", blocks)})
    stage = OcrSplitStage()
    stage.open()
    result = stage.handle(page_row("2026-01-02_B_main_01"))
    stage.close()
    assert calls == {"lock": 0, "released": 0, "texts": []}
    data = json.loads(Path(result.artifact_path).read_text())
    assert [b["block_id"] for b in data["blocks"]] == ["b100", "b101", "b102"]
    assert [b["order"] for b in data["blocks"]] == [1, 2, 3]
    assert result.detail["n_split_blocks"] == 0 and result.detail["n_text_blocks"] == 3


def test_gpu_taken_once_and_released_on_close(env, monkeypatch):
    image, blocks = fused_page(env, columns=2)
    pages = {f"2026-01-02_B_main_0{i}": artifact(image, blocks) for i in (1, 2)}
    calls = stage_env(env, monkeypatch, pages)
    stage = OcrSplitStage()
    stage.open()
    assert calls["lock"] == 0
    for page_id in pages:
        assert stage.handle(page_row(page_id)).detail["n_pieces"] == 2
    assert calls["lock"] == 1 and calls["released"] == 0 and len(calls["texts"]) == 1
    stage.close()
    assert calls["released"] == 1 and calls["texts"][0].stopped == 1


def test_column_rule_in_gap_split(tmp_path):
    image, blocks = fused_page(tmp_path, columns=2, rule=True)
    plan = plan_splits(gray(image), blocks, PARAMS)
    assert list(plan) == ["b001"] and len(plan["b001"]) == 2


def test_piece_id_letters():
    assert [piece_id("b007", i) for i in (0, 1, 25, 26)] == ["b007a", "b007b", "b007z", "b007aa"]


def test_apply_splits_without_plan_renumbers():
    blocks = [block("b002", 5, (0, 0, 1, 1)), block("b001", 2, (0, 0, 1, 1))]
    out = apply_splits(blocks, {}, {})
    assert [(b["block_id"], b["order"]) for b in out.blocks] == [("b001", 1), ("b002", 2)]


def test_stage_registered_and_versioned():
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["ocr_split"]
    assert stage.depends_on == ("ocr",) and stage.concurrency == 1
    assert (
        stage.version.startswith("colsplit-v1-") and len(stage.version) == len("colsplit-v1-") + 8
    )


# --- FR-4: overlay ---


def test_overlay_draws_numbered_boxes(tmp_path):
    image = page_image(tmp_path / "p.png", size=(1000, 800))
    blocks = [block("b001", 1, (100, 100, 400, 300)), block("b002", 2, (500, 100, 900, 700))]
    out = draw_blocks(image, blocks)
    assert out.size == (1000, 800)
    px = np.asarray(out)
    assert (px[200, 100] != 255).any()  # left edge of the first box
    assert (px[105:125, 105:120] != 255).any()  # its label
    small = draw_blocks(image, blocks, max_long_side=500)
    assert small.size == (500, 400)


def test_overlay_cli(env, monkeypatch):
    from newsrag.cli import app
    from newsrag.ocr import cli as ocr_cli

    image, blocks = fused_page(env, columns=2)
    src = env / "ocr.json"
    src.write_text(json.dumps(artifact(image, blocks)))
    seen = {}

    def fake_artifact(page_id, stage):
        seen["stage"] = stage
        return ("ocr", src) if page_id == "p1" else None

    monkeypatch.setattr(ocr_cli, "_artifact", fake_artifact)
    runner = CliRunner()
    result = runner.invoke(app, ["ocr", "overlay", "p1"])
    assert result.exit_code == 0, result.output
    target = env / "reports" / "overlays" / "p1.png"
    assert target.is_file() and Image.open(target).format == "PNG"
    out = env / "x.png"
    assert runner.invoke(app, ["ocr", "overlay", "p1", "--out", str(out)]).exit_code == 0
    assert out.is_file()
    missing = runner.invoke(app, ["ocr", "overlay", "p2", "--stage", "ocr_split"])
    assert missing.exit_code == 1 and "no ocr_split artifact for p2" in missing.output


def test_split_counts():
    from newsrag.ocr.cli import split_counts

    ocr = [block("b001", 1, (0, 0, 1, 1)), block("b002", 2, (0, 0, 1, 1), label=TITLE)]
    split = [
        {**block("b001a", 1, (0, 0, 1, 1)), "split_from": "b001"},
        {**block("b001b", 2, (0, 0, 1, 1)), "split_from": "b001"},
        block("b002", 3, (0, 0, 1, 1), label=TITLE),
    ]
    assert split_counts(ocr, split) == (1, 1, 2)


# --- E-2: one real fused block (GPU + data) ---


@pytest.mark.gpu
@pytest.mark.data
def test_real_fused_block(tmp_path, monkeypatch):
    from newsrag.models import gpu_lock

    data = json.loads(split_mod.ocr_artifact(REAL_FUSED_PAGE).read_text(encoding="utf-8"))
    patched = config.settings.model_copy(update={"work_dir": tmp_path / "work"})
    for mod in (config, base_mod):
        monkeypatch.setattr(mod, "settings", patched)
    with gpu_lock("indic-ocr") as lock:
        text = split_mod.make_text()
        text.start(lock)
        try:
            result = split_page(data, lambda: text, "colsplit-test")
        finally:
            text.stop()
    blocks = json.loads(Path(result.artifact_path).read_text())["blocks"]
    pieces = [b for b in blocks if b.get("split_from") == REAL_FUSED_BLOCK]
    assert len(pieces) >= 2, result.detail
    assert all(p["text"] for p in pieces)
