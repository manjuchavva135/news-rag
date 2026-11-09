"""Ticket 010: stage `ocr_final` and the Sarvam fallback. A fake Sarvam API stands in for the SDK
(no network): it reads the crop sizes from the uploaded PDF and answers per PDF page."""

from __future__ import annotations

import io
import json
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

import newsrag.stages.base as base_mod
from newsrag import config
from newsrag.ocr import fallback, sarvam
from newsrag.stages import ocr_final as final_mod
from newsrag.stages.base import PageRow, StageUnavailable
from newsrag.stages.ocr_final import NO_KEY, SARVAM_FAILED, OcrFinalStage, final_page

REPO = Path(__file__).resolve().parents[1]


class HttpError(Exception):
    """Like sarvamai's ApiError: carries `status_code`."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class FakeSarvam:
    """The `SarvamApi` calls. Each crop's reply is `reply(w, h)`: Sarvam blocks for that PDF page."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.lock = threading.Lock()
        self.calls: list[str] = []
        self.pdf_pages: dict[str, list[tuple[int, int]]] = {}
        self.active = 0
        self.max_active = 0
        self.state = "Completed"
        self.missing_pages: set[int] = set()
        self.errors: dict[str, list[Exception]] = {}  # call -> exceptions raised first, in order
        self.status_delay = 0.0
        self.n = 0

    def reply(self, w: int, h: int) -> list[dict]:
        return [{"text": f"crop {w}x{h}", "layout_tag": "paragraph", "reading_order": 1}]

    def _call(self, name: str) -> None:
        with self.lock:
            self.calls.append(name)
            queue = self.errors.get(name)
            if queue:
                raise queue.pop(0)

    def create(self) -> str:
        self._call("create")
        with self.lock:
            self.n += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            return f"job-{self.n}"

    def upload(self, job_id: str, pdf: Path) -> None:
        self._call("upload")
        data = pdf.read_bytes()
        widths = [int(x) for x in re.findall(rb"/Width (\d+)", data)]
        heights = [int(x) for x in re.findall(rb"/Height (\d+)", data)]
        with self.lock:
            self.pdf_pages[job_id] = list(zip(widths, heights, strict=True))

    def start(self, job_id: str) -> None:
        self._call("start")

    def status(self, job_id: str) -> str:
        self._call("status")
        if self.status_delay:
            time.sleep(self.status_delay)
        if self.state in ("Failed", "PartiallyCompleted"):
            with self.lock:
                self.active -= 1
        return self.state

    def download(self, job_id: str, out: Path) -> None:
        self._call("download")
        with zipfile.ZipFile(out, "w") as zf:
            for n, (w, h) in enumerate(self.pdf_pages[job_id], start=1):
                if n in self.missing_pages:
                    continue
                meta = {"page_num": n, "blocks": self.reply(w, h)}
                zf.writestr(f"out/metadata/page_{n:03d}.json", json.dumps(meta))
        with self.lock:
            self.active -= 1

    def jobs(self) -> int:
        return self.calls.count("create")


