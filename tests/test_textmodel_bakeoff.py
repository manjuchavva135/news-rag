"""Ticket 032: the text model bake-off (CPU only, scripted fake models, no ledger, no GPU)."""

from __future__ import annotations

import contextlib
import json
import re

import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.models import ModelStartError
from newsrag.textmodel import bakeoff as bk

TELUGU = "తెలుగు వార్త ఇక్కడ ఉంది. "
STORY_TEXT = "హైదరాబాద్: ముఖ్యమంత్రి కృష్ణారావు ఈరోజు ప్రభుత్వం రూ.5 కోట్ల నిధులు ఇచ్చిందని చెప్పారు. " + TELUGU * 20
HEADLINE = "కృష్ణారావు ప్రకటన"

# a word per candidate in its summaries, so that a fake judge can tell whose work it grades
MARK = {
    "gemma3-27b": "అమ్మ",
    "gemma4-31b": "ఆవు",
    "qwen3-30b-a3b": "ఇల్లు",
    "sarvam-m-24b": "ఈగ",
}
FAMILY = {
    "gemma3-27b": "gemma",
    "gemma4-31b": "gemma",
    "qwen3-30b-a3b": "qwen",
    "sarvam-m-24b": "mistral",
}
NAMES = list(MARK)


def story(i: int, day: str = "2025-06-10", text: str = STORY_TEXT) -> dict:
    return {"pub_date": day, "story_id": f"s_{i:03d}", "headline": HEADLINE, "text": text}


POOL = [story(i) for i in range(6)] + [
    story(10, text="short " * 10),  # too short
    story(11, text="English only text. " * 40),  # not Telugu
    story(12, text=TELUGU * 400),  # too long
]


class Fake:
    """A scripted model: replies by task, tells which task from the prompt."""

    def __init__(self, name, *, invent=False, english=False, garbage=False, grades=None):
        self.profile = name
        self.invent, self.english, self.garbage = invent, english, garbage
        self.grades = grades or {}
        self.calls = []  # (task, subject) of every request

    def chat(self, messages, **params):
        assert params.get("temperature") == 0
        assert params.get("extra_body") == bk.THINK_OFF
        text = "\n".join(m["content"] for m in messages)
        user = messages[-1]["content"]
        if "strict reviewer" in text:
            subject = next(n for n, w in MARK.items() if w in user)
            self.calls.append(("judge", subject))
            score = self.grades.get(subject, 4)
            part = {"correct": score, "natural": score}
            return json.dumps({"summary": part, "question": part, "names": part})
        if self.garbage and ('"category"' in text or '"facts"' in text):
            self.calls.append(("json", None))
            return "this is not json"
        if '"category"' in text:
            self.calls.append(("core", None))
            people = [{"name": "కృష్ణారావు", "role": "ముఖ్యమంత్రి"}]
            if self.invent:
                people.append({"name": "ఎవరో లేని వ్యక్తి", "role": None})
            return json.dumps(
                {
                    "category": "politics",
                    "people": people,
                    "organisations": [],
                    "event_date_expression": None,
                    "event_date": None,
                }
            )
        if '"facts"' in text:
            self.calls.append(("facts", None))
            fact = {
                "value_text": "రూ.5 కోట్ల",
                "value": 5,
                "unit": "INR",
                "scale": "crore",
                "refers_to": "నిధులు",
                "period_text": None,
                "place": None,
            }
            facts = [fact]
            if self.invent:
                facts.append({**fact, "value_text": "రూ.99 కోట్ల"})
            return json.dumps({"facts": facts})
        if "Write a summary" in text:
            self.calls.append(("summary", None))
            return "<think>hmm</think>" + (
                "This is an English summary."
                if self.english
                else f"ఈ వార్త {MARK[self.profile]} గురించి."
            )
        assert "Write ONE question" in text
        self.calls.append(("question", None))
        return "ఇది ఎవరి గురించి?" if not self.english else "Who is this about?"


@pytest.fixture
def set_settings(monkeypatch, tmp_path):
    import newsrag.stages.base as base_mod

    def apply(**kw):
        current = config.settings.model_copy(update=kw)
        for mod in (config, base_mod):
            monkeypatch.setattr(mod, "settings", current)
        return current

    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("# test pipeline\nsurya_version: 0.23.0\n")
    apply(
        work_dir=tmp_path / "work",
        reports_dir=tmp_path / "reports",
        pipeline_file=pipeline,
        textmodel_dir=tmp_path / "work" / "textmodel-bakeoff",
        textmodel_report_file=tmp_path / "reports" / "textmodel-bakeoff.md",
        textmodel_stories_file=tmp_path / "stories.txt",
        enrich_retry_backoff_s=(0.0,),
    )
    return apply


