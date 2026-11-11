"""Ticket 017: cross-edition story dedup (synthetic units and a fake embedder; the ledger tests use
a throw-away schema and need the docker stores)."""

from __future__ import annotations

import json
import random
import unicodedata
import zlib
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.dedup import cli as dedup_cli
from newsrag.dedup import cluster, ids, minhash
from newsrag.dedup import verify as vf
from newsrag.dedup.units import UNTRANSCRIBED, Unit, build_units
from newsrag.stages.base import PageRow
from newsrag.stages.dedup import DedupStage
from newsrag.stitch.model import Article

DAY = date(2025, 8, 2)
P = cluster.Params(jaccard=0.85, cosine=0.90, num_perm=128, shingle_size=5, lsh_threshold=0.5)


def telugu(seed: int, n_words: int = 90) -> str:
    """A reproducible run of pseudo-Telugu words (consonants, some with a vowel sign)."""
    rng = random.Random(seed)
    words = []
    for _ in range(n_words):
        word = ""
        for _ in range(rng.randint(3, 6)):
            word += chr(rng.randint(0x0C15, 0x0C39))
            if rng.random() < 0.5:
                word += chr(rng.randint(0x0C3E, 0x0C4C))
        words.append(word)
    return " ".join(words)


def edited(text: str, n: int = 2) -> str:
    """The same text with the first `n` words replaced (a local edit)."""
    words = text.split(" ")
    for i in range(n):
        words[i * 7] = "కొత్త" + words[i * 7][:2]
    return " ".join(words)


def page_id(edition: str, num: int = 1) -> str:
    return f"{DAY}_{edition}_main_{num:02d}"


def unit(edition: str, n: int, text: str, headline: str = "శీర్షిక", num: int = 1, **kw) -> Unit:
    pid = page_id(edition, num)
    uid = kw.pop("unit_id", f"{pid}#a{n:02d}")
    parts = kw.pop("parts", [{"article_id": uid, "page_id": pid, "bbox_union": [0, 0, 1, 1]}])
    return Unit(
        unit_id=uid,
        kind=kw.pop("kind", "news"),
        headline=headline,
        text=text,
        page_id=pid,
        edition_code=edition,
        section="main",
        page_num=num,
        parts=parts,
        flags=kw.pop("flags", []),
        **kw,
    )


def article(edition: str, n: int, text: str, kind: str = "news", num: int = 1, **kw) -> Article:
    pid = page_id(edition, num)
    aid = f"{pid}#a{n:02d}"
    return Article(
        article_id=aid,
        kind=kind,
        blocks=[],
        headline=kw.pop("headline", "శీర్షిక"),
        text=text,
        bbox_union=[0, 0, 10, 10],
        flags=kw.pop("flags", []),
        page_id=pid,
        edition_code=edition,
        section="main",
        page_num=num,
        parts=kw.pop(
            "parts", [{"article_id": aid, "page_id": pid, "bbox_union": [0.0, 0.0, 10.0, 10.0]}]
        ),
    )


def fake_embed(texts):
    """Hashed character-trigram vectors: near-identical texts have a cosine near 1."""
    out = np.zeros((len(texts), 512), dtype=np.float32)
    for row, text in enumerate(texts):
        norm = unicodedata.normalize("NFC", text)
        for i in range(len(norm) - 2):
            out[row, zlib.crc32(norm[i : i + 3].encode()) % 512] += 1
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)


def clusters_of(units, embed=fake_embed, params=P):
    return cluster.cluster_units(units, cluster.duplicate_pairs(units, embed, params))


def member_ids(clusters):
    return [[u.unit_id for u in c] for c in clusters]


# --- similarity and clustering (pure) ------------------------------------------------------------


def test_edit_is_a_duplicate():
    base = telugu(1)
    a, b = unit("A", 1, base), unit("B", 1, edited(base))
    pairs = cluster.duplicate_pairs([a, b], fake_embed, P)
    assert [(p.a, p.b) for p in pairs] == [(a.unit_id, b.unit_id)]
    assert pairs[0].jaccard >= 0.85 and pairs[0].cosine >= 0.90


