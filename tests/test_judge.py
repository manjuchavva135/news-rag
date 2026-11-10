"""Ticket 013: the grouping judge, the regroup pass and `group_final` (CPU only, scripted fake
models and a fake ledger: nothing touches the GPU, Postgres or the real artifacts)."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from typer.testing import CliRunner

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.grouping import judge as jg
from newsrag.grouping import parse
from newsrag.stages import eligibility
from newsrag.stages import group as group_mod
from newsrag.stages.base import PageRow, StageUnavailable
from newsrag.stages.group import group_page
from newsrag.stages.group_final import GroupFinalStage
from newsrag.stages.group_retry import GroupRetryStage
from newsrag.stages.judge import JudgeRetryStage, JudgeStage

PAGE = "2024-12-06_X_main_01"


def blk(order, bbox, text="", label="Text"):
    return {
        "block_id": f"b{order:03d}",
        "order": order,
        "label": label,
        "type": "text",
        "bbox": list(bbox),
        "text": text,
    }


BLOCKS = [
    blk(1, (20, 40, 180, 100), "First headline", "Section-header"),
    blk(2, (20, 110, 180, 300), "First story body text"),
    blk(3, (220, 40, 380, 100), "Second headline", "Section-header"),
    blk(4, (220, 110, 380, 300), "Second story body text"),
    blk(5, (20, 400, 380, 450), "SAKSHI masthead", "Page-header"),
    blk(6, (20, 470, 380, 560), "Buy our product now"),
]


def reply(*articles, unassigned=()):
    return json.dumps(
        {
            "articles": [
                {"kind": kind, "blocks": [{"n": n, "role": role} for n, role in entries]}
                for kind, entries in articles
            ],
            "unassigned": list(unassigned),
        }
    )


ORIGINAL_REPLY = reply(
    ("news", [(1, "headline"), (2, "body")]),
    ("news", [(3, "headline"), (4, "body")]),
    ("masthead", [(5, "headline")]),
    ("ad_commercial", [(6, "body")]),
)
# the regrouping puts all four text blocks in one article: a different grouping
RETRY_REPLY = reply(
    ("news", [(1, "headline"), (2, "body"), (3, "headline"), (4, "body")]),
    ("masthead", [(5, "headline")]),
    ("ad_commercial", [(6, "body")]),
)


def jreply(verdicts, missed=0):
    return json.dumps(
        {
            "articles": [{"n": n, "verdict": v} for n, v in verdicts.items()],
            "missed_regions": missed,
        }
    )


def completion(content="ok", finish="stop"):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=message)])


def image_of(messages):
    for part in messages[0]["content"]:
        if part["type"] == "image_url":
            url = part["image_url"]["url"]
            return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    raise AssertionError("no image in the request")


def text_of(messages):
    return "\n".join(p["text"] for p in messages[0]["content"] if p["type"] == "text")


class Scripted:
    """A fake `ask(messages, max_items)`: the scripted replies in order (an Exception is raised)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages, max_items=None):
        self.calls.append(messages)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
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
def page_png(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (400, 600), "white").save(path)
    return path


@pytest.fixture
def data(page_png):
    return {
        "page_id": PAGE,
        "image": {"path": str(page_png), "width": 400, "height": 600},
        "page_flags": [],
        "blocks": BLOCKS,
    }


@pytest.fixture
def original(data):
    return group_page(data, Scripted(ORIGINAL_REPLY), "qwen3-vl-32b")


def page_row(page_id=PAGE):
    return PageRow(page_id, None, "X", "main", 1, None, "", "", 1)  # type: ignore[arg-type]


class FakeModels:
    """Replaces `newsrag.models.use` and the two `make_create`s: every request is recorded and
    answered from a per-profile script."""

    def __init__(self):
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.requests: dict[str, list] = {}
        self.replies: dict[str, list] = {}

    @contextlib.contextmanager
    def use(self, name):
        self.opened.append(name)
        try:
            yield SimpleNamespace(raw=object(), profile=name)
        finally:
            self.closed.append(name)

    def make_create(self, client):
        def create(messages, max_items=None):
            self.requests.setdefault(client.profile, []).append(messages)
            return completion(self.replies[client.profile].pop(0))

        return create


@pytest.fixture
def fake_models(monkeypatch):
    fake = FakeModels()
    monkeypatch.setattr("newsrag.models.use", fake.use)
    monkeypatch.setattr(group_mod, "make_create", fake.make_create)
    monkeypatch.setattr(jg, "make_create", fake.make_create)
    return fake