class Setup:
    """The fake models of one run and an opener that serves them."""

    def __init__(self, fakes=None, failing=()):
        self.fakes = {n: Fake(n) for n in NAMES}
        self.fakes.update(fakes or {})
        self.failing = set(failing)
        self.opened = []

    @contextlib.contextmanager
    def opener(self, name):
        self.opened.append(name)
        if name in self.failing:
            raise ModelStartError(f"{name} did not start")
        yield self.fakes[name]

    def run(self, stories=None, candidates=NAMES, **kw):
        stories = bk.choose_stories(POOL, 3, 1) if stories is None else stories
        return bk.run_bakeoff(candidates, stories, opener=self.opener, **kw)

    def judged_pairs(self):
        return [
            (n, subject) for n, f in self.fakes.items() for t, subject in f.calls if t == "judge"
        ]


# --- the stories (FR-2) ----------------------------------------------------------------------------


def test_eligible_needs_telugu_and_length(set_settings):
    assert bk.eligible(story(1))
    assert not bk.eligible(story(2, text="short " * 10))
    assert not bk.eligible(story(3, text="English only text. " * 40))
    assert not bk.eligible(story(4, text=TELUGU * 400))
    assert bk.telugu_share("abc తెలుగు 123") == pytest.approx(6 / 9)
    assert bk.telugu_share("123 ...") == 0.0


def test_choose_is_seeded_and_sorted(set_settings):
    pool = [story(i) for i in range(30)]
    a = bk.choose_stories(pool, 10, seed=21)
    assert a == bk.choose_stories(pool, 10, seed=21)
    assert a != bk.choose_stories(pool, 10, seed=22)
    assert len(a) == 10
    assert [x["story_id"] for x in a] == sorted(x["story_id"] for x in a)
    assert all(bk.eligible(x) for x in bk.choose_stories(POOL, 20, 1))
    assert len(bk.choose_stories(POOL, 20, 1)) == 6


def test_stories_file_written_then_reused(set_settings, tmp_path):
    path = tmp_path / "list.txt"
    first = bk.ensure_stories_file(path, 3, 21, pool=lambda: POOL)
    lines = path.read_text().splitlines()
    assert len(lines) == 3 and lines[0].startswith("2025-06-10 s_")
    # reused: another pool and seed do not change the list
    other = [story(i, day="2025-08-02") for i in range(50, 60)] + POOL
    again = bk.ensure_stories_file(path, 3, 99, pool=lambda: other)
    assert [x["story_id"] for x in again] == [x["story_id"] for x in first]
    # another n draws again
    echoes = []
    five = bk.ensure_stories_file(path, 5, 21, pool=lambda: POOL, echo=echoes.append)
    assert len(five) == 5 and len(path.read_text().splitlines()) == 5 and echoes
    # a listed story that left the dedup artifacts is an error, not a silent redraw
    with pytest.raises(bk.BakeoffError):
        bk.ensure_stories_file(path, 5, 21, pool=lambda: POOL[:2])


# --- the rules of judging (FR-3) --------------------------------------------------------------------


def test_no_self_judging(set_settings):
    setup = Setup()
    setup.run()
    pairs = setup.judged_pairs()
    assert pairs
    assert all(judge != subject for judge, subject in pairs)


def test_no_same_family_judging(set_settings):
    setup = Setup()
    setup.run()
    pairs = set(setup.judged_pairs())
    assert pairs
    assert all(FAMILY[j] != FAMILY[s] for j, s in pairs)
    # every different-family pair was judged, and gemma never judged gemma
    expected = {(j, s) for j in NAMES for s in NAMES if FAMILY[j] != FAMILY[s]}
    assert pairs == expected
    assert ("gemma3-27b", "gemma4-31b") not in pairs and ("gemma4-31b", "gemma3-27b") not in pairs
    assert bk.judges_for("gemma3-27b", NAMES, FAMILY) == ["qwen3-30b-a3b", "sarvam-m-24b"]


