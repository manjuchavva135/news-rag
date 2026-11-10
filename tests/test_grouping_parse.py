"""Ticket 012: model input, reply parsing, retries, splitting, the stage and the overlay (CPU only,
no model: the chat call is a scripted fake)."""

from __future__ import annotations

import contextlib
import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest
from PIL import Image
from typer.testing import CliRunner

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.grouping import parse
from newsrag.grouping.overlay import draw_articles
from newsrag.stages import group as group_mod
from newsrag.stages.base import PageRow
from newsrag.stages.group import GroupStage, group_page, send, split_halves

REQUEST = httpx.Request("POST", "http://127.0.0.1:8100/v1/chat/completions")


def blk(order, bbox, text="", label="Text"):
    return {
        "block_id": f"b{order:03d}",
        "order": order,
        "label": label,
        "type": "text",
        "bbox": list(bbox),
        "text": text,
    }


@pytest.fixture
def set_settings(monkeypatch):
    """`set_settings(name=value, ...)`: replace the (frozen) settings for this test."""
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


def page_data(page_png, blocks, page_flags=()):
    return {
        "page_id": "2024-12-06_X_main_01",
        "image": {"path": str(page_png), "width": 400, "height": 600},
        "page_flags": list(page_flags),
        "blocks": blocks,
    }


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


