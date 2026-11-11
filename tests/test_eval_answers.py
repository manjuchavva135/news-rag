"""Ticket 034 FR-4/FR-5: the graded answer check and the report, with fake search and models."""

from __future__ import annotations

import contextlib
import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.evals import answers as a
from newsrag.evals import retrieval as rt
from newsrag.models.client import ModelBusyError
from newsrag.search.hybrid import Hit

GOLD = "తిరుమల టోకెన్లు\nజనవరి 9న ఉదయం 5 గంటల నుంచి టోకెన్లు జారీ చేస్తారు."
TRANSLIT = "తిరుమలలో టోకెన్లు ఎప్పుడు"
SET = [
    {"qid": "q0001", "lang": "te", "question": "తిరుమల టోకెన్లు ఎప్పుడు?", "gold_page_ids": ["P1"]},
    {"qid": "q0002", "lang": "en", "question": "When are the tokens issued?", "gold_page_ids": ["P1"]},
    {"qid": "q0003", "lang": "te_latn", "question": "Tirumala lo tokens eppudu?",
     "gold_page_ids": ["P1"]},
    {"qid": "q0004", "lang": "en", "question": "Nothing matches this", "gold_page_ids": ["P1"]},
]  # fmt: skip


@pytest.fixture
def files(tmp_path, monkeypatch):
    current = config.settings.model_copy(
        update={
            "eval_2026_set_file": tmp_path / "set.jsonl",
            "eval_2026_retrieval_file": tmp_path / "retrieval-2026.json",
            "eval_2026_answers_file": tmp_path / "answers-2026.json",
            "eval_2026_report_file": tmp_path / "eval-2026.md",
            "customer_answer_retries": 2,
            "customer_judge_retries": 2,
            "customer_concurrency": 2,
        }
    )
    monkeypatch.setattr(config, "settings", current)
    set_file = tmp_path / "set.jsonl"
    rows = [{**q, "story_id": "s1", "gold_text": GOLD, "pub_date": "2026-10-08",
             "in_baseline_window": False} for q in SET]  # fmt: skip
    set_file.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
    retrieval = {
        "set": str(set_file),
        "by_lang": {"te_latn_translit": {"n": 1}},
        "per_question": [
            {"qid": "q0003", "lang": "te_latn", "query": SET[2]["question"],
             "translit_query": TRANSLIT}
        ],
    }  # fmt: skip
    (tmp_path / "retrieval-2026.json").write_text(json.dumps(retrieval, ensure_ascii=False))
    return tmp_path


class FakeClient:
    """Answers `answer of <question>`; grades from `grades[question]` (a label, or raw reply)."""

    def __init__(self, grades=None, answer_error=None):
        self.grades = grades or {}
        self.answer_error = answer_error
        self.prompts: list[str] = []

    def chat(self, prompt, **params):
        self.prompts.append(prompt)
        q = prompt.split("Question:\n", 1)[1].split("\n", 1)[0]
        if "Sources:" in prompt:
            if self.answer_error is not None and q == self.answer_error:
                raise RuntimeError("model down")
            return f"answer of {q} [1]"
        reply = self.grades.get(q, "correct")
        return reply if reply.startswith("not json") else json.dumps({"label": reply})


class Opener:
    def __init__(self, client):
        self.client = client
        self.events: list[str] = []
        self.held: str | None = None

    @contextlib.contextmanager
    def __call__(self, profile):
        if self.held is not None:
            raise ModelBusyError(self.held, profile)  # like models.use: one profile at a time
        self.held = profile
        self.events.append(profile)
        try:
            yield self.client
        finally:
            self.held = None


def good_hit():
    return Hit("c1", "s1", 1.0, ["P1"], GOLD.split("\n", 1)[1], "తిరుమల టోకెన్లు", "2026-10-08")


def run(client, **kw):
    searched: list[str] = []

    def search_fn(query, k):
        searched.append(query)
        return [] if query == "Nothing matches this" else [good_hit()]

    opener = Opener(client)
    report = a.run_answers(
        search_fn=search_fn,
        opener=opener,
        story_meta=lambda ids: {i: {"page_id": "P1", "editions": ["E1"]} for i in ids},
        covered_years=[2026],
        embed_profile="e5",
        today=date(2026, 11, 11),
        echo=lambda _m: None,
        **kw,
    )
    return report, opener, searched


def test_grade_counts_and_pcts(files):
    client = FakeClient(grades={"తిరుమల టోకెన్లు ఎప్పుడు?": "partial"})
    report, opener, searched = run(client)
    assert opener.events[0] == "e5" and len(opener.events) == 3  # embed, answer, judge
    assert TRANSLIT in searched and "Tirumala lo tokens eppudu?" not in searched
    by = {it["qid"]: it for it in report["items"]}
    assert by["q0003"]["query"] == TRANSLIT and by["q0003"]["query_typed"] == SET[2]["question"]
    assert by["q0004"]["grade"] == "wrong" and by["q0004"]["no_sources"] is True
    assert by["q0001"]["grade"] == "partial" and by["q0001"]["retrieved_gold"] is True
    assert report["n"] == 4 and report["n_failed"] == 0
    assert report["overall"] == {"correct_pct": 50.0, "partial_pct": 25.0, "wrong_pct": 25.0}
    assert report["by_lang"]["en"]["correct_pct"] == 50.0
    assert sorted(report["by_lang"]) == ["en", "te", "te_latn"]
    # no judge call for the no-hits question
    assert not any("Nothing matches this" in p for p in client.prompts)
    assert Path(files / "answers-2026.md").exists()


