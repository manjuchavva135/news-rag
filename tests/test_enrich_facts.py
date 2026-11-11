"""Ticket 021: `enrich_facts` (a fake text model, the real committed `data/geo` files)."""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsrag import config, enrich
from newsrag.enrich import cli as enrich_cli
from newsrag.enrich import core, facts
from newsrag.enrich import validate as v
from newsrag.stages.enrich_facts import EnrichFactsStage

DAY = date(2025, 6, 10)
EVENT = date(2025, 6, 9)
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


def fact(value_text, **kw) -> dict:
    """One fact of a model reply; the fields default to a plausible, valid answer."""
    body = {
        "value_text": value_text,
        "value": 1,
        "unit": "INR",
        "scale": "none",
        "refers_to": "రాష్ట్ర అప్పు",
        "period_text": None,
        "place": None,
    }
    body.update(kw)
    return body


def reply(*items) -> str:
    return json.dumps({"facts": list(items)}, ensure_ascii=False)


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


def story_of(items, text: str, headline: str = "శీర్షిక", event: date = EVENT, print_date=DAY):
    """Enrich one story whose fake reply holds `items`: (story record, summary)."""
    client = FakeClient(lambda _t, _i: reply(*items))
    records, summary = facts.enrich_facts(
        client,
        [{"story_id": "s1", "headline": headline, "text": text}],
        print_date,
        {"s1": event.isoformat()},
    )
    return records["s1"], summary


def one_fact(value_text: str, text: str, **kw) -> dict:
    """The single fact of a story that prints `value_text` once, in `text`."""
    rec, _ = story_of([fact(value_text, **kw)], text)
    assert len(rec["facts"]) == 1, rec
    return rec["facts"][0]


# --- FR-1 / FR-2: validation and normalisation ----------------------------------------------------


def test_crore_normalised():
    f = one_fact("₹2,500 కోట్లు", "బడ్జెట్ ₹2,500 కోట్లు కేటాయించారు")
    assert f["value"] == 2500 and f["scale"] == "crore" and f["unit"] == "INR"
    assert f["normalized_value"] == 2.5e10


@pytest.mark.parametrize(
    ("value_text", "value", "scale", "normalized", "unit"),
    [
        ("రూ.3.50 లక్షల", 3.5, "lakh", 350000, "INR"),
        ("రూ.27వేల", 27, "thousand", 27000, "INR"),
        ("Rs 2.5 crore", 2.5, "crore", 25000000, "INR"),
        ("Rs 18,000.", 18000, "none", 18000, "INR"),
        ("12,34,567", 1234567, "none", 1234567, "count"),  # Indian grouping, unit from the model
        ("1,234,567", 1234567, "none", 1234567, "count"),  # Western grouping
        ("రూ.7.5 లక్షల కోట్ల", 750000, "crore", 7.5e12, "INR"),  # a lakh of crore
        ("రూ.10 వేల కోట్లు", 10000, "crore", 1e11, "INR"),
        ("4 million", 4, "million", 4e6, "count"),
        ("2 billion", 2, "billion", 2e9, "count"),
        ("83.33 శాతం", 83.33, "none", 83.33, "percent"),
        ("46.5%", 46.5, "none", 46.5, "percent"),
    ],
)
def test_value_scale_and_normalised_value_from_text(value_text, value, scale, normalized, unit):
    f = one_fact(value_text, f"వార్త {value_text} గురించి", unit="count")
    assert (f["value"], f["scale"], f["normalized_value"], f["unit"]) == (
        value,
        scale,
        normalized,
        unit,
    )


def test_telugu_digits_verbatim_match():
    # the story prints Telugu digits, the model copies them (or writes ASCII digits)
    text = "ఖర్చు రూ.౨,౫౦౦ కోట్లు అయింది"
    for given in ("రూ.౨,౫౦౦ కోట్లు", "రూ.2,500 కోట్లు"):
        f = one_fact(given, text)
        assert f["value"] == 2500 and f["scale"] == "crore"
        assert f["value_text"] == given  # stored as printed by the model, not rewritten
    # and the other way round
    assert one_fact("రూ.౨,౫౦౦ కోట్లు", "ఖర్చు రూ.2,500 కోట్లు అయింది")["normalized_value"] == 2.5e10


def test_non_verbatim_dropped():
    text = "అప్పు రూ.2,500 కోట్లు, వడ్డీ 12 శాతం"
    rec, summary = story_of(
        [
            fact("రూ.2,500 కోట్లు"),
            fact("రూ.3,000 కోట్లు"),  # not in the story
            fact("12 percent", unit="percent"),  # a translation
            fact("12 శాతం", unit="percent"),
        ],
        text,
    )
    assert [f["value_text"] for f in rec["facts"]] == ["రూ.2,500 కోట్లు", "12 శాతం"]
    assert summary["dropped_non_verbatim"] == 2 and summary["dropped_unparsed"] == 0