class Scripted:
    """A fake `ask(messages)`: returns the scripted replies in order (an Exception is raised)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []
        self.max_items: list[int | None] = []

    def __call__(self, messages, max_items=None):
        self.calls.append(messages)
        self.max_items.append(max_items)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def text_of(messages):
    content = messages[0]["content"]
    return "\n".join(p["text"] for p in content if p["type"] == "text")


def numbers_in(messages):
    return [int(n) for n in re.findall(r"^#(\d+) \[", text_of(messages), re.MULTILINE)]


BLOCKS = [blk(1, (10, 10, 300, 60), "Head"), blk(2, (10, 70, 300, 200), "Body text")]


# --- parse (FR-3, FR-4) -----------------------------------------------------------------------


def test_parse_valid_reply_with_fence():
    text = "```json\n" + reply(("news", [(1, "headline"), (2, "body")]), unassigned=[3]) + "\n```"
    got = parse.parse_reply(text, {1, 2, 3})
    assert got == {
        "articles": [
            {"kind": "news", "blocks": [{"n": 1, "role": "headline"}, {"n": 2, "role": "body"}]}
        ],
        "unassigned": [3],
    }


def test_invalid_json_retried_twice_then_fails(page_png):
    ask = Scripted("not json", "{broken", "still {not json")
    with pytest.raises(parse.GroupingError, match="3 replies"):
        group_page(page_data(page_png, BLOCKS), ask, "m")
    assert len(ask.calls) == 3  # first try + 2 retries
    # the retry carries the model's reply and the error
    retry = ask.calls[1]
    assert retry[-2] == {"role": "assistant", "content": "not json"}
    assert "not valid" in retry[-1]["content"] and "JSON" in retry[-1]["content"]
    assert ask.calls[2][-2]["content"] == "{broken"


def test_invalid_reply_then_valid_succeeds(page_png):
    good = reply(("news", [(1, "headline"), (2, "body")]))
    ask = Scripted("nope", good)
    artifact = group_page(page_data(page_png, BLOCKS), ask, "m")
    assert len(ask.calls) == 2
    assert artifact["articles"][0]["headline"] == "Head"


def test_unknown_block_number_is_schema_error():
    with pytest.raises(parse.SchemaError, match="not a block number"):
        parse.parse_reply(reply(("news", [(1, "headline"), (9, "body")])), {1, 2})
    with pytest.raises(parse.SchemaError, match="unassigned"):
        parse.parse_reply(reply(("news", [(1, "headline")]), unassigned=[7]), {1, 2})


@pytest.mark.parametrize(
    "bad",
    [
        '{"articles": [{"kind": "story", "blocks": []}]}',  # unknown kind
        '{"articles": [{"kind": "news", "blocks": [{"n": 1, "role": "title"}]}]}',  # unknown role
        '{"articles": [{"kind": "news", "blocks": [{"n": "1", "role": "body"}]}]}',  # n not int
        '{"articles": [{"kind": "news", "blocks": [{"n": true, "role": "body"}]}]}',
        '{"articles": [{"kind": "news", "blocks": [1]}]}',
        '{"articles": [{"kind": "news"}]}',
        '{"articles": {}}',
        '{"unassigned": []}',
        "[1, 2]",
        '{"articles": [], "unassigned": "1"}',
    ],
)
def test_schema_errors(bad):
    with pytest.raises(parse.SchemaError):
        parse.parse_reply(bad, {1, 2})


def test_empty_article_dropped():
    text = reply(("news", []), ("news", [(1, "headline")]))
    assert len(parse.parse_reply(text, {1, 2})["articles"]) == 1


def test_missing_unassigned_is_empty():
    got = parse.parse_reply('{"articles": []}', {1})
    assert got == {"articles": [], "unassigned": []}


def test_zero_block_page_no_model_call(page_png):
    ask = Scripted()
    artifact = group_page(
        page_data(page_png, [], page_flags=["low_confidence:sarvam_failed"]), ask, "m"
    )
    assert ask.calls == []
    assert artifact["articles"] == [] and artifact["unassigned"] == []
    assert artifact["page_flags"] == ["low_confidence:sarvam_failed", "low_confidence:no_blocks"]


def test_ocr_page_flags_copied(page_png):
    ask = Scripted(reply(("news", [(1, "headline")])))  # block 2 omitted
    data = page_data(page_png, BLOCKS, page_flags=["low_confidence:sarvam_failed"])
    artifact = group_page(data, ask, "m")
    assert artifact["page_flags"] == ["low_confidence:sarvam_failed", "blocks_omitted"]
    assert artifact["unassigned"] == ["b002"]


def test_reply_length_is_capped_by_the_block_count(page_png):
    ask = Scripted(reply(("news", [(1, "headline"), (2, "body")])))
    group_page(page_data(page_png, BLOCKS), ask, "m")
    assert ask.max_items == [2]


def test_artifact_keys(page_png):
    ask = Scripted(reply(("news", [(1, "headline"), (2, "body")])))
    artifact = group_page(page_data(page_png, BLOCKS), ask, "qwen3-vl-32b")
    assert sorted(artifact) == [
        "articles",
        "model",
        "page_flags",
        "page_id",
        "prompt_version",
        "unassigned",
    ]
    assert artifact["model"] == "qwen3-vl-32b" and artifact["prompt_version"] == 1
    assert sorted(artifact["articles"][0]) == [
        "article_id",
        "bbox_union",
        "blocks",
        "flags",
        "headline",
        "kind",
        "text",
    ]


# --- model input (FR-2) -----------------------------------------------------------------------


def test_block_list_format():
    block = blk(12, (75.4, 537, 459, 613.6), "  మాజీ ఎంపీ\n  హర్షకుమార్  ", label="SectionHeader")
    assert gin.block_line(block, 80) == "#12 [SectionHeader] (75,537,459,614) మాజీ ఎంపీ హర్షకుమార్"
    long = blk(1, (0, 0, 10, 10), "x" * 200)
    assert gin.block_line(long, 80).endswith(" " + "x" * 80)
    assert gin.block_line(long, 40).endswith(" " + "x" * 40)


def test_block_list_cut_over_150_blocks():
    many = [blk(i, (0, i, 10, i + 1), "y" * 100) for i in range(1, 151)]
    assert gin.block_list(many).splitlines()[0].endswith("y" * 80)
    assert not gin.block_list(many).splitlines()[0].endswith("y" * 81)
    many.append(blk(151, (0, 0, 10, 10), "y" * 100))
    first = gin.block_list(many).splitlines()[0]
    assert first.endswith(" " + "y" * 40)


def test_prompt_states_page_size_and_lists_blocks(page_png):
    ask = Scripted(reply(("news", [(1, "headline"), (2, "body")])))
    group_page(page_data(page_png, BLOCKS), ask, "m")
    messages = ask.calls[0]
    assert [p["type"] for p in messages[0]["content"]] == ["image_url", "text"]
    assert messages[0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    text = text_of(messages)
    assert "400 x 600 pixels" in text
    assert "#1 [Text] (10,10,300,60) Head" in text and "#2 [Text] (10,70,300,200) Body text" in text
    assert "{blocks}" not in text and "{width}" not in text


def test_image_resized_to_group_image_max_px(page_png, set_settings):
    set_settings(group_image_max_px=200)
    jpeg = gin.page_image(page_data(page_png, BLOCKS), BLOCKS)
    import io

    assert max(Image.open(io.BytesIO(jpeg)).size) == 200


# --- splitting and transport (FR-8) -----------------------------------------------------------


def test_split_on_length(page_png):
    blocks = [
        blk(1, (10, 10, 150, 60), "Left head"),
        blk(2, (10, 70, 150, 200), "Left body"),
        blk(3, (250, 10, 390, 60), "Right head"),
        blk(4, (250, 70, 390, 200), "Right body"),
    ]
    ask = Scripted(
        parse.TooLong("context"),
        reply(("news", [(1, "headline"), (2, "body")])),
        reply(("brief_item", [(3, "headline"), (4, "body")])),
    )
    artifact = group_page(page_data(page_png, blocks), ask, "m")
    assert numbers_in(ask.calls[1]) == [1, 2] and numbers_in(ask.calls[2]) == [3, 4]
    assert [a["article_id"] for a in artifact["articles"]] == [
        "2024-12-06_X_main_01#a01",
        "2024-12-06_X_main_01#a02",
    ]
    assert [a["kind"] for a in artifact["articles"]] == ["news", "brief_item"]
    assert artifact["unassigned"] == []
    assert "split_halves" in artifact["page_flags"]


def test_split_halves_by_centre_line():
    blocks = [blk(1, (0, 0, 190, 10)), blk(2, (190, 0, 400, 10)), blk(3, (150, 0, 250, 10))]
    left, right = split_halves(blocks, 400)
    assert [b["order"] for b in left] == [1] and [b["order"] for b in right] == [2, 3]


def test_split_half_still_fails_fails_page(page_png):
    blocks = [blk(1, (10, 10, 150, 60), "a"), blk(2, (250, 10, 390, 60), "b")]
    ask = Scripted(
        parse.TooLong("x"), reply(("news", [(1, "headline")])), parse.TooLong("still too long")
    )
    with pytest.raises(parse.TooLong):
        group_page(page_data(page_png, blocks), ask, "m")
    assert len(ask.calls) == 3  # no second split


def test_page_with_blocks_on_one_side_cannot_split(page_png):
    ask = Scripted(parse.TooLong("x"))
    with pytest.raises(parse.GroupingError, match="cannot be split"):
        group_page(page_data(page_png, BLOCKS), ask, "m")


def completion(content="ok", finish="stop"):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=message)])


def http_error(cls, status, message="x"):
    return cls(message, response=httpx.Response(status, request=REQUEST), body=None)


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(group_mod.time, "sleep", waits.append)
    return waits


def test_transport_retry(no_sleep):
    outcomes = [
        openai.APITimeoutError(request=REQUEST),
        http_error(openai.InternalServerError, 503),
        completion("fine"),
    ]
    calls = []

    def create(messages, max_items=None):
        calls.append(messages)
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    assert send(create)([{"role": "user", "content": "q"}]) == "fine"
    assert len(calls) == 3
    assert no_sleep == [2.0, 8.0]  # group_retry_backoff_s


def test_transport_retry_gives_up_after_two_retries(no_sleep):
    n = []

    def create(messages, max_items=None):
        n.append(1)
        raise openai.APIConnectionError(request=REQUEST)

    with pytest.raises(openai.APIConnectionError):
        send(create)([])
    assert len(n) == 3


def test_http_4xx_other_than_context_is_not_retried(no_sleep):
    n = []

    def create(messages, max_items=None):
        n.append(1)
        raise http_error(openai.BadRequestError, 400, "invalid image")

    with pytest.raises(openai.BadRequestError):
        send(create)([])
    assert len(n) == 1 and no_sleep == []


def test_context_length_400_and_length_finish_raise_too_long():
    def too_big(messages, max_items=None):
        raise http_error(
            openai.BadRequestError, 400, "This model's maximum context length is 65536 tokens"
        )

    with pytest.raises(parse.TooLong):
        send(too_big)([])
    with pytest.raises(parse.TooLong):
        send(lambda messages, max_items=None: completion("{", finish="length"))([])


def test_make_create_uses_temperature_0_and_8192_tokens():
    seen = {}

    class Completions:
        def create(self, **kwargs):
            seen.update(kwargs)
            return completion()

    class Raw:
        def with_options(self, **kwargs):
            seen["options"] = kwargs
            return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    client = SimpleNamespace(raw=Raw(), profile="qwen3-vl-32b")
    group_mod.make_create(client)([{"role": "user", "content": "q"}], 12)
    assert seen["temperature"] == 0 and seen["max_tokens"] == 8192
    assert seen["model"] == "qwen3-vl-32b"
    assert seen["stop"] == ["\n\n"]
    # the reply schema: known kinds and roles, no empty article, no list longer than the block count
    schema = seen["response_format"]["json_schema"]["schema"]
    articles = schema["properties"]["articles"]
    assert articles["maxItems"] == 12 and schema["properties"]["unassigned"]["maxItems"] == 12
    assert articles["items"]["properties"]["blocks"]["minItems"] == 1
    assert articles["items"]["properties"]["kind"]["enum"] == list(parse.KINDS)
    assert articles["items"]["properties"]["blocks"]["items"]["properties"]["role"]["enum"] == list(
        parse.ROLES
    )
    assert seen["options"] == {"timeout": 300.0, "max_retries": 0}


# --- the stage (FR-1) -------------------------------------------------------------------------


def test_version_ends_with_config_hash(monkeypatch, set_settings):
    version = GroupStage().version
    assert re.fullmatch(r"qwen3-vl-32b-group-p1-[0-9a-f]{8}", version)
    assert GroupStage().version == version
    set_settings(group_max_gap_px=61)
    assert GroupStage().version != version
    assert GroupStage().version.startswith("qwen3-vl-32b-group-p1-")
    set_settings(group_model="gemma3-27b")
    assert GroupStage().version.startswith("gemma3-27b-group-p1-")
    # the prompt is part of the version
    monkeypatch.setattr(gin, "PROMPT_FILE", Path(__file__))
    assert GroupStage().version != version


def test_registered_and_import_is_lazy():
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["group"]
    assert stage.depends_on == ("ocr_final",)
    assert stage.concurrency == 16
    assert stage.models_needed == ("qwen3-vl-32b",)


def test_model_opened_once_for_all_threads(tmp_path, page_png, monkeypatch, capsys, set_settings):
    entered, exited = [], []
    seen_clients = []

    @contextlib.contextmanager
    def fake_use(name):
        entered.append(name)
        client = SimpleNamespace(raw=object(), profile=name)
        try:
            yield client
        finally:
            exited.append(name)

    def fake_create(client):
        seen_clients.append(client)
        return lambda messages, max_items=None: completion(
            reply(("news", [(1, "headline"), (2, "body")]))
        )

    monkeypatch.setattr("newsrag.models.use", fake_use)
    monkeypatch.setattr(group_mod, "make_create", fake_create)
    set_settings(work_dir=tmp_path / "work")
    monkeypatch.setattr(gin, "load_ocr_final", lambda page_id: page_data(page_png, BLOCKS))

    stage = GroupStage()
    stage.open()
    pages = [
        PageRow(f"p{i}", None, "X", "main", i, None, "", "", 1)  # type: ignore[arg-type]
        for i in range(20)
    ]
    results = []

    def work(page):
        results.append(stage.handle(page))

    threads = [threading.Thread(target=work, args=(p,)) for p in pages]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stage.close()
    assert entered == ["qwen3-vl-32b"] and exited == ["qwen3-vl-32b"]
    assert len(seen_clients) == 1 and len(results) == 20
    artifact = json.loads(Path(results[0].artifact_path).read_text())
    assert artifact["articles"][0]["headline"] == "Head"
    out = capsys.readouterr().out
    assert re.search(r"group: 20 pages in [\d.]+ s \(excl\. model load\) = [\d.]+ pages/s", out)


# --- stats and overlay (FR-7, FR-8) -----------------------------------------------------------


def test_stats_line():
    from newsrag.grouping.cli import count_page, stats_line

    assert stats_line(10, 1, 200, 5) == "pages=10 failed=1 text_blocks=200 unassigned=5 pct=2.5"
    assert stats_line(0, 0, 0, 0) == "pages=0 failed=0 text_blocks=0 unassigned=0 pct=0.0"
    s = config.settings
    text_label, title_label = s.text_labels[0], s.title_labels[0]
    final = {
        "blocks": [
            blk(1, (0, 0, 1, 1), label=text_label),
            blk(2, (0, 0, 1, 1), label=title_label),
            blk(3, (0, 0, 1, 1), label="Picture"),
            blk(4, (0, 0, 1, 1), label=text_label),
        ]
    }
    assert count_page(final, {"unassigned": ["b002", "b003"]}) == (3, 1)


def test_group_overlay_cli_errors(monkeypatch):
    from newsrag.cli import app
    from newsrag.grouping import input as gin_mod

    monkeypatch.setattr(gin_mod, "done_artifact", lambda stage, page_id: None)
    result = CliRunner().invoke(app, ["group", "overlay", "p1", "--stage", "group_final"])
    assert result.exit_code == 1
    assert "no group_final artifact for p1" in result.output
    result = CliRunner().invoke(app, ["group", "overlay", "p1"])
    assert result.exit_code == 1 and "no group artifact for p1" in result.output


def test_group_overlay_cli_writes_png(tmp_path, page_png, monkeypatch):
    from newsrag.cli import app
    from newsrag.grouping import input as gin_mod

    artifact = group_page(
        page_data(page_png, BLOCKS), Scripted(reply(("news", [(1, "headline"), (2, "body")]))), "m"
    )
    path = tmp_path / "group.json"
    path.write_text(json.dumps(artifact))
    monkeypatch.setattr(gin_mod, "done_artifact", lambda stage, page_id: path)
    monkeypatch.setattr(gin_mod, "load_ocr_final", lambda page_id: page_data(page_png, BLOCKS))
    out = tmp_path / "o.png"
    result = CliRunner().invoke(app, ["group", "overlay", "p1", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert Image.open(out).size == (400, 600)


def overlay_fixture(page_png):
    blocks = [
        blk(1, (20, 40, 180, 100), "h"),
        blk(2, (20, 110, 180, 300), "b"),
        blk(3, (220, 40, 380, 300), "ad"),
    ]
    artifact = {
        "page_id": "p",
        "unassigned": [],
        "articles": [
            {
                "article_id": "p#a01",
                "kind": "news",
                "blocks": [
                    {"block_id": "b001", "role": "headline"},
                    {"block_id": "b002", "role": "body"},
                ],
                "bbox_union": [20, 40, 180, 300],
                "flags": ["disconnected"],
                "headline": "h",
                "text": "b",
            },
            {
                "article_id": "p#a02",
                "kind": "ad_commercial",
                "blocks": [{"block_id": "b003", "role": "body"}],
                "bbox_union": [220, 40, 380, 300],
                "flags": [],
                "headline": "",
                "text": "ad",
            },
        ],
    }
    return blocks, artifact


def test_draw_articles_colours_and_labels(page_png):
    blocks, artifact = overlay_fixture(page_png)
    img = draw_articles(page_png, artifact, blocks=blocks)
    assert img.size == (400, 600)
    assert img.getpixel((100, 250)) != (255, 255, 255)  # inside the news article
    assert img.getpixel((300, 250)) != (255, 255, 255)  # inside the ad article
    assert img.getpixel((100, 250)) != img.getpixel((300, 250))  # one colour per article
    assert img.getpixel((200, 450)) == (255, 255, 255)  # outside every block
    assert img.getpixel((22, 42)) != (255, 255, 255)  # the number/kind label at the top-left
    # the flagged article has a red outline on its union box, the unflagged one does not
    r, g, b = img.getpixel((20, 200))
    assert r > 150 and g < 100 and b < 100
    assert img.getpixel((220, 200)) != (220, 0, 0)


def test_draw_articles_mark_flags_false_has_no_red(page_png):
    blocks, artifact = overlay_fixture(page_png)
    img = draw_articles(page_png, artifact, mark_flags=False, blocks=blocks)
    r, g, b = img.getpixel((20, 200))
    assert not (r > 150 and g < 100 and b < 100)


def test_draw_articles_kinds(page_png):
    blocks, artifact = overlay_fixture(page_png)
    img = draw_articles(page_png, artifact, mark_flags=False, kinds={"news"}, blocks=blocks)
    assert img.getpixel((100, 250)) != (255, 255, 255)
    assert img.getpixel((300, 250)) == (255, 255, 255)  # the ad is not drawn
    only_ads = draw_articles(page_png, artifact, kinds={"ad_commercial"}, blocks=blocks)
    assert only_ads.getpixel((100, 250)) == (255, 255, 255)
    assert only_ads.getpixel((300, 250)) != (255, 255, 255)


def test_draw_articles_max_long_side(page_png):
    blocks, artifact = overlay_fixture(page_png)
    img = draw_articles(page_png, artifact, max_long_side=300, blocks=blocks)
    assert max(img.size) == 300