class World:
    """A fake ledger plus artifacts on disk for one page."""

    def __init__(self, tmp_path, data, monkeypatch):
        self.rows: dict[tuple[str, str], dict] = {}
        self.data = data
        monkeypatch.setattr(gin, "stage_row", lambda stage, pid: self.rows.get((stage, pid)))
        monkeypatch.setattr(gin, "load_ocr_final", lambda pid: self.data)
        monkeypatch.setattr(gin, "done_artifact", self.done_artifact)
        self.tmp = tmp_path

    def done_artifact(self, stage, pid):
        row = self.rows.get((stage, pid))
        if row and row["status"] == "done" and row["artifact_path"]:
            return Path(row["artifact_path"])
        return None

    def put(self, stage, artifact=None, status="done", detail=None, attempt=1, pid=PAGE):
        path = None
        if artifact is not None:
            path = self.tmp / f"{stage}-{pid}.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")
        self.rows[(stage, pid)] = {
            "status": status,
            "attempt": attempt,
            "detail": detail,
            "artifact_path": str(path) if path else None,
            "error": None,
        }

    def run(self, stage, pid=PAGE):
        result = stage.handle(page_row(pid))
        self.put(
            stage.name,
            json.loads(Path(result.artifact_path).read_text()) if result.artifact_path else None,
            detail=result.detail,
            pid=pid,
        )
        if result.artifact_path:
            self.rows[(stage.name, pid)]["artifact_path"] = result.artifact_path
        return result


@pytest.fixture
def world(tmp_path, data, monkeypatch, set_settings):
    set_settings(work_dir=tmp_path / "work")
    return World(tmp_path, data, monkeypatch)


def judge_artifact(grouping, score, verdicts=None):
    return {
        "page_id": PAGE,
        "score": score,
        "verdicts": verdicts
        if verdicts is not None
        else {a["article_id"]: "correct" for a in grouping["articles"] if a["kind"] == "news"},
        "missed_regions": 0,
        "n_judged": 2,
    }


# --- FR-1: settings, families, stage wiring ------------------------------------------------------


def test_settings_defaults():
    s = config.settings
    assert s.judge_model == "gemma3-27b" and s.judge_threshold == 0.8


def test_same_family_refused(set_settings):
    JudgeStage().preflight()  # gemma judging a qwen grouper: fine
    set_settings(judge_model="qwen3-vl-32b")
    for stage in (JudgeStage(), JudgeRetryStage()):
        with pytest.raises(
            StageUnavailable, match="judge_model and group_model must be different families"
        ):
            stage.preflight()


def test_same_family_exits_2_before_any_db_access(set_settings):
    from newsrag.cli import app

    set_settings(judge_model="qwen3-vl-32b")
    result = CliRunner().invoke(app, ["stage", "run", "judge", "--limit", "1"])
    assert result.exit_code == 2
    assert "judge_model and group_model must be different families" in result.output


def test_every_profile_has_a_family():
    from newsrag.models import load_profiles

    profiles = load_profiles()
    assert all(p.family for p in profiles.values())
    assert profiles["gemma3-27b"].family != profiles["qwen3-vl-32b"].family


def test_stage_wiring_and_versions(set_settings):
    stages = {"judge": JudgeStage(), "group_retry": GroupRetryStage()}
    stages |= {"judge_retry": JudgeRetryStage(), "group_final": GroupFinalStage()}
    assert stages["judge"].depends_on == ("group",)
    assert stages["group_retry"].depends_on == ("group", "judge")
    assert stages["judge_retry"].depends_on == ("group_retry",)
    assert stages["group_final"].depends_on == ("group", "judge", "group_retry", "judge_retry")
    assert stages["judge"].models_needed == ("gemma3-27b",)
    assert stages["judge_retry"].models_needed == ("gemma3-27b",)
    assert stages["group_retry"].models_needed == ("qwen3-vl-32b",)
    assert stages["group_final"].models_needed == ()
    assert re.fullmatch(r"gemma3-27b-judge-p1-[0-9a-f]{8}", stages["judge"].version)
    assert re.fullmatch(r"gemma3-27b-judge-p1-t0\.8-[0-9a-f]{8}", stages["judge_retry"].version)
    assert re.fullmatch(r"qwen3-vl-32b-regroup-p1-t0\.8-[0-9a-f]{8}", stages["group_retry"].version)
    assert re.fullmatch(r"final-t0\.8-[0-9a-f]{8}", stages["group_final"].version)
    set_settings(judge_threshold=0.9)
    assert GroupRetryStage().version.startswith("qwen3-vl-32b-regroup-p1-t0.9-")
    assert JudgeRetryStage().version.startswith("gemma3-27b-judge-p1-t0.9-")
    assert GroupFinalStage().version.startswith("final-t0.9-")


def test_judge_version_ignores_the_threshold(set_settings):
    version = JudgeStage().version
    set_settings(judge_threshold=0.5)
    assert JudgeStage().version == version
    set_settings(judge_text_chars=100)
    assert JudgeStage().version != version


def test_registered_and_import_is_lazy():
    from newsrag.stages.registry import REGISTRY

    for name in ("judge", "group_retry", "judge_retry", "group_final"):
        assert name in REGISTRY


