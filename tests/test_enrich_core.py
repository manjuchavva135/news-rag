"""Ticket 019: `enrich_core` (a fake text model; the one live test is marked gpu)."""

from __future__ import annotations

import contextlib
import json
import threading
import time
import unicodedata
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsrag import config, enrich
from newsrag.enrich import cli as enrich_cli
from newsrag.enrich import core
from newsrag.enrich import validate as v
from newsrag.enrich import verify as vf
from newsrag.stages.base import PageRow
from newsrag.stages.enrich_core import EnrichCoreStage

DAY = date(2026, 4, 3)
HEADLINE = "ముఖ్యమంత్రి ప్రకటన"
TEXT = "ముఖ్యమంత్రి చంద్రబాబు నాయుడు నిన్న విలేకరుల సమావేశంలో తెలుగుదేశం పార్టీ గురించి మాట్లాడారు."


def reply(**fields) -> str:
    """A model reply: a valid empty answer updated with `fields`."""
    body = {
        "category": "politics",
        "people": [],
        "organisations": [],
        "event_date_expression": None,
        "event_date": None,
    }
    body.update(fields)
    return json.dumps(body, ensure_ascii=False)


def set_settings(monkeypatch, **update):
    """Replace the settings in `newsrag.config` and in `newsrag.stages.base` (version hashes)."""
    import newsrag.stages.base as base_mod

    new = config.settings.model_copy(update=update)
    monkeypatch.setattr(config, "settings", new)
    monkeypatch.setattr(base_mod, "settings", new)
    return new


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    set_settings(monkeypatch, enrich_retry_backoff_s=(0.0, 0.0, 0.0))


class FakeClient:
    """`chat(messages, **kw)` answers with `answer(user_text, call_index)`; records every call."""

    def __init__(self, answer, delay: float = 0.0):
        self.answer = answer
        self.delay = delay
        self.calls: list[dict] = []
        self.warmups: list[list[dict]] = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def chat(self, messages, images=None, **kw):
        if kw.get("max_tokens") == 1:  # the cache warm-up of the stage, not a story
            self.warmups.append(messages)
            return ""
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            index = len(self.calls)
            self.calls.append({"messages": messages, **kw})
        try:
            if self.delay:
                time.sleep(self.delay)
            return self.answer(messages[-1]["content"], index)
        finally:
            with self.lock:
                self.active -= 1


def one(answer, headline=HEADLINE, text=TEXT, print_date=DAY):
    """Enrich one story with a fake client; returns (outcome, client)."""
    client = FakeClient(answer)
    outcome = core.enrich_story(client, core.build_prompt(print_date), headline, text, print_date)
    return outcome, client


def parsed_of(**fields) -> v.Parsed:
    outcome, _ = one(lambda _t, _i: reply(**fields))
    assert outcome.error is None, outcome.error
    return outcome.parsed


# --- validation (FR-3) ---------------------------------------------------------------------------


def test_non_verbatim_name_dropped():
    p = parsed_of(
        people=[
            {"name": "చంద్రబాబు నాయుడు", "role": "ముఖ్యమంత్రి"},
            {"name": "జగన్ మోహన్ రెడ్డి", "role": None},  # not in the text
            {"name": "Chandrababu Naidu", "role": None},  # a translation
        ],
        organisations=[{"name": "తెలుగుదేశం పార్టీ", "type": "party"}, {"name": "వైసీపీ", "type": "party"}],
    )
    assert [x["name"] for x in p.people] == ["చంద్రబాబు నాయుడు"]
    assert [x["name"] for x in p.organisations] == ["తెలుగుదేశం పార్టీ"]
    assert p.dropped_names == 3


def test_names_match_after_nfc_and_whitespace_normalisation():
    decomposed = unicodedata.normalize("NFD", "café")
    text = "The  café\n opened   today"  # two spaces and a line break inside
    p = parsed_of_text(text, people=[{"name": decomposed, "role": None}])
    assert [x["name"] for x in p.people] == ["café"]
    p = parsed_of_text(text, people=[{"name": "café opened", "role": None}])
    assert [x["name"] for x in p.people] == ["café opened"]
    assert p.dropped_names == 0


