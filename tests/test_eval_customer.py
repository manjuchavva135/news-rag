"""Ticket 028: the customer-question answer check, with fake search and fake models."""

from __future__ import annotations

import contextlib
import json
import re
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.evals import customer as c
from newsrag.search.hybrid import Hit

QUESTIONS = [
    {"id": "Q01", "english": "Mining leases", "telugu": "మైనింగ్ లీజులు ఎన్ని?"},
    {"id": "Q02", "english": "Debt", "telugu": "అప్పులు ఎంత?"},
]


@pytest.fixture
def reports(tmp_path, monkeypatch):
    current = config.settings.model_copy(
        update={
            "customer_answers_file": tmp_path / "customer-answers.json",
            "customer_answers_md_file": tmp_path / "customer-answers.md",
            "customer_signoff_file": tmp_path / "customer-answers-signoff.json",
            "customer_answer_retries": 2,
            "customer_judge_retries": 2,
            "customer_concurrency": 2,
        }
    )
    monkeypatch.setattr(config, "settings", current)
    return tmp_path


def hit(n: int, story: str = "S1", text: str = "మూల వచనం") -> Hit:
    return Hit(
        chunk_id=f"C{n}",
        story_id=story,
        score=1.0 / n,
        page_ids=[f"P{n}"],
        text=text,
        headline=f"శీర్షిక {n}",
        pub_date="2026-03-01",
    )


class FakeClient:
    """`answers[qtext]` is the answer (or an exception / a list of them, one per try); the judge
    says `supported` when the claim text is in `supports[source n]` and `factual` unless the unit
    is in `meta`."""

    def __init__(self, answers=None, supports=None, meta=(), judge_reply=None):
        self.answers = answers or {}
        self.supports = supports or {}
        self.meta = set(meta)
        self.judge_reply = judge_reply
        self.prompts: list[str] = []
        self.tries: dict[str, int] = {}

    def chat(self, prompt, **params):
        self.prompts.append(prompt)
        if "Sources:" in prompt and "Question:" in prompt:
            q = prompt.split("Question:\n", 1)[1].split("\n", 1)[0]
            self.tries[q] = self.tries.get(q, 0) + 1
            reply = self.answers[q]
            if isinstance(reply, list):
                reply = reply[min(self.tries[q], len(reply)) - 1]
            if isinstance(reply, Exception):
                raise reply
            return reply
        if self.judge_reply is not None:
            return self.judge_reply
        if "Claim:" in prompt:
            claim = prompt.split("Claim:\n", 1)[1].split("\n\nSource:", 1)[0].strip()
            n = int(re.search(r"Source:\n\[(\d+)\]", prompt).group(1))
            ok = any(claim.startswith(x) for x in self.supports.get(n, ()))
            return json.dumps({"label": "supported" if ok else "not_supported"})
        unit = prompt.split("Sentence:\n", 1)[1].strip()
        return json.dumps({"label": "not_factual" if unit in self.meta else "factual"})


class Opener:
    def __init__(self, client):
        self.client = client
        self.events: list[str] = []
        self.open: list[str] = []
        self.max_open = 0

    @contextlib.contextmanager
    def __call__(self, profile):
        self.open.append(profile)
        self.max_open = max(self.max_open, len(self.open))
        self.events.append(f"open {profile}")
        try:
            yield self.client
        finally:
            self.open.remove(profile)
            self.events.append(f"close {profile}")


def run(client, hits_by_q, opener=None, **kw):
    opener = opener or Opener(client)
    calls = []

    def search_fn(query, k):
        calls.append((query, list(opener.open)))
        return hits_by_q.get(query, [])[:k]

    report = c.run_customer(
        k=12,
        questions=kw.pop("questions", QUESTIONS),
        search_fn=search_fn,
        opener=opener,
        story_meta=lambda ids: {i: {"page_id": f"{i}-p1", "editions": ["E1"]} for i in ids},
        covered_years=kw.pop("covered_years", [2026]),
        embed_profile="bge-m3",
        old_answers=kw.pop("old_answers", {}),
        today=date(2026, 11, 11),
        echo=lambda _m: None,
        **kw,
    )
    return report, opener, calls


# --- FR-1 ---------------------------------------------------------------------------------------


def test_questions_copied():
    qs = c.load_questions(Path(config.settings.repo_root) / "data/eval/customer-questions.json")
    assert [q["id"] for q in qs] == [f"Q{i:02d}" for i in range(1, 31)]


# --- units and citations ------------------------------------------------------------------------


