"""Ticket 007: the OCR stage, its client, labels and install. CPU tests use a fake Surya worker
(`_fake_ocr_worker.py`) and a fake vLLM for the text model (`_fake_ocr_vllm.py`); the
`gpu`+`data` test runs the real models on 2 pages."""

from __future__ import annotations

import io
import json
import os
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

import newsrag.stages.base as base_mod
from newsrag import config
from newsrag.ocr import client as client_mod
from newsrag.ocr import install as install_mod
from newsrag.ocr import labels as labels_mod
from newsrag.ocr import text as text_mod
from newsrag.ocr.client import (
    OcrClient,
    Worker,
    WorkerDied,
    WorkerError,
    WorkerSpec,
    WorkerTimeout,
)
from newsrag.ocr.text import TABLE_PROMPT, TEXT_PROMPT, TextOcr, TextServerDied
from newsrag.stages import ocr as ocr_mod
from newsrag.stages.base import PageRow, StageUnavailable
from newsrag.stages.ocr import NOT_INSTALLED, OcrStage, ocr_page

FAKE_WORKER = Path(__file__).parent / "_fake_ocr_worker.py"
FAKE_VLLM = Path(__file__).parent / "_fake_ocr_vllm.py"
REPO_LABELS = Path(__file__).resolve().parents[1] / "labels.yaml"


# --- fixtures ---


class Env:
    def __init__(self, tmp: Path, monkeypatch):
        self.tmp = tmp
        self.mp = monkeypatch
        self.images = tmp / "images"
        self.fake = tmp / "fake"
        self.layouts_file = tmp / "layouts.json"
        self.layouts: dict = {}
        self.images.mkdir()
        monkeypatch.setenv("FAKE_OCR_DIR", str(self.fake))
        monkeypatch.setenv("FAKE_OCR_LAYOUTS", str(self.layouts_file))
        monkeypatch.delenv("FAKE_OCR_CHILD", raising=False)
        monkeypatch.delenv("FAKE_OCR_VLLM_MODE", raising=False)
        self.save_layouts()
        self.vllm_env = tmp / "vllm-env"
        (self.vllm_env / "bin").mkdir(parents=True)
        fake_vllm = self.vllm_env / "bin" / "vllm"
        fake_vllm.write_text(f"#!{sys.executable}\n" + FAKE_VLLM.read_text())
        fake_vllm.chmod(0o755)
        base = free_port_range()
        self.set(
            images_dir=self.images,
            work_dir=tmp / "work",
            logs_dir=tmp / "logs",
            gpu_lock_path=tmp / ".gpu.lock",
            labels_file=REPO_LABELS,
            surya_version="9.9.9",
            indic_ocr_revision="0123456789abcdef0123456789abcdef01234567",
            ocr_tokenizer_root=tmp / "tokenizers",
            vllm_env_dir=self.vllm_env,
            model_port=base,
            model_port_max=base + 10,
            model_start_timeout_s=20.0,
            model_stop_timeout_s=5.0,
            ocr_request_timeout_s=20.0,
            ocr_start_timeout_s=20.0,
            ocr_stop_timeout_s=2.0,
        )

    def set(self, **kw) -> config.Settings:
        patched = config.settings.model_copy(update=kw)
        for mod in (config, base_mod):
            self.mp.setattr(mod, "settings", patched)
        return patched

    def save_layouts(self) -> None:
        self.layouts_file.write_text(json.dumps(self.layouts))

    def page(self, page_id: str, size=(100, 100)) -> PageRow:
        Image.new("RGB", size, "white").save(self.images / f"{page_id}.png")
        return PageRow(
            page_id=page_id,
            pub_date=date(2025, 3, 4),
            edition_code="B",
            section="main",
            page_num=1,
            page_end=None,
            source_path=str(self.tmp / f"{page_id}.pdf"),
            source_archive="2025.zip",
            revision=1,
        )

    def requests(self, kind: str) -> list[dict]:
        """`surya` worker requests, or `vllm` chat requests (prompt, max_tokens, size)."""
        path = self.fake / f"{kind}-requests.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def surya_spec(self) -> WorkerSpec:
        return WorkerSpec("surya", [sys.executable, "-I", str(FAKE_WORKER)])

    def text(self) -> TextOcr:
        profile = text_mod.indic_profile(self.tmp / "weights", self.tmp / "tokenizer")
        return TextOcr(profile)

    def client(self) -> OcrClient:
        return OcrClient(self.surya_spec(), self.text())


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