def test_verbatim_check_after_nfc_and_whitespace_normalisation():
    decomposed = "రూ.2,500  కోట్లు".replace("  ", "\n")
    f = one_fact("రూ.2,500 కోట్లు", f"అప్పు {decomposed} అయింది")
    assert f["value"] == 2500


def test_value_only_in_the_truncated_part_is_dropped(monkeypatch):
    set_settings(monkeypatch, enrich_input_chars=30)
    rec, summary = story_of([fact("రూ.5 కోట్లు")], "అ" * 100 + " రూ.5 కోట్లు")
    assert rec["facts"] == [] and summary["dropped_non_verbatim"] == 1


def test_value_parsed_from_text_not_model():
    # the model says 25000 lakh for `2,500 కోట్లు`: the code reads 2500 crore
    f = one_fact("2,500 కోట్లు", "అప్పు 2,500 కోట్లు", value=25000, scale="lakh", unit="INR")
    assert f["value"] == 2500 and f["scale"] == "crore"
    assert f["normalized_value"] == 2.5e10
    assert (
        v.FAILED_FLAG
        not in story_of([fact("2,500 కోట్లు", scale="lakh")], "అప్పు 2,500 కోట్లు")[0]["flags"]
    )


@pytest.mark.parametrize("value_text", ["20-25 శాతం", "రూపాయలు", "2 లక్షల 50 వేల", "1,2 శాతం", "అనేక"])
def test_unparseable_value_dropped(value_text):
    text = f"వార్త {value_text} గురించి"
    rec, summary = story_of([fact(value_text, value=20), fact("12 శాతం")], text + " 12 శాతం")
    assert [f["value_text"] for f in rec["facts"]] == ["12 శాతం"]
    assert summary["dropped_unparsed"] == 1 and summary["dropped_non_verbatim"] == 0


def test_a_span_with_a_month_name_is_a_date_not_an_amount():
    rec, summary = story_of(
        [fact("మే 15 నుంచి", unit="other"), fact("June 5", unit="other"), fact("15 శాతం")],
        "మే 15 నుంచి వేతనం లేదు. June 5 నాటికి 15 శాతం",
    )
    assert [f["value_text"] for f in rec["facts"]] == ["15 శాతం"]
    assert summary["dropped_unparsed"] == 2


def test_facts_without_a_refers_to_or_not_an_object_are_dropped():
    rec, summary = story_of(
        [fact("12 శాతం", refers_to=""), "12", fact("13 శాతం")], "12 శాతం మరియు 13 శాతం"
    )
    assert [f["value_text"] for f in rec["facts"]] == ["13 శాతం"]
    assert summary["dropped_invalid"] == 2


def test_period_defaults_to_event_date():
    f = one_fact("12 శాతం", "అప్పు 12 శాతం పెరిగింది")
    assert f["period_text"] is None
    assert f["period_start"] == f["period_end"] == EVENT.isoformat()


def test_financial_year_period():
    text = "2023-24 బడ్జెట్ లో రూ.5 కోట్లు, 2024-2025 లో రూ.6 కోట్లు, 2019-24 లో రూ.7 కోట్లు"
    rec, _ = story_of(
        [
            fact("రూ.5 కోట్లు", period_text="2023-24 బడ్జెట్"),
            fact("రూ.6 కోట్లు", period_text="2024-2025"),
            fact("రూ.7 కోట్లు", period_text="2019-24"),
        ],
        text,
    )
    a, b, c = rec["facts"]
    assert (a["period_start"], a["period_end"]) == ("2023-04-01", "2024-03-31")
    assert (b["period_start"], b["period_end"]) == ("2024-04-01", "2025-03-31")
    assert (c["period_start"], c["period_end"]) == (EVENT.isoformat(),) * 2  # not a financial year
    assert rec["flags"] == [facts.PERIOD_UNPARSED_FLAG]


@pytest.mark.parametrize(
    ("period_text", "start", "end"),
    [
        ("జూన్ 2024", "2024-06-01", "2024-06-30"),
        ("June 2024", "2024-06-01", "2024-06-30"),
        ("ఫిబ్రవరి 2024", "2024-02-01", "2024-02-29"),
        ("జూన్‌లో", "2025-06-01", "2025-06-30"),  # no year: the latest such month
        ("డిసెంబర్ నెలలో", "2024-12-01", "2024-12-31"),
        ("2019", "2019-01-01", "2019-12-31"),
        ("2019లో", "2019-01-01", "2019-12-31"),
        ("౨౦౧౯", "2019-01-01", "2019-12-31"),
    ],
)
def test_period_months_and_years(period_text, start, end):
    f = one_fact("5 శాతం", f"వార్త {period_text} నాటికి 5 శాతం", period_text=period_text)
    assert (f["period_start"], f["period_end"]) == (start, end)


