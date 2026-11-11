"""Ticket 034 FR-2: Romanized Telugu -> Telugu script, with fakes for the model."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from newsrag.search import translit as tr

TE = "తిరుమలలో దర్శనం టోకెన్లు ఎప్పుడు ఇస్తారు"


def test_translit_ok():
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return json.dumps({"telugu": TE}, ensure_ascii=False)

    result = tr.to_telugu("Tirumala lo darshanam tokens eppudu istharu", ask)
    assert result == tr.Translit("Tirumala lo darshanam tokens eppudu istharu", TE, False)
    assert len(asked) == 1 and "Tirumala lo darshanam" in asked[0]


def test_translit_three_calls_then_fallback():
    calls = []

    def ask(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"telugu": "Tirumala lo darshanam"})  # still Latin letters

    result = tr.to_telugu("Tirumala lo darshanam", ask)
    assert len(calls) == 3  # retrieval_max_tries calls in total
    assert result.failed and result.text == "Tirumala lo darshanam"


def test_translit_request_errors_count_as_tries():
    calls = []

    def ask(prompt: str) -> str:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("server down")
        return json.dumps({"telugu": TE}, ensure_ascii=False)

    result = tr.to_telugu("x lo", ask)
    assert not result.failed and result.text == TE and len(calls) == 3


def test_translit_many_keeps_order():
    def ask(prompt: str) -> str:
        q = prompt.rsplit("Query:\n", 1)[1].strip()
        return json.dumps({"telugu": f"{TE} {len(q)}"}, ensure_ascii=False)

    out = tr.to_telugu_many(["a", "bbb", "cc"], ask)
    assert [t.text for t in out] == [f"{TE} 1", f"{TE} 3", f"{TE} 2"]
    assert tr.to_telugu_many([], ask) == []


def test_search_cli_translit(monkeypatch):
    from newsrag import search as search_mod
    from newsrag.cli import app
    from newsrag.search.hybrid import Hit

    seen = []
    monkeypatch.setattr(tr, "to_telugu_many", lambda qs, ask=None: [tr.Translit(qs[0], TE, False)])

    def fake_search(query, **kw):
        seen.append(query)
        return [Hit("c1", "s1", 0.5, ["P1"], "t", "శీర్షిక", "2026-10-08")]

    monkeypatch.setattr(search_mod, "search", fake_search)
    monkeypatch.setattr(search_mod, "close", lambda: None)
    result = CliRunner().invoke(app, ["search", "Tirumala lo tokens", "--translit", "--k", "1"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == f"translit: {TE}"
    assert seen == [TE]

    monkeypatch.setattr(
        tr, "to_telugu_many", lambda qs, ask=None: [tr.Translit(qs[0], qs[0], True)]
    )
    result = CliRunner().invoke(app, ["search", "Tirumala lo tokens", "--translit"])
    assert result.output.splitlines()[0] == "translit: failed, searching as typed"
    assert seen[-1] == "Tirumala lo tokens"