class Env:
    def __init__(self, tmp: Path, monkeypatch):
        self.tmp = tmp
        self.mp = monkeypatch
        self.rules_file = tmp / "ocr_fallback.yaml"
        self.rules_file.write_text("rules: []\n")
        self.images = tmp / "images"
        self.images.mkdir()
        self.sleeps: list[float] = []
        self.set(
            work_dir=tmp / "work",
            images_dir=self.images,
            ocr_fallback_file=self.rules_file,
            sarvam_cache_dir=tmp / "work" / "sarvam-cache",
            sarvam_api_key="test-key",
            text_labels=("Text",),
            title_labels=("SectionHeader",),
        )
        monkeypatch.setattr(sarvam, "_sleep", self.sleeps.append)
        monkeypatch.setattr(sarvam, "_slots", None)
        self.fake = FakeSarvam(tmp)
        monkeypatch.setattr(final_mod, "make_api", lambda: self.fake)

    def set(self, **kw) -> config.Settings:
        if "sarvam_api_key" in kw and isinstance(kw["sarvam_api_key"], str):
            kw["sarvam_api_key"] = config.SecretStr(kw["sarvam_api_key"])
        current = config.settings.model_copy(update=kw)
        for mod in (config, base_mod):
            self.mp.setattr(mod, "settings", current)
        return current

    def rules(self, text: str) -> None:
        self.rules_file.write_text(text)

    def page(self, page_id="2020-04-02_B_main_01", year=2020, page_num=1, page_end=None, **kw):
        return PageRow(
            page_id=page_id,
            pub_date=date(year, 4, 2),
            edition_code=kw.get("edition_code", "B"),
            section=kw.get("section", "main"),
            page_num=page_num,
            page_end=page_end,
            source_path=str(self.tmp / f"{page_id}.pdf"),
            source_archive=f"{year}.zip",
            revision=1,
            quarantine_reason=kw.get("quarantine_reason"),
        )

    def split(self, page_id: str, boxes: list[tuple[str, tuple[int, int, int, int]]], size=None):
        """An `ocr_split` artifact for a white page with a distinct mark in every box."""
        width = size[0] if size else 2000
        height = size[1] if size else 4000
        img = Image.new("RGB", (width, height), "white")
        for i, (_, (x1, y1, x2, y2)) in enumerate(boxes):
            img.paste((i * 7 % 256, 0, 0), (x1 + 3, y1 + 3, x1 + 6, y1 + 6))
        path = self.images / f"{page_id}.png"
        img.save(path)
        blocks = [
            {
                "block_id": f"b{i + 1:03d}",
                "order": i + 1,
                "label": label,
                "type": "text",
                "bbox": list(bbox),
                "conf": 0.9,
                "text": f"indic {i + 1}",
            }
            for i, (label, bbox) in enumerate(boxes)
        ]
        return {
            "page_id": page_id,
            "image": {"path": str(path), "width": width, "height": height},
            "model": {"layout": "surya@test"},
            "blocks": blocks,
            "markdown": "\n\n".join(b["text"] for b in blocks),
        }

    def run(self, data: dict, page: PageRow | None = None):
        page = page or self.page(data["page_id"])
        matched = fallback.matching_rules(fallback.load_rules(), page)
        result = final_page(data, matched, final_mod.make_api)
        return result, json.loads(Path(result.artifact_path).read_text())


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def column(n: int, label="Text", w0=100) -> list[tuple[str, tuple[int, int, int, int]]]:
    """`n` boxes of distinct sizes (so the fake can tell the crops apart)."""
    return [(label, (50, 30 + 60 * i, 50 + w0 + i, 30 + 60 * i + 40 + (i % 3))) for i in range(n)]


def crop_size(bbox, pad=2) -> tuple[int, int]:
    x1, y1, x2, y2 = bbox
    return x2 - x1 + 2 * pad, y2 - y1 + 2 * pad


MATCH_2020 = "rules:\n  - years: [2020]\n"


# --- E-1 / FR-2: pass-through ---


def test_committed_default_has_no_rules():
    text = (REPO / "ocr_fallback.yaml").read_text()
    assert re.search(r"^rules: *\[\] *$", text, re.MULTILINE)
    assert fallback.load_rules(REPO / "ocr_fallback.yaml") == []


def test_unmatched_page_is_copied_without_network(env, monkeypatch):
    def boom():
        raise AssertionError("Sarvam must not be called")

    monkeypatch.setattr(final_mod, "make_api", boom)
    env.set(sarvam_api_key=None)
    data = env.split("2020-04-02_B_main_01", column(3))
    result, art = env.run(data)
    assert result.detail == {"fallback": False}
    assert [b["text"] for b in art["blocks"]] == ["indic 1", "indic 2", "indic 3"]
    assert {b["ocr_source"] for b in art["blocks"]} == {"indic"}
    assert "/ocr_final/final-v1/" in result.artifact_path


def test_page_flags_present(env):
    _, art = env.run(env.split("p1", column(1)))
    assert art["page_flags"] == []
    env.rules(MATCH_2020)
    _, art = env.run(env.split("p1", column(1)))
    assert art["page_flags"] == []  # a successful re-read flags nothing


# --- E-2 / FR-3: the fallback path ---