def parsed_of_text(text, **fields) -> v.Parsed:
    outcome, _ = one(lambda _t, _i: reply(**fields), headline="", text=text)
    return outcome.parsed


def test_name_only_in_truncated_part_is_dropped(monkeypatch):
    set_settings(monkeypatch, enrich_input_chars=50)
    text = "x" * 100 + " చంద్రబాబు"
    p = parsed_of_text(text, people=[{"name": "చంద్రబాబు", "role": None}])
    assert p.people == [] and p.dropped_names == 1


def test_names_deduplicated():
    p = parsed_of(
        people=[
            {"name": "చంద్రబాబు", "role": None},
            {"name": " చంద్రబాబు ", "role": "ముఖ్యమంత్రి"},
            {"name": "నాయుడు", "role": None},
        ],
        organisations=[
            {"name": "తెలుగుదేశం", "type": "party"},
            {"name": "తెలుగుదేశం", "type": "company"},
        ],
    )
    assert [x["name"] for x in p.people] == ["చంద్రబాబు", "నాయుడు"]
    assert [x["name"] for x in p.organisations] == ["తెలుగుదేశం"]
    assert p.dropped_names == 0  # duplicates are merged, not dropped


def test_org_type_outside_list_becomes_other():
    p = parsed_of(organisations=[{"name": "తెలుగుదేశం", "type": "political outfit"}])
    assert p.organisations == [{"name": "తెలుగుదేశం", "type": "other"}]


def test_category_must_be_in_list():
    for bad in ("crime", "", None, 3, ["sports"]):
        outcome, client = one(lambda _t, _i, bad=bad: reply(category=bad))
        assert outcome.parsed.category == "other"
        assert v.FAILED_FLAG in outcome.parsed.flags
        assert len(client.calls) == 3  # the first try and two retries
    for good in v.CATEGORIES:
        assert parsed_of(category=good).category == good


def test_people_and_organisations_must_be_lists():
    for key in ("people", "organisations"):
        outcome, _ = one(lambda _t, _i, key=key: reply(**{key: {"name": "x"}}))
        assert v.FAILED_FLAG in outcome.parsed.flags


def test_invalid_json_falls_back_to_other():
    outcome, client = one(lambda _t, _i: "this is not json")
    p = outcome.parsed
    assert p.category == "other" and p.people == [] and p.organisations == []
    assert p.flags == ["low_confidence:enrich_failed"]
    assert p.event_date == "2026-04-03" and p.event_date_basis == "print_date_fallback"
    assert len(client.calls) == 3
    # every try is constrained to the reply schema; the retries allow shorter lists
    limits = [
        c["response_format"]["json_schema"]["schema"]["properties"]["people"]["maxItems"]
        for c in client.calls
    ]
    assert limits == [6, 4, 2]
    schema = core.reply_schema(6)
    assert schema["properties"]["category"]["enum"] == list(v.CATEGORIES)
    assert client.calls[0]["response_format"]["json_schema"]["schema"] == schema


def test_retry_asks_for_shorter_lists():
    outcome, client = one(lambda _t, _i: '{"category": "politics", "people": [{"name": "చంద్ర')
    assert v.FAILED_FLAG in outcome.parsed.flags
    systems = [c["messages"][0]["content"] for c in client.calls]
    assert "Your previous reply" not in systems[0]
    assert "at most 4 people and 4 organisations" in systems[1]
    assert "at most 2 people and 2 organisations" in systems[2]
    assert systems[1].startswith(systems[0])  # the prompt prefix stays the same


def test_good_reply_after_a_bad_one():
    answers = ["oops", reply(category="sports")]
    outcome, client = one(lambda _t, i: answers[i])
    assert outcome.parsed.category == "sports" and outcome.parsed.flags == []
    assert len(client.calls) == 2


def test_fenced_json_is_accepted():
    outcome, _ = one(lambda _t, _i: "```json\n" + reply(category="health") + "\n```")
    assert outcome.parsed.category == "health"


def test_default_event_date_is_print_date():
    p = parsed_of()
    assert (p.event_date, p.event_date_basis, p.flags) == ("2026-04-03", "print_date", [])
    # an expression the model could not resolve
    p = parsed_of(event_date_expression="ఇటీవల")
    assert (p.event_date, p.event_date_basis, p.flags) == ("2026-04-03", "print_date_fallback", [])