def test_split_units_rules():
    answer = (
        "మొత్తం 2.5 కోట్లు ఖర్చు చేశారు [1]. రెండో వాక్యం [2]? మూడవది!\n"
        "* బుల్లెట్ ఒకటి. ఇంకా అదే బుల్లెట్ [3]\n"
        "| ఖనిజం | లీజులు |\n| :--- | :--- |\n| మైకా | 2 [1] |\n"
        "ముగింపు వాక్యం. [4]"
    )
    units = c.split_units(answer)
    assert units == [
        "మొత్తం 2.5 కోట్లు ఖర్చు చేశారు [1].",
        "రెండో వాక్యం [2]?",
        "మూడవది!",
        "* బుల్లెట్ ఒకటి. ఇంకా అదే బుల్లెట్ [3]",
        "| ఖనిజం | లీజులు |",
        "| మైకా | 2 [1] |",
        "ముగింపు వాక్యం. [4]",
    ]
    assert c.citations("a [1][3] b [1, 2]") == [1, 3, 2]


def test_judge_family_differs(monkeypatch):
    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"judge_model": "qwen3-vl-32b"})
    )
    with pytest.raises(c.FamilyError, match="must be different families"):
        c.check_families()
    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"answer_judge_model": "gemma3-27b"})
    )
    c.check_families()  # answer_judge_model wins over judge_model
    assert c.judge_profile() == "gemma3-27b"


def test_family_check_exit_2(monkeypatch, reports):
    from newsrag.cli import app

    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"judge_model": "qwen3-vl-32b"})
    )
    result = CliRunner().invoke(app, ["eval", "customer"])
    assert result.exit_code == 2
    assert "judge_model and answer_model must be different families" in result.output


# --- the claim check ----------------------------------------------------------------------------


def test_supported_pct_formula(reports):
    client = FakeClient(
        answers={
            QUESTIONS[0]["telugu"]: "ఒకటి [1]. రెండు [2]. మూడు [1]. నాలుగు [2].",
            QUESTIONS[1]["telugu"]: "ఐదు [1].",
        },
        supports={1: ["ఒకటి", "మూడు", "ఐదు"]},
    )
    hits = {q["telugu"]: [hit(1), hit(2)] for q in QUESTIONS}
    report, _, _ = run(client, hits)
    assert report["n_claims"] == 5
    assert report["supported_pct"] == 60.0  # 3 of 5
    per = {q["id"]: q for q in report["per_question"]}
    assert per["Q01"]["n_supported"] == 2 and per["Q02"]["n_supported"] == 1


def test_supported_pct_zero_without_claims(reports):
    client = FakeClient(answers={q["telugu"]: "మూలాల్లో సమాచారం లేదు." for q in QUESTIONS})
    report, _, _ = run(client, {q["telugu"]: [hit(1)] for q in QUESTIONS})
    assert report["n_claims"] == 0 and report["supported_pct"] == 0.0


def test_uncited_counted_separately(reports):
    meta = "ఈ అంశంపై మూలాల్లో సమాచారం లేదు."
    client = FakeClient(
        answers={QUESTIONS[0]["telugu"]: f"ఒకటి [1]. ముఖ్యమంత్రి 3 పర్యటనలు చేశారు. {meta}"},
        supports={1: ["ఒకటి"]},
        meta=[meta],
    )
    report, _, _ = run(client, {QUESTIONS[0]["telugu"]: [hit(1)]}, questions=QUESTIONS[:1])
    assert report["n_claims"] == 1 and report["supported_pct"] == 100.0
    assert report["uncited"] == 1  # the factual one; the meta sentence is not counted


def test_multi_citation_any_supports(reports):
    client = FakeClient(
        answers={QUESTIONS[0]["telugu"]: "ఒకటి [1][2]. రెండు [1][2]."},
        supports={2: ["ఒకటి"]},
    )
    report, _, _ = run(client, {QUESTIONS[0]["telugu"]: [hit(1), hit(2)]}, questions=QUESTIONS[:1])
    units = report["per_question"][0]["units"]
    assert [u["verdict"] for u in units] == ["supported", "not_supported"]
    assert units[0]["per_source"] == {"1": "not_supported", "2": "supported"}


def test_out_of_range_citation_not_supported(reports):
    client = FakeClient(
        answers={QUESTIONS[0]["telugu"]: "ఒకటి [7]. రెండు [0][1]."},
        supports={1: ["ఒకటి", "రెండు"]},
    )
    report, _, _ = run(client, {QUESTIONS[0]["telugu"]: [hit(1)]}, questions=QUESTIONS[:1])
    units = report["per_question"][0]["units"]
    assert units[0]["verdict"] == "not_supported"
    assert units[0]["per_source"] == {"7": "out_of_range"}
    assert units[1]["verdict"] == "supported"  # [0] is out of range, [1] supports it