def test_matching_page_replaces_block_text_keeps_ids(env):
    env.rules(MATCH_2020)
    boxes = [*column(2), ("Picture", (900, 100, 1300, 500)), ("SectionHeader", (50, 300, 700, 380))]
    data = env.split("p1", boxes)
    env.fake.reply = lambda w, h: [
        {"text": "second", "layout_tag": "paragraph", "reading_order": 2},
        {"text": "first", "layout_tag": "headline", "reading_order": 1},
        {"text": f"{w}x{h}", "layout_tag": "paragraph", "reading_order": 3},
    ]
    result, art = env.run(data)
    blocks = art["blocks"]
    for got, sent in zip(blocks, data["blocks"], strict=True):
        for key in ("block_id", "order", "bbox", "label", "type", "conf"):
            assert got[key] == sent[key]
    w, h = crop_size(boxes[0][1])
    assert blocks[0]["text"] == f"first\nsecond\n{w}x{h}"  # reading order, joined with \n
    assert [b["ocr_source"] for b in blocks] == ["sarvam", "sarvam", "indic", "sarvam"]
    assert blocks[2]["text"] == "indic 3"  # a Picture is not a re-read label
    assert result.detail["fallback"] is True
    assert result.detail["n_sent"] == 3 and result.detail["n_replaced"] == 3
    assert result.detail["sarvam_job_ids"] == ["job-1"]
    assert result.detail["sarvam_failed_blocks"] == 0
    assert art["markdown"].startswith("first\nsecond")
    assert "/ocr_final/final-v1-sarvam-" in result.artifact_path


def test_sarvam_text_is_nfc(env):
    env.rules(MATCH_2020)
    decomposed = "é"
    env.fake.reply = lambda w, h: [{"text": decomposed, "layout_tag": "paragraph"}]
    _, art = env.run(env.split("p1", column(1)))
    assert art["blocks"][0]["text"] == "é"


def test_image_tag_blocks_discarded(env):
    env.rules(MATCH_2020)
    env.fake.reply = lambda w, h: [
        {"text": "ఈ చిత్రంలో ఒక వ్యక్తి", "layout_tag": "image", "reading_order": 1},
        {"text": "అసలు వాక్యం", "layout_tag": "paragraph", "reading_order": 2},
    ]
    _, art = env.run(env.split("p1", column(1)))
    assert art["blocks"][0]["text"] == "అసలు వాక్యం"


def test_empty_sarvam_keeps_indic_text(env):
    env.rules(MATCH_2020)
    env.fake.reply = lambda w, h: (
        [{"text": "  ", "layout_tag": "paragraph"}, {"text": "pic", "layout_tag": "image"}]
        if w % 2
        else [{"text": "read", "layout_tag": "paragraph"}]
    )
    data = env.split("p1", column(2))  # widths 104 and 105 with padding
    result, art = env.run(data)
    by_source = {b["ocr_source"]: b["text"] for b in art["blocks"]}
    assert by_source == {"sarvam": "read", "indic_kept": "indic 2"}
    assert result.detail["n_sent"] == 2 and result.detail["n_replaced"] == 1
    assert art["page_flags"] == [] and result.detail["sarvam_failed_blocks"] == 0


def test_ten_crops_per_job(env):
    env.rules(MATCH_2020)
    boxes = column(25)
    result, art = env.run(env.split("p1", boxes))
    assert sorted(len(p) for p in env.fake.pdf_pages.values()) == [5, 10, 10]
    assert env.fake.jobs() == 3 and len(result.detail["sarvam_job_ids"]) == 3
    for block, (_, bbox) in zip(art["blocks"], boxes, strict=True):  # each crop's own text
        w, h = crop_size(bbox)
        assert block["text"] == f"crop {w}x{h}"


def test_retry_on_429_four_attempts(env):
    env.rules(MATCH_2020)
    env.fake.errors["create"] = [HttpError(429), HttpError(503), ConnectionError("reset")]
    result, art = env.run(env.split("p1", column(1)))
    assert env.fake.calls.count("create") == 4
    assert env.sleeps == [2.0, 8.0, 30.0]
    assert art["blocks"][0]["ocr_source"] == "sarvam"
    assert result.detail["sarvam_failed_blocks"] == 0


def test_retry_gives_up_after_four_attempts(env):
    env.rules(MATCH_2020)
    env.fake.errors["status"] = [HttpError(500) for _ in range(4)]
    result, art = env.run(env.split("p1", column(2)))
    assert env.fake.calls.count("status") == 4
    assert [b["ocr_source"] for b in art["blocks"]] == ["indic_kept", "indic_kept"]
    assert art["page_flags"] == [SARVAM_FAILED]
    assert result.detail["sarvam_failed_blocks"] == 2


def test_no_retry_on_400_and_auth_error_fails_page(env):
    env.rules(MATCH_2020)
    env.fake.errors["upload"] = [HttpError(400)]
    result, _ = env.run(env.split("p1", column(1)))
    assert env.fake.calls.count("upload") == 1 and result.detail["sarvam_failed_blocks"] == 1
    env.fake.errors["create"] = [HttpError(401)]
    with pytest.raises(sarvam.SarvamAuthError, match="HTTP 401"):
        env.run(env.split("p2", column(1, w0=300)))