def test_both_thresholds_required():
    base = telugu(2)
    a, b = unit("A", 1, base), unit("B", 1, edited(base))
    # MinHash passes but the embeddings disagree: no pair
    orthogonal = lambda texts: np.eye(len(texts), 8, dtype=np.float32)
    assert cluster.duplicate_pairs([a, b], orthogonal, P) == []
    # the embeddings agree but the texts are different: never a pair (and never embedded)
    c, d = unit("A", 2, telugu(3)), unit("B", 2, telugu(4))
    same = lambda texts: np.ones((len(texts), 4), dtype=np.float32) / 2
    assert cluster.duplicate_pairs([c, d], same, P) == []
    # a pair just under the cosine bar is rejected, just over is kept
    high = cluster.Params(0.85, 0.999999, 128, 5, 0.5)
    assert cluster.duplicate_pairs([a, b], fake_embed, high) == []


def test_same_page_never_merged():
    base = telugu(5)
    one, two = unit("A", 1, base), unit("A", 2, base)  # identical, but on the same page
    assert member_ids(clusters_of([one, two])) == [[one.unit_id], [two.unit_id]]


def test_transitive_clusters():
    base = telugu(6, 200)
    a = unit("A", 1, base)
    b = unit("B", 1, edited(base, 1))
    c = unit("C", 1, edited(edited(base, 1), 3))
    d = unit("D", 1, telugu(7))
    pairs = cluster.duplicate_pairs([a, b, c, d], fake_embed, P)
    assert cluster.cluster_units([a, b, c, d], pairs[:0]) != []  # no pairs: all alone
    found = member_ids(cluster.cluster_units([a, b, c, d], pairs))
    assert found == [[a.unit_id, b.unit_id, c.unit_id], [d.unit_id]]


def test_same_page_union_skipped():
    base = telugu(8)
    # a (page A) ~ b (page B) ~ a2 (page A again): b joins one of them, never both
    a, a2 = unit("A", 1, base), unit("A", 2, base)
    b = unit("B", 1, edited(base, 1))
    pairs = [
        cluster.Pair(a.unit_id, b.unit_id, 0.95, 0.99),
        cluster.Pair(a2.unit_id, b.unit_id, 0.95, 0.95),
    ]
    got = member_ids(cluster.cluster_units([a, a2, b], pairs))
    assert got == [[a.unit_id, b.unit_id], [a2.unit_id]]  # the higher cosine wins
    # a stitched unit counts all its pages: its second part is on page B1
    long = unit(
        "A",
        3,
        base,
        parts=[
            {"article_id": "x", "page_id": page_id("A"), "bbox_union": []},
            {"article_id": "y", "page_id": page_id("B"), "bbox_union": []},
        ],
    )
    got = member_ids(
        cluster.cluster_units(
            [long, b], [cluster.Pair(*sorted([long.unit_id, b.unit_id]), 0.95, 0.99)]
        )
    )
    assert got == [[long.unit_id], [b.unit_id]]


def test_canonical_is_longest():
    base = telugu(9)
    short = unit("A", 1, base[: len(base) - 40], kind="brief_item")
    longer = unit("C", 1, base + " అదనపు వాక్యం")
    tie_a, tie_b = unit("B", 1, base), unit("B", 2, base)
    assert cluster.canonical([short, longer]) is longer
    assert cluster.canonical([tie_b, tie_a]) is tie_a  # equal length: smallest unit_id
    story = cluster.story_record("s_x", [short, longer])
    assert story["canonical_unit_id"] == longer.unit_id
    assert story["text"] == longer.text and story["kind"] == "news"  # the canonical's kind


def test_normalisation_keeps_vowel_signs():
    # a vowel sign (Mc/Mn) is kept: these are different words
    assert minhash.normalise("", "కి") != minhash.normalise("", "కు")
    assert "ి" in minhash.normalise("", "కి")
    # Unicode punctuation (also the danda) and all whitespace go
    assert minhash.normalise("శీర్షిక", "ఆ, ఈ।\n\t(ఉ)") == "శీర్షికఆఈఉ"
    # NFC: a decomposed and a composed spelling are the same text
    assert minhash.normalise("", "é") == minhash.normalise("", "é")
    assert minhash.shingles("abc", 5) == {"abc"} and minhash.shingles("", 5) == set()
    assert len(minhash.shingles("abcdefg", 5)) == 3


# --- units ------------------------------------------------------------------------------------


PAGES = {
    page_id(e): SimpleNamespace(page_id=page_id(e), edition_code=e, section="main", page_num=1)
    for e in "ABC"
}