def test_judging_reloads_only_when_needed(set_settings):
    setup = Setup()
    setup.run()
    # 4 loads to generate, 3 more to grade what earlier models could not (the last one stays)
    assert len(setup.opened) == 7 and setup.opened.count("sarvam-m-24b") == 1


def test_judged_is_mean_over_judges_scaled(set_settings):
    fakes = {
        "qwen3-30b-a3b": Fake("qwen3-30b-a3b", grades={"gemma3-27b": 5}),
        "sarvam-m-24b": Fake("sarvam-m-24b", grades={"gemma3-27b": 3}),
    }
    rows, _, _ = Setup(fakes).run()
    row = next(r for r in rows if r.candidate == "gemma3-27b")
    assert row.judge_means == {
        "qwen3-30b-a3b": pytest.approx(1.0),
        "sarvam-m-24b": pytest.approx(0.5),
    }
    assert row.judged == pytest.approx(0.75)
    gemma4 = next(r for r in rows if r.candidate == "gemma4-31b")
    assert gemma4.judged == pytest.approx(0.75)  # a 4 from both: (4 - 1) / 4


def test_parse_scores():
    ok = {"summary": {"correct": 5, "natural": 4}, "question": {"correct": 3, "natural": 3},
          "names": {"correct": 1, "natural": 2}}  # fmt: skip
    assert bk.parse_scores(json.dumps(ok)) == [5, 4, 3, 3, 1, 2]
    assert bk.parse_scores("```json\n" + json.dumps(ok) + "\n```") == [5, 4, 3, 3, 1, 2]
    assert bk.parse_scores("nope") is None
    assert bk.parse_scores('{"summary": {"correct": 6, "natural": 1}}') is None
    bad = json.loads(json.dumps(ok))
    bad["names"]["natural"] = 0
    assert bk.parse_scores(json.dumps(bad)) is None
    bad["names"]["natural"] = True
    assert bk.parse_scores(json.dumps(bad)) is None
    assert bk.scale(1) == 0.0 and bk.scale(5) == 1.0 and bk.scale(3) == 0.5


def test_unusable_judge_replies_are_not_counted(set_settings):
    class Mute(Fake):
        def chat(self, messages, **params):
            if "strict reviewer" in messages[-1]["content"]:
                return "I cannot grade this"
            return super().chat(messages, **params)

    setup = Setup({"qwen3-30b-a3b": Mute("qwen3-30b-a3b")})
    rows, _, path = setup.run()
    by = {r.candidate: r for r in rows}
    assert "qwen3-30b-a3b" not in by["gemma3-27b"].judge_means
    assert by["gemma3-27b"].judged is not None  # the other judge still counts
    # a judge with no usable reply at all is noted and nothing is saved for it
    assert "qwen3-30b-a3b as judge: RuntimeError: judging: no usable reply" in path.read_text()
    assert not bk.judged_path("qwen3-30b-a3b", "gemma3-27b").exists()


# --- the objective scores ----------------------------------------------------------------------------


def test_objective_scores_clean(set_settings):
    rows, _, _ = Setup().run()
    for r in rows:
        assert r.scores == {
            "json_ok": 1.0,
            "names_verbatim": 1.0,
            "facts_verbatim": 1.0,
            "telugu_output": 1.0,
        }
        assert r.seconds_per_story is not None


def test_objective_scores_catch_bad_work(set_settings):
    bad = Fake("qwen3-30b-a3b", invent=True, english=True)
    rows, _, _ = Setup({"qwen3-30b-a3b": bad}).run()
    r = next(x for x in rows if x.candidate == "qwen3-30b-a3b")
    assert r.scores["names_verbatim"] == pytest.approx(0.5)  # one real, one invented name
    assert r.scores["facts_verbatim"] == pytest.approx(0.5)
    assert r.scores["telugu_output"] == 0.0
    assert r.scores["json_ok"] == 1.0


def test_garbage_json_lowers_json_ok(set_settings):
    rows, _, _ = Setup({"sarvam-m-24b": Fake("sarvam-m-24b", garbage=True)}).run()
    r = next(x for x in rows if x.candidate == "sarvam-m-24b")
    assert r.scores["json_ok"] == 0.0
    assert r.scores["names_verbatim"] == 0.0 and r.scores["facts_verbatim"] == 0.0


def test_thinking_is_stripped():
    assert bk.strip_thinking("<think>a\nb</think>\nహలో") == "హలో"
    assert bk.strip_thinking("a reasoning</think>హలో") == "హలో"
    assert bk.strip_thinking("హలో") == "హలో"


