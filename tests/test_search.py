"""Ticket 025: hybrid search (fusion, filters, generation/collection override, backends)."""

from __future__ import annotations

import secrets
from datetime import date
from typing import Any

import pytest

from newsrag import config
from newsrag.search import hybrid

# --- unit -------------------------------------------------------------------------------------


def test_rrf_fusion_order():
    # a: dense 1 + bm25 3; b: dense 2 + bm25 1; c: dense 3 only; d: bm25 2 only
    fused = hybrid.rrf_fuse([["a", "b", "c"], ["b", "d", "a"]], c=60)
    ids = [doc for doc, _ in fused]
    assert ids == ["b", "a", "d", "c"]
    scores = dict(fused)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 63)
    assert scores["d"] == pytest.approx(1 / 62)
    assert scores["c"] == pytest.approx(1 / 63)
    # a tie keeps first-seen order (dense list first)
    assert [d for d, _ in hybrid.rrf_fuse([["x"], ["y"]])] == ["x", "y"]


def test_bm25_body_fields_and_filter():
    body = hybrid.bm25_body("q", 50, {})
    assert body["size"] == 50
    assert body["query"]["multi_match"]["fields"] == [
        "headline_te^2",
        "text_te",
        "headline_en^2",
        "text_en",
    ]
    filtered = hybrid.bm25_body("q", 50, {"page_ids": ["p1", "p2"]})
    assert filtered["query"]["bool"]["filter"] == [{"terms": {"page_ids": ["p1", "p2"]}}]


def test_unknown_filter_rejected():
    with pytest.raises(ValueError, match="unknown search filter"):
        hybrid.check_filters({"district": "x"})
    with pytest.raises(ValueError, match="list"):
        hybrid.check_filters({"page_ids": "p1"})


def test_baseline_not_configured_error():
    hybrid.unregister_backend("baseline")
    with pytest.raises(hybrid.StoreNotConfigured, match="^baseline stores not configured$"):
        hybrid.search("q", store="baseline")


def test_baseline_backend_registry():
    calls: list[dict[str, Any]] = []

    def backend(query: str, **kwargs: Any) -> list[hybrid.Hit]:
        calls.append({"query": query, **kwargs})
        return [hybrid.Hit("c1", "s1", 1.0, ["p1"], "t", "h", "2026-04-01")]

    hybrid.register_backend("baseline", backend)
    try:
        hits = hybrid.search("q", k=3, store="baseline", filters={"page_ids": ["p1"]})
    finally:
        hybrid.unregister_backend("baseline")
    assert [h.chunk_id for h in hits] == ["c1"]
    assert calls[0]["query"] == "q" and calls[0]["k"] == 3
    assert calls[0]["filters"] == {"page_ids": ["p1"]}


def test_profile_of_generation():
    assert hybrid.profile_of("bge-m3__v1") == "bge-m3"
    assert hybrid.profile_of("multilingual-e5-large__v2") == "multilingual-e5-large"


def test_search_cli_line():
    from newsrag.search.cli import hit_line

    hit = hybrid.Hit("c", "s", 0.03125, ["2025-06-10_A_main_01", "x"], "", "h " * 50, "2025-06-10")
    line = hit_line(3, hit)
    assert line.startswith(" 3  0.0312  2025-06-10  2025-06-10_A_main_01  h h")
    assert len(line.split("2025-06-10_A_main_01  ", 1)[1]) == 60


# --- stores -----------------------------------------------------------------------------------

DIM = 4


def _vec(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i % DIM] = 1.0
    return v


def _chunk(chunk_id: str, story_id: str, page: str, text: str, day: date) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "story_id": story_id,
        "seq": 1,
        "kind": "news",
        "headline": f"headline {story_id}",
        "text": text,
        "prefix": "",
        "n_tokens": 10,
        "pub_date": day.isoformat(),
        "page_ids": [page],
    }