def item(pid, n, typ="table", article_id=None, md="| a |\n|---|\n| 1 |", **kw):
    return {
        "item_id": f"{pid}#t{n:02d}",
        "source": "block",
        "block_ids": kw.pop("block_ids", ["b001"]),
        "article_id": article_id,
        "type": typ,
        "title": kw.pop("title", None),
        "markdown": md,
        "department": kw.pop("department", None),
        "notice_number": kw.pop("notice_number", None),
        "deadline": kw.pop("deadline", None),
        "flags": kw.pop("flags", []),
        "inlined": kw.pop("inlined", False),
    }


def build(articles, items=None):
    return build_units(
        articles,
        items or {},
        PAGES,
        lambda pid: {"b001": [10, 20, 30, 40], "b002": [5, 50, 60, 70]},
    )


def test_ads_and_masthead_excluded():
    arts = [
        article("A", 1, telugu(10), "news"),
        article("A", 2, telugu(11), "ad_commercial"),
        article("A", 3, telugu(12), "masthead"),
        article("A", 4, telugu(13), "other"),
        article("A", 5, telugu(14), "editorial"),
        article("A", 6, telugu(15), "brief_item"),
        article("A", 7, telugu(16), "ad_commercial"),
    ]
    items = {
        page_id("A"): [
            item(page_id("A"), 1, "commercial", f"{page_id('A')}#a07", md="shop"),
            item(page_id("A"), 2, "commercial", None, md="sale"),
        ]
    }
    units, excluded = build(arts, items)
    assert [u.kind for u in units] == ["news", "editorial", "brief_item"]
    assert excluded == {"ad_commercial": 1, "masthead": 1, "other": 1, "commercial": 2}


def test_class_overrides_kind_and_notice_text():
    pid = page_id("A")
    arts = [
        article("A", 1, "ocr garbage", "ad_commercial"),  # the VLM says notice
        article("A", 2, "ocr text", "notice_tender"),  # the VLM says commercial
        article("A", 3, "ocr tender", "notice_tender"),
    ]
    items = {
        pid: [
            item(
                pid,
                1,
                "notice_tender",
                f"{pid}#a01",
                md="TENDER NOTICE 7",
                department="Mines",
                notice_number="7/2025",
                deadline="2025-09-01",
                flags=["low_confidence:x"],
            ),
            item(pid, 2, "commercial", f"{pid}#a02", md=""),
            item(pid, 3, "notice_tender", f"{pid}#a03", md="FULL NOTICE", department=None),
        ]
    }
    units, excluded = build(arts, items)
    assert [u.unit_id for u in units] == [f"{pid}#a01", f"{pid}#a03"]
    first = units[0]
    assert first.kind == "notice_tender" and first.text == "TENDER NOTICE 7"
    assert (first.department, first.notice_number, first.deadline) == (
        "Mines",
        "7/2025",
        "2025-09-01",
    )
    assert first.flags == ["low_confidence:x"]
    assert excluded["commercial"] == 1 and excluded["ad_commercial"] == 0
    story = cluster.story_record("s_n", [first])
    assert story["department"] == "Mines" and story["deadline"] == "2025-09-01"
    news_story = cluster.story_record("s_m", [unit("A", 9, "text")])
    assert news_story["department"] is None and news_story["notice_number"] is None


def test_untranscribed_notice():
    units, _ = build([article("A", 1, "ocr text of tender", "notice_tender", flags=["f"])])
    (u,) = units
    assert u.kind == "notice_tender" and u.text == "ocr text of tender"
    assert (u.department, u.notice_number, u.deadline) == (None, None, None)
    assert u.flags == ["f", UNTRANSCRIBED]
    # an item with no markdown is not usable either
    pid = page_id("A")
    units, _ = build(
        [article("A", 1, "ocr", "notice_tender")],
        {pid: [item(pid, 1, "notice_tender", f"{pid}#a01", md="  ")]},
    )
    assert units[0].flags == [UNTRANSCRIBED]
    # an ad without a notice item stays excluded
    units, excluded = build([article("A", 2, "ocr", "ad_commercial")])
    assert units == [] and excluded["ad_commercial"] == 1


def test_item_flags_unioned():
    pid = page_id("A")
    arts = [article("A", 1, "ignored text", "table_only", flags=["low_confidence:a"])]
    items = {
        pid: [
            item(
                pid,
                1,
                "table",
                f"{pid}#a01",
                md="| x |\n|---|\n| 1 |",
                flags=["low_confidence:b"],
                inlined=True,
            ),
            item(
                pid,
                2,
                "infographic",
                f"{pid}#a01",
                md="Title\n- a: 1",
                flags=["low_confidence:a", "low_confidence:c"],
                inlined=True,
            ),
        ]
    }
    (u,), _ = build(arts, items)
    assert u.kind == "table_only"
    assert u.text == "| x |\n|---|\n| 1 |\n\nTitle\n- a: 1"
    assert u.flags == ["low_confidence:a", "low_confidence:b", "low_confidence:c"]
    # a table item inside a news article is not a unit of its own
    units, _ = build([article("A", 2, telugu(1))], {pid: [item(pid, 3, "table", f"{pid}#a02")]})
    assert [u.unit_id for u in units] == [f"{pid}#a02"]


