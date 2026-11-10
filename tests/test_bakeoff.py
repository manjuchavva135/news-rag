"""Ticket 014: the grouping model bake-off (CPU only, scripted fake models, no ledger, no GPU)."""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from typer.testing import CliRunner

from newsrag import config
from newsrag.grouping import bakeoff as bk

ONE_STORY = '{"stories": 1}'


def blk(order, bbox, text, label="Text", kind="text"):
    return {
        "block_id": f"b{order:03d}",
        "order": order,
        "label": label,
        "type": kind,
        "bbox": list(bbox),
        "text": text,
    }


BLOCKS = [
    blk(1, (20, 40, 180, 100), "మొదటి వార్త శీర్షిక", "SectionHeader", "title"),
    blk(2, (20, 110, 180, 300), "First story body text"),
    blk(3, (220, 40, 380, 100), "రెండవ వార్త శీర్షిక", "SectionHeader", "title"),
    blk(4, (220, 110, 380, 300), "Second story body text"),
]
GOOD = json.dumps(
    {
        "articles": [
            {"kind": "news", "blocks": [{"n": 1, "role": "headline"}, {"n": 2, "role": "body"}]},
            {"kind": "news", "blocks": [{"n": 3, "role": "headline"}, {"n": 4, "role": "body"}]},
        ],
        "unassigned": [],
    }
)


def jreply(*verdicts, missed=0):
    return json.dumps(
        {
            "articles": [{"n": k + 1, "verdict": v} for k, v in enumerate(verdicts)],
            "missed_regions": missed,
        }
    )


PAGES = ["2024-12-02_BG_main_01", "2024-12-06_GNOCLT_main_16"]


@pytest.fixture
def set_settings(monkeypatch, tmp_path):
    import newsrag.stages.base as base_mod

    def apply(**kw):
        current = config.settings.model_copy(update=kw)
        for mod in (config, base_mod):
            monkeypatch.setattr(mod, "settings", current)
        return current

    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("# test pipeline\nsurya_version: 0.23.0\ngroup_model: x\n")
    apply(
        work_dir=tmp_path / "work",
        reports_dir=tmp_path / "reports",
        pipeline_file=pipeline,
        bakeoff_telugu_file=tmp_path / "telugu-headlines.txt",
        bakeoff_pages_file=tmp_path / "bakeoff-pages.txt",
        group_retry_backoff_s=(0.0,),
        judge_story_kinds=(),
    )
    return apply


@pytest.fixture
def datas(tmp_path):
    png = tmp_path / "page.png"
    Image.new("RGB", (400, 600), "white").save(png)
    out = {}
    for k, pid in enumerate(PAGES):
        blocks = [dict(b) for b in BLOCKS]
        if k == 1:  # the second page's text is marked, so fake models can tell the pages apart
            blocks[1]["text"] = "SECONDPAGE story body text"
        out[pid] = {
            "page_id": pid,
            "image": {"path": str(png), "width": 400, "height": 600},
            "page_flags": [],
            "blocks": blocks,
        }
    return out


class Fakes:
    """`connect(profile)` for run_bakeoff: per-profile group / judge / read replies (callables of
    the messages, or a fixed string); `fail` lists profiles whose model does not start."""

    def __init__(self, group=None, judge=None, read=None, fail=()):
        self.group = group or {}
        self.judge = judge or {}
        self.read = read or {}
        self.fail = set(fail)
        self.opened: list[str] = []
        self.group_calls: dict[str, int] = {}

    @staticmethod
    def _answer(value, messages):
        item = value(messages) if callable(value) else value
        if isinstance(item, Exception):
            raise item
        return item

    @contextlib.contextmanager
    def connect(self, profile):
        self.opened.append(profile)
        if profile in self.fail:
            raise RuntimeError(f"{profile} did not start")

        def group(messages, max_items=None):
            self.group_calls[profile] = self.group_calls.get(profile, 0) + 1
            return self._answer(self.group.get(profile, GOOD), messages)

        def judge(messages, max_items=None):
            if max_items == bk.jg.STORY_CHECK:
                return ONE_STORY
            return self._answer(self.judge.get(profile, jreply("correct", "correct")), messages)

        def read(messages, max_items=None):
            return self._answer(self.read.get(profile, ""), messages)

        yield SimpleNamespace(group=group, judge=judge, read=read)


