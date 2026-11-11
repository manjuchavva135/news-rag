"""Ticket 015: tables, infographics and notices transcription (CPU only, a scripted fake model and
a fake ledger: nothing touches the GPU, Postgres or the real artifacts)."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from typer.testing import CliRunner

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.stages import transcribe as tr
from newsrag.stages.base import PageRow
from newsrag.stages.transcribe import TranscribeStage, select_targets, transcribe_page
from newsrag.transcribe import spotcheck as sc

PAGE = "2024-12-06_X_main_01"
TABLE = config.settings.table_labels[0]
FIGURE = config.settings.figure_labels[0]
CHART = config.settings.chart_labels[0]


def blk(order, bbox, label="Text", text=""):
    return {
        "block_id": f"b{order:03d}",
        "order": order,
        "label": label,
        "bbox": list(bbox),
        "text": text,
    }


def art(n, kind, entries, flags=()):
    return {
        "article_id": f"{PAGE}#a{n:02d}",
        "kind": kind,
        "blocks": [{"block_id": f"b{o:03d}", "role": role} for o, role in entries],
        "flags": list(flags),
    }


# A page with: a news story holding a table block (2) and an infographic-role block (3); a notice
# article (4, 5) whose block 5 has a table label; an ad (6); a standalone table (7) and photo (8)
# in no article; a photo inside the news story (9, not a target).
BLOCKS = [
    blk(1, (10, 10, 190, 60), "SectionHeader", "Story headline"),
    blk(2, (10, 70, 190, 200), TABLE),
    blk(3, (10, 210, 190, 300), FIGURE),
    blk(4, (210, 10, 390, 60), "Text", "TENDER NOTICE"),
    blk(5, (210, 70, 390, 200), TABLE),
    blk(6, (210, 210, 390, 300), "Text", "Buy now"),
    blk(7, (10, 320, 190, 450), TABLE),
    blk(8, (210, 320, 390, 450), FIGURE),
    blk(9, (10, 460, 190, 590), FIGURE),
]
GROUPING = {
    "page_id": PAGE,
    "articles": [
        art(1, "news", [(1, "headline"), (2, "table"), (3, "infographic"), (9, "photo")]),
        art(2, "notice_tender", [(4, "headline"), (5, "table")]),
        art(3, "ad_commercial", [(6, "body")]),
    ],
    "unassigned": ["b007", "b008"],
    "page_flags": [],
}


def table_reply(markdown="| a | b |\n|---|---|\n| 1 | 2 |", title="T"):
    return json.dumps({"type": "table", "title": title, "markdown": markdown})


def info_reply():
    return json.dumps({"type": "infographic", "title": "Debt", "markdown": "Debt\n- 2024: 10"})


PHOTO = json.dumps({"type": "photo_no_text", "title": None, "markdown": ""})


def notice_reply(klass="notice_tender"):
    return json.dumps(
        {
            "class": klass,
            "markdown": "టెండర్ ప్రకటన" if klass == "notice_tender" else "",
            "department": "Mines" if klass == "notice_tender" else None,
            "notice_number": "12/2024" if klass == "notice_tender" else None,
            "deadline": "2024-12-20" if klass == "notice_tender" else None,
        }
    )


class Scripted:
    """`ask(messages, kind)`: replies from a queue, every request recorded."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[tuple[list, int]] = []

    def __call__(self, messages, kind=None):
        self.requests.append((messages, kind))
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def set_settings(monkeypatch):
    import newsrag.stages.base as base_mod

    def apply(**kw):
        current = config.settings.model_copy(update=kw)
        for mod in (config, base_mod):
            monkeypatch.setattr(mod, "settings", current)
        return current

    return apply


@pytest.fixture
def data(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (400, 600), "white").save(path)
    return {"page_id": PAGE, "image": {"path": str(path)}, "page_flags": [], "blocks": BLOCKS}


def grouping(**changes):
    g = json.loads(json.dumps(GROUPING))
    g.update(changes)
    return g


def run(data, g=None, ask=None):
    ask = ask or _script_in_order()
    return transcribe_page(data, g or grouping(), lambda: ask), ask


def by_id(artifact):
    return {i["item_id"].split("#")[1]: i for i in artifact["items"]}


# --- E-1 --------------------------------------------------------------------------------------


def test_targets_tables_infographics_notices(data):
    targets = select_targets(data, grouping())
    got = [(t.item_id.split("#")[1], t.source, t.block_ids) for t in targets]
    assert got == [
        ("t01", "block", ["b002"]),
        ("t02", "block", ["b003"]),
        ("t03", "article", ["b004", "b005"]),
        ("t04", "article", ["b006"]),
        ("t05", "block", ["b007"]),
        ("t06", "block", ["b008"]),
    ]