def test_retry_concurrency():
    assert config.settings.group_concurrency == 16
    assert GroupRetryStage.concurrency == config.settings.group_concurrency
    assert JudgeRetryStage.concurrency == config.settings.group_concurrency
    assert JudgeStage.concurrency == 16


# --- FR-2: what the judge sees, the score ----------------------------------------------------------


def test_score_formula(data, original):
    ids = [a["article_id"] for a in original["articles"]]
    assert [a["kind"] for a in original["articles"]] == [
        "news",
        "news",
        "masthead",
        "ad_commercial",
    ]
    # 2 judged articles (masthead and ad are not sent and not counted)
    one = jg.judge_grouping(data, original, ask=Scripted(jreply({1: "correct", 2: "merged"})))
    assert one["score"] == 0.5
    assert one["verdicts"] == {ids[0]: "correct", ids[1]: "merged"}
    # missed regions go into the denominator: 2 / (2 + 1)
    two = jg.judge_grouping(data, original, ask=Scripted(jreply({1: "correct", 2: "correct"}, 1)))
    assert two["score"] == 0.667 and two["missed_regions"] == 1
    # 3 decimals
    three = jg.judge_grouping(data, original, ask=Scripted(jreply({1: "correct", 2: "split"}, 1)))
    assert three["score"] == 0.333
    assert jg.page_score(2, 2, 0) == 1.0
    assert jg.page_score(0, 0, 2) == 0.0


def test_page_without_judged_articles_is_skipped(data, original):
    # only mastheads and ads, nothing missed: null score
    only_ads = dict(original, articles=original["articles"][2:])
    result = jg.judge_grouping(data, only_ads, ask=Scripted(jreply({}, 0)))
    assert result["score"] is None and result["n_judged"] == 0
    # a page with no OCR blocks: not even sent to the model
    ask = Scripted()
    no_blocks = dict(data, blocks=[])
    assert jg.judge_grouping(no_blocks, dict(original, articles=[]), ask=ask)["score"] is None
    assert not ask.calls
    # blocks that all went unassigned are sent: the judge can report the missed stories
    empty = dict(original, articles=[])
    assert jg.judge_grouping(data, empty, ask=Scripted(jreply({}, 3)))["score"] == 0.0
    # but a missed story on a page of ads only is a score of 0
    assert jg.judge_grouping(data, only_ads, ask=Scripted(jreply({}, 2)))["score"] == 0.0


def test_judge_input_lists_articles_without_mastheads_and_ads(data, original):
    ask = Scripted(jreply({1: "correct", 2: "correct"}))
    jg.judge_grouping(data, original, image_long_side=300, ask=ask)
    prompt = text_of(ask.calls[0])
    assert re.findall(r"^#(\d+) \[(\w+)\]", prompt, re.MULTILINE) == [("1", "news"), ("2", "news")]
    assert "#1 [news] First headline | " in prompt
    assert "SAKSHI masthead" not in prompt and "Buy our product" not in prompt
    assert max(image_of(ask.calls[0]).size) == 300


def test_article_text_cut_at_300_chars(data, original):
    long = dict(original["articles"][0], text="x" * 500)
    grouping = dict(original, articles=[long])
    ask = Scripted(jreply({1: "correct"}))
    jg.judge_grouping(data, grouping, ask=ask)
    line = next(line for line in text_of(ask.calls[0]).splitlines() if line.startswith("#1 "))
    assert line.endswith("| " + "x" * 300)


def test_judge_grouping_function_returns_score_and_verdicts(data, original, fake_models):
    fake_models.replies["gemma3-27b"] = [jreply({1: "correct", 2: "wrong_blocks"})]
    result = jg.judge_grouping(data, original)  # opens the judge model itself
    assert set(result) >= {"score", "verdicts"} and result["score"] == 0.5
    assert fake_models.opened == ["gemma3-27b"] and fake_models.closed == ["gemma3-27b"]
    assert list(result["verdicts"].values()) == ["correct", "wrong_blocks"]


def test_judge_grouping_function_retries_a_repeated_or_unknown_number(data, original):
    ask = Scripted(
        jreply({1: "correct"}).replace("]", ',{"n":1,"verdict":"merged"}]'),  # 1 twice, 2 missing
        jreply({1: "correct", 2: "correct"}),
    )
    result = jg.judge_grouping(data, original, ask=ask)
    assert result["score"] == 1.0 and len(ask.calls) == 2
    retry_note = ask.calls[1][-1]["content"]
    assert "not valid" in retry_note and "answered more than once" in retry_note
    assert ask.calls[1][-2]["role"] == "assistant"