def test_model_event_date_kept():
    p = parsed_of(event_date_expression="నిన్న", event_date="2026-04-02")
    assert (p.event_date, p.event_date_basis) == ("2026-04-02", "model")
    assert p.event_date_expression == "నిన్న" and p.flags == []
    assert parsed_of(event_date="2023-04-03").event_date_basis == "model"  # exactly 3 years back


def test_future_date_within_366_kept():
    p = parsed_of(event_date_expression="వచ్చే నెల", event_date="2026-05-03")
    assert (p.event_date, p.event_date_basis, p.flags) == ("2026-05-03", "model_future", [])
    last = date.fromordinal(DAY.toordinal() + 366).isoformat()
    assert parsed_of(event_date=last).event_date == last
    assert parsed_of(event_date="2026-04-03").event_date_basis == "model"  # the print date itself


def test_future_event_date_rejected():
    too_late = date.fromordinal(DAY.toordinal() + 367).isoformat()
    p = parsed_of(event_date_expression="x", event_date=too_late)
    assert p.event_date == "2026-04-03" and p.event_date_basis == "print_date_fallback"
    assert p.flags == ["event_date_rejected"]
    assert v.FAILED_FLAG not in p.flags


def test_too_old_and_invalid_event_dates_rejected():
    for bad in ("2023-04-02", "2026-02-30", "03-04-2026", "yesterday", 20260402, "2026-4-2"):
        p = parsed_of(event_date=bad)
        assert p.flags == ["event_date_rejected"], bad
        assert (p.event_date, p.event_date_basis) == ("2026-04-03", "print_date_fallback"), bad


def test_event_date_never_null():
    cases = [
        {},
        {"event_date": None},
        {"event_date": ""},
        {"event_date": "garbage"},
        {"event_date_expression": "నిన్న"},
        {"event_date": "1999-01-01"},
        {"event_date": "2030-01-01"},
        {"event_date": 5},
        {"event_date_expression": 7},
    ]
    for fields in cases:
        p = parsed_of(**fields)
        assert p.event_date is not None, fields
        date.fromisoformat(p.event_date)
        assert p.event_date_basis in {"model", "model_future", "print_date", "print_date_fallback"}
    # and the fallback for a story the model could not enrich
    assert v.fallback(DAY).event_date == "2026-04-03"
    assert core.enrich_story(FakeClient(lambda *_: ""), "p", "", "", DAY).parsed.event_date


def test_leap_day_print_date():
    p = parsed_of_date(date(2024, 2, 29), event_date="2021-03-01")
    assert p.event_date_basis == "model"
    assert parsed_of_date(date(2024, 2, 29), event_date="2021-02-27").flags == [
        "event_date_rejected"
    ]


def parsed_of_date(print_date, **fields) -> v.Parsed:
    outcome, _ = one(lambda _t, _i: reply(**fields), print_date=print_date)
    return outcome.parsed


# --- the request (FR-1, FR-2) --------------------------------------------------------------------


def test_request_shape():
    _, client = one(lambda _t, _i: reply())
    call = client.calls[0]
    system, user = call["messages"]
    assert system["role"] == "system" and user == {
        "role": "user",
        "content": HEADLINE + "\n" + TEXT,
    }
    assert (call["temperature"], call["max_tokens"], call["timeout"]) == (0, 1024, 120.0)
    assert "2026-04-03" in system["content"] and "<<" not in system["content"]


def test_input_truncated_to_6000_chars():
    _, client = one(lambda _t, _i: reply(), headline="h", text="ఆ" * 9000)
    user = client.calls[0]["messages"][-1]["content"]
    assert len(user) == 6000 and user.startswith("h\n")


def test_prompt_explains_relative_dates():
    prompt = core.build_prompt(DAY)
    assert "నిన్న (yesterday) = 2026-04-02" in prompt
    assert "రేపు (tomorrow) = 2026-04-04" in prompt
    assert "మంగళవారం = 2026-03-31" in prompt  # the most recent Tuesday
    assert "politics" in core.category_rules() and "obituaries" in core.category_rules()