def _script_in_order():
    # t01 table, t02 infographic, t03 notice, t04 ad, t05 standalone table, t06 photo
    return Scripted(
        table_reply(),
        info_reply(),
        notice_reply(),
        notice_reply("commercial"),
        table_reply(),
        PHOTO,
    )


def test_photo_no_text_dropped(data):
    artifact, _ = run(data, ask=_script_in_order())
    assert "t06" not in by_id(artifact)
    assert artifact["dropped"] == [{"item_id": f"{PAGE}#t06", "reason": "photo_no_text"}]
    assert artifact["transcribe_failed"] == 0 and artifact["page_flags"] == []


def test_commercial_not_kept(data):
    artifact, _ = run(data, ask=_script_in_order())
    ad = by_id(artifact)["t04"]
    assert ad["type"] == "commercial" and ad["article_id"] == f"{PAGE}#a03"
    assert all(ad[k] is None for k in ("title", "markdown", "department", "notice_number"))
    assert ad["deadline"] is None
    kept = [i["item_id"] for _, i in sc.kept_items([artifact])]
    assert f"{PAGE}#t04" not in kept and f"{PAGE}#t01" in kept


def test_table_in_article_keeps_article_id(data):
    artifact, _ = run(data, ask=_script_in_order())
    items = by_id(artifact)
    assert items["t01"]["article_id"] == f"{PAGE}#a01" and items["t01"]["type"] == "table"
    assert items["t02"]["article_id"] == f"{PAGE}#a01" and items["t02"]["type"] == "infographic"
    assert items["t01"]["markdown"].startswith("| a | b |")


def test_standalone_table_has_null_article(data):
    artifact, _ = run(data, ask=_script_in_order())
    item = by_id(artifact)["t05"]
    assert item["article_id"] is None and item["source"] == "block" and item["type"] == "table"


def test_block_transcribed_once(data):
    # block 5 has a table label inside the notice article: only the whole-article crop reads it
    targets = select_targets(data, grouping())
    seen = [b for t in targets for b in t.block_ids]
    assert len(seen) == len(set(seen))
    assert [t.block_ids for t in targets if "b005" in t.block_ids] == [["b004", "b005"]]
    # a block listed in two articles is still one target
    g = grouping()
    g["articles"].append(art(4, "brief_item", [(2, "table")]))
    seen = [b for t in select_targets(data, g) for b in t.block_ids]
    assert seen.count("b002") == 1


def test_class_overrides_kind(data):
    # the grouper called the notice an ad and the ad a notice: the VLM decides
    g = grouping()
    g["articles"][1]["kind"], g["articles"][2]["kind"] = "ad_commercial", "notice_tender"
    ask = Scripted(
        table_reply(),
        info_reply(),
        notice_reply("commercial"),
        notice_reply(),
        table_reply(),
        PHOTO,
    )
    items = by_id(run(data, g, ask)[0])
    assert items["t03"]["type"] == "commercial" and items["t03"]["markdown"] is None
    notice = items["t04"]
    assert notice["type"] == "notice_tender" and notice["title"] is None
    assert (notice["department"], notice["notice_number"], notice["deadline"]) == (
        "Mines",
        "12/2024",
        "2024-12-20",
    )
    assert ask.requests[2][1] == tr.NOTICE_REQUEST and ask.requests[0][1] == tr.BLOCK_REQUEST


def test_bad_output_dropped_and_flagged(data):
    bad_enum = json.dumps({"type": "chart", "title": None, "markdown": "x"})
    ask = Scripted(
        "not json",
        bad_enum,
        "{",  # t01: 3 invalid replies (1 + 2 retries): dropped
        info_reply(),
        notice_reply(),
        notice_reply("commercial"),
        table_reply(),
        PHOTO,
    )
    artifact, _ = run(data, ask=ask)
    assert "t01" not in by_id(artifact)
    assert {"item_id": f"{PAGE}#t01", "reason": "failed"} in artifact["dropped"]
    assert artifact["transcribe_failed"] == 1
    assert artifact["page_flags"] == [tr.FAILED]
    # the retries carry the error back to the model
    retry_messages = ask.requests[1][0]
    assert retry_messages[-1]["role"] == "user" and "not valid" in retry_messages[-1]["content"]


