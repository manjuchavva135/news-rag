"""The short-query test: name scoring, query checks, the set builder and the run."""

from __future__ import annotations

import json
from datetime import date

from newsrag.evals import short
from newsrag.search.hybrid import Hit

ZWNJ = chr(0x200C)
NAMES = [
    {
        "id": "amit_shah",
        "type": "person",
        "te": "అమిత్ షా",
        "latin": "amit shah",
        "typo": "amith shah",
        "surfaces": ["అమిత్ షా"],
    },
    {"id": "dsc", "type": "topic", "te": "డీఎస్సీ", "latin": "dsc", "surfaces": ["డీఎస్సీ"]},
]
STORIES = {
    "s1": {"kind": "news", "headline": "అమిత్ షా పర్యటన", "text": "కేంద్ర మంత్రి వచ్చారు."},
    "s2": {
        "kind": "news",
        "headline": "సభ",
        "text": f"అమిత్{ZWNJ}షా మాట్లాడారు. అమిత్ షా అన్నారు. అమిత్ షా.",
    },
    "s3": {"kind": "news", "headline": "సభ", "text": "అమిత్ షా వచ్చారు."},
    "s4": {"kind": "brief_item", "headline": "డీఎస్సీ ఫలితాలు", "text": "నేడు."},
    "s5": {"kind": "news", "headline": "వర్షం", "text": "భారీ వర్షం కురిసింది."},
}


def hit(story_id: str, chunk: str = "c", text: str = "", pages: tuple[str, ...] = ()) -> Hit:
    return Hit(
        chunk_id=f"{story_id}:{chunk}",
        story_id=story_id,
        score=1.0,
        text=text,
        page_ids=list(pages),
    )


def test_norm_and_surface_forms():
    assert short.norm(f"  అమిత్{ZWNJ}  షా ") == "అమిత్ షా"
    assert short.surface_forms(["అమిత్ షా", "డీఎస్సీ"]) == ["అమిత్ షా", "అమిత్షా", "డీఎస్సీ"]


def test_name_stories_mention_and_about():
    named = short.name_stories(NAMES, STORIES)
    # s1: in the headline; s2: three times (once joined by a ZWNJ); s3: once only
    assert named["amit_shah"] == {"s1": True, "s2": True, "s3": False}
    assert named["dsc"] == {"s4": True}


def test_score_name_hits():
    named = short.name_stories(NAMES, STORIES)["amit_shah"]
    kinds = {sid: st["kind"] for sid, st in STORIES.items()}
    hits = [hit("s1"), hit("s1", "d"), hit("s3"), hit("s4")]
    scores = short.score_name_hits(hits, named, kinds)
    assert scores["hits"] == 4
    assert scores["mention"] == 0.75  # s1, s1, s3
    assert scores["about"] == 0.5  # s1 twice
    assert scores["distinct"] == 0.75  # three stories in four hits
    assert scores["other_kind"] == 0.25  # the brief item
    assert short.score_name_hits([], named, kinds)["mention"] == 0.0


def test_check_query():
    headline = "రాజధాని నిర్మాణానికి కొత్త నిధులు"
    assert short.check_query("అమరావతి నిధుల కేటాయింపు", short.TE, headline) is None
    assert short.check_query("amaravati funds", short.LATIN, headline) is None
    assert "words" in short.check_query("అమరావతి", short.TE, headline)
    assert "words" in short.check_query("a b c d e f", short.LATIN, headline)
    assert short.check_query("amaravati funds", short.TE, headline) == "not in Telugu script"
    assert "Latin" in short.check_query("అమరావతి funds", short.LATIN, headline)
    assert "question" in short.check_query("నిధులు ఎన్ని ఇచ్చారు?", short.TE, headline)
    assert short.check_query("నిర్మాణానికి కొత్త నిధులు ఎన్ని", short.TE, headline) == "copies the headline"
    assert short.check_query(None, short.TE, headline) == "empty"


def story(i: int, kind: str = "news") -> dict:
    return {
        "story_id": f"st{i}",
        "pub_date": date(2026, 10, 1 + i),
        "kind": kind,
        "headline": f"శీర్షిక {i}",
        "text": "కథనం వివరాలు " * 60,
        "page_ids": [f"P{i}"],
    }