def test_empty_story_needs_no_request():
    outcome, client = one(lambda _t, _i: reply(), headline=" ", text="")
    assert client.calls == []
    assert outcome.parsed.category == "other" and outcome.parsed.flags == ["empty_story"]


def test_transport_failure_flags_story():
    import httpx
    import openai

    request = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions")

    def boom(_t, _i):
        raise openai.APIConnectionError(request=request)

    outcome, client = one(boom)
    assert outcome.parsed.flags == ["low_confidence:enrich_failed"]
    assert outcome.parsed.category == "other" and outcome.parsed.event_date == "2026-04-03"
    assert len(client.calls) == 4  # the first try and three retries

    # a 5xx, a timeout and a plain connection error are retried; a success ends it
    for exc in (
        openai.InternalServerError(
            "boom", response=httpx.Response(503, request=request), body=None
        ),
        openai.APITimeoutError(request=request),
        ConnectionResetError("reset"),
        TimeoutError("slow"),
    ):

        def flaky(_t, i, exc=exc):
            if i < 2:
                raise exc
            return reply(category="health")

        outcome, client = one(flaky)
        assert outcome.parsed.category == "health" and outcome.parsed.flags == []
        assert len(client.calls) == 3


def test_other_errors_are_not_retried():
    import httpx
    import openai

    request = httpx.Request("POST", "http://x")

    def bad_request(_t, _i):
        raise openai.BadRequestError(
            "too long", response=httpx.Response(400, request=request), body=None
        )

    outcome, client = one(bad_request)
    assert outcome.parsed.flags == ["low_confidence:enrich_failed"]
    assert len(client.calls) == 1


# --- the stage (FR-1, FR-4, FR-7) ----------------------------------------------------------------


def stories_of(n: int) -> list[dict]:
    return [
        {"story_id": f"s_{i:03d}", "headline": f"శీర్షిక {i}", "text": TEXT + f" {i}"}
        for i in range(n)
    ]


def stage_for(monkeypatch, tmp_path, stories, client) -> EnrichCoreStage:
    """A stage with an open fake client whose dedup artifact holds `stories`."""
    from newsrag.dedup import load

    set_settings(monkeypatch, work_dir=tmp_path)
    art = tmp_path / "dedup.json"
    art.write_text(json.dumps({"pub_date": "2026-04-03", "stories": stories}), encoding="utf-8")
    monkeypatch.setattr(load, "done_dedup", lambda d=None: {"2026-04-03": art})
    stage = EnrichCoreStage()
    stage._client = client
    return stage


PAGES: list[PageRow] = []


def test_pool_inside_handle(monkeypatch, tmp_path, capsys):
    client = FakeClient(
        lambda text, _i: reply(
            category="sports" if "శీర్షిక 1\n" in text else "politics",
            people=[{"name": "చంద్రబాబు", "role": None}],
        ),
        delay=0.02,
    )
    stage = stage_for(monkeypatch, tmp_path, stories_of(100), client)
    assert stage.concurrency == 1  # one date at a time; the pool is inside handle
    result = stage.handle(DAY, PAGES)
    assert 1 < client.max_active <= config.settings.enrich_concurrency
    assert len(client.calls) == 100
    art = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert list(art) == ["pub_date", "stories"] and art["pub_date"] == "2026-04-03"
    assert list(art["stories"]) == [f"s_{i:03d}" for i in range(100)]  # in dedup order
    assert set(art["stories"]["s_001"]) == {
        "category", "people", "organisations", "event_date", "event_date_basis",
        "event_date_expression", "flags",
    }  # fmt: skip
    assert art["stories"]["s_001"]["category"] == "sports"
    assert art["stories"]["s_002"]["people"] == [{"name": "చంద్రబాబు", "role": None}]
    d = result.detail
    assert d["stories"] == 100 and d["enrich_failed"] == 0 and d["request_seconds"] > 0
    assert d["categories"]["sports"] == 1 and d["categories"]["politics"] == 99
    assert d["stories_per_s"] == pytest.approx(100 / d["request_seconds"], rel=0.05)
    assert "enrich_core 2026-04-03: 100 stories" in capsys.readouterr().err
    # the artifact path carries the stage version
    assert stage.version.replace(".", "_") in result.artifact_path