def run(fakes, datas, candidates=("qwen3-vl-32b", "qwen3-vl-30b-a3b"), judge="gemma4-31b", **kw):
    headlines = [(PAGES[0], "b001"), (PAGES[1], "b003")]
    return bk.run_bakeoff(
        list(candidates),
        judge,
        PAGES,
        headlines,
        load_page=datas.__getitem__,
        connect_model=fakes.connect,
        echo=lambda _line: None,
        **kw,
    )


# --- pages and headlines ----------------------------------------------------------------------


def test_pages_distinct():
    sample = [
        "2020-01-05_B_main_01",
        "2020-01-05_B_main_04",
        "2024-12-02_BG_main_01",
        "2025-10-20_ALL_main_02",
        "2026-02-01_BG_main_01",
        "2026-02-01_V_main_07",
        "2025-03-01_BG_fea_02",
        "2026-02-01_BG_main_01",  # repeated line
    ]
    pages = bk.choose_pages(sample)
    assert pages == [
        "2020-01-05_B_main_01",
        "2026-02-01_BG_main_01",
        "2025-10-20_ALL_main_02",
        "2026-02-01_V_main_07",
        "2025-03-01_BG_fea_02",
    ]
    assert len(set(pages)) == 5


def test_pages_fallbacks_take_next_unused_line():
    sample = ["2024-12-02_BG_main_01", "2024-12-05_BGNOP_main_01", "2025-10-20_ALL_main_02",
              "2024-12-06_GNOCLT_main_16", "2024-12-08_BGNOPSVACLT_main_10",
              "2020-03-01_B_main_05", "2024-12-09_R_main_03"]  # fmt: skip
    pages = bk.choose_pages(sample)
    # slot 4 has no 2026 single-letter inner page and takes the 2020 one
    assert pages == [
        "2024-12-02_BG_main_01",
        "2024-12-05_BGNOP_main_01",
        "2025-10-20_ALL_main_02",
        "2020-03-01_B_main_05",
        "2024-12-06_GNOCLT_main_16",
    ]
    assert len(set(pages)) == 5


