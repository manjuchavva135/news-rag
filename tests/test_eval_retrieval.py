"""Ticket 025: the retrieval test set builder and the scoring."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from newsrag.evals import retrieval as r
from newsrag.search.hybrid import Hit

GOLD = (
    "అమరావతి రాజధాని నిర్మాణానికి ప్రభుత్వం కొత్త నిధులు కేటాయించింది. "
    "మొదటి దశలో రహదారులు, భవనాలు పూర్తి చేస్తామని మంత్రి తెలిపారు. "
    "రెండో దశలో నీటి సరఫరా, విద్యుత్ పనులు మొదలవుతాయి."
)
QUESTION = {"gold_page_ids": ["P1", "P9"], "gold_text": GOLD}


# --- the hit rule -----------------------------------------------------------------------------


def test_ngrams_nfc_no_whitespace():
    assert r.ngrams("ab c\nde", 5) == {"abcde"}
    assert r.ngrams("abc", 5) == set()


def test_hit_needs_page_and_text_overlap():
    inside = "మొదటి దశలో రహదారులు, భవనాలు పూర్తి చేస్తామని"
    assert r.is_correct(["P1"], inside, ["P1"], GOLD)
    assert not r.is_correct(["P2"], inside, ["P1"], GOLD)  # text matches, page doesn't
    other = "క్రికెట్ జట్టు ఘన విజయం సాధించింది ఫైనల్లో"
    assert not r.is_correct(["P1"], other, ["P1"], GOLD)  # page matches, text doesn't
    assert not r.is_correct(["P1"], "", ["P1"], GOLD)
    # the threshold: half of the hit inside the story
    half = inside + " " + other
    share = r.overlap_share(half, GOLD, 5)
    assert 0.3 < share < 0.7
    assert r.is_correct(["P1"], half, ["P1"], GOLD, overlap=0.3)
    assert not r.is_correct(["P1"], half, ["P1"], GOLD, overlap=share + 0.01)


def test_hit_on_later_chunk_counts():
    later_chunk = "రెండో దశలో నీటి సరఫరా, విద్యుత్ పనులు మొదలవుతాయి."
    hits = [
        Hit("x#c01", "x", 0.9, ["P5"], "వేరే వార్త పూర్తిగా వేరే విషయం", "", ""),
        Hit("s#c03", "s", 0.8, ["P9"], later_chunk, "", ""),  # 3rd chunk, second printing
    ]
    assert r.first_correct_rank(hits, QUESTION) == 2


def test_metrics():
    m = r.metrics([1, None, 2, 11], k=10)
    assert m == {"n": 4, "recall_at_10": 0.5, "mrr": round((1 + 0.5) / 4, 4)}
    assert r.metrics([], 10)["n"] == 0


# --- question checks --------------------------------------------------------------------------


def test_copies_headline():
    headline = "రాజధాని నిర్మాణానికి కొత్త నిధులు కేటాయింపు ప్రభుత్వ నిర్ణయం"
    assert r.copies_headline("రాజధాని నిర్మాణానికి కొత్త నిధులు ఎంత?", headline, 4)
    assert not r.copies_headline("రాజధాని పనులకు ప్రభుత్వం ఎంత డబ్బు ఇచ్చింది?", headline, 4)
    assert r.copies_headline("Why did the State Budget Grow so much?", "state budget grow so", 4)


def test_check_question_language():
    assert r.check_question("అమరావతికి ఎంత డబ్బు కేటాయించారు?", "te", "h") is None
    assert r.check_question("How much money went to Amaravati?", "te", "h") == "not in Telugu"
    assert r.check_question("How much money went to Amaravati?", "en", "h") is None
    assert r.check_question("", "en", "h") == "empty"
    assert r.check_question(None, "en", "h") == "empty"


# --- the builder ------------------------------------------------------------------------------


def story(i: int, day: date, headline: str | None = None) -> dict[str, Any]:
    return {
        "story_id": f"s{i:03d}",
        "pub_date": day,
        "kind": "news",
        "headline": headline or f"శీర్షిక {i}",
        "text": f"కథనం {i} " * 20,
        "page_ids": [f"{day.isoformat()}_A_main_{i:02d}", f"{day.isoformat()}_B_main_{i:02d}"],
    }


def corpus() -> list[dict[str, Any]]:
    out = [story(i, date(2026, 4, 1 + i % 7)) for i in range(30)]  # window
    out += [story(100 + i, date(2025, 6, 10)) for i in range(10)]
    out += [story(200 + i, date(2020, 4, 2)) for i in range(3)]  # short month
    return out


def good_ask(prompt: str) -> str:
    te = "Telugu script" in prompt
    sid = prompt.rsplit("కథనం ", 1)[-1].split()[0]
    text = f"కథనం సంఖ్య {sid} లో ఏమి జరిగింది?" if te else f"What happened in story number {sid}?"
    return json.dumps({"question": text}, ensure_ascii=False)


def test_builder_strata_langs_and_lines(tmp_path: Path):
    out = tmp_path / "set.jsonl"
    echoed: list[str] = []
    summary = r.build_retrieval_set(
        n=20, seed=7, out=out, window_n=10, ask=good_ask, stories=corpus(), echo=echoed.append
    )
    lines = r.read_set(out)
    assert summary["n"] == len(lines) == 20
    assert sum(x["in_baseline_window"] for x in lines) == 10
    months = [x["pub_date"][:7] for x in lines if not x["in_baseline_window"]]
    assert months.count("2020-04") == 3 and months.count("2025-06") == 7  # water-filled
    assert echoed == ["short: 2020-04"]
    assert sum(x["lang"] == "te" for x in lines) == 14  # 70%
    first = lines[0]
    assert set(first) == {
        "qid",
        "lang",
        "question",
        "story_id",
        "gold_page_ids",
        "gold_text",
        "pub_date",
        "in_baseline_window",
    }
    assert len(first["gold_page_ids"]) == 2 and first["gold_text"].startswith("శీర్షిక")
    # the same seed gives the same set
    again = tmp_path / "again.jsonl"
    r.build_retrieval_set(n=20, seed=7, out=again, window_n=10, ask=good_ask, stories=corpus())
    assert again.read_text() == out.read_text()


def test_short_window_printed(tmp_path: Path):
    echoed: list[str] = []
    summary = r.build_retrieval_set(
        n=12,
        seed=1,
        out=tmp_path / "s.jsonl",
        window_n=10,
        ask=good_ask,
        stories=[story(100 + i, date(2025, 6, 10)) for i in range(5)],
        echo=echoed.append,
    )
    assert echoed == ["short: window"]  # 2025-06 has more than the 2 other questions
    assert summary["n"] == 2 and summary["in_window"] == 0


def test_headline_copy_regenerated(tmp_path: Path):
    headline = "రాజధాని నిర్మాణానికి కొత్త నిధులు కేటాయింపు"
    stories = [story(1, date(2025, 6, 10), headline), story(2, date(2025, 6, 10), headline)]
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        first_try = "rejected" not in prompt
        if "కథనం 1 " in prompt and not first_try:
            return json.dumps({"question": "అమరావతి పనులకు ఎంత డబ్బు?"}, ensure_ascii=False)
        return json.dumps({"question": f"{headline} ఎంత?"}, ensure_ascii=False)  # a copy

    out = tmp_path / "set.jsonl"
    summary = r.build_retrieval_set(n=1, seed=0, out=out, window_n=0, ask=ask, stories=stories)
    lines = r.read_set(out)
    # s002 always copies the headline (3 tries, then replaced); s001's second try is good
    assert summary["n"] == 1 and lines[0]["story_id"] == "s001"
    assert not r.copies_headline(lines[0]["question"], headline)
    tried = [p for p in prompts if "కథనం 1 " in p]
    assert len(tried) == 2 and "copies the headline" in tried[1]
    assert len([p for p in prompts if "కథనం 2 " in p]) in (0, 3)


def test_story_replaced_after_three_tries(tmp_path: Path):
    stories = [story(1, date(2025, 6, 10)), story(2, date(2025, 6, 10))]
    asked: dict[str, int] = {}

    def ask(prompt: str) -> str:
        sid = "1" if "కథనం 1 " in prompt else "2"
        asked[sid] = asked.get(sid, 0) + 1
        return "not json" if sid == "1" else json.dumps({"question": "రెండో కథనంలో ఏమైంది?"})

    out = tmp_path / "set.jsonl"
    # seed 3 puts s001 first in the shuffled stratum (checked below)
    summary = r.build_retrieval_set(n=1, seed=3, out=out, window_n=0, ask=ask, stories=stories)
    line = r.read_set(out)[0]
    assert line["story_id"] == "s002"
    if asked.get("1"):
        assert asked["1"] == 3 and summary["replaced"] == 1


def test_builder_story_filter(tmp_path: Path):
    out = tmp_path / "set.jsonl"
    keep = {"s100", "s101", "s102"}
    summary = r.build_retrieval_set(
        n=3,
        seed=7,
        out=out,
        window_n=0,
        story_filter=lambda st: st["story_id"] in keep,
        ask=good_ask,
        stories=corpus(),
    )
    assert summary["n"] == 3 and {x["story_id"] for x in r.read_set(out)} == keep


# --- scoring ----------------------------------------------------------------------------------


def write_set(path: Path) -> None:
    rows = [
        {"qid": "q1", "lang": "te", "question": "a", "gold_page_ids": ["P1"], "gold_text": GOLD,
         "in_baseline_window": True},
        {"qid": "q2", "lang": "en", "question": "b", "gold_page_ids": ["P2"], "gold_text": GOLD,
         "in_baseline_window": False},
        {"qid": "q3", "lang": "te", "question": "c", "gold_page_ids": ["P3"], "gold_text": GOLD,
         "in_baseline_window": True},
    ]  # fmt: skip
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))


class FakeSearch:
    """q1: correct at rank 1, q2: correct at rank 2, q3: never correct."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, query: str, **kw: Any) -> list[Hit]:
        self.calls.append({"query": query, **kw})
        good = "మొదటి దశలో రహదారులు, భవనాలు పూర్తి చేస్తామని"
        page = {"a": "P1", "b": "P2", "c": "P0"}[query]
        hits = [Hit("x", "x", 1.0, ["P8"], good), Hit("y", "y", 0.5, [page], good)]
        return hits[1:] if query == "a" else hits