def test_system_prompt_warmed_up_once_before_the_pool(monkeypatch, tmp_path):
    order = []

    class Client(FakeClient):
        def chat(self, messages, images=None, **kw):
            order.append("warm" if kw.get("max_tokens") == 1 else "story")
            return super().chat(messages, images, **kw)

    client = Client(lambda *_: reply())
    stage = stage_for(monkeypatch, tmp_path, stories_of(5), client)
    stage.handle(DAY, PAGES)
    assert order == ["warm"] + ["story"] * 5
    assert client.warmups[0][0] == {"role": "system", "content": core.build_prompt(DAY)}


def test_warm_up_errors_are_ignored():
    class Down:
        def chat(self, *_a, **_kw):
            raise ConnectionError("down")

    core.warm_up(Down(), "prompt")  # does not raise


def test_concurrency_setting_caps_the_pool(monkeypatch, tmp_path):
    set_settings(monkeypatch, enrich_concurrency=3)
    client = FakeClient(lambda *_: reply(), delay=0.02)
    stage = stage_for(monkeypatch, tmp_path, stories_of(20), client)
    stage.handle(DAY, PAGES)
    assert client.max_active == 3


def test_date_fails_over_20_percent(monkeypatch, tmp_path):
    def answer_for(bad: int):
        return lambda text, _i: (
            "nope" if any(f"శీర్షిక {k}\n" in text for k in range(bad)) else reply()
        )

    # 2 of 10 is exactly 20%: the date is done, the two stories carry the flag
    stage = stage_for(monkeypatch, tmp_path, stories_of(10), FakeClient(answer_for(2)))
    result = stage.handle(DAY, PAGES)
    art = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert result.detail["enrich_failed"] == 2
    assert art["stories"]["s_000"]["flags"] == ["low_confidence:enrich_failed"]
    assert art["stories"]["s_005"]["flags"] == []
    # 3 of 10 is over 20%: the date fails, so it is retried as a whole
    stage = stage_for(monkeypatch, tmp_path, stories_of(10), FakeClient(answer_for(3)))
    with pytest.raises(RuntimeError, match="3 of 10 stories could not be enriched"):
        stage.handle(DAY, PAGES)


def test_fail_rule_counts_all(monkeypatch, tmp_path):
    """Transport failures and invalid output both count towards the 20%."""

    def answer(text, _i):
        if "శీర్షిక 0\n" in text:
            raise ConnectionError("down")  # transport failure
        if "శీర్షిక 1\n" in text or "శీర్షిక 2\n" in text:
            return "{not json"  # invalid output
        return reply()

    stage = stage_for(monkeypatch, tmp_path, stories_of(10), FakeClient(answer))
    with pytest.raises(RuntimeError, match="3 of 10"):  # 1 + 2: neither kind alone is over 20%
        stage.handle(DAY, PAGES)
    # one of each in ten is fine
    stage = stage_for(monkeypatch, tmp_path, stories_of(10), FakeClient(
        lambda text, i: answer(text, i) if "శీర్షిక 2\n" not in text else reply()
    ))  # fmt: skip
    assert stage.handle(DAY, PAGES).detail["enrich_failed"] == 2


def test_model_down_fails_the_date(monkeypatch, tmp_path):
    def down(*_):
        raise ConnectionError("down")

    stage = stage_for(monkeypatch, tmp_path, stories_of(5), FakeClient(down))
    with pytest.raises(RuntimeError, match="5 of 5"):
        stage.handle(DAY, PAGES)


def test_handle_needs_open_model_and_dedup(monkeypatch, tmp_path):
    stage = EnrichCoreStage()
    with pytest.raises(RuntimeError, match="not open"):
        stage.handle(DAY, PAGES)
    stage = stage_for(monkeypatch, tmp_path, stories_of(1), FakeClient(lambda *_: reply()))
    from newsrag.dedup import load

    monkeypatch.setattr(load, "done_dedup", lambda d=None: {})
    with pytest.raises(FileNotFoundError, match="no done dedup artifact"):
        stage.handle(DAY, PAGES)


