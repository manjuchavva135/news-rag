"""Ticket 026: embedding model bake-off (fake embedder; the stores test uses throw-away names)."""

from __future__ import annotations

import contextlib
import hashlib
import secrets
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.embed import bakeoff as bk
from newsrag.embed import records
from newsrag.models import client as model_client

runner = CliRunner()


class FakeEmbedder:
    def __init__(self, dim: int = 8) -> None:
        self._dim = dim
        self.calls: list[tuple[str, int]] = []  # (kind, number of texts)

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Any, kind: str = "doc", batch_size: int = 64) -> np.ndarray:
        items = [texts] if isinstance(texts, str) else list(texts)
        self.calls.append((kind, len(items)))
        rows = []
        for text in items:
            digest = hashlib.sha256(text.encode()).digest()
            rows.append([b / 255 + 0.01 for b in digest[: self._dim]])
        v = np.asarray(rows, dtype=np.float32).reshape(len(items), self._dim)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def corpus(n_stories: int, per_story: int = 1) -> list[dict[str, Any]]:
    return [
        {"chunk_id": f"s{i}_c{j}", "story_id": f"s{i}", "prefix": f"P{i}", "text": f"text {i} {j}"}
        for i in range(n_stories)
        for j in range(per_story)
    ]


def questions(n: int, lang: str = "te") -> list[dict[str, Any]]:
    return [
        {
            "qid": f"q{i}",
            "lang": lang,
            "question": f"question {i}",
            "story_id": f"s{i}",
            "gold_page_ids": [f"p{i}"],
            "gold_text": f"text {i} 0 and more",
        }
        for i in range(n)
    ]


def payloads_for(chunks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        records.point_id(c["chunk_id"]): {
            "chunk_id": c["chunk_id"],
            "story_id": c["story_id"],
            "page_ids": [f"p{c['story_id'][1:]}"],
            "pub_date": "2025-06-10",
        }
        for c in chunks
    }


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("# test pipeline\nembed_model: bge-m3\n", encoding="utf-8")
    s = config.settings.model_copy(
        update={
            "embed_bakeoff_report_file": tmp_path / "embed-bakeoff.md",
            "embed_bakeoff_dir": tmp_path / "embed-bakeoff",
            "pipeline_file": pipeline,
            "embed_model": "bge-m3",
            "embed_bakeoff_min_questions": 3,
        }
    )
    monkeypatch.setattr(config, "settings", s)
    return s