@pytest.fixture
def ocr(env):
    client = env.client()
    client.start()
    yield client
    client.stop()


def free_port_range(width: int = 10) -> int:
    from _models_env import free_port_range as pick

    return pick(width)


def region(label: str, position: int, bbox=None, conf=0.5) -> dict:
    """A fake Surya region; by default each position gets its own box, one under the other."""
    if bbox is None:
        bbox = (10, 2 + 10 * position, 50, 10 + 10 * position)  # on the 100 px page
    return {"bbox": list(bbox), "label": label, "position": position, "confidence": conf}


def wait_dead(pid: int, timeout_s: float = 10.0) -> bool:
    from newsrag.models.lock import pid_alive

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


# --- E-7: the stage with fake workers ---


def test_surya_layout_then_indic_blocks(env, ocr):
    env.layouts["p1.png"] = [
        region("SectionHeader", 1, (10, 5, 90, 15), 0.71234),
        region("Text", 0, (10, 20, 90, 60), 0.9),
        region("PageFooter", 2, (10, 90, 90, 99)),
    ]
    env.save_layouts()
    page = env.page("p1")
    version = OcrStage().version
    result = ocr_page(ocr, page, version)

    surya, text = env.requests("surya"), env.requests("vllm")
    assert surya == [{"op": "layout", "image": str(env.images / "p1.png")}]
    assert len(text) == 3 and {r["prompt"] for r in text} == {TEXT_PROMPT}

    path = Path(result.artifact_path)
    assert path == env.tmp / "work" / "ocr" / base_mod.version_slug(version) / "p1.json"
    art = json.loads(path.read_text())
    assert sorted(art) == ["blocks", "image", "markdown", "model", "page_id"]
    assert art["page_id"] == "p1"
    assert art["image"] == {"path": str(env.images / "p1.png"), "width": 100, "height": 100}
    assert art["model"] == {
        "layout": "surya@9.9.9",
        "ocr": {
            "repo": "bodhan-ai/indic-ocr",
            "revision": "0123456789abcdef0123456789abcdef01234567",
        },
    }
    blocks = art["blocks"]
    assert sorted(blocks[0]) == [
        "bbox",
        "block_id",
        "conf",
        "label",
        "order",
        "text",
        "type",
    ]
    # Surya's reading order (position), numbered 1..n.
    assert [(b["block_id"], b["order"], b["label"]) for b in blocks] == [
        ("b001", 1, "Text"),
        ("b002", 2, "SectionHeader"),
        ("b003", 3, "PageFooter"),
    ]
    assert [b["type"] for b in blocks] == ["text", "title", "header_footer"]
    assert blocks[1]["conf"] == 0.712
    assert [b["text"] for b in blocks] == ["తెలుగు", "తెలుగు", "తెలుగు"]  # stripped
    assert art["markdown"] == "తెలుగు\n\nతెలుగు\n\nతెలుగు"
    assert result.detail == {
        "n_blocks": 3,
        "n_text_blocks": 1,
        "n_title_blocks": 1,
        "label_counts": {"PageFooter": 1, "SectionHeader": 1, "Text": 1},
        "chars": 18,
        "zero_blocks": False,
        "unknown_labels": 0,
        "dup_regions_dropped": 0,
    }


def test_figures_not_sent(env, ocr):
    env.layouts["p2.png"] = [
        region("Text", 0),
        region("Picture", 1),
        region("Figure", 2),
        region("Diagram", 3),
        region("Caption", 4),
        region("BlankPage", 5),
    ]
    env.save_layouts()
    result = ocr_page(ocr, env.page("p2"), OcrStage().version)
    assert len(env.requests("vllm")) == 2  # Text and Caption only
    art = json.loads(Path(result.artifact_path).read_text())
    by_label = {b["label"]: b for b in art["blocks"]}
    assert len(art["blocks"]) == 6  # all Surya regions are kept
    for label in ("Picture", "Figure", "Diagram", "BlankPage"):
        assert by_label[label]["text"] == ""
    assert by_label["Picture"]["type"] == "figure"
    assert by_label["Diagram"]["type"] == "chart"
    assert by_label["BlankPage"]["type"] == "other"
    assert by_label["Caption"]["type"] == "caption" and by_label["Caption"]["text"]