def test_open_uses_the_model_once(monkeypatch):
    from newsrag import models

    events = []

    @contextlib.contextmanager
    def fake_use(name, device="cuda", report=None):
        events.append(("open", name))
        try:
            yield FakeClient(lambda *_: reply())
        finally:
            events.append(("close", name))

    monkeypatch.setattr(models, "use", fake_use)
    stage = EnrichCoreStage()
    assert stage.models_needed == ("qwen3-30b-a3b",)
    stage.open()
    assert stage._client is not None and events == [("open", "qwen3-30b-a3b")]
    stage.close()
    assert stage._client is None and events[-1] == ("close", "qwen3-30b-a3b")


def test_version_and_registration(monkeypatch):
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["enrich_core"]
    assert stage.name == "enrich_core" and stage.depends_on == ("dedup",)
    v0 = stage.version
    assert v0.startswith("qwen3-30b-a3b-core-p1-")
    base = config.settings
    for update in (
        {"enrich_model": "gemma3-27b"},
        {"enrich_input_chars": 3000},
        {"enrich_event_future_days": 100},
    ):
        set_settings(monkeypatch, **update)
        assert EnrichCoreStage().version != v0
        set_settings(monkeypatch, **{k: getattr(base, k) for k in update})
    set_settings(monkeypatch, enrich_concurrency=2)  # speed only: the output does not change
    assert EnrichCoreStage().version == v0


def test_preflight(monkeypatch):
    from newsrag.stages.base import StageUnavailable

    EnrichCoreStage().preflight()
    for model in ("no-such-model", "bge-m3"):
        set_settings(monkeypatch, enrich_model=model)
        with pytest.raises(StageUnavailable):
            EnrichCoreStage().preflight()


# --- load (FR-6, FR-7) ---------------------------------------------------------------------------


CORE = {
    "pub_date": "2026-04-03",
    "stories": {
        "s_a": {
            "category": "sports",
            "event_date": "2026-04-02",
            "flags": ["low_confidence:enrich_failed"],
        },
        "s_b": {"category": "other", "event_date": "2026-04-03", "flags": []},
    },
}
PLACES = {
    "pub_date": "2026-04-03",
    "stories": {
        "s_a": {"places": ["గుంటూరు"], "flags": ["place_unresolved", "event_date_rejected"]}
    },
}
FACTS = {
    "pub_date": "2026-04-03",
    "stories": {"s_a": {"amounts": [1], "flags": ["event_date_rejected", "amount_odd"]}},
}


def test_load_merges_partial():
    # only enrich_core exists
    assert enrich.merge({"enrich_core": CORE}) == {
        "s_a": {
            "category": "sports",
            "event_date": "2026-04-02",
            "flags": ["low_confidence:enrich_failed"],
        },
        "s_b": {"category": "other", "event_date": "2026-04-03", "flags": []},
    }
    assert enrich.merge({}) == {}
    # core plus one later stage, which only knows some of the stories
    merged = enrich.merge({"enrich_core": CORE, "enrich_places": PLACES})
    assert merged["s_a"]["places"] == ["గుంటూరు"] and merged["s_a"]["category"] == "sports"
    assert "places" not in merged["s_b"]
    # a stage that sees a story the core stage does not
    assert "s_z" in enrich.merge(
        {"enrich_core": CORE, "enrich_facts": {"stories": {"s_z": {"flags": []}}}}
    )


def test_load_reads_the_done_artifacts(monkeypatch, tmp_path):
    paths = {}
    for stage, art in (("enrich_core", CORE), ("enrich_facts", FACTS)):
        paths[stage] = tmp_path / f"{stage}.json"
        paths[stage].write_text(json.dumps(art), encoding="utf-8")
    asked = []

    def done(stage, pub_date=None):
        asked.append((stage, pub_date))
        return {"2026-04-03": paths[stage]} if stage in paths else {}

    monkeypatch.setattr(enrich, "done_artifacts", done)
    for arg in (DAY, "2026-04-03"):
        merged = enrich.load(arg)
        assert set(merged) == {"s_a", "s_b"} and merged["s_a"]["amounts"] == [1]
    assert [a[0] for a in asked[:3]] == ["enrich_core", "enrich_places", "enrich_facts"]
    monkeypatch.setattr(enrich, "done_artifacts", lambda stage, pub_date=None: {})
    assert enrich.load(DAY) == {}  # nothing enriched yet