def test_judge_grouping_function_after_retries_numbers_are_tolerated(data, original):
    bad = jreply({1: "correct", 9: "correct"})  # 2 missing, 9 unknown
    ask = Scripted(bad, bad, bad)
    result = jg.judge_grouping(data, original, ask=ask)
    assert len(ask.calls) == 3  # first try + 2 retries
    # the unknown number is ignored, the missing one stays in the denominator as not correct
    ids = [a["article_id"] for a in original["articles"]]
    assert result["verdicts"] == {ids[0]: "correct", ids[1]: "missing"}
    assert result["score"] == 0.5
    repeated = jreply({1: "correct", 2: "merged"}).replace("]", ',{"n":2,"verdict":"correct"}]')
    result = jg.judge_grouping(data, original, ask=Scripted(repeated, repeated, repeated))
    assert result["verdicts"][ids[1]] == "merged"  # first use kept


def test_judge_grouping_function_invalid_json_fails(data, original):
    ask = Scripted("not json", "{", jreply({1: "correct", 2: "bogus"}))
    with pytest.raises(parse.GroupingError):
        jg.judge_grouping(data, original, ask=ask)
    assert len(ask.calls) == 3
    with pytest.raises(parse.SchemaError):
        jg.parse_reply('{"articles": [], "missed_regions": -1}', set())
    with pytest.raises(parse.SchemaError):
        jg.parse_reply('{"articles": [{"n": "1", "verdict": "correct"}], "missed_regions": 0}', {1})


def test_judge_grouping_function_shrinks_texts_when_the_page_is_too_long(data, original):
    ask = Scripted(
        parse.TooLong("context length"),
        parse.TooLong("context length"),
        jreply({1: "correct", 2: "correct"}),
    )
    assert jg.judge_grouping(data, original, ask=ask)["score"] == 1.0
    assert "| " in text_of(ask.calls[0]) and "| " in text_of(ask.calls[1])
    assert "| " not in text_of(ask.calls[2])
    with pytest.raises(jg.JudgeError):
        jg.judge_grouping(data, original, ask=Scripted(*[parse.TooLong("x")] * 3))


def test_overlay_hides_unjudged(data, original):
    """The judge's picture holds only the judged articles: the masthead and the ad are not
    painted, nothing is outlined in red, and nothing marks unassigned blocks."""
    flagged = dict(
        original, articles=[dict(a, flags=["disconnected"]) for a in original["articles"]]
    )
    flagged["unassigned"] = ["b002"]
    ask = Scripted(jreply({1: "correct", 2: "correct"}))
    jg.judge_grouping(data, flagged, ask=ask)
    img = image_of(ask.calls[0]).convert("RGB")

    def white(xy):
        return all(c > 245 for c in img.getpixel(xy))

    assert not white((100, 250)) and not white((300, 250))  # the two news articles are painted
    assert white((200, 425)) and white((200, 520))  # masthead and ad are not
    # no red outline of the flagged articles' union boxes
    r, g, b = img.getpixel((20, 200))
    assert not (r > 150 and g < 100 and b < 100)


# --- the judge stage ---------------------------------------------------------------------------


def test_judge_stage_writes_artifact_and_opens_model_once(world, original, fake_models, tmp_path):
    world.put("group", original)
    fake_models.replies["gemma3-27b"] = [jreply({1: "correct", 2: "merged"})] * 3
    stage = JudgeStage()
    stage.open()
    results = [world.run(stage) for _ in range(3)]
    stage.close()
    assert fake_models.opened == ["gemma3-27b"] and fake_models.closed == ["gemma3-27b"]
    artifact = json.loads(Path(results[0].artifact_path).read_text())
    assert artifact["score"] == 0.5 and artifact["source"] == "group"
    assert results[0].detail["score"] == 0.5 and results[0].detail["judge_skipped"] is False


def test_judge_stage_skipped_page_has_null_score(world, original, fake_models):
    world.put("group", dict(original, articles=original["articles"][2:]))
    fake_models.replies["gemma3-27b"] = [jreply({}, 0)]
    stage = JudgeStage()
    stage.open()
    result = world.run(stage)
    stage.close()
    assert result.detail["score"] is None and result.detail["judge_skipped"] is True


# --- FR-3: group_retry -------------------------------------------------------------------------


@pytest.mark.parametrize("score", [0.8, 0.9, 1.0, None])
def test_high_score_passes_through_without_model_call(world, original, fake_models, score):
    world.put("group", original)
    world.put("judge", judge_artifact(original, score))
    stage = GroupRetryStage()
    stage.open()
    result = world.run(stage)
    stage.close()
    assert result.detail == {"retried": False} and result.artifact_path is None
    assert fake_models.opened == [] and fake_models.requests == {}