def test_standalone_item_printing():
    pid = page_id("B")
    items = {
        pid: [
            item(
                pid,
                1,
                "table",
                None,
                md="| a |",
                title="Budget",
                block_ids=["b001", "b002"],
                flags=["low_confidence:z"],
            ),
            item(pid, 2, "infographic", None, md="- x: 1", title=None),
        ]
    }
    units, _ = build([], items)
    assert [u.unit_id for u in units] == [f"{pid}#t01", f"{pid}#t02"]
    first = units[0]
    assert (first.kind, first.headline, first.text) == ("table", "Budget", "| a |")
    assert (first.edition_code, first.section, first.page_num) == ("B", "main", 1)
    assert first.parts == [
        {"article_id": None, "page_id": pid, "bbox_union": [5.0, 20.0, 60.0, 70.0]}
    ]
    assert first.flags == ["low_confidence:z"] and units[1].headline == ""
    printing = cluster.printing(first)
    assert printing["page_id"] == pid and printing["parts"][0]["bbox_union"] == [5, 20, 60, 70]


def test_stitched_unit_keeps_all_parts():
    pid_a, pid_b = page_id("A"), page_id("A", 2)
    parts = [
        {"article_id": f"{pid_a}#a01", "page_id": pid_a, "bbox_union": [0.0, 0.0, 1.0, 1.0]},
        {"article_id": f"{pid_b}#a01", "page_id": pid_b, "bbox_union": [0.0, 0.0, 2.0, 2.0]},
    ]
    (u,), _ = build([article("A", 1, "x", parts=parts)])
    assert u.pages == {pid_a, pid_b} and u.parts == parts


# --- story ids (ledger) ---------------------------------------------------------------------


def uid(edition: str, n: int = 1) -> str:
    return f"{page_id(edition)}#a{n:02d}"


def rows(c=None):
    from newsrag.ledger import db

    with db.conn() as conn:
        return {
            r["unit_id"]: (r["story_id"], r["first_seen"])
            for r in db.fetch_all(conn, "select * from story_ids")
        }


def test_new_story_id_skips_taken_k():
    first = ids.new_story_id("u1", "2025-08-02", set())
    assert first.startswith("s_") and len(first) == 18
    assert ids.new_story_id("u1", "2025-08-02", {first}) != first
    assert ids.new_story_id("u1", "2025-08-02", set()) == first  # stable


def test_plan_ids_most_members_keep_id():
    old = date(2025, 1, 1)
    existing = {
        "u1": ("X", old),
        "u2": ("X", old),
        "u3": ("Y", old),
        "u4": ("Y", old),
        "u5": ("Y", old),
    }
    used = {"X", "Y"}
    # {u1,u2,u3,u4,u5}: Y is held by 3 members, X by 2: the cluster keeps Y
    assert ids.plan_ids("2025-08-02", [["u1", "u2", "u3", "u4", "u5"]], existing, used) == ["Y"]
    # a split: {u3,u4,u5} (3 hold Y) is handled first and keeps Y; {u1,u2} keeps X
    assert ids.plan_ids("2025-08-02", [["u1", "u2"], ["u3", "u4", "u5"]], existing, used) == [
        "X",
        "Y",
    ]
    # tie on count: the oldest first_seen wins, then the smallest id
    tied = {"a": ("B", date(2025, 3, 1)), "b": ("A", date(2025, 2, 1))}
    assert ids.plan_ids("2025-08-02", [["a", "b"]], tied, {"A", "B"}) == ["A"]
    tied = {"a": ("B", old), "b": ("A", old)}
    assert ids.plan_ids("2025-08-02", [["a", "b"]], tied, {"A", "B"}) == ["A"]
    # the cluster that lost its id to a bigger one gets a new id
    out = ids.plan_ids(
        "2025-08-02",
        [["u1"], ["u2", "u3"]],
        {"u1": ("X", old), "u2": ("X", old), "u3": ("X", old)},
        {"X"},
    )
    assert out[1] == "X" and out[0] not in ("X", "") and out[0].startswith("s_")