def test_load_flags_union():
    merged = enrich.merge({"enrich_facts": FACTS, "enrich_core": CORE, "enrich_places": PLACES})
    # core, then places, then facts; each flag once
    assert merged["s_a"]["flags"] == [
        "low_confidence:enrich_failed",
        "place_unresolved",
        "event_date_rejected",
        "amount_odd",
    ]
    assert merged["s_b"]["flags"] == []


def test_load_key_clash_raises():
    clash = {"stories": {"s_a": {"category": "health", "flags": []}}}
    with pytest.raises(ValueError, match="'category'.*enrich_core.*enrich_places"):
        enrich.merge({"enrich_core": CORE, "enrich_places": clash})


# --- stats (FR-5, FR-7) --------------------------------------------------------------------------


def art(pub_date, *records):
    return {"pub_date": pub_date, "stories": {f"s_{i}": r for i, r in enumerate(records)}}


def rec(category="other", people=(), orgs=(), event_date="2026-04-03", flags=()):
    return {
        "category": category,
        "people": [{"name": p, "role": None} for p in people],
        "organisations": [{"name": o, "type": "other"} for o in orgs],
        "event_date": event_date,
        "flags": list(flags),
    }


def test_stats_multi_date():
    a1 = art("2026-04-03", rec("sports", ["a", "b"]), rec("politics", ["c"], ["o"], "2026-04-02"))
    a2 = art(
        "2025-06-10",
        rec("other", flags=["low_confidence:enrich_failed"], event_date="2025-06-10"),
        rec("health", ["d", "e", "f"], ["p", "q"], "2025-06-09"),
    )
    d1 = {"stories": 10, "request_seconds": 1.0, "dropped_names": 2}
    d2 = {"stories": 30, "request_seconds": 10.0, "dropped_names": 1}
    out = enrich_cli.totals([a1, a2], [d1, d2])
    assert list(out)[:7] == [f"category_{c}" for c in v.CATEGORIES]
    assert (
        out["category_sports"] == 1 and out["category_other"] == 1 and out["category_cinema"] == 0
    )
    assert out["people_per_story"] == 1.5 and out["orgs_per_story"] == 0.75
    assert out["dropped_names"] == 3
    assert out["event_date_differs"] == 2 and out["enrich_failed"] == 1
    # total stories over total request seconds, not the mean of the two rates (10 and 3)
    assert out["stories_per_s"] == round(40 / 11, 2)
    # a single date
    assert enrich_cli.totals([a1], [d1])["stories_per_s"] == 10.0
    assert enrich_cli.totals([], [])["stories_per_s"] == 0.0


def test_stats_command_repeatable_date(monkeypatch):
    seen = []

    def runs(days):
        seen.append(days)
        return [art("2026-04-03", rec("sports"))], [{"stories": 1, "request_seconds": 0.5}]

    monkeypatch.setattr(enrich_cli, "_done_runs", runs)
    result = CliRunner().invoke(
        enrich_cli.app, ["stats", "--date", "2026-04-03", "--date", "2025-06-10"]
    )
    assert result.exit_code == 0, result.output
    assert seen == [["2026-04-03", "2025-06-10"]]
    lines = result.output.splitlines()
    assert lines[:2] == ["category_sports=1", "category_politics=0"]
    assert [ln.split("=")[0] for ln in lines] == [
        *(f"category_{c}" for c in v.CATEGORIES),
        "people_per_story", "orgs_per_story", "dropped_names", "event_date_differs",
        "enrich_failed", "stories_per_s",
    ]  # fmt: skip
    assert lines[-1] == "stories_per_s=2.0"
    monkeypatch.setattr(enrich_cli, "_done_runs", lambda days: ([], []))
    assert CliRunner().invoke(enrich_cli.app, ["stats"]).exit_code == 1


# --- verify-core (NFR-1) -------------------------------------------------------------------------