def test_period_months_and_relative():
    text = "జూన్ 2024 లో 5 శాతం, గత ఏడాది 6 శాతం, ఏప్రిల్-జూన్ 2024 లో 7 శాతం"
    rec, summary = story_of(
        [
            fact("5 శాతం", period_text="జూన్ 2024"),
            fact("6 శాతం", period_text="గత ఏడాది"),  # relative: kept, event date, flagged
            fact("7 శాతం", period_text="ఏప్రిల్-జూన్ 2024"),  # a range of months
            fact("8 శాతం", period_text="మార్చి 2023"),  # not in the story: period dropped
        ],
        text + " 8 శాతం",
    )
    a, b, c, d = rec["facts"]
    assert (a["period_start"], a["period_end"]) == ("2024-06-01", "2024-06-30")
    assert b["period_text"] == "గత ఏడాది" and b["period_start"] == EVENT.isoformat()
    assert c["period_text"] == "ఏప్రిల్-జూన్ 2024" and c["period_end"] == EVENT.isoformat()
    assert d["period_text"] is None and d["period_start"] == EVENT.isoformat()
    assert rec["flags"] == [facts.PERIOD_UNPARSED_FLAG]
    assert summary["dropped_period"] == 1


def test_a_month_name_inside_another_word_is_not_a_month():
    f = one_fact("5 శాతం", "మేము 5 శాతం", period_text="మేము")
    assert f["period_start"] == EVENT.isoformat()


def test_period_flag_is_informational():
    assert not facts.PERIOD_UNPARSED_FLAG.startswith("low_confidence:")
    assert not facts.SCALE_FROM_MODEL_FLAG.startswith("low_confidence:")


def test_fact_id_format():
    text = "మొదట 5 శాతం, తరువాత రూ.7 కోట్లు, చివరగా 9 శాతం"
    rec, _ = story_of(  # the model lists them out of order; ids follow the story
        [fact("9 శాతం"), fact("5 శాతం"), fact("రూ.7 కోట్లు")], text
    )
    assert [f["fact_id"] for f in rec["facts"]] == ["s1#f001", "s1#f002", "s1#f003"]
    assert [f["value_text"] for f in rec["facts"]] == ["5 శాతం", "రూ.7 కోట్లు", "9 శాతం"]


def test_the_same_fact_listed_twice_is_kept_once():
    rec, _ = story_of([fact("5 శాతం"), fact("5 శాతం"), fact("5 శాతం", refers_to="వేరే")], "5 శాతం")
    assert [f["fact_id"] for f in rec["facts"]] == ["s1#f001", "s1#f002"]


def test_artifact_fields_and_order():
    rec, _ = story_of([fact("రూ.5 కోట్లు", place="కర్నూలు")], "కర్నూలు లో రూ.5 కోట్లు")
    assert list(rec) == ["facts", "flags"]
    assert list(rec["facts"][0]) == [
        "fact_id", "value_text", "value", "scale", "normalized_value", "unit", "refers_to",
        "period_text", "period_start", "period_end", "place", "districts_current",
        "districts_at_print", "state",
    ]  # fmt: skip


def test_unit_from_the_model_only_when_the_text_has_no_marker():
    assert one_fact("5,000", "5,000 మంది", unit="count")["unit"] == "count"
    assert one_fact("5,000 శాతం", "5,000 శాతం", unit="count")["unit"] == "percent"
    assert one_fact("5,000", "5,000 మంది", unit="people")["unit"] == "other"  # outside the list
    assert one_fact("94.497 ఎకరాల", "94.497 ఎకరాల భూమి", unit="acres")["unit"] == "acres"


def test_inr_markers():
    for marker in (
        "₹5,000",
        "రూ.5,000",
        "రూ. 5,000",
        "Rs5,000",
        "Rs. 5,000",
        "Rs 5,000",
        "INR 5,000",
    ):
        f = one_fact(marker, f"ధర {marker} మాత్రమే", unit="count")
        assert f["unit"] == "INR" and f["value"] == 5000, marker
    f = one_fact("5,000 రూపాయలు", "ధర 5,000 రూపాయలు మాత్రమే", unit="count")
    assert f["unit"] == "INR"
    # the unit of "5,000 మంది" is not money
    assert one_fact("5,000", "5,000 మంది", unit="count")["unit"] == "count"


# --- FR-5: scale from context -----------------------------------------------------------------------


TABLE = """రాష్ట్ర ఆదాయం
(రూ. కోట్లలో)
| శాఖ | 2023-24 | మొత్తం (రూ. లక్షల్లో) |
|---|---|---|
| రవాణా | 2,500 | 300 |
| విద్య | 1,100 | 90 |
"""