def test_low_score_regroups_at_1_5x(world, original, fake_models, set_settings):
    set_settings(work_dir=world.tmp / "work", group_image_max_px=200)
    world.put("group", original)
    world.put("judge", judge_artifact(original, 0.5))
    fake_models.replies["qwen3-vl-32b"] = [RETRY_REPLY]
    stage = GroupRetryStage()
    stage.open()
    result = world.run(stage)
    stage.close()
    assert result.detail["retried"] is True and result.detail["image_long_side"] == 300
    assert max(image_of(fake_models.requests["qwen3-vl-32b"][0]).size) == 300
    assert fake_models.opened == ["qwen3-vl-32b"] and fake_models.closed == ["qwen3-vl-32b"]
    artifact = json.loads(Path(result.artifact_path).read_text())
    assert artifact["model"] == "qwen3-vl-32b" and len(artifact["articles"]) == 3
    assert "group_retry" in result.artifact_path


def test_default_retry_size_is_5376():
    from newsrag.stages.group_retry import retry_long_side

    assert retry_long_side() == 5376


def test_model_opens_at_the_first_page_that_needs_a_retry(world, original, fake_models):
    world.put("group", original)
    world.put("judge", judge_artifact(original, 0.95))
    stage = GroupRetryStage()
    stage.open()
    world.run(stage)
    assert fake_models.opened == []
    world.put("judge", judge_artifact(original, 0.3))
    fake_models.replies["qwen3-vl-32b"] = [RETRY_REPLY]
    world.run(stage)
    stage.close()
    assert fake_models.opened == ["qwen3-vl-32b"]


# --- FR-4: judge_retry -------------------------------------------------------------------------


def test_judge_retry_passes_through_when_not_retried(world, original, fake_models):
    world.put("group_retry", None, detail={"retried": False})
    stage = JudgeRetryStage()
    stage.open()
    result = world.run(stage)
    stage.close()
    assert result.detail == {"retried": False} and result.artifact_path is None
    assert fake_models.opened == []


def test_judge_retry_judges_the_regrouping(world, original, fake_models, data):
    regrouped = group_page(data, Scripted(RETRY_REPLY), "qwen3-vl-32b")
    world.put("group_retry", regrouped, detail={"retried": True})
    fake_models.replies["gemma3-27b"] = [jreply({1: "correct"})]
    stage = JudgeRetryStage()
    stage.open()
    assert fake_models.opened == []  # lazy
    result = world.run(stage)
    stage.close()
    assert fake_models.opened == ["gemma3-27b"]
    assert result.detail["retried"] is True and result.detail["score"] == 1.0
    assert json.loads(Path(result.artifact_path).read_text())["source"] == "group_retry"


# --- FR-5: group_final ---------------------------------------------------------------------------


def final_world(world, original, data, score, retry_score, retry_reply=RETRY_REPLY):
    regrouped = group_page(data, Scripted(retry_reply), "qwen3-vl-32b")
    world.put("group", original)
    world.put("judge", judge_artifact(original, score))
    if retry_score == "none":
        world.put("group_retry", None, detail={"retried": False})
        world.put("judge_retry", None, detail={"retried": False})
        return regrouped
    world.put("group_retry", regrouped, detail={"retried": True, "image_long_side": 5376})
    world.put("judge_retry", judge_artifact(regrouped, retry_score), detail={"retried": True})
    return regrouped


def run_final(world):
    result = world.run(GroupFinalStage())
    return json.loads(Path(result.artifact_path).read_text())


def test_not_retried_page_passes_original_through(world, original, data):
    final_world(world, original, data, 0.9, "none")
    out = run_final(world)
    assert out["judge_score"] == 0.9 and out["retried"] is False
    assert [a["article_id"] for a in out["articles"]] == [
        a["article_id"] for a in original["articles"]
    ]
    assert {"articles", "page_flags", "unassigned", "judge_score", "retried"} <= set(out)
    assert "low_confidence:judge_low" not in out["page_flags"]
    assert all("low_confidence:judge_low" not in a["flags"] for a in out["articles"])
    # the ticket-012 schema is unchanged
    assert {"page_id", "model", "prompt_version"} <= set(out)
    assert set(out["articles"][0]) >= {
        "article_id",
        "kind",
        "blocks",
        "headline",
        "text",
        "bbox_union",
    }


def test_better_grouping_kept(world, original, data):
    final_world(world, original, data, 0.5, 0.9)
    out = run_final(world)
    assert out["judge_score"] == 0.9 and out["retried"] is True
    assert len(out["articles"]) == 3  # the regrouping
    assert "low_confidence:judge_low" not in out["page_flags"]


def test_worse_or_equal_retry_keeps_the_first_grouping(world, original, data):
    final_world(world, original, data, 0.6, 0.4)
    out = run_final(world)
    assert out["judge_score"] == 0.6 and out["retried"] is True and len(out["articles"]) == 4
    final_world(world, original, data, 0.6, 0.6)  # a tie keeps the first
    out = run_final(world)
    assert out["judge_score"] == 0.6 and len(out["articles"]) == 4