def test_tables_sent_as_table(env, ocr):
    env.layouts["p3.png"] = [
        region("Text", 0),
        region("Table", 1, (0, 0, 60, 60)),
        region("Form", 2),
    ]
    env.save_layouts()
    result = ocr_page(ocr, env.page("p3"), OcrStage().version)
    sent = env.requests("vllm")
    assert sorted(r["prompt"] for r in sent) == sorted([TEXT_PROMPT, TABLE_PROMPT, TABLE_PROMPT])
    tables = [r for r in sent if r["prompt"] == TABLE_PROMPT]
    assert all(r["max_tokens"] == 2048 for r in tables)  # tables keep the full budget
    # A 60x60 box is scaled up to indic-ocr's minimum crop area (256^2).
    assert [256, 256] in [r["size"] for r in tables]
    art = json.loads(Path(result.artifact_path).read_text())
    assert art["blocks"][1]["type"] == "table"
    assert art["blocks"][1]["text"] == "<table><tr><td>1</td></tr></table>"  # raw HTML kept


def test_error_reply_keeps_worker(env, ocr):
    pid = ocr.surya.pid
    with pytest.raises(WorkerError, match="cannot read error.png"):
        ocr.layout(env.images / "error.png")
    assert ocr.surya.pid == pid and ocr.surya.alive()
    assert ocr.layout(env.images / "ok.png")["regions"]
    assert ocr.surya.pid == pid and ocr.surya.starts == 1


def test_worker_restart_on_crash(env, ocr):
    pid = ocr.surya.pid
    with pytest.raises(WorkerDied, match=r"surya worker died \(exit 3\)"):
        ocr.layout(env.images / "crash.png")
    assert wait_dead(pid)
    assert ocr.surya.alive() and ocr.surya.pid != pid and ocr.surya.starts == 2
    assert ocr.layout(env.images / "ok.png")["regions"]


def test_text_server_restart_on_death(env, ocr):
    from newsrag.models.lock import kill_group

    env.page("ok")
    block = [{"bbox": [0, 0, 50, 50], "type": "text"}]
    assert ocr.blocks(env.images / "ok.png", block) == [" తెలుగు "]
    pgid = ocr.text.server.pgid
    kill_group(pgid, grace_s=2)
    assert wait_dead(pgid)
    assert ocr.blocks(env.images / "ok.png", block) == [" తెలుగు "]  # restarted first
    assert ocr.text.starts == 2 and ocr.text.server.pgid != pgid
    assert len(list(env.fake.glob("vllm-start-*.json"))) == 2


def test_timeout_kills_worker(env, ocr):
    env.set(ocr_request_timeout_s=1.0)
    pid = ocr.surya.pid
    started = time.monotonic()
    with pytest.raises(WorkerTimeout, match="no answer within 1 s"):
        ocr.layout(env.images / "hang.png")
    assert time.monotonic() - started < 15
    assert wait_dead(pid)
    assert ocr.surya.alive() and ocr.surya.pid != pid
    assert ocr.layout(env.images / "ok.png")["regions"]