def test_item_ids_stable_when_photo_dropped(data):
    artifact, _ = run(data, ask=_script_in_order())
    assert [i["item_id"].split("#")[1] for i in artifact["items"]] == [
        "t01",
        "t02",
        "t03",
        "t04",
        "t05",
    ]
    # the photo t06 is dropped; with a photo first in order the others keep their numbers
    g = grouping()
    blocks = [blk(0, (0, 0, 5, 5), FIGURE), *BLOCKS]
    d = {**data, "blocks": blocks}
    ask = Scripted(PHOTO, *_script_in_order().replies)
    ids = [i["item_id"].split("#")[1] for i in run(d, g, ask)[0]["items"]]
    assert ids == ["t02", "t03", "t04", "t05", "t06"]


def test_spotcheck_line_rules():
    item = {
        "title": "Budget",
        "markdown": "| a | b |\n|---|:---:|\n\n| 1 | 2 |\n  \n| 3 | 4 |",
    }
    assert sc.item_lines(item) == ["Budget", "| a | b |", "| 1 | 2 |", "| 3 | 4 |"]
    # an infographic's title already is its first markdown line: counted once
    info = {"title": "Debt", "markdown": "Debt\n- 2024: 10\n- 2025: 12"}
    assert sc.item_lines(info) == ["Debt", "- 2024: 10", "- 2025: 12"]
    assert sc.is_separator("--- | ---") and not sc.is_separator("| - | 5 |")
    results = [
        {"lines": [{"verdict": "present"}, {"verdict": "not_present"}, {"verdict": "unsure"}]},
        {"lines": [{"verdict": "present"}] * 3},
    ]
    assert sc.invented_pct(results) == 16.67


# --- E-5 --------------------------------------------------------------------------------------


def test_page_flags_and_item_flags(data):
    g = grouping(page_flags=["low_confidence:judge_low", "split_halves"])
    g["articles"][0]["flags"] = ["low_confidence:geometry", "order_changed"]
    ask = Scripted(
        "nope", "nope", "nope", info_reply(), notice_reply(), notice_reply("commercial"),
        table_reply(), PHOTO,
    )  # fmt: skip
    artifact, _ = run(data, g, ask)
    items = by_id(artifact)
    assert artifact["page_flags"] == [tr.FAILED]
    assert items["t02"]["flags"] == [
        "low_confidence:judge_low",
        "low_confidence:geometry",
        tr.FAILED,
    ]
    assert items["t05"]["flags"] == ["low_confidence:judge_low", tr.FAILED]
    for item in artifact["items"]:
        assert tr.FAILED in item["flags"]
        assert all(f.startswith("low_confidence:") for f in item["flags"])


def test_label_or_role(data):
    # a table-labelled block with role `body` is a target; a chart-labelled one too; a figure
    # with role `photo` is not; a text block with role `table` is
    blocks = [
        blk(1, (0, 0, 50, 50), TABLE),
        blk(2, (0, 60, 50, 110), CHART),
        blk(3, (0, 120, 50, 170), FIGURE),
        blk(4, (0, 180, 50, 230), "Text"),
    ]
    g = {
        "articles": [art(1, "news", [(1, "body"), (2, "caption"), (3, "photo"), (4, "table")])],
        "page_flags": [],
    }
    targets = select_targets({**data, "blocks": blocks}, g)
    assert [t.block_ids for t in targets] == [["b001"], ["b002"], ["b004"]]
    assert {t.article_id for t in targets} == {f"{PAGE}#a01"}


def test_masthead_table_standalone(data):
    blocks = [blk(1, (0, 0, 50, 50), TABLE), blk(2, (0, 60, 50, 110), "Text")]
    g = {
        "articles": [
            art(1, "masthead", [(1, "table")]),
            art(2, "other", [(2, "infographic")]),
        ],
        "page_flags": [],
    }
    targets = select_targets({**data, "blocks": blocks}, g)
    assert [(t.block_ids, t.article_id) for t in targets] == [(["b001"], None), (["b002"], None)]


def test_table_only_inlined(data):
    blocks = [blk(1, (0, 0, 50, 50), TABLE), blk(2, (0, 60, 50, 110), TABLE)]
    g = {
        "articles": [art(1, "table_only", [(1, "table")]), art(2, "news", [(2, "table")])],
        "page_flags": [],
    }
    artifact, _ = run({**data, "blocks": blocks}, g, Scripted(table_reply(), table_reply()))
    items = by_id(artifact)
    assert items["t01"]["inlined"] is True and items["t01"]["article_id"] == f"{PAGE}#a01"
    assert items["t02"]["inlined"] is False