def test_scale_from_table_header():
    rec, _ = story_of([fact("2,500"), fact("1,100", scale="none")], TABLE)
    a, b = rec["facts"]
    assert a["scale"] == "crore" and a["normalized_value"] == 2.5e10  # the title names crore
    assert b["scale"] == "crore" and facts.SCALE_FROM_MODEL_FLAG not in rec["flags"]
    # a column header that names its own scale wins over the title
    col = story_of([fact("300")], TABLE)[0]["facts"][0]
    assert col["scale"] == "lakh" and col["normalized_value"] == 3e7


def test_scale_from_a_scale_phrase_before_the_number():
    f = one_fact("5,900", "మొత్తం విలువ (రూ. కోట్లలో) 5,900 గా ఉంది", unit="INR")
    assert f["scale"] == "crore"
    # a scale word that belongs to another number is not a scale phrase
    text = "రూ.12 లక్షల కంటే ఎక్కువ ఆదాయం ఉన్న 5,900 మంది"
    rec, _ = story_of([fact("5,900", unit="count")], text)
    assert rec["facts"][0]["scale"] == "none" and rec["flags"] == []
    # more than 40 characters before the number: too far
    far = "కోట్లలో " + "అ" * 45 + " 5,900 మంది"
    assert one_fact("5,900", far, unit="count")["scale"] == "none"


def test_ordinary_scale_words_are_not_a_scale_phrase():
    # "కోట్ల రూపాయల విలువైన" is a description, not "(in crores)": the 50 acres keep scale none
    rec, _ = story_of([fact("50 ఎకరాల", unit="acres")], "కోట్ల రూపాయల విలువైన 50 ఎకరాల భూమి")
    assert rec["facts"][0]["scale"] == "none" and rec["facts"][0]["normalized_value"] == 50
    assert facts.context_scale("కోటి మంది ప్రజలకు 25 మండలాల్లో", "25", 40) is None
    for text in ("Figures in crore: 25", "Rs crore 25", "(lakhs) 25", "లక్షల్లో 25"):
        assert facts.context_scale(text, "25", 40) is not None, text


def test_full_dates_are_periods():
    for text, day in (
        ("10-06-2024", "2024-06-10"),
        ("2024-06-10", "2024-06-10"),
        ("10.6.2024", "2024-06-10"),
    ):
        f = one_fact("5 శాతం", f"{text} న 5 శాతం", period_text=text)
        assert f["period_start"] == f["period_end"] == day, text
    f = one_fact("5 శాతం", "31-02-2024 న 5 శాతం", period_text="31-02-2024")
    assert f["period_start"] == EVENT.isoformat()


def test_scale_from_model_is_flagged():
    rec, _ = story_of([fact("5,900", scale="lakh")], "మొత్తం 5,900 మందికి")
    f = rec["facts"][0]
    assert f["scale"] == "lakh" and f["normalized_value"] == 5.9e8
    assert rec["flags"] == [facts.SCALE_FROM_MODEL_FLAG]
    # a model that says "none" (or nonsense) has nothing to flag
    rec, _ = story_of([fact("5,900", scale="none")], "మొత్తం 5,900 మందికి")
    assert rec["flags"] == []
    rec, _ = story_of([fact("5,900", scale="hundreds")], "మొత్తం 5,900 మందికి")
    assert rec["facts"][0]["scale"] == "none" and rec["flags"] == []


def test_percent_never_takes_a_scale():
    rec, _ = story_of([fact("12 శాతం", unit="percent", scale="crore")], "(రూ. కోట్లలో) 12 శాతం")
    assert rec["facts"][0]["scale"] == "none" and rec["flags"] == []


def test_table_markdown():
    table = "| ఏడాది | అప్పు |\n|---|---|\n| 2023 | ₹2,500 కోట్లు |\n"
    client = FakeClient(lambda *_: reply(fact("₹2,500 కోట్లు", refers_to="అప్పు")))
    records, summary = facts.enrich_facts(
        client, [{"story_id": "t1", "headline": "", "text": table}], DAY
    )
    (call,) = client.calls
    assert call["messages"][-1]["content"].endswith(table)  # the table markdown was sent
    (f,) = records["t1"]["facts"]
    assert f["normalized_value"] == 2.5e10 and f["fact_id"] == "t1#f001"
    assert summary["facts"] == 1


def test_table_without_a_scale_title_has_none():
    table = "| శాఖ | నిధులు |\n|---|---|\n| రవాణా | 2,500 |\n"
    f = story_of([fact("2,500")], table)[0]["facts"][0]
    assert f["scale"] == "none"


# --- FR-2 / FR-5: place, districts and state --------------------------------------------------------


def test_place_resolved_like_ticket_020():
    f = one_fact("రూ.5 కోట్లు", "కర్నూలులో రూ.5 కోట్లు ఖర్చు", place="కర్నూలులో")
    assert f["place"] == "కర్నూలులో"  # case suffix stripped for the lookup only
    assert f["districts_current"] == ["kurnool"] and f["districts_at_print"] == ["kurnool"]
    assert f["state"] == "andhra_pradesh"