def test_unparseable_grade_is_null_and_failed(files):
    client = FakeClient(grades={TRANSLIT: "not json at all"})
    report, _, _ = run(client)
    item = next(it for it in report["items"] if it["qid"] == "q0003")
    assert item["grade"] is None and report["n_failed"] == 1
    judge_calls = [p for p in client.prompts if "Story:" in p and TRANSLIT in p]
    assert len(judge_calls) == 3  # 1 + customer_judge_retries


def test_failed_answer_counts_in_n_failed(files):
    report, _, _ = run(FakeClient(answer_error="When are the tokens issued?"))
    item = next(it for it in report["items"] if it["qid"] == "q0002")
    assert item["grade"] is None and "model down" in item["error"]
    assert report["n_failed"] == 1


def test_retrieval_without_translit_exits_1(files):
    (files / "retrieval-2026.json").write_text(json.dumps({"set": str(files / "set.jsonl")}))
    from newsrag.cli import app

    result = CliRunner().invoke(app, ["eval", "answers"])
    assert result.exit_code == 1
    assert "no transliterated queries (run eval retrieval --translit)" in result.output


def test_retrieval_for_another_set_rejected(files):
    data = json.loads((files / "retrieval-2026.json").read_text())
    data["set"] = "/elsewhere/set.jsonl"
    (files / "retrieval-2026.json").write_text(json.dumps(data))
    with pytest.raises(a.InputError, match="retrieval report is for"):
        run(FakeClient())


def test_grade_prompt_cuts_gold(files, monkeypatch):
    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"retrieval_story_chars": 10})
    )
    p = a.grade_prompt("q", "ans", "x" * 50)
    assert p.rstrip().endswith("x" * 10) and "x" * 11 not in p


# --- FR-5 the report --------------------------------------------------------------------------


def write_inputs(files: Path) -> None:
    set_file = files / "set.jsonl"
    rt.meta_file(set_file).write_text(
        json.dumps(
            {"n": 4, "by_lang": {"te": 1, "en": 2, "te_latn": 1}, "seed": 7,
             "from_date": "2026-01-01", "stories_available": 50, "writer_model": "qwen3-30b-a3b",
             "strata": {"2026-10": 2, "2026-11": 2}, "built_at": "2026-11-12T00:00:00+00:00"}
        )
    )  # fmt: skip
    metric = {"n": 1, "recall_at_1": 1.0, "recall_at_5": 1.0, "recall_at_10": 1.0, "mrr": 1.0}
    retrieval = {
        "set": str(set_file),
        "k": 10,
        "hit_overlap": 0.3,
        "ngram": 5,
        "generation": "multilingual-e5-large-instruct__v1",
        "translit_model": "qwen3-30b-a3b",
        "by_lang": {"te": metric, "en": {**metric, "n": 2}, "te_latn": metric,
                    "te_latn_translit": metric},
        "per_question": [
            {"qid": "q0001", "lang": "te", "query": "తిరుమల", "rank": 1, "top1_headline": "h"},
            {"qid": "q0003", "lang": "te_latn", "query": "Tirumala lo", "rank": None,
             "top1_headline": "x", "translit_query": TRANSLIT, "rank_translit": 1,
             "top1_headline_translit": "h"},
        ],
    }  # fmt: skip
    (files / "retrieval-2026.json").write_text(json.dumps(retrieval, ensure_ascii=False))
    pcts = {"n": 1, "correct_pct": 100.0, "partial_pct": 0.0, "wrong_pct": 0.0}
    answers = {"answer_model": "qwen3-30b-a3b", "judge_model": "sarvam-m-24b", "k": 12,
               "n_failed": 0, "by_lang": {"te": pcts, "en": pcts, "te_latn": pcts}}  # fmt: skip
    (files / "answers-2026.json").write_text(json.dumps(answers))


def test_report_2026_sections_and_rows(files):
    write_inputs(files)
    from newsrag.cli import app

    result = CliRunner().invoke(app, ["eval", "report-2026"])
    assert result.exit_code == 0, result.output
    text = (files / "eval-2026.md").read_text()
    assert "## How it was measured" in text and "## Examples" in text
    rows = [ln for ln in text.splitlines() if ln.startswith("| te")]
    assert [ln.split("|")[1].strip() for ln in rows] == [
        "te",
        "te_latn",
        "te_latn (transliterated)",
    ]
    latn = next(ln for ln in rows if ln.startswith("| te_latn |"))
    assert latn.rstrip().endswith("| - | - | - |")  # answers go on the transliterated row
    assert "100.00" in next(ln for ln in rows if "transliterated" in ln)
    assert "(only 0)" in text  # fewer than 3 examples are shown with a note
    for word in ("5-grams", "0.3", "sarvam-m-24b", "synthetic", "2026-01-01"):
        assert word in text


def test_report_2026_missing_input_exits_1(files):
    from newsrag.cli import app

    result = CliRunner().invoke(app, ["eval", "report-2026"])
    assert result.exit_code == 1
    assert "missing" in result.output and ".meta.json" in result.output