# --- the total, the winner (FR-3, FR-4) -----------------------------------------------------------------


def test_total_formula():
    objective = {"json_ok": 1.0, "names_verbatim": 0.8, "facts_verbatim": 0.6, "telugu_output": 0.4}
    assert bk.total_score(0.5, objective) == pytest.approx(0.5 * 0.5 + 0.125 * 2.8)
    assert bk.total_score(0.0, dict.fromkeys(objective, 0.0)) == 0.0
    assert bk.total_score(1.0, dict.fromkeys(objective, 1.0)) == pytest.approx(1.0)
    row = bk.Row("x", "f", scores=objective, judged=0.5)
    assert row.total == pytest.approx(bk.total_score(0.5, objective))
    assert bk.Row("x", "f", scores=objective).total is None


def R(name, total, judged, seconds):
    return bk.Ranked(name, FAMILY.get(name, "x"), total, judged, seconds)


def test_tie_breaks(set_settings):
    # a clear leader wins on total
    assert bk.pick_winner([R("a", 0.80, 0.1, 9), R("b", 0.70, 0.9, 1)]) == "a"
    # within 0.01 of the best: the higher judged wins even with the lower total
    assert bk.pick_winner([R("a", 0.800, 0.60, 1), R("b", 0.795, 0.70, 9)]) == "b"
    # outside 0.01 the judged score does not matter
    assert bk.pick_winner([R("a", 0.800, 0.60, 1), R("b", 0.780, 0.90, 9)]) == "a"
    # same judged: the faster one
    assert bk.pick_winner([R("a", 0.800, 0.70, 9), R("b", 0.795, 0.70, 3)]) == "b"
    assert bk.pick_winner([]) is None
    assert bk.rank([R("a", 0.5, 0.5, 1), R("b", 0.9, 0.5, 1), R("c", 0.7, 0.5, 1)]) == [
        "b",
        "c",
        "a",
    ]


def test_winner_in_report(set_settings):
    fakes = {"qwen3-30b-a3b": Fake("qwen3-30b-a3b", grades={n: 5 for n in NAMES})}
    setup = Setup(fakes)
    setup.fakes["gemma3-27b"].grades = {n: 1 for n in NAMES}
    rows, winner, path = setup.run()
    # nobody is judged better than the others' fixed grade; the winner line is consistent
    text = path.read_text(encoding="utf-8")
    assert text.rstrip().splitlines()[-1] == f"winner: {winner}"
    parsed_winner, items = bk.parse_report(text)
    assert parsed_winner == winner
    assert {i.candidate for i in items} == set(NAMES)
    best = max(r.total for r in rows)
    assert next(r for r in rows if r.candidate == winner).total == pytest.approx(best, abs=0.01)
    for column in ("json_ok", "names_verbatim", "facts_verbatim", "telugu_output", "judged",
                   "total", "seconds/story"):  # fmt: skip
        assert column in text
    for name in NAMES:
        assert f"| {name} |" in text


def test_failed_candidate_excluded(set_settings):
    setup = Setup(failing={"qwen3-30b-a3b"})
    rows, winner, path = setup.run()
    by = {r.candidate: r for r in rows}
    assert by["qwen3-30b-a3b"].status == bk.FAILED
    assert "did not start" in by["qwen3-30b-a3b"].reason
    assert winner not in (None, "qwen3-30b-a3b")
    assert "qwen3-30b-a3b" not in {p[0] for p in setup.judged_pairs()}  # it judged nobody
    assert all(s != "qwen3-30b-a3b" for _, s in setup.judged_pairs())
    text = path.read_text(encoding="utf-8")
    assert "| qwen3-30b-a3b | qwen | failed |" in text
    _, items = bk.parse_report(text)
    assert "qwen3-30b-a3b" not in {i.candidate for i in items}
    # the others were still judged, by the candidates of another family that did start
    assert by["gemma3-27b"].judge_means.keys() == {"sarvam-m-24b"}


def test_candidate_without_other_family_judge_is_not_a_winner(set_settings):
    rows, winner, path = Setup().run(candidates=["gemma3-27b", "gemma4-31b"])
    assert winner is None
    assert {r.status for r in rows} == {bk.UNJUDGED}
    assert path.read_text().rstrip().endswith("winner: none")