def test_not_installed_preflight(env):
    env.set(surya_env_dir=env.tmp / "no-surya", vllm_env_dir=env.tmp / "no-vllm")
    with pytest.raises(StageUnavailable) as exc:
        OcrStage().preflight()
    assert str(exc.value) == NOT_INSTALLED
    assert NOT_INSTALLED == "OCR models not installed: run uv run newsrag ocr install"
    env.mp.setattr(ocr_mod, "_snapshot_ok", lambda: True)
    (env.tmp / "surya" / "bin").mkdir(parents=True)
    (env.tmp / "surya" / "bin" / "python").write_text("")
    env.set(surya_env_dir=env.tmp / "surya", vllm_env_dir=env.vllm_env)
    # Venv and vLLM there, but no tokenizer folder: still not installed.
    with pytest.raises(StageUnavailable, match="not installed"):
        OcrStage().preflight()
    folder = text_mod.tokenizer_dir()
    folder.mkdir(parents=True)
    for name in text_mod.TOKENIZER_FILES:
        (folder / name).write_text("{}")
    # labels.yaml missing: still not installed.
    env.set(labels_file=env.tmp / "missing.yaml")
    with pytest.raises(StageUnavailable, match="not installed"):
        OcrStage().preflight()
    env.set(labels_file=REPO_LABELS)
    OcrStage().preflight()  # everything there now
    env.mp.setattr(ocr_mod, "_snapshot_ok", lambda: False)
    with pytest.raises(StageUnavailable, match="not installed"):
        OcrStage().preflight()


def test_not_installed_cli_exits_2_before_db(env, monkeypatch):
    from newsrag.cli import app
    from newsrag.ledger import db

    env.set(surya_env_dir=env.tmp / "no-surya")

    def no_db():
        raise AssertionError("the DB was touched before preflight")

    monkeypatch.setattr(db, "conn", no_db)
    result = CliRunner().invoke(app, ["stage", "run", "ocr"])
    assert result.exit_code == 2
    assert NOT_INSTALLED in result.output


# --- E-5: zero regions ---


def test_zero_blocks_detail(env, ocr):
    env.layouts["empty.png"] = []
    env.save_layouts()
    result = ocr_page(ocr, env.page("empty"), OcrStage().version)
    assert result.detail["zero_blocks"] is True and result.detail["n_blocks"] == 0
    assert env.requests("vllm") == []  # nothing to read
    art = json.loads(Path(result.artifact_path).read_text())
    assert art["blocks"] == [] and art["markdown"] == ""


@pytest.mark.stores
def test_zero_blocks_page_is_done(env, ledger, registry, monkeypatch):
    from newsrag.ledger import db
    from newsrag.stages.base import PageStage, StageResult
    from newsrag.stages.runner import RunFilters, run_stage

    class FakeRender(PageStage):
        name = "render"
        version = "fake-render-v1-00000000"

        def handle(self, page):
            return StageResult(None, {})

    env.layouts["empty.png"] = []
    env.layouts["full.png"] = [region("Text", 0)]
    env.save_layouts()
    for page_id in ("empty", "full"):
        row = env.page(page_id)
        db.upsert_page(
            page_id=row.page_id,
            pub_date=row.pub_date,
            edition_code=row.edition_code,
            section=row.section,
            page_num=row.page_num,
            page_end=None,
            source_path=row.source_path,
            source_archive=row.source_archive,
        )
    stage = OcrStage()
    registry["render"] = FakeRender()
    registry["ocr"] = stage
    monkeypatch.setattr(ocr_mod, "make_client", env.client)
    monkeypatch.setattr(OcrStage, "preflight", lambda self: None)
    run_stage(registry["render"], registry=registry, out=io.StringIO())
    out = io.StringIO()
    printed = io.StringIO()
    monkeypatch.setattr(sys, "stdout", printed)
    result = run_stage(stage, RunFilters(), registry=registry, out=out)
    assert result.processed == 2 and result.failed == 0
    with db.conn() as c:
        rows = {
            r["page_id"]: r for r in db.fetch_all(c, "select * from stage_runs where stage = 'ocr'")
        }
    assert rows["empty"]["status"] == "done"
    assert rows["empty"]["detail"]["zero_blocks"] is True
    assert rows["full"]["detail"]["zero_blocks"] is False
    line = [x for x in printed.getvalue().splitlines() if x.startswith("ocr: ")]
    assert len(line) == 1 and line[0].startswith("ocr: 2 pages in ")
    assert "(excl. model load) = " in line[0] and line[0].endswith(" pages/s")
    assert not (env.tmp / ".gpu.lock").exists()  # released by close()
    starts = [json.loads(p.read_text())["argv"] for p in env.fake.glob("vllm-start-*.json")]
    assert len(starts) == 1  # one vLLM server for the whole run
    argv = starts[0]
    assert argv[argv.index("--served-model-name") + 1] == "indic-ocr"
    assert argv[argv.index("--tokenizer") + 1] == str(env.tmp / "tokenizer")

    # `ocr show`: one line per block; no artifact -> exit 1.
    from newsrag.cli import app

    shown = CliRunner().invoke(app, ["ocr", "show", "full"])
    assert shown.exit_code == 0
    assert shown.output.splitlines() == ["1\tText\t10,10,50,50\tతెలుగు"]
    missing = CliRunner().invoke(app, ["ocr", "show", "nope"])
    assert missing.exit_code == 1 and "no ocr artifact for nope" in missing.output
    missing = CliRunner().invoke(app, ["ocr", "show", "full", "--stage", "ocr_split"])
    assert missing.exit_code == 1 and "no ocr_split artifact for full" in missing.output