def test_state_field():
    f = one_fact("రూ.5 కోట్లు", "తెలంగాణలో రూ.5 కోట్లు", place="తెలంగాణలో")
    assert f["state"] == "telangana" and f["districts_current"] == []
    f = one_fact("Rs 5 crore", "Andhra Pradesh gets Rs 5 crore", place="Andhra Pradesh")
    assert f["state"] == "andhra_pradesh"
    f = one_fact("రూ.5 కోట్లు", "కర్ణాటకలో రూ.5 కోట్లు", place="కర్ణాటక")  # not in the lists
    assert f["state"] is None and f["districts_current"] == []


def test_place_not_verbatim_is_dropped_but_the_fact_stays():
    rec, summary = story_of([fact("రూ.5 కోట్లు", place="Kurnool")], "కర్నూలులో రూ.5 కోట్లు")
    assert rec["facts"][0]["place"] is None and rec["facts"][0]["districts_current"] == []
    assert summary["dropped_place"] == 1


def test_unresolved_place_keeps_the_name_without_districts():
    f = one_fact("రూ.5 కోట్లు", "ఎక్కడో రూ.5 కోట్లు", place="ఎక్కడో")
    assert f["place"] == "ఎక్కడో" and f["districts_current"] == [] and f["state"] is None


def test_ambiguous_place_has_no_districts(monkeypatch):
    from newsrag import geo

    def two(name, on=None):
        return [
            geo.Place("town", "a", "A", "ఎ", "andhra_pradesh", ["d1"], ["d1"]),
            geo.Place("town", "b", "B", "ఎ", "andhra_pradesh", ["d2"], ["d2"]),
        ]

    monkeypatch.setattr(facts.places.geo, "resolve", two)
    f = one_fact("రూ.5 కోట్లు", "ఎ లో రూ.5 కోట్లు", place="ఎ")
    assert f["districts_current"] == [] and f["districts_at_print"] == []
    assert f["state"] == "andhra_pradesh"  # all candidates share it


def test_districts_of_the_print_date():
    old = date(2020, 4, 2)
    rec, _ = story_of([fact("రూ.5 కోట్లు", place="తిరుపతి")], "తిరుపతి లో రూ.5 కోట్లు", print_date=old)
    f = rec["facts"][0]
    assert f["districts_at_print"] == ["chittoor"] and f["districts_current"] == ["tirupati"]


def test_place_kind_is_read_from_the_name():
    day = date(2020, 4, 2)

    def districts(name):
        rec, _ = story_of([fact("రూ.5 కోట్లు", place=name)], f"{name} లో రూ.5 కోట్లు", print_date=day)
        return rec["facts"][0]["districts_at_print"]

    assert districts("తిరుపతి") == ["chittoor"]  # a bare name: the town
    # a district written as such: the district's own old districts (it was split out of two)
    assert districts("తిరుపతి జిల్లా") == ["chittoor", "sri_potti_sriramulu_nellore"]
    # a "district" in the name picks district matches when the list has them
    current = one_fact("రూ.5 కోట్లు", "తిరుపతి జిల్లా లో రూ.5 కోట్లు", place="తిరుపతి జిల్లా")
    assert "tirupati" in current["districts_current"]


# --- the requests (FR-1) -----------------------------------------------------------------------------