@pytest.fixture
def fake_use(monkeypatch):
    """`models.use` opens a FakeEmbedder (no download, no GPU lock); counts the real opens.
    Profiles named in `failing` raise when opened."""
    state: dict[str, Any] = {"opens": [], "failing": set(), "embedders": {}}

    def fake_open(profile, device, report):
        state["opens"].append(profile.name)
        if profile.name in state["failing"]:
            raise RuntimeError(f"CUDA out of memory loading {profile.name}\nmore detail")
        emb = FakeEmbedder(dim=int(profile.dim or 8) // 128 or 8)
        state["embedders"][profile.name] = emb
        return model_client._Loaded(profile.name, emb, contextlib.ExitStack())

    monkeypatch.setattr(model_client, "_open", fake_open)
    return state


@pytest.fixture
def no_qdrant_writes(monkeypatch):
    written: dict[str, list] = {}
    monkeypatch.setattr(bk, "recreate_collection", lambda name, dim: written.setdefault(name, []))
    monkeypatch.setattr(
        bk, "write_points", lambda name, points: written.setdefault(name, []).extend(points)
    )
    return written


@pytest.fixture
def loaded(monkeypatch):
    """The corpus / question / payload loaders return in-memory data."""
    data: dict[str, Any] = {"corpus": corpus(5), "questions": questions(5)}
    calls: list[str] = []

    def load_payloads(generation):
        calls.append("payloads")
        return payloads_for(data["corpus"])

    monkeypatch.setattr(bk, "active_generation", lambda: "bge-m3__v1")
    monkeypatch.setattr(bk, "load_corpus", lambda g: data["corpus"])
    monkeypatch.setattr(bk, "load_payloads", load_payloads)
    from newsrag.evals import retrieval

    monkeypatch.setattr(retrieval, "read_set", lambda path: data["questions"])
    data["calls"] = calls
    return data


def row(name: str, recall: float, mrr: float, dim: int) -> bk.Row:
    return bk.Row(name, dim, recall=recall, mrr=mrr)


# --- pure -------------------------------------------------------------------------------------


def test_tie_breaks_by_mrr_then_dim_then_order():
    # clear recall winner
    assert bk.pick_winner([row("a", 0.90, 0.9, 1024), row("b", 0.95, 0.5, 4096)], 0.01) == "b"
    # recall tie (within 0.01, also exactly 0.01) -> better MRR
    rows = [row("a", 0.985, 0.70, 1024), row("b", 0.995, 0.80, 4096)]
    assert bk.pick_winner(rows, 0.01) == "b"
    # recall and MRR tie -> smaller dim
    rows = [row("a", 0.99, 0.80, 4096), row("b", 0.995, 0.795, 1024)]
    assert bk.pick_winner(rows, 0.01) == "b"
    # everything tied -> the --candidates order
    rows = [row("x", 0.99, 0.80, 1024), row("y", 0.99, 0.80, 1024)]
    assert bk.pick_winner(rows, 0.01) == "x"
    assert bk.pick_winner(list(reversed(rows)), 0.01) == "y"
    # the MRR tie is taken within the recall tie set only: c has the best MRR but is out
    rows = [row("a", 0.99, 0.60, 1024), row("b", 0.99, 0.65, 1024), row("c", 0.90, 0.99, 512)]
    assert bk.pick_winner(rows, 0.01) == "b"
    # failed rows never win; none finished -> None
    failed = bk.Row("z", 512, error="boom")
    assert bk.pick_winner([failed, row("a", 0.5, 0.5, 1024)], 0.01) == "a"
    assert bk.pick_winner([failed], 0.01) is None


def test_report_format():
    rows = [
        bk.Row("bge-m3", 1024, 0.995, 0.8236, 0.99, 1.0, 12.34),
        bk.Row("qwen3-embedding-8b", 4096, error="RuntimeError: CUDA out of memory"),
    ]
    text = bk.render_report(rows, 200, "bge-m3", "bge-m3__v1", 432)
    assert "\nquestions: 200\n" in text
    assert "| candidate | recall@10 | mrr | recall@10 te | recall@10 en | embed seconds |" in text
    assert "| bge-m3 | 0.9950 | 0.8236 | 0.9900 | 1.0000 | 12.3 |" in text
    assert "| qwen3-embedding-8b | failed | - | - | - | - |" in text
    assert text.rstrip().endswith("winner: bge-m3")
    assert "winner: none" in bk.render_report(rows[1:], 200, None, "g", 1)


def test_corpus_questions_filter():
    qs = questions(4) + [{"story_id": "s99"}]
    kept = bk.corpus_questions(qs, corpus(3))
    assert [q["story_id"] for q in kept] == ["s0", "s1", "s2"]


def test_payload_copied():
    chunks = corpus(2, per_story=2)
    payloads = payloads_for(chunks)
    payloads[records.point_id("s0_c0")]["category"] = "politics"
    vectors = FakeEmbedder().embed(bk.doc_texts(chunks))
    points = bk.build_points(chunks, vectors, payloads)
    assert [p[0] for p in points] == [records.point_id(c["chunk_id"]) for c in chunks]
    assert points[0][2] == payloads[records.point_id("s0_c0")]
    assert points[0][2] is not payloads[records.point_id("s0_c0")]  # a copy
    assert points[1][1] == pytest.approx(vectors[1].tolist())
    del payloads[records.point_id("s1_c1")]
    with pytest.raises(bk.BakeoffError, match="s1_c1 has no point"):
        bk.build_points(chunks, vectors, payloads)


def test_doc_text_is_prefix_newline_text():
    assert bk.doc_texts([{"prefix": "P", "text": "T"}]) == ["P\nT"]


def test_collection_names(monkeypatch):
    s = config.settings.model_copy(update={"store_alias_prefix": ""})
    monkeypatch.setattr(config, "settings", s)
    assert bk.collection_for("bge-m3") == "bakeoff__bge-m3"
    s = config.settings.model_copy(update={"store_alias_prefix": "test_x_"})
    monkeypatch.setattr(config, "settings", s)
    assert bk.collection_for("bge-m3") == "test_x_bakeoff__bge-m3"


def test_profiles_present():
    from newsrag.models import get_profile

    qwen = get_profile("qwen3-embedding-8b")
    e5 = get_profile("multilingual-e5-large-instruct")
    prefix = "Instruct: Given a news question, retrieve the news article that answers it\nQuery: "
    assert (qwen.hf_id, qwen.family, qwen.kind, qwen.dim, qwen.dtype) == (
        "Qwen/Qwen3-Embedding-8B",
        "qwen",
        "embed",
        4096,
        "bfloat16",
    )
    assert (e5.hf_id, e5.family, e5.kind, e5.dim, e5.dtype) == (
        "intfloat/multilingual-e5-large-instruct",
        "e5",
        "embed",
        1024,
        "float16",
    )
    assert qwen.query_prefix == e5.query_prefix == prefix
    assert qwen.doc_prefix == e5.doc_prefix == ""


# --- runs with fakes --------------------------------------------------------------------------


def test_small_corpus_exits_1(tmp_settings, loaded, fake_use, monkeypatch):
    from newsrag.cli import app

    s = tmp_settings.model_copy(update={"embed_bakeoff_min_questions": 200})
    monkeypatch.setattr(config, "settings", s)
    result = runner.invoke(app, ["embed", "bakeoff"])
    assert result.exit_code == 1
    assert "bake-off corpus too small: 5 questions" in result.output
    assert not s.embed_bakeoff_report_file.exists()


def test_size_check_first(tmp_settings, loaded, fake_use, no_qdrant_writes, monkeypatch):
    # 5 questions, but only 2 of their stories are in the corpus
    loaded["corpus"] = corpus(2)
    with pytest.raises(bk.CorpusTooSmall, match="^bake-off corpus too small: 2 questions$"):
        bk.run_bakeoff(["bge-m3"])
    assert fake_use["opens"] == []  # no model loaded
    assert loaded["calls"] == []  # no Qdrant read
    assert no_qdrant_writes == {}  # nothing embedded or written


def _stub_search(monkeypatch, seen: dict[str, Any]) -> None:
    """Dense / BM25 / chunk rows for search_new without stores: the gold chunk ranks first."""
    from newsrag.search import hybrid
    from newsrag.warehouse import db as wdb

    def dense(vector, collection, limit, filters):
        seen.setdefault("collections", []).append(collection)
        seen.setdefault("dims", []).append(len(vector))
        return [{"chunk_id": seen["next"], "page_ids": [seen["page"]]}]

    def bm25(query, index, limit, filters):
        seen.setdefault("indexes", []).append(index)
        return []

    def rows(generation, ids):
        seen.setdefault("generations", []).append(generation)
        return {i: {"story_id": i.split("_")[0], "text": seen["text"]} for i in ids}

    def embed_query(query, profile):
        seen.setdefault("profiles", []).append(profile)
        return hybrid._embedder(profile).embed([query], "query", 1)[0].tolist()

    monkeypatch.setattr(hybrid, "dense_search", dense)
    monkeypatch.setattr(hybrid, "bm25_search", bm25)
    monkeypatch.setattr(hybrid, "chunk_rows", rows)
    monkeypatch.setattr(hybrid, "embed_query", embed_query)
    monkeypatch.setattr(wdb, "active_generation", lambda: "bge-m3__v1")


def test_reuses_open_embedder(tmp_settings, fake_use, no_qdrant_writes, monkeypatch):
    from newsrag import embed, search

    seen: dict[str, Any] = {"next": "s0_c0", "page": "p0", "text": "text 0 0 and more"}
    _stub_search(monkeypatch, seen)
    chunks = corpus(3)
    qs = questions(1)
    result = bk.run_candidate(
        "multilingual-e5-large-instruct", chunks, payloads_for(chunks), qs, echo=lambda m: None
    )
    assert result.ok, result.error
    # one load for the documents and the queries: search() reused the open embedder
    assert fake_use["opens"] == ["multilingual-e5-large-instruct"]
    emb = fake_use["embedders"]["multilingual-e5-large-instruct"]
    assert ("doc", 3) in emb.calls and ("query", 1) in emb.calls
    assert seen["profiles"] == ["multilingual-e5-large-instruct"]
    assert seen["collections"] == [bk.collection_for("multilingual-e5-large-instruct")]
    # BM25 and the texts from the active generation
    assert seen["indexes"] == [embed.collection_name("bge-m3__v1")]
    assert seen["generations"] == ["bge-m3__v1"]
    assert result.recall == 1.0 and result.mrr == 1.0 and result.recall_te == 1.0
    assert result.recall_en is None
    # the vectors went into the candidate's collection with the active payloads
    points = no_qdrant_writes[bk.collection_for("multilingual-e5-large-instruct")]
    assert [p[2] for p in points] == [payloads_for(chunks)[p[0]] for p in points]
    assert search.hybrid._embed_state["profile"] is None  # closed again
    assert model_client._loaded is None  # and the model unloaded


def test_failed_candidate_excluded(tmp_settings, loaded, fake_use, no_qdrant_writes, monkeypatch):
    from newsrag.cli import app

    scores = {
        "bge-m3": {"recall_at_10": 0.9, "mrr": 0.7},
        "multilingual-e5-large-instruct": {"recall_at_10": 0.95, "mrr": 0.75},
    }

    def evaluate(candidate, collection, qs):
        assert len(qs) == 5
        return {**scores[candidate], "by_lang": {"te": {"n": 5, "recall_at_10": 0.9}}}

    monkeypatch.setattr(bk, "evaluate", evaluate)
    fake_use["failing"].add("qwen3-embedding-8b")
    result = runner.invoke(app, ["embed", "bakeoff"])
    assert result.exit_code == 0, result.output
    text = tmp_settings.embed_bakeoff_report_file.read_text(encoding="utf-8")
    assert "| qwen3-embedding-8b | failed | - | - | - | - |" in text
    assert "failed qwen3-embedding-8b: RuntimeError: CUDA out of memory loading" in text
    assert "winner: multilingual-e5-large-instruct" in text
    assert "questions: 5" in text
    # every candidate was opened, in order, one at a time
    assert fake_use["opens"] == ["bge-m3", "qwen3-embedding-8b", "multilingual-e5-large-instruct"]

    # none finishes -> winner: none, exit 1
    fake_use["failing"].update(scores)
    result = runner.invoke(app, ["embed", "bakeoff", "--candidates", "bge-m3,qwen3-embedding-8b"])
    assert result.exit_code == 1
    assert "winner: none" in tmp_settings.embed_bakeoff_report_file.read_text(encoding="utf-8")


def test_unknown_candidate_rejected(tmp_settings, loaded, fake_use):
    from newsrag.cli import app

    result = runner.invoke(app, ["embed", "bakeoff", "--candidates", "bge-m3,nope"])
    assert result.exit_code == 1
    assert "nope" in result.output
    result = runner.invoke(app, ["embed", "bakeoff", "--candidates", "bge-m3,qwen3-30b-a3b"])
    assert result.exit_code == 1 and "not an embed profile" in result.output
    assert fake_use["opens"] == []


def _pipeline(s) -> str:
    return Path(s.pipeline_file).read_text(encoding="utf-8")


def test_apply_without_report(tmp_settings):
    from newsrag.cli import app

    before = _pipeline(tmp_settings)
    result = runner.invoke(app, ["embed", "bakeoff", "--apply"])
    assert result.exit_code == 1
    assert "no bake-off report" in result.output
    tmp_settings.embed_bakeoff_report_file.write_text("questions: 3\n\nwinner: none\n")
    result = runner.invoke(app, ["embed", "bakeoff", "--apply"])
    assert result.exit_code == 1
    assert "winner: none" in result.output
    assert _pipeline(tmp_settings) == before


def test_apply_writes_winner(tmp_settings, monkeypatch):
    from newsrag.cli import app

    tmp_settings.embed_bakeoff_report_file.write_text(
        "questions: 3\n\n| ... |\n\nwinner: qwen3-embedding-8b\n"
    )
    result = runner.invoke(app, ["embed", "bakeoff", "--apply"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == (
        "embed_model set to qwen3-embedding-8b; chunk and index stages will rerun"
    )
    text = _pipeline(tmp_settings)
    assert "embed_model: qwen3-embedding-8b" in text and text.startswith("# test pipeline")

    s = tmp_settings.model_copy(update={"embed_model": "qwen3-embedding-8b"})
    monkeypatch.setattr(config, "settings", s)
    result = runner.invoke(app, ["embed", "bakeoff", "--apply"])
    assert result.exit_code == 0
    assert result.output.strip() == "embed_model unchanged (qwen3-embedding-8b)"
    assert _pipeline(s) == text


def test_apply_and_cleanup_exclusive(tmp_settings):
    from newsrag.cli import app

    result = runner.invoke(app, ["embed", "bakeoff", "--apply", "--cleanup"])
    assert result.exit_code != 0


# --- the stores -------------------------------------------------------------------------------


@pytest.mark.stores
def test_embed_corpus_and_cleanup_on_qdrant(monkeypatch):
    from newsrag import embed
    from newsrag.embed import index
    from newsrag.stores import qdrant

    prefix = f"test_{secrets.token_hex(4)}_"
    s = config.settings.model_copy(update={"store_alias_prefix": prefix, "embed_bakeoff_slab": 2})
    monkeypatch.setattr(config, "settings", s)
    chunks = corpus(3)
    generation = "fake__v1"
    source = index.ensure_collection(generation, 8)
    try:
        from qdrant_client import models as qm

        emb = FakeEmbedder(8)
        payloads = payloads_for(chunks)
        qdrant().upsert(
            source,
            points=[
                qm.PointStruct(id=pid, vector=[1.0] * 8, payload=payload)
                for pid, payload in payloads.items()
            ],
            wait=True,
        )
        read = bk.load_payloads(generation)
        assert read == payloads

        target = bk.collection_for("bge-m3")
        assert target == f"{prefix}bakeoff__bge-m3"
        bk.embed_corpus(emb, chunks, read, target, echo=lambda m: None)
        bk.embed_corpus(emb, chunks[:1], read, target, echo=lambda m: None)  # recreated
        points, _ = qdrant().scroll(target, limit=10, with_payload=True, with_vectors=True)
        assert len(points) == 1
        assert points[0].payload == payloads[str(points[0].id)]
        assert points[0].vector != [1.0] * 8  # new vector, not the source's

        assert bk.cleanup() == [target]
        assert not qdrant().collection_exists(target)
        assert qdrant().collection_exists(source)  # not a bakeoff collection
    finally:
        for name in (source, *bk.bakeoff_collections()):
            if qdrant().collection_exists(name):
                qdrant().delete_collection(name)
        assert embed.collection_name(generation) == source