def test_build_short_set(tmp_path):
    out = tmp_path / "short.jsonl"

    def ask(prompt: str) -> str:
        telugu = short.LANGUAGE_RULES[short.TE] in prompt
        return json.dumps({"question": "కొత్త రహదారి పనులు" if telugu else "new road works"})

    stories = [story(i) for i in range(6)] + [story(9, kind="notice_tender")]
    summary = short.build_short_set(n=4, seed=1, out=out, ask=ask, stories=stories, names=NAMES)
    rows = [json.loads(line) for line in out.read_text("utf-8").splitlines()]
    assert summary["names"] == 5 and summary["stories"] == 4
    assert [r["question"] for r in rows[:5]] == ["అమిత్ షా", "amit shah", "amith shah", "డీఎస్సీ", "dsc"]
    story_rows = [r for r in rows if r["part"] == "story"]
    assert sorted(r["form"] for r in story_rows) == ["latin", "latin", "te", "te"]
    assert all(r["story_id"] != "st9" for r in story_rows)  # a notice gets no query
    assert all(r["gold_page_ids"] and r["gold_text"] for r in story_rows)
    assert json.loads((tmp_path / "short.jsonl.meta.json").read_text("utf-8"))["stories"] == 4


def test_build_short_set_replaces_a_story_without_query(tmp_path):
    def ask(prompt: str) -> str:
        if "శీర్షిక 0" in prompt:
            return json.dumps({"question": "ఒకటి"})  # one word: never usable
        telugu = short.LANGUAGE_RULES[short.TE] in prompt
        return json.dumps({"question": "కొత్త రహదారి పనులు" if telugu else "new road works"})

    out = tmp_path / "short.jsonl"
    # no spare story: the one without a query is missing from the set
    summary = short.build_short_set(
        n=3, seed=1, out=out, ask=ask, stories=[story(i) for i in range(3)], names=[]
    )
    assert summary["stories"] == 2 and summary["replaced"] == 1
    # a spare story takes its place (when the unusable one was drawn at all)
    summary = short.build_short_set(
        n=3, seed=1, out=out, ask=ask, stories=[story(i) for i in range(4)], names=[]
    )
    assert summary["stories"] == 3
    assert '"st0"' not in out.read_text("utf-8")


def test_run_short(tmp_path):
    set_file = tmp_path / "short.jsonl"
    gold = "రహదారి పనులు ప్రారంభమయ్యాయి. మంత్రి శంకుస్థాపన చేశారు. నిధులు విడుదలయ్యాయి."
    rows = [
        *short.name_queries(NAMES[:1]),
        {
            "qid": "s0001",
            "part": "story",
            "form": "te",
            "question": "రహదారి పనులు",
            "story_id": "s9",
            "gold_page_ids": ["P9"],
            "gold_text": gold,
        },
    ]
    set_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    real = [{"qid": "r0001", "kind": "topic", "lang": "latin", "words": 1, "question": "health"}]
    answers = {
        "అమిత్ షా": [hit("s1"), hit("s2")],
        "amit shah": [hit("s1"), hit("s5")],
        "amith shah": [],
        "రహదారి పనులు": [hit("s5"), hit("s9", text=gold[:40], pages=("P9",))],
        "health": [hit("s4"), hit("s4", "d")],
    }

    def search_fn(query: str, k: int = 10):
        return answers[query][:k]

    report = short.run_short(
        set_file=set_file,
        out=tmp_path / "report.json",
        search_fn=search_fn,
        stories=STORIES,
        names=NAMES[:1],
        real=real,
    )
    assert report["names"]["te"] == {
        "n": 1, "no_hits": 0, "mention": 1.0, "about": 1.0, "distinct": 1.0, "other_kind": 0.0,
    }  # fmt: skip
    assert report["names"]["latin"]["mention"] == 0.5
    assert report["names"]["latin"]["same_as_te"] == 0.5  # s1 of {s1, s2}
    assert report["names"]["typo"]["no_hits"] == 1 and report["names"]["typo"]["same_as_te"] == 0.0
    assert report["stories"]["te"]["recall_at_10"] == 1.0 and report["stories"]["te"]["mrr"] == 0.5
    assert report["real"]["all"] == {"n": 1, "no_hits": 0, "distinct": 0.5, "other_kind": 1.0}
    assert json.loads((tmp_path / "report.json").read_text("utf-8"))["k"] == 10
    assert any("recall@10" in line for line in short.summary_lines(report))