def test_still_low_flagged(world, original, data):
    final_world(world, original, data, 0.4, 0.6)
    out = run_final(world)
    assert out["judge_score"] == 0.6
    assert "low_confidence:judge_low" in out["page_flags"]
    assert all("low_confidence:judge_low" in a["flags"] for a in out["articles"])  # incl. masthead
    final_world(world, original, data, 0.79, "none")
    assert "low_confidence:judge_low" in run_final(world)["page_flags"]
    final_world(world, original, data, 0.8, "none")  # at the threshold: not low
    assert "low_confidence:judge_low" not in run_final(world)["page_flags"]


def test_article_verdict_flags(world, original, data):
    ids = [a["article_id"] for a in original["articles"]]
    final_world(world, original, data, 0.5, "none")
    world.put(
        "judge",
        judge_artifact(original, 0.9, {ids[0]: "correct", ids[1]: "merged"}),
    )
    out = run_final(world)
    first, second, masthead, ad = out["articles"]
    assert first["judge_verdict"] == "correct" and second["judge_verdict"] == "merged"
    assert not any(f.startswith("judge:") for f in first["flags"])
    assert "judge:merged" in second["flags"]
    assert masthead["judge_verdict"] is None and ad["judge_verdict"] is None
    assert not any(f.startswith("judge:") for f in masthead["flags"] + ad["flags"])
    for verdict in ("split", "wrong_blocks", "not_an_article", "missing"):
        world.put("judge", judge_artifact(original, 0.9, {ids[0]: verdict, ids[1]: "correct"}))
        assert f"judge:{verdict}" in run_final(world)["articles"][0]["flags"]


def test_geometry_flags_become_low_confidence_geometry(world, original, data):
    final_world(world, original, data, 0.9, "none")
    grouping = json.loads(json.dumps(original))
    grouping["articles"][0]["flags"] = ["no_headline"]
    grouping["articles"][1]["flags"] = ["order_changed", "duplicate_block", "order_from_model"]
    world.put("group", grouping)
    out = run_final(world)
    assert "low_confidence:geometry" in out["articles"][0]["flags"]
    assert "no_headline" in out["articles"][0]["flags"]
    assert "low_confidence:geometry" not in out["articles"][1]["flags"]  # informational only
    for flag in ("disconnected", "overlaps_other", "headline_below_body"):
        grouping["articles"][1]["flags"] = [flag]
        world.put("group", grouping)
        assert "low_confidence:geometry" in run_final(world)["articles"][1]["flags"]
    for page_flag in ("blocks_omitted", "block_duplicated"):
        grouping["articles"][1]["flags"] = []
        grouping["articles"][0]["flags"] = []
        grouping["page_flags"] = [page_flag]
        world.put("group", grouping)
        out = run_final(world)
        assert all("low_confidence:geometry" in a["flags"] for a in out["articles"])


# --- FR-7: failures ------------------------------------------------------------------------------


def test_judge_failed_passthrough(world, original):
    world.put("group", original)
    world.put("judge", None, status="failed", attempt=3)
    out = run_final(world)
    assert out["judge_score"] is None and out["retried"] is False
    assert "low_confidence:judge_failed" in out["page_flags"]
    assert all("low_confidence:judge_failed" in a["flags"] for a in out["articles"])
    assert all(a["judge_verdict"] is None for a in out["articles"])
    assert [a["article_id"] for a in out["articles"]] == [
        a["article_id"] for a in original["articles"]
    ]


def test_retry_failed_keeps_original(world, original, data):
    world.put("group", original)
    world.put("judge", judge_artifact(original, 0.4))
    world.put("group_retry", None, status="failed", attempt=3)
    out = run_final(world)
    assert out["judge_score"] == 0.4 and out["retried"] is False and len(out["articles"]) == 4
    assert "low_confidence:retry_failed" in out["page_flags"]
    assert "low_confidence:judge_low" in out["page_flags"]
    assert all("low_confidence:retry_failed" in a["flags"] for a in out["articles"])
    # judge_retry failed after a good group_retry
    final_world(world, original, data, 0.4, 0.9)
    world.put("judge_retry", None, status="failed", attempt=3)
    out = run_final(world)
    assert out["judge_score"] == 0.4 and len(out["articles"]) == 4
    assert "low_confidence:retry_failed" in out["page_flags"]


# --- eligibility: group_final also runs after a failure for good ---------------------------------


def make_context(rows):
    from newsrag.stages.registry import REGISTRY

    return eligibility.PageContext(REGISTRY, page_row(), rows), REGISTRY["group_final"]


def fake_rows(stage_states):
    """Ledger rows `{stage: (status, attempt)}` whose versions and inputs are current."""
    from newsrag.stages.registry import REGISTRY

    rows: dict[str, dict] = {}
    order = [
        "render",
        "ocr",
        "ocr_split",
        "ocr_final",
        "folio",
        "group",
        "judge",
        "group_retry",
        "judge_retry",
    ]
    ctx = eligibility.PageContext(REGISTRY, page_row(), rows)
    for name in order:
        if name not in stage_states:
            continue
        status, attempt = stage_states[name]
        stage = REGISTRY[name]
        rows[name] = {
            "status": status,
            "attempt": attempt,
            "version": stage.version_for(page_row()),
            "input_versions": None,
            "finished_at": None,
        }
        ctx._current.clear()
        rows[name]["input_versions"] = ctx.inputs(stage)
    ctx._current.clear()
    return rows