def test_request_shape():
    client = FakeClient(lambda *_: reply())
    facts.enrich_facts(client, [{"story_id": "s1", "headline": "H", "text": "T"}], DAY)
    (call,) = client.calls
    system, user = call["messages"]
    assert system["role"] == "system" and "<<" not in system["content"]
    assert "16 facts" in system["content"] and user["content"] == "H\nT"
    assert call["temperature"] == 0
    assert call["max_tokens"] == config.settings.enrich_max_tokens
    assert call["timeout"] == config.settings.enrich_request_timeout_s
    schema = call["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["facts"]["maxItems"] == 16
    props = schema["properties"]["facts"]["items"]["properties"]
    assert props["unit"]["enum"] == list(facts.UNITS) and props["scale"]["enum"] == list(
        facts.SCALES
    )
    assert client.warmups, "the system prompt is warmed up"


def test_input_is_cut_to_6000_chars():
    client = FakeClient(lambda *_: reply())
    facts.enrich_facts(client, [{"story_id": "s1", "headline": "H", "text": "x" * 9000}], DAY)
    assert len(client.calls[0]["messages"][1]["content"]) == 6000


def test_retry_asks_for_fewer_facts_and_good_reply_after_a_bad_one():
    answers = ["not json", reply(fact("5 శాతం"))]
    client = FakeClient(lambda _t, i: answers[i])
    outcome = facts.enrich_story(client, facts.build_prompt(), "", "5 శాతం", DAY, EVENT)
    assert [f["value_text"] for f in outcome.parsed.facts] == ["5 శాతం"] and not outcome.failed
    assert len(client.calls) == 2
    second = client.calls[1]
    assert "at most 8 facts" in second["messages"][0]["content"]
    assert (
        second["response_format"]["json_schema"]["schema"]["properties"]["facts"]["maxItems"] == 8
    )


def test_reply_must_hold_a_list_of_facts():
    for bad in ('{"facts": "x"}', "[]", '{"other": []}'):
        client = FakeClient(lambda *_, b=bad: b)
        outcome = facts.enrich_story(client, facts.build_prompt(), "", "5 శాతం", DAY, EVENT)
        assert outcome.failed and len(client.calls) == config.settings.enrich_format_retries + 1


def test_list_at_the_cap_is_flagged(monkeypatch):
    set_settings(monkeypatch, enrich_max_facts=2)
    rec, _ = story_of([fact("5 శాతం"), fact("6 శాతం")], "5 శాతం 6 శాతం")
    assert v.CAPPED_FLAG in rec["flags"]


def test_empty_story_needs_no_request():
    client = FakeClient(lambda *_: reply())
    records, _ = facts.enrich_facts(client, [{"story_id": "s1", "headline": "", "text": ""}], DAY)
    assert records["s1"] == {"facts": [], "flags": ["empty_story"]} and not client.calls


def test_story_without_amounts_has_no_facts_and_no_flag():
    rec, summary = story_of([], "ఏమీ లేదు")
    assert rec == {"facts": [], "flags": []} and summary["facts"] == 0


def test_failed_story_has_no_facts_and_the_failure_flag():
    def boom(_t, _i):
        raise ConnectionError("down")

    records, summary = facts.enrich_facts(
        FakeClient(boom), [{"story_id": "s1", "headline": "h", "text": "t"}], DAY
    )
    assert records["s1"] == {"facts": [], "flags": [FAILED]}
    assert summary["enrich_failed"] == 1


def test_a_bad_fact_does_not_fail_the_story():
    rec, summary = story_of([fact("9 శాతం"), fact("5 శాతం")], "5 శాతం")
    assert [f["value_text"] for f in rec["facts"]] == ["5 శాతం"]
    assert rec["flags"] == [] and summary["enrich_failed"] == 0


def test_event_date_missing_falls_back_to_the_print_date():
    client = FakeClient(lambda *_: reply(fact("5 శాతం")))
    records, _ = facts.enrich_facts(
        client, [{"story_id": "s1", "headline": "", "text": "5 శాతం"}], DAY, {}
    )
    assert records["s1"]["facts"][0]["period_start"] == DAY.isoformat()


def test_prompt_describes_the_reply_and_lists_the_excluded_numbers():
    text = facts.PROMPT_FILE.read_text(encoding="utf-8")
    assert facts.PROMPT_FILE.name == "enrich_facts_v1.md" and "<<MAX_FACTS>>" in text
    for key in ("value_text", "value", "unit", "scale", "refers_to", "period_text", "place"):
        assert f'"{key}"' in text
    for word in [*facts.UNITS, *facts.SCALES]:
        assert f'"{word}"' in text


def test_excluded_number_kinds_prompt():
    text = facts.PROMPT_FILE.read_text(encoding="utf-8").lower()
    for kind in (
        "years", "dates", "times", "phone", "id, notice", "case", "page or edition",
        "sports scores", "ranks", "ages",
    ):  # fmt: skip
        assert kind in text, kind


# --- the stage -------------------------------------------------------------------------------------


def stories_of(n: int) -> list[dict]:
    return [
        {"story_id": f"s_{i:03d}", "headline": f"శీర్షిక {i}", "text": "అప్పు 12 శాతం పెరిగింది"}
        for i in range(n)
    ]


def stage_for(monkeypatch, tmp_path, stories, client, core_events=None) -> EnrichFactsStage:
    """A stage with an open fake client whose dedup artifact holds `stories`."""
    from newsrag.dedup import load

    set_settings(monkeypatch, work_dir=tmp_path)
    art = tmp_path / "dedup.json"
    art.write_text(json.dumps({"pub_date": "2025-06-10", "stories": stories}), encoding="utf-8")
    monkeypatch.setattr(load, "done_dedup", lambda d=None: {"2025-06-10": art})
    core_art = tmp_path / "core.json"
    core_art.write_text(
        json.dumps(
            {
                "pub_date": "2025-06-10",
                "stories": {k: {"event_date": e} for k, e in (core_events or {}).items()},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        enrich,
        "done_artifacts",
        lambda stage, d=None: {"2025-06-10": core_art} if stage == "enrich_core" else {},
    )
    stage = EnrichFactsStage()
    stage._client = client
    return stage


def test_stage_artifact_and_detail(monkeypatch, tmp_path, capsys):
    client = FakeClient(lambda *_: reply(fact("12 శాతం", unit="percent")))
    stage = stage_for(monkeypatch, tmp_path, stories_of(20), client, {"s_000": "2025-06-01"})
    assert stage.concurrency == 1
    result = stage.handle(DAY, [])
    assert len(client.calls) == 20
    art = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert list(art) == ["pub_date", "stories"] and art["pub_date"] == "2025-06-10"
    assert list(art["stories"]) == [f"s_{i:03d}" for i in range(20)]  # in dedup order
    first = art["stories"]["s_000"]["facts"][0]
    assert first["fact_id"] == "s_000#f001" and first["period_start"] == "2025-06-01"
    assert art["stories"]["s_001"]["facts"][0]["period_start"] == "2025-06-10"  # print date
    d = result.detail
    assert d["stories"] == 20 and d["facts"] == 20 and d["enrich_failed"] == 0
    assert d["units"]["percent"] == 20 and d["dropped_non_verbatim"] == 0
    assert "enrich_facts 2025-06-10: 20 stories" in capsys.readouterr().err
    assert stage.version.replace(".", "_") in result.artifact_path
    assert "enrich_facts" in result.artifact_path


def test_date_fails_over_20_percent(monkeypatch, tmp_path):
    def answer(text, _i):
        if any(f"శీర్షిక {i}\n" in text for i in range(3)):
            raise ConnectionError("down")
        return reply()

    stage = stage_for(monkeypatch, tmp_path, stories_of(10), FakeClient(answer))
    with pytest.raises(RuntimeError, match="3 of 10 stories could not be enriched"):
        stage.handle(DAY, [])
    ok = stage_for(monkeypatch, tmp_path, stories_of(10), FakeClient(lambda *_: reply()))
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


def test_version_and_registration(monkeypatch):
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["enrich_facts"]
    assert stage.name == "enrich_facts" and stage.depends_on == ("enrich_places",)
    assert stage.kind == "date"
    v0 = stage.version
    assert v0.startswith("qwen3-30b-a3b-facts-p1-")
    assert v0 != REGISTRY["enrich_places"].version
    base = config.settings
    for update in (
        {"enrich_model": "gemma3-27b"},
        {"enrich_input_chars": 3000},
        {"enrich_max_facts": 4},
        {"enrich_facts_scale_window": 10},
    ):
        set_settings(monkeypatch, **update)
        assert EnrichFactsStage().version != v0
        set_settings(monkeypatch, **{k: getattr(base, k) for k in update})
    set_settings(monkeypatch, enrich_concurrency=2)  # speed only: the output does not change
    assert EnrichFactsStage().version == v0


def test_preflight(monkeypatch):
    from newsrag.stages.base import StageUnavailable

    EnrichFactsStage().preflight()
    for model in ("no-such-model", "bge-m3"):
        set_settings(monkeypatch, enrich_model=model)
        with pytest.raises(StageUnavailable):
            EnrichFactsStage().preflight()


# --- merged by load() ------------------------------------------------------------------------------------


def test_load_merges_facts_with_the_other_enrich_artifacts():
    rec, _ = story_of([fact("5 శాతం")], "5 శాతం")
    merged = enrich.merge(
        {
            "enrich_core": {"stories": {"s1": {"category": "other", "flags": ["a"]}}},
            "enrich_places": {"stories": {"s1": {"states": [], "flags": ["a", "b"]}}},
            "enrich_facts": {"stories": {"s1": {**rec, "flags": ["c"]}}},
        }
    )
    assert merged["s1"]["category"] == "other" and len(merged["s1"]["facts"]) == 1
    assert merged["s1"]["flags"] == ["a", "b", "c"]


# --- statistics (FR-4) ---------------------------------------------------------------------------------


def art(day: str, per_story: list[list[str]]) -> dict:
    """An artifact whose stories hold facts of the given units."""
    return {
        "pub_date": day,
        "stories": {
            f"s{i}": {"facts": [{"unit": u} for u in units], "flags": []}
            for i, units in enumerate(per_story)
        },
    }


def test_fact_totals():
    artifacts = [art("2025-06-10", [["INR", "percent"], [], ["INR"]]), art("2024-12-26", [[]])]
    details = [
        {"dropped_non_verbatim": 2, "dropped_unparsed": 1},
        {"dropped_non_verbatim": 1, "dropped_unparsed": 0},
    ]
    out = facts.fact_totals(artifacts, details)
    assert out["facts_per_story"] == 0.75  # zero-fact stories count
    assert out["unit_INR"] == 2 and out["unit_percent"] == 1 and out["unit_count"] == 0
    assert out["dropped_non_verbatim"] == 3 and out["dropped_unparsed"] == 1
    assert facts.fact_totals([], [])["facts_per_story"] == 0.0


def test_stats_facts_command(monkeypatch):
    seen = []

    def runs(days, stage="enrich_core"):
        seen.append((days, stage))
        return [art("2025-06-10", [["INR"], []])], [
            {"dropped_non_verbatim": 4, "dropped_unparsed": 5}
        ]

    monkeypatch.setattr(enrich_cli, "_done_runs", runs)
    result = CliRunner().invoke(
        enrich_cli.app, ["stats", "--facts", "--date", "2025-06-10", "--date", "2024-12-26"]
    )
    assert result.exit_code == 0, result.output
    assert seen == [(["2025-06-10", "2024-12-26"], "enrich_facts")]
    lines = result.output.splitlines()
    assert lines[0] == "facts_per_story=0.5"
    assert [x for x in lines if x.startswith("unit_")] == [
        f"unit_{u}={int(u == 'INR')}" for u in facts.UNITS
    ]
    assert lines[-2:] == ["dropped_non_verbatim=4", "dropped_unparsed=5"]
    monkeypatch.setattr(enrich_cli, "_done_runs", lambda days, stage="": ([], []))
    assert CliRunner().invoke(enrich_cli.app, ["stats", "--facts"]).exit_code == 1


# --- verify-facts (NFR-1) ---------------------------------------------------------------------------------


def items_of(n: int) -> list[dict]:
    rec, _ = story_of(
        [fact("రూ.2,500 కోట్లు", period_text="జూన్ 2024", place="కర్నూలు")],
        "జూన్ 2024 లో కర్నూలు కు రూ.2,500 కోట్లు",
    )
    return [
        {
            "pub_date": "2025-06-10",
            "story_id": f"s{i}",
            "headline": "శీర్షిక",
            "text": "జూన్ 2024 లో కర్నూలు కు రూ.2,500 కోట్లు",
            "fact": rec["facts"][0],
        }
        for i in range(n)
    ]


def test_judge_input():
    item = items_of(1)[0]
    item["text"] = "x" * 9000
    prompt = facts.judge_prompt(item)
    assert "<<" not in prompt
    story = prompt.split("Story (headline, then text):\n")[1].split("\n\nExtracted fact:")[0]
    assert len(story) == 6000 and story.startswith("శీర్షిక\nxx")  # headline + text, 6,000 chars
    for line in (
        "- value_text: రూ.2,500 కోట్లు",
        "- value: 2500",
        "- scale: crore",
        "- unit: INR",
        "- refers_to: రాష్ట్ర అప్పు",
        "- period: జూన్ 2024 (2024-06-01 to 2024-06-30)",
        "- place: కర్నూలు",
    ):
        assert line in prompt
    item["fact"] = {**item["fact"], "period_text": None, "place": None}
    prompt = facts.judge_prompt(item)
    assert "- period: (none)" in prompt and "- place: (none)" in prompt
    assert facts.JUDGE_PROMPT_FILE.name == "facts_judge_v1.md"


def test_sample_is_seeded_and_capped():
    items = items_of(80)
    a = facts.sample(items, 50, 13)
    assert len(a) == 50 and a == facts.sample(items, 50, 13) and a != facts.sample(items, 50, 14)
    assert facts.sample(items[:10], 50, 13) == items[:10]


def test_parse_verdict():
    assert facts.parse_verdict('{"correct": true}')
    assert facts.parse_verdict('```json\n{"correct": true}\n```')
    for bad in ('{"correct": false}', '{"correct": "true"}', "yes", "[true]", "", '{"x": 1}'):
        assert not facts.parse_verdict(bad), bad


def test_verify_report_and_exit_code(monkeypatch, tmp_path):
    set_settings(monkeypatch, enrich_facts_verify_file=tmp_path / "report.json")
    monkeypatch.setattr(facts, "collect_facts", lambda day=None: items_of(60))
    state = {"wrong": 0}

    def invoke(wrong: int):
        calls = []

        def ask(prompt):
            calls.append(prompt)
            index = len(calls)
            return "garbage" if index <= wrong else '{"correct": true}'

        original = facts.run_verify
        monkeypatch.setattr(
            facts, "run_verify", lambda items, n, seed, ask_=None: original(items, n, seed, ask)
        )
        try:
            return CliRunner().invoke(enrich_cli.app, ["verify-facts", "--n", "50", "--seed", "13"])
        finally:
            monkeypatch.setattr(facts, "run_verify", original)

    ok = invoke(0)
    assert ok.exit_code == 0, ok.output
    assert "correct: 50/50" in ok.output
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["n"] == 50 and report["correct"] == 50 and report["seed"] == 13
    assert invoke(5).exit_code == 0 and "correct: 45/50" in invoke(5).output
    bad = invoke(6)  # an unparseable reply counts as incorrect: 44/50 is below the threshold
    assert bad.exit_code == 1 and "correct: 44/50" in bad.output
    assert state["wrong"] == 0
    monkeypatch.setattr(facts, "collect_facts", lambda day=None: [])
    assert CliRunner().invoke(enrich_cli.app, ["verify-facts"]).exit_code == 1


def test_core_helpers_are_reused():
    assert core.ask is not None and facts.core is core