# --- E-8: settings, process groups, labels ---


def test_label_settings(tmp_path, clean_env):
    labels = tmp_path / "labels.yaml"
    labels_mod.write(
        {
            "Text": "text",
            "ListGroup": "text",
            "SectionHeader": "title",
            "PageHeader": "header_footer",
            "PageNumber": "page_number",
            "Table": "table",
            "Picture": "figure",
            "Diagram": "chart",
            "Advert": "advertisement",
            "Caption": "caption",
            "BlankPage": "other",
        },
        labels,
    )
    s = config.Settings(labels_file=labels)
    assert s.text_labels == ("ListGroup", "Text")
    assert s.title_labels == ("SectionHeader",)
    assert s.folio_labels == ("PageHeader", "PageNumber")
    assert s.table_labels == ("Table",)
    assert s.figure_labels == ("Picture",)
    assert s.chart_labels == ("Diagram",)
    assert s.ad_labels == ("Advert",)
    assert s.caption_labels == ("Caption",)
    # Overridable from the environment, as a JSON list or comma-separated.
    clean_env.setenv("NEWSRAG_TEXT_LABELS", '["A", "B"]')
    clean_env.setenv("NEWSRAG_AD_LABELS", "X, Y")
    s = config.Settings(labels_file=labels)
    assert s.text_labels == ("A", "B") and s.ad_labels == ("X", "Y")
    # No labels.yaml: empty sets, and importing config still works.
    clean_env.delenv("NEWSRAG_TEXT_LABELS")
    clean_env.delenv("NEWSRAG_AD_LABELS")
    s = config.Settings(labels_file=tmp_path / "missing.yaml")
    assert s.text_labels == () and s.folio_labels == ()


def test_repo_labels_yaml_complete():
    mapping = labels_mod.load(REPO_LABELS)
    assert len(mapping) >= 10
    assert set(mapping.values()) <= set(config.LABEL_GROUPS)
    for group in ("text", "title", "header_footer", "table", "figure"):
        assert group in mapping.values()
    assert mapping["Text"] == "text" and mapping["SectionHeader"] == "title"
    assert mapping["PageHeader"] == mapping["PageFooter"] == "header_footer"
    assert mapping["Table"] == "table" and mapping["Picture"] == "figure"


def test_process_group_killed(env, monkeypatch):
    from newsrag.models.lock import pid_alive

    monkeypatch.setenv("FAKE_OCR_CHILD", "1")
    worker = Worker(env.surya_spec())
    worker.start()
    child = int((env.fake / "surya-child.pid").read_text())
    pid = worker.pid
    assert pid_alive(child) and os.getpgid(child) == pid
    worker.stop()
    assert wait_dead(pid) and wait_dead(child)

    # A timeout kills the whole group too, not just the worker.
    env.set(ocr_request_timeout_s=1.0)
    worker.start()
    child = int((env.fake / "surya-child.pid").read_text())
    with pytest.raises(WorkerTimeout):
        worker.request({"op": "layout", "image": str(env.images / "hang.png")})
    assert wait_dead(child)
    worker.stop()