def test_max_four_concurrent_jobs(env):
    env.rules(MATCH_2020)
    env.fake.status_delay = 0.05
    datas = [env.split(f"p{i}", column(50, w0=100 + 60 * i)) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:  # two pages at once
        for future in [pool.submit(env.run, d) for d in datas]:
            future.result()
    assert env.fake.jobs() == 10
    assert env.fake.max_active == 4


@pytest.mark.parametrize("how", ["Failed", "PartiallyCompleted", "timeout", "missing_page"])
def test_failed_job_keeps_indic_and_flags(env, monkeypatch, how):
    env.rules(MATCH_2020)
    if how == "timeout":
        env.fake.state = "Running"
        clock = iter(range(0, 10_000, 100))
        monkeypatch.setattr(sarvam, "_monotonic", lambda: float(next(clock)))
    elif how == "missing_page":
        env.fake.missing_pages = {2}
    else:
        env.fake.state = how
    result, art = env.run(env.split("p1", column(3)))
    sources = [b["ocr_source"] for b in art["blocks"]]
    texts = [b["text"] for b in art["blocks"]]
    if how == "missing_page":
        assert sources == ["sarvam", "indic_kept", "sarvam"] and texts[1] == "indic 2"
        assert result.detail["sarvam_failed_blocks"] == 1
    else:
        assert sources == ["indic_kept"] * 3 and texts == ["indic 1", "indic 2", "indic 3"]
        assert result.detail["sarvam_failed_blocks"] == 3
    assert art["page_flags"] == [SARVAM_FAILED]
    assert result.detail["sarvam_job_ids"] == ["job-1"]
    if how == "timeout":
        assert "download" not in env.fake.calls


def test_cache_prevents_second_call(env):
    env.rules(MATCH_2020)
    data = env.split("p1", column(3))
    _, art1 = env.run(data)
    assert env.fake.jobs() == 1
    env.fake.errors["create"] = [AssertionError("no second job")]
    second, art2 = env.run(data)
    assert env.fake.jobs() == 1
    assert art2["blocks"] == art1["blocks"]
    assert second.detail["n_cached"] == 3 and second.detail["sarvam_job_ids"] == ["job-1"]
    # a failed crop is not cached: only it is sent again
    env.fake.errors.clear()
    env.fake.missing_pages = {1}
    new = env.split("p2", column(2, w0=400))
    env.run(new)
    env.fake.missing_pages = set()
    _, again = env.run(new)
    assert env.fake.jobs() == 3 and len(env.fake.pdf_pages["job-3"]) == 1
    assert {b["ocr_source"] for b in again["blocks"]} == {"sarvam"}


def test_cached_page_needs_no_client(env, monkeypatch):
    env.rules(MATCH_2020)
    data = env.split("p1", column(2))
    env.run(data)

    def boom():
        raise AssertionError("client created although every crop is cached")

    monkeypatch.setattr(final_mod, "make_api", boom)
    _, art = env.run(data)
    assert {b["ocr_source"] for b in art["blocks"]} == {"sarvam"}


def test_tiny_blocks_not_sent(env):
    env.rules(MATCH_2020)
    boxes = [
        ("Text", (10, 10, 29, 400)),
        ("Text", (10, 500, 400, 519)),
        ("Text", (10, 600, 30, 620)),
    ]
    result, art = env.run(env.split("p1", boxes))
    assert [b["ocr_source"] for b in art["blocks"]] == ["indic_kept", "indic_kept", "sarvam"]
    assert [len(p) for p in env.fake.pdf_pages.values()] == [1]
    assert result.detail["n_sent"] == 1
    assert art["page_flags"] == []


def test_crop_padding_clipped_to_page():
    assert sarvam.crop_box([0, 1, 50, 99], (100, 100), 2) == (0, 0, 52, 100)
    assert sarvam.crop_box([10, 10, 20, 20], (100, 100), 2) == (8, 8, 22, 22)


def test_rule_labels_choose_blocks(env):
    env.rules("rules:\n  - years: [2020]\n    labels: [SectionHeader]\n")
    boxes = [("Text", (50, 50, 400, 100)), ("SectionHeader", (50, 200, 400, 260))]
    _, art = env.run(env.split("p1", boxes))
    assert [b["ocr_source"] for b in art["blocks"]] == ["indic", "sarvam"]


# --- E-4 / FR-4: missing key ---


def test_missing_key_fails_page(env):
    env.rules(MATCH_2020)
    env.set(sarvam_api_key=None)
    with pytest.raises(RuntimeError) as err:
        env.run(env.split("p1", column(1)))
    assert (
        str(err.value) == NO_KEY == "SARVAM_API_KEY not set but page matches an ocr_fallback rule"
    )
    assert env.fake.calls == []


@pytest.mark.stores
def test_missing_key_page_failed_in_ledger(env, ledger, registry, monkeypatch):
    from newsrag.ledger import db
    from newsrag.stages.base import PageStage, StageResult
    from newsrag.stages.runner import RunFilters, run_stage

    class FakeSplit(PageStage):
        name = "ocr_split"
        version = "fake-split-v1-00000000"

        def __init__(self, arts):
            self.arts = arts

        def handle(self, page):
            path = env.tmp / "split" / f"{page.page_id}.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(self.arts[page.page_id]))
            return StageResult(str(path), {})

    env.rules(MATCH_2020)
    env.set(sarvam_api_key=None)
    arts = {}
    for page_id, year in (("2020-04-02_B_main_01", 2020), ("2021-04-02_B_main_01", 2021)):
        page = env.page(page_id, year=year)
        arts[page_id] = env.split(page_id, column(1))
        db.upsert_page(
            page_id=page.page_id,
            pub_date=page.pub_date,
            edition_code=page.edition_code,
            section=page.section,
            page_num=page.page_num,
            page_end=None,
            source_path=page.source_path,
            source_archive=page.source_archive,
        )
    registry["ocr_split"] = FakeSplit(arts)
    registry["ocr_final"] = stage = OcrFinalStage()
    run_stage(registry["ocr_split"], registry=registry, out=io.StringIO())
    result = run_stage(stage, RunFilters(), registry=registry, out=io.StringIO())
    assert result.processed == 1 and result.failed == 1
    with db.conn() as c:
        rows = {
            r["page_id"]: r
            for r in db.fetch_all(c, "select * from stage_runs where stage = 'ocr_final'")
        }
    failed = rows["2020-04-02_B_main_01"]
    assert failed["status"] == "failed"
    assert failed["error"].strip().splitlines()[-1].endswith(NO_KEY)
    assert failed["version"].startswith("final-v1-sarvam-")
    done = rows["2021-04-02_B_main_01"]
    assert done["status"] == "done" and done["version"] == "final-v1"
    assert done["detail"] == {"fallback": False}


# --- E-5 / FR-2: versions ---


def test_version_changes_with_rules(env):
    stage = OcrFinalStage()
    p2020, p2021 = env.page("a", year=2020), env.page("b", year=2021)
    assert stage.version_for(p2020) == "final-v1"
    env.rules(MATCH_2020)
    v1 = stage.version_for(p2020)
    assert re.fullmatch(r"final-v1-sarvam-[0-9a-f]{8}", v1)
    env.rules("rules:\n  - years: [2020]\n    labels: [Text]\n")
    v2 = stage.version_for(p2020)
    assert v2 != v1 and v2.startswith("final-v1-sarvam-")
    assert stage.version_for(p2021) == "final-v1"


def test_unmatched_pages_keep_version(env):
    stage = OcrFinalStage()
    pages = [env.page("a", year=2020), env.page("b", year=2021, section="fea")]
    before = [stage.version_for(p) for p in pages]
    env.rules("rules:\n  - years: [2021]\n    sections: [fea]\n")
    after = [stage.version_for(p) for p in pages]
    assert before == ["final-v1", "final-v1"]
    assert after[0] == "final-v1" and after[1].startswith("final-v1-sarvam-")
    # adding a rule that matches only another page keeps this page's version
    env.rules("rules:\n  - years: [2021]\n    sections: [fea]\n  - years: [2019]\n")
    assert [stage.version_for(p) for p in pages] == after


def test_rule_order_does_not_change_version(env):
    stage = OcrFinalStage()
    page = env.page("a", year=2020)
    env.rules("rules:\n  - years: [2020]\n  - page_nums: [1]\n")
    first = stage.version_for(page)
    env.rules("rules:\n  - page_nums: [1]\n  - years: [2020]\n  - years: [2020]\n")
    assert stage.version_for(page) == first


def test_invalid_rules_stop_preflight(env):
    env.rules("rules:\n  - year: [2020]\n")
    with pytest.raises(StageUnavailable, match="unknown key 'year'"):
        OcrFinalStage().preflight()
    env.rules_file.unlink()
    with pytest.raises(StageUnavailable, match="not found"):
        OcrFinalStage().preflight()


# --- E-6 / FR-6 ---


def test_empty_list_means_any(env):
    page = env.page("a", year=2020, section="fea", edition_code="Z", page_num=7)
    for rule in (
        {"years": [2020], "sections": [], "edition_codes": None, "page_nums": []},
        {"sections": None},
        {},
    ):
        assert fallback.matches(fallback.normalise_rule(rule), page)
    assert not fallback.matches(fallback.normalise_rule({"years": [2021], "sections": []}), page)


def test_rule_normalisation(env):
    raw = {"page_nums": [3, 1, 3], "sections": [], "years": [2021, 2020], "labels": None}
    assert fallback.normalise_rule(raw) == {"page_nums": [1, 3], "years": [2020, 2021]}
    assert list(fallback.normalise_rule(raw)) == ["page_nums", "years"]
    page = env.page("a", year=2020, page_num=3)
    env.rules("rules:\n  - {years: [2021, 2020], page_nums: [3, 1, 3], sections: []}\n")
    v1 = OcrFinalStage().version_for(page)
    env.rules("rules:\n  - years: [2020, 2021]\n    page_nums: [1, 3]\n")
    assert OcrFinalStage().version_for(page) == v1
    with pytest.raises(fallback.RulesError, match="whole numbers"):
        fallback.normalise_rule({"years": ["twenty"]})


def test_spread_matches_page_nums(env):
    rule = fallback.normalise_rule({"page_nums": [5]})
    assert fallback.matches(rule, env.page("s", page_num=4, page_end=5))
    assert fallback.matches(rule, env.page("s", page_num=5, page_end=6))
    assert not fallback.matches(rule, env.page("s", page_num=6, page_end=7))
    assert not fallback.matches(rule, env.page("s", page_num=4))


# --- E-3 / FR-5: preview ---


def test_preview_counts(env):
    rules = fallback.parse_rules({"rules": [{"years": [2020], "page_nums": [1]}]})
    pages = [
        env.page("a", year=2020, page_num=1),
        env.page("b", year=2020, page_num=1, section="fea"),
        env.page("c", year=2020, page_num=2),
        env.page("d", year=2020, page_num=1, quarantine_reason="bad pdf"),
        env.page("e", year=2025, page_num=1),
        env.page("f", year=2020, page_num=1, edition_code="C"),
    ]
    assert fallback.preview_counts(pages, rules) == [(2020, "fea", 1), (2020, "main", 2)]
    assert fallback.preview_counts(pages, []) == []


def test_preview_no_network(env, monkeypatch):
    from newsrag.cli import app

    def boom(*a, **k):
        raise AssertionError("preview must not call Sarvam")

    monkeypatch.setattr(final_mod, "make_api", boom)
    monkeypatch.setattr(sarvam, "SdkApi", boom)
    monkeypatch.setattr(sarvam, "run_job", boom)
    pages = [env.page("a", year=2020), env.page("b", year=2020), env.page("c", year=2025)]
    monkeypatch.setattr(fallback, "registered_pages", lambda: pages)
    env.rules("rules:\n  - years: [2020]\n    page_nums: [1]\n")
    result = CliRunner().invoke(app, ["ocr", "fallback", "preview"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["2020\tmain\t2"]


# --- wiring ---


def test_registered_and_import_is_light():
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["ocr_final"]
    assert isinstance(stage, OcrFinalStage)
    assert stage.depends_on == ("ocr_split",) and stage.concurrency == 1


def test_settings_defaults(clean_env):
    s = config.Settings()
    assert s.ocr_fallback_file == s.repo_root / "ocr_fallback.yaml"
    assert s.sarvam_cache_dir == s.work_dir / "sarvam-cache"
    assert s.sarvam_retry_backoff_s == (2.0, 8.0, 30.0)
    assert (s.sarvam_crops_per_job, s.sarvam_max_jobs, s.sarvam_job_timeout_s) == (10, 4, 600.0)
    clean_env.setenv("NEWSRAG_SARVAM_RETRY_BACKOFF_S", "1,2")
    assert config.Settings().sarvam_retry_backoff_s == (1.0, 2.0)


def test_sdk_retries_off_and_status_parsing():
    assert sarvam.SdkApi.NO_RETRY == {"max_retries": 0}
    assert sarvam.retryable(HttpError(429)) and sarvam.retryable(HttpError(502))
    assert not sarvam.retryable(HttpError(400)) and not sarvam.retryable(ValueError("x"))