@pytest.fixture
def stores(monkeypatch):
    """A throw-away warehouse schema and alias prefix holding generation `fake__t1` (and a
    second Qdrant collection `alt`, the reverse of it, for the collection override)."""
    from psycopg import sql

    from newsrag.embed import index as store_index
    from newsrag.stores import pg, qdrant
    from newsrag.warehouse import db as wdb

    suffix = secrets.token_hex(4)
    schema, prefix = f"test_wh_{suffix}", f"test{suffix}_"
    monkeypatch.setattr(
        config,
        "settings",
        config.settings.model_copy(
            update={"warehouse_schema": schema, "store_alias_prefix": prefix}
        ),
    )
    day = date(2025, 6, 10)
    chunks = [
        _chunk("s1#c01", "s1", "P1", "రైతులకు రుణమాఫీ ప్రకటన", day),
        _chunk("s2#c01", "s2", "P2", "క్రికెట్ మ్యాచ్ విజయం", day),
        _chunk("s3#c01", "s3", "P3", "budget deficit grows", day),
        _chunk("s4#c01", "s4", "P4", "వర్షాలు వరదలు", day),
    ]
    wdb.migrate()
    alt = f"{prefix}alt"
    try:
        for gen in ("fake__t1", "other__t2"):
            store_index.ensure_collection(gen, DIM)
            store_index.ensure_index(gen)
            store_index.qdrant_replace_date(gen, day, chunks, [_vec(i) for i in range(4)])
            store_index.os_replace_date(gen, day, chunks)
        with wdb.conn() as c:
            for gen in ("fake__t1", "other__t2"):
                for ch in chunks:
                    c.execute(
                        "insert into chunks (generation, chunk_id, story_id, pub_date, seq, kind,"
                        " headline, prefix, text, n_tokens) values (%s,%s,%s,%s,1,'news',%s,'',"
                        "%s,10)",
                        (gen, ch["chunk_id"], ch["story_id"], day, ch["headline"],
                         f"{gen}: {ch['text']}"),
                    )  # fmt: skip
        wdb.set_active_generation("fake__t1")
        # `alt`: the same points with the vectors reversed (s4 nearest where s1 was)
        from qdrant_client import models as qm

        qdrant().create_collection(
            alt, vectors_config=qm.VectorParams(size=DIM, distance=qm.Distance.COSINE)
        )
        qdrant().create_payload_index(alt, field_name="page_ids", field_schema="keyword")
        qdrant().upsert(
            alt,
            points=[
                qm.PointStruct(id=i, vector=_vec(3 - i), payload=ch) for i, ch in enumerate(chunks)
            ],
            wait=True,
        )
        yield {"alt": alt}
    finally:
        if qdrant().collection_exists(alt):
            qdrant().delete_collection(alt)
        store_index.drop_all(["fake__t1", "other__t2"])
        with pg().connection() as c:
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def fake_query(monkeypatch):
    """Query vectors without a model: the query names the dimension, e.g. `dim0 ...`."""
    profiles: list[str] = []

    def embed_query(query: str, profile: str) -> list[float]:
        profiles.append(profile)
        word = next((w for w in query.split() if w.startswith("dim")), "dim0")
        return _vec(int(word[3:]))

    monkeypatch.setattr(hybrid, "embed_query", embed_query)
    return profiles


@pytest.mark.stores
def test_search_hits_from_active_generation(stores, fake_query):
    hits = hybrid.search("dim0 రుణమాఫీ", k=3)
    assert hits[0].chunk_id == "s1#c01"  # first in both lists
    assert hits[0].page_ids == ["P1"] and hits[0].story_id == "s1"
    assert hits[0].text == "fake__t1: రైతులకు రుణమాఫీ ప్రకటన"  # read from Postgres
    assert hits[0].headline == "headline s1" and hits[0].pub_date == "2025-06-10"
    assert len(hits) == 3
    assert fake_query == ["fake"]  # the profile named by the active generation


@pytest.mark.stores
def test_page_ids_filter(stores, fake_query):
    hits = hybrid.search("dim0 రుణమాఫీ budget", k=10, filters={"page_ids": ["P3", "P4"]})
    assert {h.chunk_id for h in hits} == {"s3#c01", "s4#c01"}
    assert all(set(h.page_ids) & {"P3", "P4"} for h in hits)


@pytest.mark.stores
def test_generation_and_collection_override(stores, fake_query):
    hits = hybrid.search("dim1 nothing", k=1, generation="other__t2")
    assert hits[0].chunk_id == "s2#c01" and hits[0].text.startswith("other__t2: ")
    assert fake_query[-1] == "other"
    # the dense side from `alt` (dim1 -> s3 there); BM25 finds nothing for this query
    hits = hybrid.search("dim1 zzz", k=1, qdrant_collection=stores["alt"])
    assert hits[0].chunk_id == "s3#c01" and hits[0].text.startswith("fake__t1: ")


@pytest.mark.stores
def test_embed_profile_flag(stores, fake_query):
    hybrid.search("dim0 q", k=1, embed_profile="candidate-model")
    assert fake_query == ["candidate-model"]