def test_unparseable_judge_counts_not_supported(reports):
    client = FakeClient(answers={QUESTIONS[0]["telugu"]: "ఒకటి [1]."}, judge_reply="no json")
    report, _, _ = run(client, {QUESTIONS[0]["telugu"]: [hit(1)]}, questions=QUESTIONS[:1])
    assert report["n_claims"] == 1 and report["supported_pct"] == 0.0
    judge_calls = [p for p in client.prompts if "Claim:" in p]
    assert len(judge_calls) == 3  # one try + 2 retries


def test_failed_question_counted(reports):
    q1, q2 = QUESTIONS[0]["telugu"], QUESTIONS[1]["telugu"]
    client = FakeClient(
        answers={
            q1: [RuntimeError("boom")] * 3,
            q2: [RuntimeError("once"), "ఐదు [1]."],
        },
        supports={1: ["ఐదు"]},
    )
    report, _, _ = run(client, {q1: [hit(1)], q2: [hit(1)]})
    per = {q["id"]: q for q in report["per_question"]}
    assert per["Q01"]["status"] == "failed" and "boom" in per["Q01"]["error"]
    assert client.tries[q1] == 3  # one try + 2 retries
    assert per["Q02"]["status"] == "answered" and client.tries[q2] == 2
    assert report["n_failed"] == 1
    assert report["n_claims"] == 1 and report["supported_pct"] == 100.0


def test_no_hits_answer(reports):
    client = FakeClient(answers={QUESTIONS[1]["telugu"]: "ఐదు [1]."}, supports={1: ["ఐదు"]})
    report, _, _ = run(client, {QUESTIONS[1]["telugu"]: [hit(1)]})
    q = report["per_question"][0]
    assert q["status"] == "no_sources" and q["answer"] == c.NO_SOURCES
    assert q["n_claims"] == 0 and q["units"] == []
    assert not any(QUESTIONS[0]["telugu"] in p for p in client.prompts)


# --- passes and models --------------------------------------------------------------------------


def test_one_model_at_a_time(reports):
    client = FakeClient(answers={q["telugu"]: "ఒకటి [1]." for q in QUESTIONS}, supports={1: ["ఒకటి"]})
    report, opener, _ = run(client, {q["telugu"]: [hit(1)] for q in QUESTIONS})
    assert opener.max_open == 1
    assert opener.events == [
        "open bge-m3",
        "close bge-m3",
        f"open {config.settings.answer_model}",
        f"close {config.settings.answer_model}",
        f"open {c.judge_profile()}",
        f"close {c.judge_profile()}",
    ]
    assert report["answer_model"] != report["judge_model"]


def test_single_embed_scope(reports):
    client = FakeClient(answers={q["telugu"]: "ఒకటి [1]." for q in QUESTIONS}, supports={1: ["ఒకటి"]})
    _, opener, calls = run(client, {q["telugu"]: [hit(1)] for q in QUESTIONS})
    assert [q for q, _ in calls] == [q["telugu"] for q in QUESTIONS]
    assert all(open_ == ["bge-m3"] for _, open_ in calls)  # every search inside the one scope
    assert opener.events.count("open bge-m3") == 1


def test_editions_union():
    rows = [
        {"story_id": "S1", "canonical_page_id": "P1", "edition_names": ["Guntur", "Krishna"]},
        {"story_id": "S1", "canonical_page_id": "P1", "edition_names": ["Krishna", "Nellore"]},
        {"story_id": "S2", "canonical_page_id": None, "edition_names": None},
    ]
    meta = c.story_meta_from_rows(rows)
    assert meta["S1"] == {"page_id": "P1", "editions": ["Guntur", "Krishna", "Nellore"]}
    assert meta["S2"] == {"page_id": "", "editions": []}
    src = c.make_sources([hit(1, "S1", "x" * 2000)], meta)[0]
    assert src.header() == "[1] శీర్షిక 1 | 2026-03-01 | Guntur, Krishna, Nellore | page P1"
    assert len(src.text) == config.settings.customer_source_chars == 1500