def test_no_finisher_means_no_winner(set_settings):
    rows, winner, path = Setup(failing=NAMES).run()
    assert winner is None and {r.status for r in rows} == {bk.FAILED}
    assert path.read_text().rstrip().endswith("winner: none")


# --- reuse ----------------------------------------------------------------------------------------------


def test_saved_outputs_are_reused(set_settings):
    first = Setup()
    rows1, winner1, _ = first.run()
    second = Setup()
    rows2, winner2, _ = second.run()
    assert second.opened == [] and winner2 == winner1
    assert [r.judged for r in rows2] == [r.judged for r in rows1]
    third = Setup()
    third.run(rerun=True)
    assert len(third.opened) == 7  # a rerun runs everything again
    # and a changed story list invalidates the saved outputs
    fourth = Setup()
    fourth.run(stories=bk.choose_stories(POOL, 2, 5))
    assert len(fourth.opened) == 7


# --- apply (FR-5) -----------------------------------------------------------------------------------------


REPORT = """# Text model Telugu bake-off (ticket 032)

{table}

winner: {winner}
"""


def report(winner, rows):
    lines = [bk.TABLE_HEADER, "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, status, judged, total, seconds in rows:
        lines.append(
            f"| {name} | {FAMILY[name]} | {status} | 1.0000 | 1.0000 | 1.0000 | 1.0000 "
            f"| {judged} | {total} | {seconds} |  |"
        )
    return REPORT.format(table="\n".join(lines), winner=winner)


def test_apply_picks_other_family_judge(set_settings):
    s = config.settings
    s.textmodel_report_file.parent.mkdir(parents=True)
    s.textmodel_report_file.write_text(
        report(
            "gemma4-31b",
            [
                ("gemma3-27b", "done", "0.9000", "0.9000", "5.00"),  # same family as the winner
                ("gemma4-31b", "done", "0.9500", "0.9500", "9.00"),
                ("qwen3-30b-a3b", "done", "0.7000", "0.8000", "2.00"),
                ("sarvam-m-24b", "done", "0.8000", "0.8500", "4.00"),
            ],
        )
    )
    assert bk.apply_report() == ("gemma4-31b", "gemma4-31b", "sarvam-m-24b")
    text = s.pipeline_file.read_text()
    assert "enrich_model: gemma4-31b" in text and "answer_model: gemma4-31b" in text
    assert "answer_judge_model: sarvam-m-24b" in text
    assert "surya_version: 0.23.0" in text and text.startswith("# test pipeline")


def test_apply_skips_failed_candidates_as_judge(set_settings):
    s = config.settings
    s.textmodel_report_file.parent.mkdir(parents=True)
    s.textmodel_report_file.write_text(
        report(
            "qwen3-30b-a3b",
            [
                ("gemma3-27b", "done", "0.6000", "0.7000", "5.00"),
                ("gemma4-31b", "failed", "-", "-", "-"),
                ("qwen3-30b-a3b", "done", "0.9000", "0.9000", "2.00"),
                ("sarvam-m-24b", "done", "0.5000", "0.6000", "4.00"),
            ],
        )
    )
    assert bk.apply_report() == ("qwen3-30b-a3b", "qwen3-30b-a3b", "gemma3-27b")


def test_apply_end_to_end_from_a_real_run(set_settings):
    setup = Setup({"qwen3-30b-a3b": Fake("qwen3-30b-a3b", grades={n: 5 for n in NAMES})})
    _, winner, _ = setup.run()
    got = bk.apply_report()
    assert got[0] == got[1] == winner
    assert FAMILY[got[2]] != FAMILY[winner]


def test_no_winner_exits_1(set_settings, monkeypatch):
    from newsrag.cli import app

    s = config.settings
    before = s.pipeline_file.read_text()
    runner = CliRunner()
    # no report at all
    result = runner.invoke(app, ["textmodel", "bakeoff", "--apply"])
    assert result.exit_code == 1
    # a report that says winner: none
    s.textmodel_report_file.parent.mkdir(parents=True, exist_ok=True)
    s.textmodel_report_file.write_text("# report\n\nwinner: none\n")
    result = runner.invoke(app, ["textmodel", "bakeoff", "--apply"])
    assert result.exit_code == 1
    assert s.pipeline_file.read_text() == before
    # no candidate of another family to be the judge: also nothing changes
    s.textmodel_report_file.write_text(
        report("gemma3-27b", [("gemma3-27b", "done", "0.9000", "0.9000", "5.00")])
    )
    result = runner.invoke(app, ["textmodel", "bakeoff", "--apply"])
    assert result.exit_code == 1 and s.pipeline_file.read_text() == before
    # a bake-off where every candidate fails also exits 1 and writes `winner: none`
    setup = Setup(failing=NAMES)
    monkeypatch.setattr("newsrag.models.use", setup.opener)
    monkeypatch.setattr(bk, "dedup_stories", lambda: POOL)
    result = runner.invoke(app, ["textmodel", "bakeoff", "--n", "3"])
    assert result.exit_code == 1
    assert "winner: none" in s.textmodel_report_file.read_text()
    assert s.pipeline_file.read_text() == before


def test_cli_bakeoff_then_apply(set_settings, monkeypatch):
    from newsrag.cli import app

    s = config.settings
    setup = Setup()
    monkeypatch.setattr("newsrag.models.use", setup.opener)
    monkeypatch.setattr(bk, "dedup_stories", lambda: POOL)
    runner = CliRunner()
    result = runner.invoke(app, ["textmodel", "bakeoff", "--n", "3", "--seed", "21"])
    assert result.exit_code == 0, result.output
    assert re.search(r"^winner: \S+$", result.output, re.MULTILINE)
    assert len(s.textmodel_stories_file.read_text().splitlines()) == 3
    result = runner.invoke(app, ["textmodel", "bakeoff", "--apply"])
    assert result.exit_code == 0, result.output
    for key in ("enrich_model", "answer_model", "answer_judge_model"):
        assert f"{key} set to " in result.output
        assert f"{key}: " in s.pipeline_file.read_text()
    bad = runner.invoke(app, ["textmodel", "bakeoff", "--candidates", "gemma3-27b,nope"])
    assert bad.exit_code == 2
    embed = runner.invoke(app, ["textmodel", "bakeoff", "--candidates", "gemma3-27b,bge-m3"])
    assert embed.exit_code == 2


# --- a server that dies is not a model that answers badly ----------------------------------------------


class Dead(Fake):
    def chat(self, messages, **params):
        raise ConnectionError("server gone")


def test_dead_server_during_generation_is_a_failed_candidate(set_settings):
    setup = Setup({"sarvam-m-24b": Dead("sarvam-m-24b")})
    rows, winner, _ = setup.run()
    by = {r.candidate: r for r in rows}
    assert by["sarvam-m-24b"].status == bk.FAILED and "requests failed" in by["sarvam-m-24b"].reason
    assert not bk.gen_path("sarvam-m-24b").exists()  # nothing saved: the next run tries again
    assert winner != "sarvam-m-24b"


def test_dead_judge_is_retried_on_the_next_run(set_settings):
    class DeadJudge(Fake):
        dead = True

        def chat(self, messages, **params):
            if self.dead and "strict reviewer" in messages[-1]["content"]:
                raise ConnectionError("server gone")
            return super().chat(messages, **params)

    judge = DeadJudge("qwen3-30b-a3b")
    rows, _, path = Setup({"qwen3-30b-a3b": judge}).run()
    assert "qwen3-30b-a3b" not in {r.candidate: r for r in rows}["gemma3-27b"].judge_means
    assert "as judge" in path.read_text()
    judge.dead = False
    rows2, _, _ = Setup({"qwen3-30b-a3b": judge}).run()
    assert "qwen3-30b-a3b" in {r.candidate: r for r in rows2}["gemma3-27b"].judge_means


def test_answer_judge_is_the_best_other_family_in_the_global_ranking(set_settings):
    items = [
        R("gemma3-27b", 0.900, 0.5, 1),
        R("gemma4-31b", 0.899, 0.6, 1),
        R("qwen3-30b-a3b", 0.895, 0.7, 1),
        R("sarvam-m-24b", 0.888, 0.9, 1),
    ]
    expected = next(n for n in bk.rank(items) if FAMILY[n] != "gemma")
    assert bk.choose_applied("gemma3-27b", items) == ("gemma3-27b", expected)


def test_sarvam_profile_turns_thinking_off_for_production_too():
    from newsrag.models.profiles import get_profile

    args = get_profile("sarvam-m-24b").extra_args
    assert "--default-chat-template-kwargs" in args
    assert json.loads(args[args.index("--default-chat-template-kwargs") + 1]) == {
        "enable_thinking": False
    }