@pytest.fixture
def done_upstream():
    return {n: ("done", 1) for n in ("render", "ocr", "ocr_split", "ocr_final", "folio", "group")}


def test_group_final_waits_while_the_judge_may_still_be_retried(done_upstream):
    for status, attempt in (("failed", 1), ("failed", 2), ("running", 1)):
        rows = fake_rows({**done_upstream, "judge": (status, attempt)})
        ctx, final = make_context(rows)
        assert not ctx.eligible(final, 3)


def test_group_final_runs_after_the_judge_failed_for_good(done_upstream):
    rows = fake_rows({**done_upstream, "judge": ("failed", 3)})
    ctx, final = make_context(rows)
    assert ctx.eligible(final, 3)
    assert not ctx.current("group_retry")  # the others still need a current judge


def test_group_final_runs_after_a_retry_failed_for_good(done_upstream):
    ok = {**done_upstream, "judge": ("done", 1)}
    for states in (
        {**ok, "group_retry": ("failed", 3)},  # judge_retry is blocked, has no row
        {**ok, "group_retry": ("done", 1), "judge_retry": ("failed", 3)},
    ):
        ctx, final = make_context(fake_rows(states))
        assert ctx.eligible(final, 3), states
    ctx, final = make_context(fake_rows({**ok, "group_retry": ("failed", 2)}))
    assert not ctx.eligible(final, 3)


def test_group_final_needs_the_group(done_upstream):
    states = {**done_upstream, "group": ("failed", 3)}
    ctx, final = make_context(fake_rows(states))
    assert not ctx.eligible(final, 3)


def test_other_stages_still_need_current_dependencies(done_upstream):
    from newsrag.stages.registry import REGISTRY

    rows = fake_rows({**done_upstream, "judge": ("failed", 3)})
    ctx = eligibility.PageContext(REGISTRY, page_row(), rows)
    assert not ctx.eligible(REGISTRY["group_retry"], 3)


# --- sanity helper and stats ----------------------------------------------------------------------


def test_merge_articles_unites_two_articles(original):
    grouping = json.loads(json.dumps(original))
    a01, a02 = (a["article_id"] for a in grouping["articles"][:2])
    before = json.dumps(grouping)
    merged = jg.merge_articles(grouping, a02, a01)  # order of the ids does not matter
    assert json.dumps(grouping) == before  # nothing changed in place
    assert len(merged["articles"]) == len(grouping["articles"]) - 1
    first = merged["articles"][0]
    assert first["article_id"] == a01 and first["headline"] == grouping["articles"][0]["headline"]
    assert len(first["blocks"]) == 4 and first["flags"] == []
    assert (
        first["text"] == grouping["articles"][0]["text"] + "\n\n" + grouping["articles"][1]["text"]
    )


def test_distinct_headlines_rejects_a_fragment_of_the_same_story():
    # 2024-12-06_GNOCLT_main_16: a05 is the broken-off tail of a02's headline
    whole = {"headline": "హామీలను నెరవేర్చలేకే డైవర్సన్ పాలిటిక్స్"}
    tail = {"headline": "నేకే డైవర్సన్ పాలిటిక్స్"}
    other = {"headline": "జగత్ కిలాడి!"}
    assert not jg.distinct_headlines(whole, tail)
    assert jg.distinct_headlines(whole, other)
    assert not jg.distinct_headlines(other, {"headline": "జగత్ కిలాడి"})  # same words
    assert not jg.distinct_headlines(other, {"headline": ""})  # no headline: not sure
    assert jg.headline_words({"headline": "‘అన్నదాతకు  అండ’"}) == {"అన్నదాతకు", "అండ"}


def test_pick_clean_pair_needs_two_correct_articles(original):
    grouping = json.loads(json.dumps(original))
    a01, a02 = (a["article_id"] for a in grouping["articles"][:2])
    third = dict(grouping["articles"][0], article_id=f"{PAGE}#a05", text="x" * 500)
    third["headline"] = "Longest story"
    grouping["articles"].append(third)
    correct = {a01: "correct", a02: "correct", third["article_id"]: "correct"}
    grouping["articles"][0]["text"] = "a" * 100
    grouping["articles"][1]["text"] = "b" * 50
    # the two longest texts (a05 then a01), returned in grouping order
    assert jg.pick_clean_pair(grouping, correct) == (a01, third["article_id"])
    # an article the stored judge did not call correct is never used
    assert jg.pick_clean_pair(grouping, {**correct, third["article_id"]: "merged"}) == (a01, a02)
    assert jg.pick_clean_pair(grouping, {a01: "correct", a02: "split"}) is None
    assert jg.pick_clean_pair(dict(grouping, articles=grouping["articles"][2:4]), correct) is None