def test_pages_file_written_once(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("# comment\n2024-12-02_BG_main_01\n2025-10-20_ALL_main_02\n")
    target = tmp_path / "pages.txt"
    assert bk.ensure_pages_file(target, sample) == [
        "2024-12-02_BG_main_01",
        "2025-10-20_ALL_main_02",
    ]
    target.write_text("2024-12-02_BG_main_01\n")
    assert bk.ensure_pages_file(target, sample) == ["2024-12-02_BG_main_01"]


def test_headlines_round_robin_and_filters(set_settings):
    set_settings(bakeoff_telugu_min_chars=8, bakeoff_telugu_min_share=0.8)
    a = [
        blk(1, (0, 0, 1, 1), "మొదటి వార్త శీర్షిక", kind="title"),
        blk(2, (0, 0, 1, 1), "రెండవ వార్త శీర్షిక", kind="title"),
        blk(3, (0, 0, 1, 1), "English headline only", kind="title"),
        blk(4, (0, 0, 1, 1), "చిన్న", kind="title"),  # fewer than 8 letters
        blk(5, (0, 0, 1, 1), "మూడవ వార్త శరీరం ఇది", kind="text"),  # not a title
    ]
    b = [blk(1, (0, 0, 1, 1), "ఇంకో పేజీ శీర్షిక", kind="title")]
    assert bk.choose_headlines([("A", a), ("B", b)], 3) == [
        ("A", "b001"),
        ("B", "b001"),
        ("A", "b002"),
    ]
    assert bk.choose_headlines([("A", a), ("B", b)], 30) == [
        ("A", "b001"),
        ("B", "b001"),
        ("A", "b002"),
    ]


def test_telugu_read_score(set_settings, datas):
    assert bk.similarity("మొదటి వార్త", "మొదటి  వార్త") == 1.0  # whitespace ignored
    assert bk.similarity("", "") == 1.0
    assert bk.similarity("abcd", "abxd") == pytest.approx(0.75)
    assert bk.similarity("", "abc") == 0.0
    # NFC: a decomposed form equals the composed one
    assert bk.similarity("é", "é") == 1.0

    fakes = Fakes(read={"qwen3-vl-32b": "మొదటి వార్త శీర్షిక", "qwen3-vl-30b-a3b": "wrong"})
    rows, _, path = run(fakes, datas)
    by = {r.candidate: r for r in rows}
    # first headline exact, the second differs in one word
    exact, other = "మొదటి వార్త శీర్షిక", "రెండవ వార్త శీర్షిక"
    expected = round((1.0 + bk.similarity(exact, other)) / 2, 4)
    assert by["qwen3-vl-32b"].telugu_read == expected
    assert by["qwen3-vl-30b-a3b"].telugu_read < 0.2
    assert "telugu_read" in path.read_text()


def test_reading_error_scores_zero(set_settings, datas):
    fakes = Fakes(read={"qwen3-vl-32b": RuntimeError("boom")})
    rows, _, _ = run(fakes, datas, candidates=("qwen3-vl-32b",))
    assert rows[0].telugu_read == 0.0


# --- candidates, families, failures ------------------------------------------------------------


def test_judge_family_candidate_skipped(set_settings, datas):
    fakes = Fakes()
    rows, winner, _ = run(fakes, datas, candidates=("qwen3-vl-32b", "qwen3-vl-30b-a3b"),
                             judge="qwen3-vl-32b")  # fmt: skip
    # no candidate left besides the judge's family: every one skipped, nothing loaded but none
    assert [r.status for r in rows] == [bk.SKIPPED, bk.SKIPPED]
    assert "same family as the judge" in rows[0].reason
    assert fakes.opened == []
    assert winner is None


def test_gemma_candidate_skipped_by_gemma_judge(set_settings, datas):
    fakes = Fakes()
    rows, winner, text_path = run(
        fakes, datas, candidates=("qwen3-vl-32b", "qwen3-vl-30b-a3b", "gemma3-27b")
    )
    by = {r.candidate: r for r in rows}
    assert by["gemma3-27b"].status == bk.SKIPPED
    assert "gemma" in by["gemma3-27b"].reason
    assert "gemma3-27b" not in fakes.opened
    assert fakes.opened.count("gemma4-31b") == 1  # one judge load for every candidate
    text = text_path.read_text()
    assert "| gemma3-27b | skipped |" in text
    assert "judge: gemma4-31b" in text
    assert winner in ("qwen3-vl-32b", "qwen3-vl-30b-a3b")


def test_unknown_candidate_rejected():
    with pytest.raises(bk.BakeoffError, match="unknown candidate"):
        bk.check_request(["no-such-model"], "gemma4-31b")
    with pytest.raises(bk.BakeoffError, match="unknown judge"):
        bk.check_request(["qwen3-vl-32b"], "no-such-model")


def test_unknown_candidate_exits_2(set_settings):
    from newsrag.cli import app

    result = CliRunner().invoke(app, ["group", "bakeoff", "--candidates", "nope"])
    assert result.exit_code == 2
    assert "unknown candidate" in result.output


def test_failed_page_scores_zero(set_settings, datas):
    # the second page never gets valid JSON from the grouper: it fails after the retries
    def group(messages):
        return "not json" if "SECONDPAGE" in json.dumps(messages, ensure_ascii=False) else GOOD

    fakes = Fakes(group={"qwen3-vl-32b": group})
    rows, _, _ = run(fakes, datas, candidates=("qwen3-vl-32b",))
    r = rows[0]
    assert r.scores == {PAGES[0]: 1.0, PAGES[1]: 0.0}
    assert r.mean_score == 0.5
    assert "grouping failed" in r.notes[PAGES[1]]
    assert r.below(0.8) == 1
    timing = json.loads((bk.group_dir("qwen3-vl-32b") / "timing.json").read_text())
    assert list(timing["failed"]) == [PAGES[1]]


def test_judge_failure_scores_zero_and_null_counts_one(set_settings, datas):
    def judge(messages):
        if "SECONDPAGE" in json.dumps(messages, ensure_ascii=False):
            return "garbage"
        return jreply("correct", "not_an_article")

    fakes = Fakes(judge={"gemma4-31b": judge})
    rows, _, _ = run(fakes, datas, candidates=("qwen3-vl-32b",))
    assert rows[0].scores == {PAGES[0]: 0.5, PAGES[1]: 0.0}
    assert "judge failed" in rows[0].notes[PAGES[1]]
    # a failed judgement is not saved: the next run asks again
    assert not (bk.judge_dir("qwen3-vl-32b", "gemma4-31b") / f"{PAGES[1]}.json").exists()

    assert bk.page_score({"score": None}) == 1.0
    assert bk.page_score(None) == 0.0
    assert bk.page_score({"score": 0.25}) == 0.25


def test_model_that_fails_to_start_is_excluded(set_settings, datas):
    fakes = Fakes(fail={"qwen3-vl-30b-a3b"})
    rows, winner, path = run(fakes, datas)
    by = {r.candidate: r for r in rows}
    assert by["qwen3-vl-30b-a3b"].status == bk.FAILED
    assert "did not start" in by["qwen3-vl-30b-a3b"].reason
    assert winner == "qwen3-vl-32b"
    assert "| qwen3-vl-30b-a3b | failed |" in path.read_text()


def test_no_winner_exits_1(set_settings, datas, monkeypatch):
    from newsrag.cli import app
    from newsrag.grouping import cli as group_cli

    fakes = Fakes(fail={"qwen3-vl-32b", "qwen3-vl-30b-a3b"})
    _, winner, path = run(fakes, datas)
    assert winner is None
    assert "winner: none" in path.read_text()

    # the command exits 1 on `winner: none`
    s = set_settings()
    s.bakeoff_pages_file.write_text("\n".join(PAGES) + "\n")
    s.bakeoff_telugu_file.write_text(f"{PAGES[0]}\tb001\n")
    real = bk.run_bakeoff
    monkeypatch.setattr(group_cli, "_load_ocr_final", datas.__getitem__)
    monkeypatch.setattr(
        bk, "run_bakeoff", lambda *a, **kw: real(*a, connect_model=fakes.connect, **kw)
    )
    monkeypatch.setattr(bk.gin, "load_ocr_final", datas.__getitem__)
    result = CliRunner().invoke(
        app, ["group", "bakeoff", "--candidates", "qwen3-vl-32b,qwen3-vl-30b-a3b"]
    )
    assert result.exit_code == 1, result.output
    assert "winner: none" in result.output


# --- winner rules -------------------------------------------------------------------------------


def row(name, mean, read=0.5, spp=10.0):
    r = bk.Row(name, seconds_per_page=spp, telugu_read=read)
    r.scores = {"p": mean}
    return r


def test_tie_within_001_picks_faster():
    rows = [row("slow", 0.805, spp=20.0), row("fast", 0.800, spp=5.0)]
    assert bk.pick_winner(rows, tie=0.01) == "fast"
    # exactly 0.01 apart is still a tie
    rows = [row("slow", 0.81, spp=20.0), row("fast", 0.80, spp=5.0)]
    assert bk.pick_winner(rows, tie=0.01) == "fast"
    # more than 0.01 apart: the better score wins whatever the speed
    rows = [row("slow", 0.83, spp=20.0), row("fast", 0.80, spp=5.0)]
    assert bk.pick_winner(rows, tie=0.01) == "slow"


def test_tie_prefers_telugu_read_before_speed():
    rows = [row("reader", 0.80, read=0.9, spp=20.0), row("fast", 0.805, read=0.6, spp=5.0)]
    assert bk.pick_winner(rows, tie=0.01) == "reader"


def test_failed_and_skipped_never_win():
    rows = [bk.Row("x", bk.FAILED), bk.Row("y", bk.SKIPPED), row("z", 0.1)]
    assert bk.pick_winner(rows) == "z"
    assert bk.pick_winner([bk.Row("x", bk.FAILED)]) is None


# --- outputs, reuse, apply -------------------------------------------------------------------


def test_outputs_outside_group_dir(set_settings, datas):
    s = config.settings
    fakes = Fakes()
    run(fakes, datas, candidates=("qwen3-vl-32b",))
    work = s.work_dir
    assert not (work / "group").exists()
    files = sorted(p.relative_to(work).parts[0] for p in work.rglob("*.json"))
    assert set(files) == {"group_bakeoff", "group_bakeoff_judge"}
    gdir = bk.group_dir("qwen3-vl-32b")
    assert gdir.name.startswith("qwen3-vl-32b-group-p1-")
    assert (gdir / "timing.json").is_file()
    jdir = bk.judge_dir("qwen3-vl-32b", "gemma4-31b")
    assert jdir.parent.name == gdir.name and jdir.name.startswith("gemma4-31b-judge-p")
    assert sorted(p.stem for p in jdir.glob("*.json")) == sorted(PAGES)


def test_rerun_reuses_outputs_and_timing(set_settings, datas):
    fakes = Fakes()
    rows1, _, _ = run(fakes, datas, candidates=("qwen3-vl-32b",))
    timing = json.loads((bk.group_dir("qwen3-vl-32b") / "timing.json").read_text())
    timing["seconds_per_page"] = 42.0
    (bk.group_dir("qwen3-vl-32b") / "timing.json").write_text(json.dumps(timing))
    again = Fakes()
    rows2, _, _ = run(again, datas, candidates=("qwen3-vl-32b",))
    assert again.opened == []  # nothing loaded: grouping, reading and judging all reused
    assert rows2[0].seconds_per_page == 42.0
    assert rows2[0].scores == rows1[0].scores
    forced = Fakes()
    run(forced, datas, candidates=("qwen3-vl-32b",), rerun=True)
    assert forced.opened == ["qwen3-vl-32b", "gemma4-31b"]


def test_report_columns_and_winner_line(set_settings, datas):
    _, winner, path = run(Fakes(), datas)
    text = path.read_text()
    header = next(line for line in text.splitlines() if line.startswith("| candidate"))
    for column in ("status", "mean judge score", "pages below judge_threshold", "seconds/page",
                   "telugu_read"):  # fmt: skip
        assert column in header
    assert f"winner: {winner}" in text
    assert bk.report_value(text, "winner") == winner
    assert bk.report_value(text, "judge") == "gemma4-31b"


def test_judge_model_set_by_apply(set_settings, datas):
    s = config.settings
    _, winner, _ = run(Fakes(), datas)
    got = bk.apply_report()
    assert got == (winner, "gemma4-31b")
    text = s.pipeline_file.read_text()
    assert f"group_model: {winner}" in text
    assert "judge_model: gemma4-31b" in text
    assert "surya_version: 0.23.0" in text and text.startswith("# test pipeline")


def test_apply_cli_without_report_or_winner_exits_1(set_settings):
    from newsrag.cli import app

    s = config.settings
    before = s.pipeline_file.read_text()
    result = CliRunner().invoke(app, ["group", "bakeoff", "--apply"])
    assert result.exit_code == 1
    s.reports_dir.mkdir(parents=True, exist_ok=True)
    bk.report_path().write_text("judge: gemma4-31b\n\nwinner: none\n")
    result = CliRunner().invoke(app, ["group", "bakeoff", "--apply"])
    assert result.exit_code == 1
    assert s.pipeline_file.read_text() == before
    bk.report_path().write_text("judge: gemma4-31b\n\nwinner: qwen3-vl-30b-a3b\n")
    result = CliRunner().invoke(app, ["group", "bakeoff", "--apply"])
    assert result.exit_code == 0
    assert "group_model set to qwen3-vl-30b-a3b" in result.output
    assert "group_model: qwen3-vl-30b-a3b" in s.pipeline_file.read_text()