def test_unknown_label_other(env, ocr):
    env.layouts["p4.png"] = [region("Text", 0), region("Mystery", 1), region("Mystery", 2)]
    env.save_layouts()
    result = ocr_page(ocr, env.page("p4"), OcrStage().version)
    art = json.loads(Path(result.artifact_path).read_text())
    assert [b["type"] for b in art["blocks"]] == ["text", "other", "other"]
    assert len(env.requests("vllm")) == 1
    assert result.detail["unknown_labels"] == 2
    assert labels_mod.group_of("Mystery", labels_mod.load()) == "other"


def test_duplicate_regions_dropped(env, ocr):
    env.layouts["p5.png"] = [
        region("SectionHeader", 0, (10, 10, 80, 30), conf=0.55),
        region("Text", 1, (11, 11, 80, 29), conf=0.32),  # the same area, second label
        region("Text", 2, (10, 40, 80, 90), conf=0.31),  # new ground: kept
    ]
    env.save_layouts()
    result = ocr_page(ocr, env.page("p5"), OcrStage().version)
    art = json.loads(Path(result.artifact_path).read_text())
    assert [(b["label"], b["order"]) for b in art["blocks"]] == [("SectionHeader", 1), ("Text", 2)]
    assert result.detail["dup_regions_dropped"] == 1
    assert len(env.requests("vllm")) == 2


def test_drop_duplicate_regions_threshold():
    a = region("Text", 0, (0, 0, 100, 100), conf=0.3)
    b = region("Text", 1, (0, 0, 100, 75), conf=0.6)  # IoU 0.75: below 0.8, both kept
    assert len(ocr_mod.drop_duplicate_regions([a, b], 0.8)) == 2
    assert ocr_mod.drop_duplicate_regions([a, b], 0.7) == [b]