def test_run_retrieval_report(tmp_path: Path):
    set_file, out = tmp_path / "set.jsonl", tmp_path / "rep.json"
    write_set(set_file)
    fake = FakeSearch()
    report = r.run_retrieval(set_file=set_file, out=out, search_fn=fake)
    assert report["n"] == 3
    assert report["recall_at_10"] == round(2 / 3, 4)
    assert report["mrr"] == round((1 + 0.5) / 3, 4)
    assert report["by_lang"]["te"] == {"n": 2, "recall_at_10": 0.5, "mrr": 0.5}
    assert report["by_lang"]["en"] == {"n": 1, "recall_at_10": 1.0, "mrr": 0.5}
    assert json.loads(out.read_text()) == report
    assert set(report) == {"store", "set", "n", "recall_at_10", "mrr", "median_search_ms",
                           "by_lang"}  # fmt: skip
    assert fake.calls[0]["k"] == 10 and fake.calls[0]["filters"] is None
    assert r.summary_line(report).endswith(" n=3")


def test_only_window(tmp_path: Path):
    set_file = tmp_path / "set.jsonl"
    write_set(set_file)
    fake = FakeSearch()
    report = r.run_retrieval(
        set_file=set_file, only_window=True, out=tmp_path / "o.json", search_fn=fake
    )
    assert report["n"] == 2 and [c["query"] for c in fake.calls] == ["a", "c"]
    assert report["by_lang"]["en"]["n"] == 0