def test_coverage_note_meta(reports):
    note = "2024, 2025 సంవత్సరాల సమాచారం ఆర్కైవ్‌లో లేదు."
    q = QUESTIONS[0]["telugu"]
    client = FakeClient(answers={q: f"ఒకటి [1]. {note}"}, supports={1: ["ఒకటి"]}, meta=[note])
    report, _, _ = run(client, {q: [hit(1)]}, questions=QUESTIONS[:1], covered_years=[2026])
    prompt = next(p for p in client.prompts if "Question:" in p)
    assert "Today is 2026-11-11" in prompt and "these years: 2026." in prompt
    assert "[1] శీర్షిక 1 | 2026-03-01 | E1 | page S1-p1" in prompt
    note_unit = next(u for u in report["per_question"][0]["units"] if not u["cited"])
    assert note_unit["verdict"] == "not_factual"
    assert report["uncited"] == 0


# --- old answers, outputs and the sign-off ------------------------------------------------------

OLD_MD = """# Customer sample questions — RAG answers

Generated 2026-07-11.

## Q01 — Mining tenders

**ప్రశ్న:** గత రెండేళ్లలో?

పాత సమాధానం ఒకటి [1].

### ఉపశీర్షిక
* వివరాలు [2].

**Sources**

- [1] 2026-05-04 — ap-b#p05 — శీర్షిక (**cited**)

## Q02 — Debt

**ప్రశ్న:** అప్పులు?

పాత సమాధానం రెండు.

**Sources**

- [1] x
"""


def test_old_answer_extracted():
    old = c.extract_old_answers(OLD_MD)
    assert set(old) == {"Q01", "Q02"}
    assert old["Q01"] == "పాత సమాధానం ఒకటి [1].\n\n### ఉపశీర్షిక\n* వివరాలు [2]."
    assert old["Q02"] == "పాత సమాధానం రెండు."


def test_real_old_results_have_all_questions():
    path = config.settings.customer_old_results_file
    if not Path(path).exists():
        pytest.skip("old results not on this machine")
    old = c.read_old_answers(path)
    assert [f"Q{i:02d}" for i in range(1, 31)] == sorted(old)
    assert all(old.values())


def test_outputs_written(reports):
    client = FakeClient(
        answers={q["telugu"]: "## ఒక శీర్షిక\nఒకటి [1]." for q in QUESTIONS}, supports={1: ["ఒకటి"]}
    )
    old = c.extract_old_answers(OLD_MD)
    report, _, _ = run(client, {q["telugu"]: [hit(1)] for q in QUESTIONS}, old_answers=old)
    data = json.loads((reports / "customer-answers.json").read_text())
    for key in ("run_id", "supported_pct", "n_claims", "uncited", "n_failed", "per_question"):
        assert key in data
    assert len(data["per_question"]) == 2
    md = (reports / "customer-answers.md").read_text()
    assert len(re.findall(r"^## Q", md, re.MULTILINE)) == 2  # answers' headings are quoted
    assert "### Old answer" in md and "> పాత సమాధానం ఒకటి [1]." in md
    assert "- [1] శీర్షిక 1 | 2026-03-01 | E1 | S1-p1" in md
    assert c.summary_line(report).startswith("supported_pct=100.0 n_claims=2 uncited=")


def test_new_run_retires_signoff(reports):
    signoff = reports / "customer-answers-signoff.json"
    signoff.write_text('{"name": "old"}')
    client = FakeClient(answers={q["telugu"]: "ఒకటి [1]." for q in QUESTIONS}, supports={1: ["ఒకటి"]})
    run(client, {q["telugu"]: [hit(1)] for q in QUESTIONS})
    assert not signoff.exists()
    assert json.loads((reports / "customer-answers-signoff.json.old").read_text()) == {
        "name": "old"
    }


def test_signoff_record(reports):
    from newsrag.cli import app

    result = CliRunner().invoke(app, ["eval", "customer-signoff", "--name", "x"])
    assert result.exit_code == 1 and "no customer answers to sign" in result.output
    answers = reports / "customer-answers.json"
    answers.write_text(json.dumps({"run_id": "R1", "supported_pct": 91.5}))
    result = CliRunner().invoke(
        app, ["eval", "customer-signoff", "--name", "reader", "--note", "ok"]
    )
    assert result.exit_code == 0, result.output
    record = json.loads((reports / "customer-answers-signoff.json").read_text())
    assert set(record) == {"name", "note", "signed_at", "supported_pct", "answers_sha1", "run_id"}
    assert record["name"] == "reader" and record["run_id"] == "R1"
    assert record["supported_pct"] == 91.5
    import hashlib

    assert record["answers_sha1"] == hashlib.sha1(answers.read_bytes()).hexdigest()