def test_labels_in_version(env):
    labels = env.tmp / "labels.yaml"
    labels.write_text(REPO_LABELS.read_text())
    env.set(labels_file=labels)
    stage = OcrStage()
    before = stage.version
    assert before.startswith("surya@9.9.9+indic-ocr@01234567-v1-")
    assert len(before.rsplit("-", 1)[1]) == 8
    mapping = labels_mod.load(labels)
    mapping["Diagram"] = "figure"
    labels_mod.write(mapping, labels)
    os.utime(labels, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    after = stage.version
    assert after != before and after.startswith("surya@9.9.9+indic-ocr@01234567-v1-")
    env.set(labels_file=labels, surya_version="9.9.10")
    assert OcrStage().version.startswith("surya@9.9.10+indic-ocr@01234567-v1-")


def test_nfc_in_client(env, monkeypatch):
    monkeypatch.setenv("FAKE_OCR_VLLM_MODE", "nfd")
    env.page("p5")
    with env.client() as ocr:
        texts = ocr.blocks(env.images / "p5.png", [{"bbox": [0, 0, 50, 50], "type": "text"}])
    nfd = "\u0c15\u0c46\u0c56"  # KA + E sign + AI length mark (what the fake sends)
    assert texts == ["\u0c15\u0c48"]  # composed: KA + AI sign
    assert texts[0] == unicodedata.normalize("NFC", nfd) != nfd


def test_token_budget_and_crops(env):
    big = Image.new("RGB", (2000, 3000))
    # Crops: clamped to the page, area scaled into [256^2, 1536^2], None without area.
    assert text_mod.crop_block(big, [0, 0, 100, 50]).size == (362, 181)
    assert text_mod.crop_block(big, [0, 0, 400, 300]).size == (400, 300)
    huge = text_mod.crop_block(big, [-50, -50, 2100, 3100])
    assert huge.width * huge.height <= 1536**2 and huge.size[0] < 2000
    assert text_mod.crop_block(big, [10, 10, 10, 50]) is None
    assert text_mod.crop_block(big, [2500, 10, 2600, 50]) is None
    # Budgets: tokens per megapixel of the crop, at least 96; tables get the max.
    assert text_mod.token_budget(Image.new("RGB", (256, 256)), "text") == 99
    assert text_mod.token_budget(Image.new("RGB", (100, 100)), "text") == 96
    assert text_mod.token_budget(Image.new("RGB", (1536, 1536)), "text") == 2048
    assert text_mod.token_budget(Image.new("RGB", (100, 100)), "table") == 2048
    env.set(ocr_tokens_per_mpx=1000, ocr_min_new_tokens=10)
    assert text_mod.token_budget(Image.new("RGB", (1000, 500)), "text") == 500
    env.page("p6")
    unstarted = TextOcr(text_mod.indic_profile(Path("/w"), Path("/t")))
    with pytest.raises(ValueError, match="unknown type"):
        unstarted.read(env.images / "p6.png", [{"bbox": [0, 0, 5, 5], "type": "x"}])


def test_text_server_died_message():
    assert issubclass(TextServerDied, RuntimeError)


# --- the worker protocol and client details ---


def test_stdout_noise_does_not_break_protocol(env, ocr):
    # The fake prints to stdout while loading; the first reply is still valid JSON.
    assert ocr.layout(env.images / "ok.png")["width"] == 100
    assert ocr.surya.load_s is not None
    assert "model loaded in" in (env.tmp / "logs" / "ocr-surya.log").read_text()


def test_bbox_scaled_to_png(env, ocr):
    env.layouts["big.png"] = [region("Text", 0, (10, 20, 50, 40))]
    env.layouts["big.png:size"] = [100, 100]  # Surya saw a 100 px image
    env.save_layouts()
    result = ocr_page(ocr, env.page("big", size=(200, 300)), OcrStage().version)
    art = json.loads(Path(result.artifact_path).read_text())
    assert art["blocks"][0]["bbox"] == [20, 60, 100, 120]


def test_start_failure_reports_tail(env):
    bad = WorkerSpec(
        "surya",
        [sys.executable, "-I", "-c", "import sys; print('boom', file=sys.stderr); sys.exit(4)"],
    )
    worker = Worker(bad)
    with pytest.raises(client_mod.WorkerStartError, match=r"exited 4 before loading(.|\n)*boom"):
        worker.start()


def test_missing_render_fails(env, ocr):
    page = env.page("gone")
    (env.images / "gone.png").unlink()
    with pytest.raises(FileNotFoundError, match="rendered page missing"):
        ocr_page(ocr, page, OcrStage().version)


def test_worker_specs_use_settings(env):
    env.set(
        surya_env_dir=Path("/x/surya"),
        ocr_vllm_gpu_memory_utilization=0.25,
        ocr_vllm_max_num_seqs=64,
        ocr_vllm_max_model_len=4096,
    )
    surya = client_mod.surya_spec()
    assert surya.argv[:3] == ["/x/surya/bin/python", "-I", str(client_mod.SURYA_WORKER)]
    assert surya.extra_env == {"HF_HUB_OFFLINE": "1"}
    profile = text_mod.indic_profile(Path("/snap/weights/ocr"), Path("/tok"))
    assert profile.name == "indic-ocr" and profile.hf_id == "/snap/weights/ocr"
    assert profile.gpu_memory_utilization == 0.25 and profile.max_model_len == 4096
    args = list(profile.extra_args)
    assert args[args.index("--tokenizer") + 1] == "/tok"
    assert args[args.index("--max-num-seqs") + 1] == "64"
    assert text_mod.tokenizer_dir() == env.tmp / "tokenizers" / (
        "0123456789abcdef0123456789abcdef01234567"
    )


def test_registered_and_import_is_light():
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["ocr"]
    assert stage.depends_on == ("render",)
    assert stage.concurrency == config.settings.ocr_pages_in_flight == 8


def test_workers_never_import_newsrag():
    for script in (client_mod.SURYA_WORKER, FAKE_WORKER, FAKE_VLLM):
        text = script.read_text()
        assert "import newsrag" not in text and "from newsrag" not in text
    assert "newsrag" not in (client_mod.HERE / "_protocol.py").read_text().split('"""', 2)[2]


# --- E-4: install messages ---


def test_missing_token(env):
    from newsrag.cli import app

    env.set(hf_token=None, indic_ocr_revision=None)
    result = CliRunner().invoke(app, ["ocr", "install"])
    assert result.exit_code == 2
    assert result.output.strip() == (
        "cannot download bodhan-ai/indic-ocr: accept the licence on huggingface.co "
        "and set HF_TOKEN in .env"
    )


def test_licence_not_accepted_message(env, monkeypatch):
    from huggingface_hub import HfApi
    from huggingface_hub.errors import GatedRepoError
    from pydantic import SecretStr

    from newsrag.cli import app

    env.set(hf_token=SecretStr("hf_fake"), indic_ocr_revision=None)

    def gated(self, *args, **kwargs):
        import httpx

        request = httpx.Request("GET", "https://huggingface.co/api/models/bodhan-ai/indic-ocr")
        raise GatedRepoError("gated", response=httpx.Response(403, request=request))

    monkeypatch.setattr(HfApi, "model_info", gated)
    result = CliRunner().invoke(app, ["ocr", "install"])
    assert result.exit_code == 2
    assert result.output.strip() == install_mod.LICENCE_MESSAGE


CONTRACT = """
from enum import StrEnum
class TableFormat(StrEnum):
    HTML = "html"
    MARKDOWN = "markdown"
TEXT_PROMPT = (
    "Transcribe the text in this image. Write any mathematical expressions in LaTeX, "
    "using $...$ for inline math and $$...$$ for display equations."
)
TABLE_PROMPTS = {
    TableFormat.HTML: (
        "Convert this table image to HTML. Preserve the structure exactly, using colspan and "
        "rowspan for merged cells and <br/> for line breaks within a cell. "
        "Output only the HTML table."
    ),
    TableFormat.MARKDOWN: "Convert this table image to markdown.",
}
raise SystemExit("this file must never be run")
"""


def test_snapshot_prompts_read_without_running(tmp_path):
    (tmp_path / "idp_contract.py").write_text(CONTRACT)
    assert install_mod.snapshot_prompts(tmp_path) == text_mod.PROMPTS
    (tmp_path / "idp_contract.py").write_text(CONTRACT.replace("Transcribe", "Read"))
    assert install_mod.snapshot_prompts(tmp_path)["text"] != TEXT_PROMPT


@pytest.mark.data
def test_prompts_match_pinned_snapshot():
    snapshot = install_mod.indic_snapshot()
    if snapshot is None:
        pytest.skip("indic-ocr snapshot not installed")
    assert install_mod.snapshot_prompts(snapshot) == text_mod.PROMPTS


def test_cuda_version_check(env):
    assert install_mod.cuda_ok("12.8") and install_mod.cuda_ok("13.2")
    assert not install_mod.cuda_ok("12.4") and not install_mod.cuda_ok(None)


def test_show_line_format():
    from newsrag.ocr.cli import block_line

    block = {"order": 3, "label": "Text", "bbox": [1, 2.4, 3.6, 4], "text": "a\nb " + "x" * 80}
    assert block_line(block) == "3\tText\t1,2,4,4\t" + ("a b " + "x" * 80)[:60]


# --- real models on 2 pages ---


@pytest.mark.gpu
@pytest.mark.data
def test_real_models_two_pages(tmp_path, monkeypatch):
    sample = [
        line.strip()
        for line in config.settings.dev_sample_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    pages = [p for p in sample if (config.settings.images_dir / f"{p}.png").exists()][:2]
    if len(pages) < 2:
        pytest.skip("needs 2 rendered dev-sample pages")
    OcrStage().preflight()
    patched = config.settings.model_copy(update={"work_dir": tmp_path})
    monkeypatch.setattr(base_mod, "settings", patched)
    stage = OcrStage()
    stage.open()
    try:
        for page_id in pages:
            row = PageRow(page_id, date(2025, 1, 1), "B", "main", 1, None, "", "", 1)
            result = stage.handle(row)
            art = json.loads(Path(result.artifact_path).read_text())
            assert result.detail["n_blocks"] > 0
            text = "".join(b["text"] for b in art["blocks"])
            assert len(text) > 500
            assert any("ఀ" <= ch <= "౿" for ch in text)  # Telugu script
            assert text == unicodedata.normalize("NFC", text)
    finally:
        stage.close()