@pytest.mark.stores
def test_story_id_reused_on_rerun(ledger):
    clusters = [[uid("A"), uid("B"), uid("C")], [uid("A", 2)]]
    first = ids.sync(DAY, clusters, today=date(2025, 9, 1))
    assert len(set(first)) == 2 and all(s.startswith("s_") for s in first)
    again = ids.sync(DAY, clusters, today=date(2025, 9, 5))
    assert again == first
    # first_seen is kept for units whose id did not change
    assert {v[1] for v in rows().values()} == {date(2025, 9, 1)}
    # a new printing joins the story: the story keeps its id, the newcomer is seen today
    grown = ids.sync(DAY, [[*clusters[0], uid("D")], clusters[1]], today=date(2025, 9, 9))
    assert grown == first
    assert rows()[uid("D")][1] == date(2025, 9, 9)


@pytest.mark.stores
def test_unit_leaving_cluster_gets_new_id(ledger):
    a, b, c = uid("A"), uid("B"), uid("C")
    (story,) = ids.sync(DAY, [[a, b, c]], today=date(2025, 9, 1))
    # c is no longer a duplicate: it gets an id of its own, the others keep theirs
    ab, solo = ids.sync(DAY, [[a, b], [c]], today=date(2025, 9, 2))
    assert ab == story and solo != story
    assert rows()[c] == (solo, date(2025, 9, 2))
    assert rows()[a] == (story, date(2025, 9, 1))


@pytest.mark.stores
def test_no_id_collision_after_split(ledger):
    a, b, c = uid("A"), uid("B"), uid("C")
    (story,) = ids.sync(DAY, [[a, b, c]])  # id = h(a + date + 0)
    assert story == ids.new_story_id(a, DAY.isoformat(), set())
    # the smallest unit now stands alone while b and c (holding the id) stay together: the
    # lone cluster hashes to the same k=0 id and must take k=1 instead
    lone, rest = ids.sync(DAY, [[a], [b, c]])
    assert rest == story
    assert lone != story and lone == ids.new_story_id(a, DAY.isoformat(), {story})
    assert len({v[0] for v in rows().values()}) == 2


@pytest.mark.stores
def test_vanished_units_are_deleted(ledger):
    a, b = uid("A"), uid("B")
    other_day = "2025-08-03_A_main_01#a01"
    ids.sync(date(2025, 8, 3), [[other_day]])
    ids.sync(DAY, [[a, b]])
    ids.sync(DAY, [[a]])
    assert set(rows()) == {a, other_day}


# --- the stage ---------------------------------------------------------------------------------


def set_settings(monkeypatch, **update):
    """Replace the settings in `newsrag.config` and in `newsrag.stages.base` (version hashes)."""
    import newsrag.stages.base as base_mod

    new = config.settings.model_copy(update=update)
    monkeypatch.setattr(config, "settings", new)
    monkeypatch.setattr(base_mod, "settings", new)
    return new


def test_cpu_device(monkeypatch):
    calls = []

    class FakeEmbedder:
        def embed(self, texts, kind="doc", batch_size=64):
            calls.append((list(texts), kind, batch_size))
            return fake_embed(texts)

    import contextlib

    from newsrag import models

    @contextlib.contextmanager
    def fake_use(name, device="cuda", report=None):
        calls.append((name, device))
        yield FakeEmbedder()

    monkeypatch.setattr(models, "use", fake_use)
    for device, needed in (("cpu", ()), ("cuda", ("bge-m3",))):
        set_settings(monkeypatch, dedup_device=device)
        stage = DedupStage()
        assert stage.models_needed == needed
        stage.open()
        stage.embed(["x", "y"])
        stage.close()
        assert calls[-2] == ("bge-m3", device)
        assert calls[-1] == (["x", "y"], "doc", 64)
    # the device is part of the stage version
    set_settings(monkeypatch, dedup_device="cpu")
    cpu_version = DedupStage().version
    set_settings(monkeypatch, dedup_device="cuda")
    assert DedupStage().version != cpu_version
    assert DedupStage().version.startswith("dedup-v1-")


def test_version_changes_with_thresholds(monkeypatch):
    v0 = DedupStage().version
    for field, value in (("dedup_jaccard", 0.8), ("dedup_cosine", 0.8)):
        set_settings(monkeypatch, **{field: value})
        assert DedupStage().version != v0