class FakeModels:
    def __init__(self, *replies):
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.replies = list(replies)

    @contextlib.contextmanager
    def use(self, name):
        self.opened.append(name)
        try:
            yield SimpleNamespace(raw=object(), profile=name)
        finally:
            self.closed.append(name)

    def make_create(self, client):
        def create(messages, max_items=None):
            message = SimpleNamespace(content=self.replies.pop(0))
            choice = SimpleNamespace(message=message, finish_reason="stop")
            return SimpleNamespace(choices=[choice], usage=None)

        return create


def page_row(page_id=PAGE):
    return PageRow(page_id, None, "X", "main", 1, None, "", "", 1)  # type: ignore[arg-type]


def test_lazy_model(data, tmp_path, monkeypatch, set_settings):
    set_settings(work_dir=tmp_path / "work")
    empty = {**data, "page_id": "P0", "blocks": [blk(1, (0, 0, 50, 50), "Text")]}
    pages = {"P0": (empty, {"articles": [], "page_flags": []}), PAGE: (data, grouping())}
    files = {}
    for pid, (_, g) in pages.items():
        files[pid] = tmp_path / f"gf-{pid}.json"
        files[pid].write_text(json.dumps(g))
    monkeypatch.setattr(gin, "done_artifact", lambda stage, pid: files[pid])
    monkeypatch.setattr(gin, "load_ocr_final", lambda pid: pages[pid][0])
    fake = FakeModels(*_script_in_order().replies)
    monkeypatch.setattr("newsrag.models.use", fake.use)
    monkeypatch.setattr(tr, "make_create", fake.make_create)

    stage = TranscribeStage()
    assert stage.models_needed == (config.settings.group_model,)
    assert stage.concurrency == config.settings.group_concurrency
    assert stage.version.startswith(f"{config.settings.group_model}-transcribe-p1-")
    stage.open()
    first = stage.handle(page_row("P0"))
    assert fake.opened == []  # no target: no model
    assert first.detail["n_targets"] == 0
    second = stage.handle(page_row(PAGE))
    assert fake.opened == [config.settings.group_model]
    assert second.detail["n_items"] == 5 and second.detail["photo_no_text"] == 1
    stage.close()
    assert fake.closed == [config.settings.group_model]
    saved = json.loads(Path(second.artifact_path).read_text())
    assert saved["page_id"] == PAGE and len(saved["items"]) == 5


def test_spotcheck_zero_items(monkeypatch, tmp_path, set_settings):
    from newsrag.cli import app

    set_settings(transcribe_spotcheck_file=tmp_path / "report.json")
    only_ads = {
        "page_id": PAGE,
        "items": [{"item_id": f"{PAGE}#t01", "type": "commercial", "markdown": None}],
    }
    monkeypatch.setattr(sc, "done_artifacts", lambda page_ids=None: [only_ads])
    pages = tmp_path / "pages.txt"
    pages.write_text(f"{PAGE}\n")
    result = CliRunner().invoke(app, ["transcribe", "spotcheck", "--pages-file", str(pages)])
    assert result.exit_code == 1
    assert "invented_pct=n/a" in result.output
    assert not (tmp_path / "report.json").exists()


def test_spotcheck_report(data, monkeypatch, tmp_path, set_settings):
    from newsrag.cli import app

    set_settings(transcribe_spotcheck_file=tmp_path / "report.json")
    artifact, _ = run(data, ask=_script_in_order())
    monkeypatch.setattr(sc, "done_artifacts", lambda page_ids=None: [artifact])
    monkeypatch.setattr(gin, "load_ocr_final", lambda pid: data)

    def verdicts(*vs):
        return json.dumps({"lines": [{"n": i, "verdict": v} for i, v in enumerate(vs, 1)]})

    # kept items: t01 table (3 lines incl. title), t02 infographic (2), t03 notice (1),
    # t05 table (3); the last line of each is judged not present
    def ask(messages, count):
        return verdicts(*(["present"] * (count - 1) + ["not_present"]))

    from newsrag.stages import group as group_mod

    monkeypatch.setattr(group_mod, "send", lambda create: ask)
    monkeypatch.setattr(sc, "make_create", lambda client: None)
    fake = FakeModels()
    monkeypatch.setattr("newsrag.models.use", fake.use)
    result = CliRunner().invoke(app, ["transcribe", "spotcheck", "--n", "50", "--seed", "1"])
    assert result.exit_code == 0, result.output
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["n_items"] == 4 and report["n_lines"] == 9
    assert report["invented_pct"] == round(4 / 9 * 100, 2)
    assert f"invented_pct={report['invented_pct']:.2f}" in result.output
    assert fake.opened == [config.settings.judge_model]