def story(i: int, **kw) -> dict:
    return {
        "pub_date": "2026-04-03", "story_id": f"s_{i:03d}", "headline": "శీర్షిక",
        "text": TEXT, "category": "politics",
        "people": [{"name": "చంద్రబాబు", "role": None}], "organisations": [], **kw,
    }  # fmt: skip


def test_judge_prompt_uses_the_same_category_rules_and_input():
    text = judge_text = vf.judge_prompt(story(1, text="ఆ" * 9000))
    assert core.category_rules() in judge_text
    assert "politics" in text and "చంద్రబాబు" in text and "(none)" in text  # no organisations
    assert "<<" not in text
    assert ("శీర్షిక\n" + "ఆ" * 9000)[:6000] in text and "ఆ" * 6001 not in text


def test_parse_verdict():
    assert vf.parse_verdict('{"category_ok": true, "names_ok": false}') == (True, False)
    assert vf.parse_verdict('```json\n{"category_ok": true, "names_ok": true}\n```') == (True, True)
    for bad in ("yes", "", "{", "[]", '{"category_ok": "true"}', '{"names_ok": 1}'):
        assert vf.parse_verdict(bad) == (False, False)


def test_verify_report_and_exit_code(monkeypatch, tmp_path):
    stories = [story(i) for i in range(80)]
    set_settings(monkeypatch, enrich_verify_file=tmp_path / "report.json")
    monkeypatch.setattr(vf, "collect_stories", lambda day=None: stories)
    prompts = []

    def run(category_bad: int, names_bad: int, unparseable: int = 0):
        def ask(prompt):
            prompts.append(prompt)
            n = len(prompts)
            if n <= unparseable:
                return "I think so"
            return json.dumps({"category_ok": n > category_bad, "names_ok": n > names_bad})

        prompts.clear()
        monkeypatch.setattr(
            vf, "run_verify",
            lambda s, n, seed, ask_=None, _orig=vf.run_verify: _orig(s, n, seed, ask),
        )  # fmt: skip
        return CliRunner().invoke(enrich_cli.app, ["verify-core", "--n", "50", "--seed", "11"])

    original = vf.run_verify
    result = run(5, 5)  # 45 and 45 of 50: just enough
    assert result.exit_code == 0, result.output
    assert "category_ok=45/50 names_ok=45/50" in result.output
    assert len(prompts) == 50
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["n"] == 50 and report["seed"] == 11 and report["model"] == "gemma3-27b"
    monkeypatch.setattr(vf, "run_verify", original)
    assert run(6, 0).exit_code == 1  # 44/50 categories
    monkeypatch.setattr(vf, "run_verify", original)
    assert run(0, 6).exit_code == 1  # 44/50 names
    monkeypatch.setattr(vf, "run_verify", original)
    result = run(0, 0, unparseable=10)  # an unparseable reply is "not ok"
    assert result.exit_code == 1 and "category_ok=40/50 names_ok=40/50" in result.output


def test_sample_is_seeded():
    stories = [story(i) for i in range(100)]
    a = vf.sample(stories, 50, 11)
    assert a == vf.sample(stories, 50, 11) and a != vf.sample(stories, 50, 12)
    assert len(vf.sample(stories[:10], 50, 11)) == 10


# --- live (E-4) ----------------------------------------------------------------------------------


@pytest.mark.gpu
def test_yesterday_live():
    from newsrag import models

    text = (
        "హైదరాబాద్: నిన్న విలేకరుల సమావేశంలో ఆరోగ్య శాఖ మంత్రి దామోదర రాజనర్సింహ మాట్లాడుతూ "
        "రాష్ట్రంలోని ప్రభుత్వ ఆసుపత్రుల్లో కొత్త వైద్యులను నియమిస్తామని చెప్పారు."
    )
    with models.use(config.settings.enrich_model) as client:
        outcome = core.enrich_story(client, core.build_prompt(DAY), "వైద్యుల నియామకం", text, DAY)
    p = outcome.parsed
    assert outcome.error is None and v.FAILED_FLAG not in p.flags
    assert p.event_date == "2026-04-02" and p.event_date_basis == "model"
    assert p.category in ("health", "politics")