def test_registered():
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["dedup"]
    assert stage.name == "dedup" and stage.depends_on == ("stitch", "transcribe")


@pytest.mark.stores
def test_handle_end_to_end(ledger, monkeypatch, tmp_path, capsys):
    from newsrag.dedup import load

    set_settings(monkeypatch, work_dir=tmp_path)
    base = telugu(20, 120)
    units = [
        unit("A", 1, base, headline="శీర్షిక A"),
        unit("B", 1, edited(base), headline="శీర్షిక A"),
        unit("C", 1, edited(base, 3) + " అదనంగా మరో వాక్యం ఇక్కడ ఉంది", headline="శీర్షిక A"),
        unit("A", 2, telugu(21)),
        unit("B", 2, telugu(22), kind="editorial", flags=["low_confidence:z"]),
    ]
    monkeypatch.setattr(load, "date_units", lambda d, pages: (units, {"masthead": 3}))
    stage = DedupStage()
    stage.embed = fake_embed  # type: ignore[method-assign]
    pages = [PageRow(page_id(e), DAY, e, "main", 1, None, "", "", 1) for e in "ABC"]
    result = stage.handle(DAY, pages)
    art = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert art["pub_date"] == "2025-08-02" and art["excluded"] == {"masthead": 3}
    assert result.detail["units"] == 5 and result.detail["stories"] == 3
    big = next(s for s in art["stories"] if len(s["printings"]) == 3)
    assert big["canonical_unit_id"] == units[2].unit_id  # the longest printing
    assert big["text"] == units[2].text and big["headline"] == "శీర్షిక A"
    assert [p["edition_code"] for p in big["printings"]] == ["A", "B", "C"]
    assert set(big) == {
        "story_id", "kind", "canonical_unit_id", "headline", "text", "department",
        "notice_number", "deadline", "printings", "flags",
    }  # fmt: skip
    assert set(big["printings"][0]) == {
        "unit_id", "page_id", "edition_code", "section", "page_num", "parts", "flags",
    }  # fmt: skip
    editorial = next(s for s in art["stories"] if s["kind"] == "editorial")
    assert editorial["flags"] == ["low_confidence:z"]
    assert "dedup 2025-08-02: 5 units, 3 stories in " in capsys.readouterr().err
    assert {v[0] for v in rows().values()} == {s["story_id"] for s in art["stories"]}
    # a second run keeps every id
    before = rows()
    stage.handle(DAY, pages)
    assert rows() == before


# --- stats and verify -----------------------------------------------------------------------------


def test_totals():
    def story(n):
        return {"printings": [{}] * n}

    out = dedup_cli.totals([{"stories": [story(1), story(2), story(5)]}, {"stories": [story(7)]}])
    assert out["units"] == 15 and out["stories"] == 4
    assert out["mean_printings"] == 3.75 and out["max_printings"] == 7
    assert out["stories_with_5plus_printings"] == 2
    assert dedup_cli.totals([])["stories"] == 0


def test_verify_report_and_exit_code(monkeypatch, tmp_path):
    pairs = [
        {
            "pub_date": "2025-08-02",
            "story_id": f"s_{i}",
            "canonical_unit_id": f"a{i}",
            "unit_id": f"b{i}",
            "headline_a": "h",
            "text_a": "t" * 5000,
            "headline_b": "",
            "text_b": "u",
        }
        for i in range(40)
    ]
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return "Yes." if len(asked) > 2 else "no"

    report = vf.run_verify(pairs, 30, 5, ask)
    assert report["n"] == 30 and report["yes"] == 28 and report["no"] == 2
    assert all("(none)" in p and len(p) < 4000 for p in asked)
    assert [x["unit_id"] for x in report["items"]] == [
        x["unit_id"] for x in vf.sample(pairs, 30, 5)
    ]
    assert vf.parse_answer("maybe") == "unclear"

    set_settings(monkeypatch, dedup_verify_file=tmp_path / "v.json")
    monkeypatch.setattr(vf, "collect_pairs", lambda day=None: pairs)
    for yes, code in ((28, 0), (27, 1)):
        monkeypatch.setattr(
            vf,
            "run_verify",
            lambda p, n, s, yes=yes: {
                "model": "m",
                "n_pairs_available": 40,
                "n": 30,
                "yes": yes,
                "items": [],
            },
        )
        result = CliRunner().invoke(dedup_cli.app, ["verify", "--n", "30", "--seed", "5"])
        assert result.exit_code == code, result.output
        assert f"yes: {yes}/30" in result.output
