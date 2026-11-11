"""Ticket 020: `enrich_places` (a fake text model, the real committed `data/geo` files)."""

from __future__ import annotations

import json
import threading
import unicodedata
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsrag import config, enrich
from newsrag.enrich import cli as enrich_cli
from newsrag.enrich import core, places
from newsrag.enrich import validate as v
from newsrag.stages.enrich_places import EnrichPlacesStage

DAY = date(2025, 6, 10)
DAY_2020 = date(2020, 4, 2)
FAILED = "low_confidence:enrich_failed"


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


def reply(*pairs) -> str:
    """A model reply listing (name, type) places."""
    return json.dumps({"places": [{"name": n, "type": t} for n, t in pairs]}, ensure_ascii=False)


class FakeClient:
    """`chat(messages, **kw)` answers with `answer(user_text, call_index)`; records every call."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []
        self.warmups: list[list[dict]] = []
        self.lock = threading.Lock()

    def chat(self, messages, images=None, **kw):
        if kw.get("max_tokens") == 1:  # the cache warm-up of the stage, not a story
            self.warmups.append(messages)
            return ""
        with self.lock:
            index = len(self.calls)
            self.calls.append({"messages": messages, **kw})
        return self.answer(messages[-1]["content"], index)


def story_of(pairs, text: str, print_date: date = DAY) -> dict:
    """Enrich one story whose fake reply lists `pairs`; the story record of the artifact."""
    client = FakeClient(lambda _t, _i: reply(*pairs))
    records, _summary = places.enrich_places(
        client, [{"story_id": "s1", "headline": "శీర్షిక", "text": text}], print_date
    )
    return records["s1"]


def place_of(name: str, llm_type: str, text: str | None = None, print_date: date = DAY) -> dict:
    """The single place record of a story that names `name` once."""
    rec = story_of([(name, llm_type)], text or f"వార్త {name} గురించి", print_date)
    assert len(rec["places"]) == 1, rec
    return rec["places"][0]


# --- FR-1: the model's places --------------------------------------------------------------------


def test_non_verbatim_places_dropped():
    text = "కర్నూలు జిల్లా ఆదోని మండలంలో వర్షం కురిసింది."
    rec = story_of(
        [
            ("కర్నూలు", "district"),
            ("Kurnool", "district"),  # a translation
            ("నంద్యాల", "district"),  # not in the text
            ("ఆదోని", "mandal"),
        ],
        text,
    )
    assert [p["name"] for p in rec["places"]] == ["కర్నూలు", "ఆదోని"]
    _, summary = places.enrich_places(
        FakeClient(lambda *_: reply(("Kurnool", "district"), ("కర్నూలు", "district"))),
        [{"story_id": "s1", "headline": "", "text": text}],
        DAY,
    )
    assert summary["dropped_places"] == 1


def test_names_match_after_nfc_and_whitespace_normalisation():
    decomposed = unicodedata.normalize("NFD", "Café")
    parsed, dropped, _ = places.parse_places(
        json.loads(reply((decomposed, "other"), ("New   York", "city"))),
        "The  Café\n opened in New York",
    )
    assert parsed == [("Café", "other"), ("New York", "city")] and dropped == 0


def test_place_only_in_the_truncated_part_is_dropped(monkeypatch):
    set_settings(monkeypatch, enrich_input_chars=10)
    rec = story_of([("విజయవాడ", "city")], "ఇది ఒక వార్త ఇంకా చాలా విజయవాడ")
    assert rec["places"] == []


def test_places_deduplicated_by_name_and_type():
    text = "గుంటూరు గుంటూరు విజయవాడ"
    rec = story_of(
        [
            ("గుంటూరు", "city"),
            ("గుంటూరు ", "city"),  # same after whitespace normalisation
            ("గుంటూరు", "district"),  # another type: kept
            ("విజయవాడ", "city"),
        ],
        text,
    )
    assert [(p["name"], p["llm_type"]) for p in rec["places"]] == [
        ("గుంటూరు", "city"),
        ("గుంటూరు", "district"),
        ("విజయవాడ", "city"),
    ]


def test_type_outside_the_list_becomes_other():
    p = place_of("కొండ", "mountain")
    assert p["llm_type"] == "other" and p["flags"] == []


def test_reply_must_hold_a_list_of_places():
    for bad in ('{"places": "x"}', "[]", '{"other": []}'):
        client = FakeClient(lambda _t, _i, bad=bad: bad)
        outcome = places.enrich_story(client, "p", "h", "t")
        assert outcome.failed and len(client.calls) == config.settings.enrich_format_retries + 1


def test_request_shape():
    client = FakeClient(lambda *_: reply())
    places.enrich_places(client, [{"story_id": "s1", "headline": "H", "text": "T"}], DAY)
    (call,) = client.calls
    system, user = call["messages"]
    assert system["role"] == "system" and "<<" not in system["content"]
    assert "ZWNJ" not in system["content"] and "16 places" in system["content"]
    assert user["content"] == "H\nT"
    assert call["temperature"] == 0
    assert call["max_tokens"] == config.settings.enrich_max_tokens
    assert call["timeout"] == config.settings.enrich_request_timeout_s
    schema = call["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["places"]["maxItems"] == 16
    assert schema["properties"]["places"]["items"]["properties"]["type"]["enum"] == list(
        places.PLACE_TYPES
    )
    assert client.warmups, "the system prompt is warmed up"


def test_retry_asks_for_fewer_places_and_good_reply_after_a_bad_one():
    answers = ["not json", reply(("గుంటూరు", "city"))]
    client = FakeClient(lambda _t, i: answers[i])
    outcome = places.enrich_story(client, places.build_prompt(), "", "గుంటూరు")
    assert outcome.places == [("గుంటూరు", "city")] and not outcome.failed
    assert len(client.calls) == 2
    second = client.calls[1]
    assert "at most 8 places" in second["messages"][0]["content"]
    assert (
        second["response_format"]["json_schema"]["schema"]["properties"]["places"]["maxItems"] == 8
    )


def test_list_at_the_cap_is_flagged(monkeypatch):
    set_settings(monkeypatch, enrich_max_places=2)
    rec = story_of([("గుంటూరు", "city"), ("విజయవాడ", "city")], "గుంటూరు విజయవాడ")
    assert v.CAPPED_FLAG in rec["flags"]


def test_empty_story_needs_no_request():
    client = FakeClient(lambda *_: reply())
    records, _ = places.enrich_places(client, [{"story_id": "s1", "headline": "", "text": ""}], DAY)
    assert records["s1"]["flags"] == ["empty_story"] and not client.calls


def test_failed_story_has_no_places_and_the_failure_flag():
    def boom(_t, _i):
        raise ConnectionError("down")

    records, summary = places.enrich_places(
        FakeClient(boom), [{"story_id": "s1", "headline": "h", "text": "t"}], DAY
    )
    assert records["s1"] == {
        "places": [],
        "districts_current": [],
        "districts_at_print": [],
        "states": [],
        "flags": [FAILED],
    }
    assert summary["enrich_failed"] == 1


# --- FR-2: resolution ----------------------------------------------------------------------------


def test_2020_story_gets_old_and_new_district():
    rec = story_of([("తిరుపతి", "city")], "తిరుపతి నగరంలో ఈ రోజు ఉత్సవం.", DAY_2020)
    (p,) = rec["places"]
    assert p["kind"] == "town" and p["state"] == "andhra_pradesh"
    assert p["districts_at_print"] == ["chittoor"]
    assert p["districts_current"] == ["tirupati"]
    assert rec["districts_at_print"] == ["chittoor"]
    assert rec["districts_current"] == ["tirupati"]
    assert rec["states"] == ["andhra_pradesh"] and rec["flags"] == []


def test_same_story_printed_in_2025_has_one_district():
    p = place_of("తిరుపతి", "city", print_date=DAY)
    assert p["districts_at_print"] == p["districts_current"] == ["tirupati"]


def test_ambiguous_flag():
    # చిత్తూరు is a district and a town; as a "mandal" neither fits and they disagree on districts
    rec = story_of([("చిత్తూరు", "mandal")], "చిత్తూరు వార్త")
    (p,) = rec["places"]
    assert p["flags"] == ["place_ambiguous"]
    assert p["kind"] is None and p["state"] is None
    assert p["districts_current"] == [] and p["districts_at_print"] == []
    assert {c["kind"] for c in p["candidates"]} == {"district", "town"}
    assert all(c["state"] == "andhra_pradesh" for c in p["candidates"])
    assert {"kind", "id", "name_en", "state", "districts_current", "districts_at_print"} <= set(
        p["candidates"][0]
    )
    # no district from an ambiguous place; a state when all candidates share one
    assert rec["districts_current"] == [] and rec["districts_at_print"] == []
    assert rec["states"] == ["andhra_pradesh"]
    assert rec["flags"] == ["place_ambiguous"]


def test_ambiguous_candidates_in_two_states_give_no_state(monkeypatch):
    def fake(name, on_date=None):
        return [
            places.geo.Place("town", s, "X", "ఎక్స్", s, [f"{s}_d"], [f"{s}_d"])
            for s in ("andhra_pradesh", "telangana")
        ]

    monkeypatch.setattr(places.geo, "resolve", fake)
    rec = story_of([("ఎక్స్", "city")], "ఎక్స్ వార్త")
    assert rec["places"][0]["flags"] == ["place_ambiguous"] and rec["states"] == []


def test_unresolved_kept():
    rec = story_of([("జనసేనపురం", "city")], "జనసేనపురం వార్త")
    (p,) = rec["places"]
    assert p["name"] == "జనసేనపురం" and p["llm_type"] == "city"
    assert p["kind"] is None and p["districts_current"] == [] and p["districts_at_print"] == []
    assert p["flags"] == ["place_unresolved"]
    assert rec["flags"] == ["place_unresolved"] and rec["states"] == []


def test_place_flags_are_informational_not_low_confidence():
    for name in ("జనసేనపురం", "చిత్తూరు"):
        rec = story_of([(name, "mandal")], f"{name} వార్త")
        assert not any(f.startswith("low_confidence:") for f in rec["flags"])


def test_case_suffix_stripped():
    p = place_of("తిరుపతిలో", "city")
    assert p["name"] == "తిరుపతిలో"  # the name stays as written
    assert p["kind"] == "town" and p["id"] == "tirupati"
    assert p["districts_current"] == ["tirupati"] and p["flags"] == []


@pytest.mark.parametrize(
    "suffix", ["లోని", "లో", "కు", "కి", "ను", "నుంచి", "నుండి", "పై", "తో", "వద్ద", "గా"]
)
def test_each_case_suffix_is_stripped(suffix):
    assert places.strip_case_suffix("గుంటూరు" + suffix) == "గుంటూరు"
    p = place_of("గుంటూరు" + suffix, "district")
    assert p["id"] == "guntur" and p["flags"] == []


def test_only_one_suffix_is_stripped():
    assert places.strip_case_suffix("గుంటూరులోకి") is None or (
        places.strip_case_suffix("గుంటూరులోకి") != "గుంటూరు"
    )
    assert places.strip_case_suffix("లో") is None  # nothing would be left
    assert places.strip_case_suffix("గుంటూరు") is None


def test_name_as_written_tried_first():
    # అరకు ends in the suffix కు; as written it is the alias of Araku Valley, stripped it is అర
    assert places.geo.resolve("అరకు")
    p = place_of("అరకు", "mandal")
    assert p["id"] == "araku_valley" and p["kind"] == "mandal" and p["flags"] == []
    assert p["districts_current"] == ["alluri_sitharama_raju"]


def test_zwnj_removed_after_strip():
    name = "హైదరాబాద్‌లో"  # హైదరాబాద్ + ZWNJ + లో
    assert places.strip_case_suffix(name) == "హైదరాబాద్"
    p = place_of(name, "city")
    assert p["id"] == "hyderabad" and p["kind"] == "town" and p["flags"] == []
    assert p["name"] == name


def test_kind_must_fit_the_llm_type():
    # కృష్ణ: a district and (in Telangana) a mandal; the type decides
    krishna = "Krishna"
    d = place_of(krishna, "district")
    m = place_of(krishna, "mandal")
    assert (d["kind"], d["flags"]) == ("district", [])
    assert (m["kind"], m["flags"]) == ("mandal", [])
    assert d["districts_current"] != m["districts_current"]


def test_matches_collapse_when_districts_agree():
    # గుంటూరు as "mandal": no mandal of that name, the district and the town remain and agree
    matches = places.geo.resolve("గుంటూరు", DAY)
    assert {m.kind for m in matches} == {"district", "town"}
    p = place_of("గుంటూరు", "mandal")
    assert p["flags"] == [] and "candidates" not in p
    assert p["kind"] == "district"  # the first of the agreeing matches
    assert p["districts_current"] == ["guntur"] and p["districts_at_print"] == ["guntur"]


def test_one_fitting_match_wins_over_the_others():
    # in 2020 the district Guntur spans three of today's districts, the town only one: type city picks the town
    p = place_of("గుంటూరు", "city")
    assert p["kind"] == "town" and p["districts_current"] == ["guntur"]
    d = place_of("గుంటూరు", "district", print_date=DAY_2020)
    assert d["kind"] == "district" and d["districts_at_print"] == ["guntur"]
    assert d["districts_current"] == ["bapatla", "guntur", "palnadu"]  # redrawn in 2022


def test_state_type_maps_state():
    expected = {
        "ఆంధ్రప్రదేశ్": "andhra_pradesh",
        "Andhra Pradesh": "andhra_pradesh",
        "తెలంగాణ": "telangana",
        "Telangana": "telangana",
        "కర్ణాటక": None,
    }
    for name, state in expected.items():
        p = place_of(name, "state")
        assert p["state"] == state and p["kind"] is None and p["flags"] == [], name
        assert p["districts_current"] == [] and p["districts_at_print"] == []
    rec = story_of([("తెలంగాణ", "state"), ("కర్ణాటక", "state")], "తెలంగాణ కర్ణాటక సరిహద్దు")
    assert rec["states"] == ["telangana"]  # a state outside the two adds nothing
    assert place_of("తెలంగాణలో", "state")["state"] == "telangana"


def test_village_not_resolved_no_flag():
    for kind in ("village", "constituency", "other"):
        p = place_of("గుంటూరు", kind)  # a name that would resolve as a city
        assert p["kind"] is None and p["state"] is None and p["flags"] == [], kind
        assert p["districts_current"] == [] and p["districts_at_print"] == []


# --- FR-3 / FR-4: story fields, artifact ---------------------------------------------------------


def test_story_fields_are_sorted_unique_over_non_ambiguous_places():
    text = "విజయవాడ గుంటూరు హైదరాబాద్ చిత్తూరు జనసేనపురం తెలంగాణ గుంటూరు"
    rec = story_of(
        [
            ("విజయవాడ", "city"),
            ("గుంటూరు", "city"),
            ("గుంటూరు", "district"),
            ("హైదరాబాద్", "city"),
            ("చిత్తూరు", "mandal"),  # ambiguous: no district
            ("జనసేనపురం", "city"),  # unresolved: no district
            ("తెలంగాణ", "state"),
        ],
        text,
        DAY_2020,
    )
    # printed in 2020: the old Krishna district held Vijayawada, the old Guntur is now three
    assert rec["districts_current"] == ["bapatla", "guntur", "hyderabad", "ntr", "palnadu"]
    assert rec["districts_at_print"] == ["guntur", "hyderabad", "krishna"]
    assert rec["states"] == ["andhra_pradesh", "telangana"]
    assert rec["flags"] == ["place_ambiguous", "place_unresolved"]
    assert list(rec) == ["places", "districts_current", "districts_at_print", "states", "flags"]
    assert set(rec["places"][0]) == {
        "name", "llm_type", "kind", "id", "state", "districts_current", "districts_at_print", "flags",
    }  # fmt: skip


# --- the stage -----------------------------------------------------------------------------------

TEXT = "కర్నూలు జిల్లా అరకులోయ మండలంలో వర్షం. హైదరాబాద్‌లో సమావేశం."


def stories_of(n: int) -> list[dict]:
    return [{"story_id": f"s_{i:03d}", "headline": f"శీర్షిక {i}", "text": TEXT} for i in range(n)]


def stage_for(monkeypatch, tmp_path, stories, client) -> EnrichPlacesStage:
    """A stage with an open fake client whose dedup artifact holds `stories`."""
    from newsrag.dedup import load

    set_settings(monkeypatch, work_dir=tmp_path)
    art = tmp_path / "dedup.json"
    art.write_text(json.dumps({"pub_date": "2025-06-10", "stories": stories}), encoding="utf-8")
    monkeypatch.setattr(load, "done_dedup", lambda d=None: {"2025-06-10": art})
    stage = EnrichPlacesStage()
    stage._client = client
    return stage


def test_stage_artifact_and_detail(monkeypatch, tmp_path, capsys):
    client = FakeClient(
        lambda *_: reply(("కర్నూలు జిల్లా", "district"), ("అరకులోయ", "mandal"), ("హైదరాబాద్", "city"))
    )
    stage = stage_for(monkeypatch, tmp_path, stories_of(20), client)
    assert stage.concurrency == 1
    result = stage.handle(DAY, [])
    assert len(client.calls) == 20
    art = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert list(art) == ["pub_date", "stories"] and art["pub_date"] == "2025-06-10"
    assert list(art["stories"]) == [f"s_{i:03d}" for i in range(20)]  # in dedup order
    rec = art["stories"]["s_000"]
    assert set(rec) == {"places", "districts_current", "districts_at_print", "states", "flags"}
    assert rec["districts_current"] == ["alluri_sitharama_raju", "hyderabad", "kurnool"]
    d = result.detail
    assert d["stories"] == 20 and d["places"] == 60 and d["enrich_failed"] == 0
    assert d["resolved"] == 60 and d["ambiguous"] == 0 and d["unresolved"] == 0
    assert d["place_types"] == {"district": 20, "mandal": 20, "city": 20}
    assert "enrich_places 2025-06-10: 20 stories" in capsys.readouterr().err
    assert stage.version.replace(".", "_") in result.artifact_path
    assert "enrich_places" in result.artifact_path


def test_date_fails_over_20_percent(monkeypatch, tmp_path):
    def answer(text, _i):
        if "శీర్షిక 0\n" in text or "శీర్షిక 1\n" in text or "శీర్షిక 2\n" in text:
            raise ConnectionError("down")
        return reply()

    stories = stories_of(10)  # 3 of 10 failed = 30%
    stage = stage_for(monkeypatch, tmp_path, stories, FakeClient(answer))
    with pytest.raises(RuntimeError, match="3 of 10 stories could not be enriched"):
        stage.handle(DAY, [])
    ok = stage_for(monkeypatch, tmp_path, stories[:3] + stories[3:], FakeClient(lambda *_: reply()))
    assert ok.handle(DAY, []).detail["enrich_failed"] == 0


def test_handle_needs_open_model_and_dedup(monkeypatch, tmp_path):
    stage = stage_for(monkeypatch, tmp_path, stories_of(1), None)
    with pytest.raises(RuntimeError, match="not open"):
        stage.handle(DAY, [])
    from newsrag.dedup import load

    stage._client = FakeClient(lambda *_: reply())
    monkeypatch.setattr(load, "done_dedup", lambda d=None: {})
    with pytest.raises(FileNotFoundError):
        stage.handle(DAY, [])


def test_version_and_registration(monkeypatch, tmp_path):
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["enrich_places"]
    assert stage.name == "enrich_places" and stage.depends_on == ("enrich_core",)
    assert stage.kind == "date"
    v0 = stage.version
    assert v0.startswith("qwen3-30b-a3b-places-p1-")
    assert v0 != REGISTRY["enrich_core"].version
    base = config.settings
    for update in (
        {"enrich_model": "gemma3-27b"},
        {"enrich_input_chars": 3000},
        {"enrich_max_places": 4},
    ):
        set_settings(monkeypatch, **update)
        assert EnrichPlacesStage().version != v0
        set_settings(monkeypatch, **{k: getattr(base, k) for k in update})
    set_settings(monkeypatch, enrich_concurrency=2)  # speed only: the output does not change
    assert EnrichPlacesStage().version == v0
    # an alias file change reruns the stage
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("- {alias: X, kind: town, id: guntur}\n", encoding="utf-8")
    set_settings(monkeypatch, geo_aliases_file=aliases)
    v1 = EnrichPlacesStage().version
    assert v1 != v0
    aliases.write_text("- {alias: Y, kind: town, id: guntur}\n", encoding="utf-8")
    assert EnrichPlacesStage().version != v1


def test_preflight(monkeypatch):
    from newsrag.stages.base import StageUnavailable

    EnrichPlacesStage().preflight()
    for model in ("no-such-model", "bge-m3"):
        set_settings(monkeypatch, enrich_model=model)
        with pytest.raises(StageUnavailable):
            EnrichPlacesStage().preflight()


# --- merged with enrich_core by load() -----------------------------------------------------------


def test_load_merges_with_enrich_core():
    core_art = {"pub_date": "2025-06-10", "stories": {"s1": {"category": "other", "flags": ["a"]}}}
    places_art = {
        "pub_date": "2025-06-10",
        "stories": {
            "s1": {
                "places": [],
                "districts_current": [],
                "districts_at_print": [],
                "states": [],
                "flags": ["place_unresolved"],
            }
        },
    }
    merged = enrich.merge({"enrich_places": places_art, "enrich_core": core_art})
    assert merged["s1"]["category"] == "other" and merged["s1"]["places"] == []
    assert merged["s1"]["flags"] == ["a", "place_unresolved"]


# --- FR-5: statistics ----------------------------------------------------------------------------


def place(name, llm_type, flag=None):
    return {"name": name, "llm_type": llm_type, "flags": [flag] if flag else []}


def art(day, *stories):
    return {"pub_date": day, "stories": {f"s{i}": {"places": p} for i, p in enumerate(stories)}}


def test_place_totals():
    a1 = art(
        "2025-06-10",
        [
            place("a", "district"),
            place("b", "district", "place_ambiguous"),
            place("c", "mandal", "place_unresolved"),
            place("v", "village"),
        ],
        [place("d", "city"), place("c", "mandal", "place_unresolved")],
    )
    a2 = art("2025-01-28", [place("e", "city"), place("f", "city", "place_unresolved")], [])
    out = places.place_totals([a1, a2])
    assert out["places_per_story"] == round(8 / 4, 2)
    assert out["district"] == {"resolved": 50.0, "ambiguous": 50.0, "unresolved": 0.0, "n": 2}
    assert out["mandal"] == {"resolved": 0.0, "ambiguous": 0.0, "unresolved": 100.0, "n": 2}
    assert out["city"]["resolved"] == 66.7 and out["city"]["unresolved"] == 33.3
    # 1 + 0 + 2 resolved of 2 + 2 + 3 counted places (the village is not counted)
    assert out["combined_resolved"] == round(100 * 3 / 7, 1)
    assert out["top_unresolved"] == [("c", 2), ("f", 1)]
    empty = places.place_totals([])
    assert empty["places_per_story"] == 0.0 and empty["combined_resolved"] == 0.0


def test_top_unresolved_is_limited_to_ten():
    stories = [[place(f"n{i}", "city", "place_unresolved")] for i in range(15)]
    assert len(places.place_totals([art("2025-06-10", *stories)])["top_unresolved"]) == 10


def test_stats_places_command(monkeypatch):
    seen = []

    def found(days):
        seen.append(days)
        return [art("2025-06-10", [place("a", "city"), place("b", "mandal", "place_unresolved")])]

    monkeypatch.setattr(enrich_cli, "_done_place_artifacts", found)
    result = CliRunner().invoke(
        enrich_cli.app, ["stats", "--places", "--date", "2025-06-10", "--date", "2024-12-26"]
    )
    assert result.exit_code == 0, result.output
    assert seen == [["2025-06-10", "2024-12-26"]]
    lines = result.output.splitlines()
    assert lines[0] == "places_per_story=2.0"
    assert lines[1] == "district: resolved=0.0 ambiguous=0.0 unresolved=0.0"
    assert lines[2] == "mandal: resolved=0.0 ambiguous=0.0 unresolved=100.0"
    assert lines[3] == "city: resolved=100.0 ambiguous=0.0 unresolved=0.0"
    assert lines[4] == "combined_resolved=50.0"
    assert lines[5].startswith("top_unresolved") and lines[6] == "1 b"
    monkeypatch.setattr(enrich_cli, "_done_place_artifacts", lambda days: [])
    assert CliRunner().invoke(enrich_cli.app, ["stats", "--places"]).exit_code == 1


def test_stats_without_places_still_reads_enrich_core(monkeypatch):
    monkeypatch.setattr(enrich_cli, "_done_runs", lambda days: ([], []))
    monkeypatch.setattr(
        enrich_cli, "_done_place_artifacts", lambda days: pytest.fail("places read")
    )
    assert CliRunner().invoke(enrich_cli.app, ["stats"]).exit_code == 1


def test_prompt_describes_the_reply():
    text = core.PROMPTS.joinpath("enrich_places_v1.md").read_text(encoding="utf-8")
    assert places.PROMPT_FILE.name == "enrich_places_v1.md"
    for t in places.PLACE_TYPES:
        assert f'"{t}"' in text
    assert '{"places"' in text and "<<MAX_PLACES>>" in text