def test_merge_caught():
    assert (
        jg.merge_caught("merged") and jg.merge_caught("split") and jg.merge_caught("wrong_blocks")
    )
    assert not jg.merge_caught("correct") and not jg.merge_caught(jg.MISSING)


@pytest.mark.parametrize(
    ("verdict", "line", "total"), [("merged", "caught", 1), ("correct", "missed", 0)]
)
def test_sanity_counts_the_merged_article_verdict(
    world, original, fake_models, tmp_path, verdict, line, total
):
    from newsrag.cli import app

    world.put("group", original)
    world.put("judge", judge_artifact(original, 1.0))
    pages = tmp_path / "pages.txt"
    pages.write_text(PAGE + "\n")
    fake_models.replies["gemma3-27b"] = [jreply({1: verdict})]
    result = CliRunner().invoke(app, ["judge", "sanity", "--pages-file", str(pages), "--n", "5"])
    assert result.exit_code == 0, result.output
    assert f"pair=a01+a02 verdict={verdict}" in result.output and line in result.output
    assert f"caught: {total}/1" in result.output
    judged = text_of(fake_models.requests["gemma3-27b"][0])
    assert "#2 " not in judged  # the second article was merged into #1


def test_sanity_skips_a_page_without_a_clean_pair(world, original, fake_models, tmp_path):
    from newsrag.cli import app

    a02 = original["articles"][1]["article_id"]
    verdicts = {original["articles"][0]["article_id"]: "correct", a02: "merged"}
    world.put("group", original)
    world.put("judge", judge_artifact(original, 0.5, verdicts))
    pages = tmp_path / "pages.txt"
    pages.write_text(PAGE + "\n")
    result = CliRunner().invoke(app, ["judge", "sanity", "--pages-file", str(pages)])
    assert result.exit_code == 1
    assert "no two correct news articles with distinct headlines" in result.output
    assert fake_models.opened == []


def test_stats_lines():
    from newsrag.grouping.judge_cli import stats_lines

    pages = [
        {"page_id": "a", "judge_score": 0.9, "retried": False, "page_flags": []},
        {
            "page_id": "b",
            "judge_score": 0.7,
            "retried": True,
            "page_flags": ["low_confidence:judge_low"],
        },
        {"page_id": "c", "judge_score": 0.9, "retried": True, "page_flags": []},
        {"page_id": "d", "judge_score": None, "retried": False, "page_flags": []},
    ]
    lines = stats_lines(pages, {"a": 0.9, "b": 0.5, "c": 0.6, "d": None})
    assert lines == [
        "pages=4",
        "mean_score=0.833",
        "judge_low_pct=25.0",
        "retried_pct=50.0",
        "retry_gain=0.250",
    ]
    none = stats_lines(
        [{"page_id": "a", "judge_score": 1.0, "retried": False, "page_flags": []}], {}
    )
    assert none[-1] == "retry_gain=n/a"
    assert stats_lines([], {})[0] == "pages=0"


# --- the lazy model holder ------------------------------------------------------------------------


def test_lazy_model_failure_to_open_is_not_retried_per_page(world, original, monkeypatch):
    world.put("group", original)
    world.put("judge", judge_artifact(original, 0.2))
    calls = []

    def broken(name):
        calls.append(name)
        raise RuntimeError("vllm failed to start")

    monkeypatch.setattr("newsrag.models.use", broken)
    stage = GroupRetryStage()
    stage.open()
    for _ in range(3):
        with pytest.raises(RuntimeError, match="vllm failed to start"):
            stage.handle(page_row())
    assert calls == ["qwen3-vl-32b"]  # one start, not one per page
    stage.close()
    stage.open()  # a new run tries again
    with pytest.raises(RuntimeError):
        stage.handle(page_row())
    assert len(calls) == 2


def test_lazy_model_is_not_reopened_after_close(world, original, fake_models):
    world.put("group", original)
    world.put("judge", judge_artifact(original, 0.2))
    fake_models.replies["qwen3-vl-32b"] = [RETRY_REPLY]
    stage = GroupRetryStage()
    stage.open()
    world.run(stage)
    stage.close()
    with pytest.raises(RuntimeError, match="released"):
        stage.handle(page_row())
    assert fake_models.opened == ["qwen3-vl-32b"]


def test_judge_result_records_the_text_length_sent(data, original):
    result = jg.judge_grouping(data, original, ask=Scripted(jreply({1: "correct", 2: "correct"})))
    assert result["text_chars"] == 300
    ask = Scripted(parse.TooLong("context length"), jreply({1: "correct", 2: "correct"}))
    assert jg.judge_grouping(data, original, ask=ask)["text_chars"] == 100