def test_restrict_pages_and_overrides_passed(tmp_path: Path):
    set_file, pages = tmp_path / "set.jsonl", tmp_path / "pages.txt"
    write_set(set_file)
    pages.write_text("P1\n\nP2\n")
    fake = FakeSearch()
    r.run_retrieval(
        set_file=set_file,
        restrict_pages=pages,
        qdrant_collection="cand",
        embed_profile="cand-model",
        out=tmp_path / "o.json",
        search_fn=fake,
    )
    assert fake.calls[0]["filters"] == {"page_ids": ["P1", "P2"]}
    assert fake.calls[0]["qdrant_collection"] == "cand"
    assert fake.calls[0]["embed_profile"] == "cand-model"
    with pytest.raises(ValueError, match="store new only"):
        r.run_retrieval(set_file=set_file, store="baseline", restrict_pages=pages, search_fn=fake)


def test_embed_profile_flag(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from newsrag.evals import cli

    seen: dict[str, Any] = {}

    def fake_run(**kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {"recall_at_10": 0.5, "mrr": 0.25, "median_search_ms": 12.0, "n": 2}

    monkeypatch.setattr(r, "run_retrieval", fake_run)
    result = CliRunner().invoke(
        cli.app, ["retrieval", "--embed-profile", "cand", "--qdrant-collection", "c"]
    )
    assert result.exit_code == 0, result.output
    assert seen["embed_profile"] == "cand" and seen["qdrant_collection"] == "c"
    assert result.output.strip() == "recall@10=0.5000 mrr=0.2500 median_search_ms=12.0 n=2"
