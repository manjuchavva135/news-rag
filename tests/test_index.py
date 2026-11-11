"""Ticket 024: embedding and indexing into Qdrant, OpenSearch and the Postgres warehouse.

Tests marked `stores` need the docker stores. They run in a throw-away warehouse schema, a
throw-away ledger schema and under a throw-away `store_alias_prefix`, with a fake embedder (no
GPU); they never touch the real `warehouse` schema or the real `chunks` aliases. The two tests
marked `data` read the real index.
"""

from __future__ import annotations

import json
import secrets
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from newsrag import aliases, config, embed
from newsrag.cli import app
from newsrag.embed import generations, records
from newsrag.models import Embedder, Profile
from newsrag.stages.base import StageUnavailable
from newsrag.stages.index import IndexStage
from newsrag.warehouse import rows as wrows

runner = CliRunner()
DAY = date(2026, 4, 3)
DIM = 8


# --- a fake embedder and a small world of fake chunks ------------------------------------------


class FakeEmbedder:
    """Deterministic unit vectors from the text; remembers what it was asked."""

    def __init__(self, dim: int = DIM, salt: str = "") -> None:
        self.dim = dim
        self.salt = salt
        self.calls: list[tuple[list[str], str, int]] = []

    def embed(self, texts: Any, kind: str = "doc", batch_size: int = 64) -> np.ndarray:
        items = [texts] if isinstance(texts, str) else list(texts)
        self.calls.append((items, kind, batch_size))
        out = np.zeros((len(items), self.dim), dtype=np.float32)
        for i, text in enumerate(items):
            seed = int.from_bytes(f"{self.salt}{text}".encode()[:8].ljust(8, b"\0"), "big")
            vec = np.random.default_rng(seed).normal(size=self.dim)
            out[i] = vec / np.linalg.norm(vec)
        return out


TEXTS = {
    "s1": ("వర్షాలు విజయనగరం జిల్లాలో", "విజయనగరం జిల్లాలో భారీ వర్షాలు కురిశాయి. రైతులు ఆనందం వ్యక్తం చేశారు."),
    "s2": ("Budget session", "The finance minister presented the budget in the assembly today."),
    "s3": ("ప్రకటన", "టెండర్ నోటీసు నంబర్ 45 గడువు ఏప్రిల్ పదిహేను"),
}


def chunk_record(story_id: str, seq: int, pub_date: date, **over: Any) -> dict[str, Any]:
    headline, text = TEXTS[story_id.split("_")[-1]]
    record = {
        "chunk_id": f"{story_id}#c{seq:02d}",
        "story_id": story_id,
        "kind": "news",
        "seq": seq,
        "headline": headline,
        "prefix": f"{headline} | {pub_date.strftime('%d-%m-%Y')} | 1 printing",
        "text": text,
        "n_tokens": 40,
        "pub_date": pub_date.isoformat(),
        "event_date": pub_date.isoformat(),
        "category": "weather",
        "editions": ["HYD"],
        "edition_names": ["Hyderabad"],
        "edition_districts": ["hyderabad"],
        "edition_states": ["telangana"],
        "states": ["andhra_pradesh"],
        "districts_current": ["vizianagaram"],
        "districts_at_print": ["vizianagaram"],
        "entity_ids": ["e_1"],
        "facts_ids": [f"{story_id}#f001"],
        "department": None,
        "notice_number": None,
        "deadline": None,
        "page_ids": [f"{pub_date.isoformat()}_HYD_main_1"],
        "canonical_page_id": f"{pub_date.isoformat()}_HYD_main_1",
        "low_confidence": False,
        "low_confidence_reasons": [],
    }
    record.update(over)
    return record


class World:
    """Fake ledger rows, artifacts and alias table for a few stories of one or more dates."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.entities = [
            {
                "entity_id": f"e_{i}",
                "type": "person",
                "org_type": None,
                "canonical": f"పేరు {i}",
                "canonical_te": f"పేరు {i}",
                "canonical_en": None,
                "aliases": [f"పేరు {i}"],
                "story_count": 1,
            }
            for i in (1, 2, 3)
        ]
        self.write_entities()

    def write_entities(self) -> None:
        path = aliases.entities_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        name_to_entity = {
            aliases.key("person", e["canonical"]): e["entity_id"] for e in self.entities
        }
        path.write_text(
            json.dumps({"entities": self.entities, "name_to_entity": name_to_entity}),
            encoding="utf-8",
        )

    def _run(self, pub_date: date, stage: str, payload: dict[str, Any]) -> None:
        from newsrag.ledger import db

        path = self.root / stage / f"{pub_date.isoformat()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with db.conn() as c:
            c.execute("delete from date_runs where pub_date = %s and stage = %s", (pub_date, stage))
            c.execute(
                "insert into date_runs (pub_date, stage, version, status, artifact_path) "
                "values (%s, %s, 'v', 'done', %s)",
                (pub_date, stage, str(path)),
            )

    def build(self, pub_date: date, story_ids: list[str], chunks_per_story: int = 1) -> list[dict]:
        """Write the dedup, enrich and chunk artifacts of the date; returns the chunk records."""
        from newsrag.ledger import db

        day = pub_date.isoformat()
        page_id = f"{day}_HYD_main_1"
        db.upsert_page(
            page_id=page_id,
            pub_date=pub_date,
            edition_code="HYD",
            section="main",
            page_num=1,
            source_path=f"/src/{page_id}.pdf",
            source_archive="test.rar",
        )
        with db.conn() as c:
            c.execute(
                "insert into page_editions (page_id, edition_names, districts_current, states, "
                "source) values (%s, %s, %s, %s, 'test') on conflict (page_id) do nothing",
                (page_id, ["Hyderabad"], ["hyderabad"], ["telangana"]),
            )
        stories, core, places, facts, chunks = [], {}, {}, {}, []
        for story_id in story_ids:
            headline, text = TEXTS[story_id.split("_")[-1]]
            stories.append(
                {
                    "story_id": story_id,
                    "kind": "news",
                    "canonical_unit_id": f"{story_id}@u",
                    "headline": headline,
                    "text": text,
                    "department": None,
                    "notice_number": None,
                    "deadline": None,
                    "printings": [
                        {
                            "unit_id": f"{story_id}@u",
                            "page_id": page_id,
                            "edition_code": "HYD",
                            "section": "main",
                            "page_num": 1,
                            "parts": [{"article_id": f"{story_id}@u", "page_id": page_id}],
                            "flags": [],
                        }
                    ],
                    "flags": [],
                }
            )
            core[story_id] = {
                "category": "weather",
                "people": [{"name": "పేరు 1", "role": "మంత్రి"}],
                "organisations": [],
                "event_date": day,
                "flags": [],
            }
            places[story_id] = {
                "places": [
                    {
                        "name": "విజయనగరం",
                        "llm_type": "city",
                        "kind": "town",
                        "id": "vizianagaram",
                        "state": "andhra_pradesh",
                        "districts_current": ["vizianagaram"],
                        "districts_at_print": ["vizianagaram"],
                        "flags": [],
                    }
                ],
                "districts_current": ["vizianagaram"],
                "districts_at_print": ["vizianagaram"],
                "states": ["andhra_pradesh"],
                "flags": [],
            }
            facts[story_id] = {
                "facts": [
                    {
                        "fact_id": f"{story_id}#f001",
                        "value_text": "45",
                        "value": 45,
                        "scale": "none",
                        "normalized_value": 45,
                        "unit": "count",
                        "refers_to": "notice",
                        "period_text": None,
                        "period_start": day,
                        "period_end": day,
                        "place": None,
                        "districts_current": [],
                        "districts_at_print": [],
                        "state": None,
                    }
                ],
                "flags": [],
            }
            for seq in range(1, chunks_per_story + 1):
                chunks.append(chunk_record(story_id, seq, pub_date))
        self._run(pub_date, "dedup", {"pub_date": day, "stories": stories, "excluded": {}})
        for stage, stories_of in (
            ("enrich_core", core),
            ("enrich_places", places),
            ("enrich_facts", facts),
        ):
            self._run(pub_date, stage, {"pub_date": day, "stories": stories_of})
        self._run(pub_date, "chunk", {"pub_date": day, "chunks": chunks})
        return chunks


def _count(table: str) -> int:
    from newsrag.warehouse import db as wdb

    with wdb.conn() as c:
        return c.execute(f"select count(*) from {table}").fetchone()[0]


@pytest.fixture
def env(ledger, tmp_path, monkeypatch):
    """Throw-away ledger schema, warehouse schema and alias prefix; a World of fake artifacts."""
    from psycopg import sql

    from newsrag.embed import index as store_index
    from newsrag.stores import pg
    from newsrag.warehouse import db as wdb

    suffix = secrets.token_hex(4)
    schema, prefix = f"test_wh_{suffix}", f"test{suffix}_"
    monkeypatch.setattr(
        config,
        "settings",
        config.settings.model_copy(
            update={
                "warehouse_schema": schema,
                "store_alias_prefix": prefix,
                "alias_dir": tmp_path / "aliases",
            }
        ),
    )
    wdb.migrate()
    try:
        yield World(tmp_path / "artifacts")
    finally:
        store_index.drop_all()
        with pg().connection() as c:
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def make_stage(**kwargs: Any) -> IndexStage:
    stage = IndexStage(embedder=kwargs.pop("embedder", FakeEmbedder()), **kwargs)
    stage.open()
    return stage


def counts_by_store(generation: str, day: date) -> tuple[int, int, int]:
    from newsrag.embed import index as store_index
    from newsrag.warehouse import write as wwrite

    return (
        store_index.qdrant_counts(generation).get(day, 0),
        store_index.os_counts(generation).get(day, 0),
        wwrite.counts(generation).get(day, 0),
    )


# --- unit tests: records, rows, embedding input -----------------------------------------------


def test_point_id_is_uuid5_of_chunk_id():
    import uuid

    cid = "s_abc#c01"
    assert records.point_id(cid) == str(uuid.uuid5(uuid.NAMESPACE_URL, cid))
    assert records.point_id(cid) == records.point_id(cid) != records.point_id("s_abc#c02")


def test_date_int():
    assert records.date_int("2026-04-03") == 20260403
    assert records.date_int(date(2020, 4, 2)) == 20200402
    assert records.date_int(None) is None
    assert records.date_int("garbage") is None


def test_qdrant_payload_drops_text_and_prefix_adds_date_ints():
    chunk = chunk_record("s_s1", 1, DAY, event_date="2026-04-01")
    payload = records.qdrant_payload(chunk)
    assert "text" not in payload and "prefix" not in payload
    assert payload["pub_date_int"] == 20260403 and payload["event_date_int"] == 20260401
    assert set(chunk) - {"text", "prefix"} <= set(payload)
    for field in records.QDRANT_KEYWORD_INDEXES + records.QDRANT_INT_INDEXES:
        assert field in payload
    assert "low_confidence" in payload


def test_script_routing():
    telugu = "విజయనగరం జిల్లాలో భారీ వర్షాలు కురిశాయి"
    assert records.text_fields(telugu, 0.2) == ["te"]
    assert records.text_fields("The budget was presented today", 0.2) == ["en"]
    both = "విజయనగరం జిల్లాలో వర్షాలు budget session"
    assert records.text_fields(both, 0.2) == ["te", "en"]
    assert records.text_fields("1234 -- 56", 0.2) == ["te"]  # no letters: the newspaper language
    # a few Latin letters (below 20%) in a Telugu text do not add `_en`
    assert records.text_fields(telugu + " GST", 0.2) == ["te"]


def test_os_document_fields():
    doc = records.os_document(chunk_record("s_s1", 1, DAY), 0.2)
    assert doc["text_te"] and "text_en" not in doc
    assert doc["headline_te"] and doc["headline"]
    assert doc["page_ids"] == ["2026-04-03_HYD_main_1"] and doc["low_confidence"] is False
    assert doc["pub_date_int"] == 20260403
    english = records.os_document(chunk_record("s_s2", 1, DAY), 0.2)
    assert english["text_en"] and english["headline_en"] and "text_te" not in english


def test_build_date_rows_only_stories_with_chunks():
    stories = [
        {
            "story_id": "s_s1",
            "kind": "news",
            "headline": "h",
            "text": "t",
            "printings": [
                {
                    "unit_id": "u1",
                    "page_id": "p1",
                    "edition_code": "HYD",
                    "section": "main",
                    "page_num": 1,
                    "parts": [{"article_id": "u1"}],
                },
                {
                    "unit_id": "u2",
                    "page_id": "p2",
                    "edition_code": "VJA",
                    "section": "main",
                    "page_num": 2,
                    "parts": [],
                },
            ],
        },
        {"story_id": "s_none", "kind": "news", "headline": "x", "text": "", "printings": []},
    ]
    chunks = [
        chunk_record("s_s1", 1, DAY, low_confidence=True, low_confidence_reasons=["ocr"]),
        chunk_record("s_s1", 2, DAY, low_confidence_reasons=["table"]),
    ]
    enriched = {
        "s_s1": {
            "people": [{"name": "పేరు 1", "role": "x"}, {"name": "unknown", "role": "x"}],
            "organisations": [],
            "places": [
                {
                    "name": "A",
                    "llm_type": "city",
                    "kind": "town",
                    "state": "x",
                    "districts_current": ["d"],
                    "districts_at_print": ["d"],
                }
            ],
            "facts": [
                {
                    "fact_id": "s_s1#f001",
                    "value": 1.5,
                    "period_start": "2026-04-03",
                    "period_end": "bad",
                    "state": "telangana",
                }
            ],
        }
    }
    rows = wrows.build_date_rows(
        DAY, "g", stories, enriched, chunks, {aliases.key("person", "పేరు 1"): "e_1"}, []
    )
    assert rows.story_ids() == ["s_s1"]
    story = rows.stories[0]
    assert story["n_printings"] == 2 and story["low_confidence"] is True
    assert story["low_confidence_reasons"] == ["ocr"]  # the story's own, not the table's
    assert [p["unit_id"] for p in rows.story_printings] == ["u1", "u2"]
    assert [c["generation"] for c in rows.chunks] == ["g", "g"]
    assert rows.story_entities == [
        {"story_id": "s_s1", "entity_id": "e_1", "surface_forms": ["పేరు 1"]}
    ]
    assert rows.story_places[0]["seq"] == 1 and rows.story_places[0]["districts_current"] == ["d"]
    fact = rows.facts[0]
    assert fact["period_start"] == DAY and fact["period_end"] is None
    assert fact["state"] == "telangana" and fact["districts_current"] == []


def test_validate_line_format():
    assert generations.line((DAY, 5, 5, 5)) == "ok 2026-04-03 5"
    assert generations.line((DAY, 0, 0, 0)) == "ok 2026-04-03 0"
    assert generations.line((DAY, 5, 4, 5)) == "MISMATCH 2026-04-03 q=5 os=4 pg=5"


def test_generation_names(monkeypatch):
    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"store_alias_prefix": "p_"})
    )
    assert embed.generation_name("bge-m3") == "bge-m3__v1"
    assert embed.alias_name() == "p_chunks"
    assert embed.collection_name("bge-m3__v1") == "p_chunks__bge-m3__v1"
    assert embed.generation_of("p_chunks__bge-m3__v1") == "bge-m3__v1"
    assert embed.generation_of("chunks__bge-m3__v1") is None


def test_registered_and_version():
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["index"]
    assert isinstance(stage, IndexStage) and stage.depends_on == ("chunk",)
    assert stage.version.startswith("index-v1-bge-m3-")
    assert stage.models_needed == ("bge-m3",)


def test_version_follows_embed_model(monkeypatch):
    stage = IndexStage()
    before = stage.version
    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"embed_model": "other"})
    )
    assert stage.version.startswith("index-v1-other-") and stage.version != before


def test_doc_prefix_once():
    """The stage passes `prefix + "\\n" + text` with kind doc; the model adds `doc_prefix` once."""
    seen: list[list[str]] = []

    class Model:
        def encode(self, texts, **kwargs):
            seen.append(list(texts))
            return np.ones((len(texts), 4), dtype=np.float32)

    profile = Profile(
        name="t", hf_id="x/t", family="f", kind="embed", dim=4, doc_prefix="passage: "
    )
    stage = IndexStage(embedder=Embedder(profile, Model()))
    chunks = [chunk_record("s_s1", 1, DAY)]
    vectors = stage.embed_chunks(chunks)
    assert seen == [[f"passage: {chunks[0]['prefix']}\n{chunks[0]['text']}"]]
    assert vectors.shape == (1, 4) and abs(np.linalg.norm(vectors[0]) - 1) < 1e-6


def test_fake_embedder_is_used_and_batch_size_passed(monkeypatch):
    fake = FakeEmbedder()
    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"embed_batch_size": 7})
    )
    stage = IndexStage(embedder=fake)
    stage.open()  # an injected embedder: no model is loaded
    vectors = stage.embed_chunks([chunk_record("s_s1", 1, DAY), chunk_record("s_s2", 1, DAY)])
    assert vectors.shape == (2, DIM)
    assert fake.calls[0][1:] == ("doc", 7)
    assert stage.embed_chunks([]).shape == (0, DIM)


# --- tests that need the stores ----------------------------------------------------------------


@pytest.mark.stores
def test_schema_isolation(env):
    """E-6: a test run writes only to its throw-away schema, collections and aliases."""
    from newsrag.embed import index as store_index
    from newsrag.stores import pg, qdrant

    def real_only():
        """The real warehouse's story count and active generation, the real `chunks` alias and
        the real collections: none of it may change."""
        with pg().connection() as c:
            has = c.execute("select to_regclass('warehouse.stories') is not null").fetchone()[0]
            n = c.execute("select count(*) from warehouse.stories").fetchone()[0] if has else 0
            gens = (
                c.execute("select generation from warehouse.active_generation").fetchall()
                if has
                else []
            )
        real_alias = [
            (a.alias_name, a.collection_name)
            for a in qdrant().get_aliases().aliases
            if a.alias_name == "chunks"
        ]
        real_collections = sorted(
            c.name for c in qdrant().get_collections().collections if c.name.startswith("chunks__")
        )
        return n, gens, real_alias, real_collections

    before = real_only()
    env.build(DAY, ["s_s1", "s_s2"])
    stage = make_stage()
    stage.handle(DAY, [])
    assert real_only() == before
    assert config.settings.store_alias_prefix.startswith("test")
    assert store_index.qdrant_generations() == ["bge-m3__v1"]
    assert _count("stories") == 2 and _count("chunks") == 2


@pytest.mark.stores
def test_fake_embedder_end_to_end(env, monkeypatch):
    """E-6: `stores` tests need no GPU; every store holds the date, with payload and fields."""
    from newsrag import models
    from newsrag.embed import index as store_index
    from newsrag.stores import opensearch, qdrant
    from newsrag.warehouse import db as wdb

    def no_gpu(*args, **kwargs):
        raise AssertionError("the model must not be loaded")

    monkeypatch.setattr(models, "use", no_gpu)
    env.build(DAY, ["s_s1", "s_s2", "s_s3"])
    stage = make_stage()
    result = stage.handle(DAY, [])
    gen = "bge-m3__v1"
    assert counts_by_store(gen, DAY) == (3, 3, 3)
    assert result.detail["chunks"] == 3 and result.detail["chunks_per_s"] > 0
    assert result.detail["activated"] is True  # the first generation ever written

    name = embed.collection_name(gen)
    info = qdrant().get_collection(name)
    assert info.config.params.vectors.size == DIM
    assert info.config.params.vectors.distance.name == "COSINE"
    point = qdrant().retrieve(name, [records.point_id("s_s1#c01")], with_vectors=True)[0]
    assert point.payload["chunk_id"] == "s_s1#c01" and "text" not in point.payload
    assert point.payload["pub_date_int"] == 20260403
    assert abs(np.linalg.norm(point.vector) - 1) < 1e-5
    indexed = set(info.payload_schema)
    assert set(records.QDRANT_KEYWORD_INDEXES + records.QDRANT_INT_INDEXES) <= indexed

    doc = opensearch().get(index=name, id="s_s2#c01")["_source"]
    assert doc["text_en"] and "text_te" not in doc and doc["pub_date_int"] == 20260403

    with wdb.conn() as c:
        assert c.execute("select count(*) from pages").fetchone()[0] == 1
        printings = c.execute("select count(*) from story_printings").fetchone()[0]
        assert printings == 3 and _count("facts") == 3 and _count("story_places") == 3
        assert c.execute("select count(*) from story_entities").fetchone()[0] == 3
        assert c.execute("select count(*) from entities").fetchone()[0] == 3
        page = c.execute("select edition_names, districts_current, states from pages").fetchone()
        assert page == (["Hyderabad"], ["hyderabad"], ["telangana"])
    assert store_index.qdrant_alias_target() == gen
    assert store_index.os_alias_targets() == [gen]
    assert wdb.active_generation() == gen


@pytest.mark.stores
def test_warehouse_missing_preflight(env, monkeypatch):
    """E-6: no warehouse schema, no run."""
    monkeypatch.setattr(
        config,
        "settings",
        config.settings.model_copy(update={"warehouse_schema": "test_wh_missing_" + "x" * 8}),
    )
    with pytest.raises(StageUnavailable, match="run 'newsrag db init' first"):
        IndexStage(embedder=FakeEmbedder()).preflight()


@pytest.mark.stores
def test_preflight_passes_when_ready(env):
    IndexStage(embedder=FakeEmbedder()).preflight()


@pytest.mark.stores
def test_reindex_idempotent(env):
    """E-4: running a date again leaves every store as it was (same ids, no duplicates)."""
    from newsrag.embed import index as store_index
    from newsrag.stores import qdrant
    from newsrag.warehouse import db as wdb

    env.build(DAY, ["s_s1", "s_s2"], chunks_per_story=2)
    stage = make_stage()
    stage.handle(DAY, [])
    gen = "bge-m3__v1"
    tables = ("stories", "story_printings", "chunks", "story_entities", "story_places", "facts")
    first = {t: _count(t) for t in tables}
    ids = sorted(
        p.id for p in qdrant().scroll(embed.collection_name(gen), limit=100, with_payload=False)[0]
    )
    result = stage.handle(DAY, [])
    assert {t: _count(t) for t in tables} == first
    assert counts_by_store(gen, DAY) == (4, 4, 4)
    assert result.detail["activated"] is False
    again = sorted(
        p.id for p in qdrant().scroll(embed.collection_name(gen), limit=100, with_payload=False)[0]
    )
    assert again == ids
    assert store_index.os_counts(gen) == {DAY: 4}
    with wdb.conn() as c:
        assert c.execute("select count(*) from pages").fetchone()[0] == 1


@pytest.mark.stores
def test_stale_removed(env):
    """E-4: a story that is no longer there, and a chunk that is gone, disappear everywhere."""
    from newsrag.stores import qdrant

    other = date(2026, 4, 4)
    env.build(DAY, ["s_s1", "s_s2"], chunks_per_story=2)
    env.build(other, ["s_s3"])
    stage = make_stage()
    stage.handle(DAY, [])
    stage.handle(other, [])
    gen = "bge-m3__v1"
    assert counts_by_store(gen, DAY) == (4, 4, 4)

    env.build(DAY, ["s_s1"], chunks_per_story=1)  # s_s2 gone, s_s1 lost its second chunk
    result = stage.handle(DAY, [])
    assert result.detail["removed_stories"] == 1
    assert counts_by_store(gen, DAY) == (1, 1, 1)
    assert counts_by_store(gen, other) == (1, 1, 1)  # the other date is untouched
    from newsrag.warehouse import db as wdb

    with wdb.conn() as c:
        left = {r[0] for r in c.execute("select story_id from stories")}
        assert left == {"s_s1", "s_s3"}
        for table in ("story_printings", "story_entities", "story_places", "facts"):
            ids = {r[0] for r in c.execute(f"select story_id from {table}")}
            assert ids == {"s_s1", "s_s3"}, table
        chunk_ids = {r[0] for r in c.execute("select chunk_id from chunks")}
        assert chunk_ids == {"s_s1#c01", "s_s3#c01"}
    points = qdrant().scroll(embed.collection_name(gen), limit=100, with_payload=True)[0]
    assert {p.payload["chunk_id"] for p in points} == {"s_s1#c01", "s_s3#c01"}


@pytest.mark.stores
def test_empty_date_clears_old_rows(env):
    env.build(DAY, ["s_s1"])
    stage = make_stage()
    stage.handle(DAY, [])
    env.build(DAY, [])
    result = stage.handle(DAY, [])
    assert result.detail["chunks"] == 0 and result.detail["chunks_per_s"] == 0
    assert counts_by_store("bge-m3__v1", DAY) == (0, 0, 0)
    assert _count("stories") == 0


@pytest.mark.stores
def test_activate_generation(env):
    """E-5: `index activate` moves both aliases and `warehouse.active_generation` together."""
    from newsrag.embed import index as store_index
    from newsrag.warehouse import db as wdb

    env.build(DAY, ["s_s1", "s_s2"])
    first = make_stage(generation="m1__v1")
    first.handle(DAY, [])
    second = make_stage(embedder=FakeEmbedder(dim=4, salt="x"), generation="m2__v1")
    result = second.handle(DAY, [])
    assert result.detail["activated"] is False  # only the first generation activates itself
    assert wdb.active_generation() == "m1__v1"
    assert store_index.qdrant_alias_target() == "m1__v1"

    out = runner.invoke(app, ["index", "collections"])
    assert out.exit_code == 0, out.output
    lines = {line.split()[0]: line for line in out.output.splitlines()}
    assert lines["m1__v1"].endswith("active") and not lines["m2__v1"].endswith("active")
    assert "qdrant=2 opensearch=2 postgres=2" in lines["m2__v1"]

    out = runner.invoke(app, ["index", "activate", "m2__v1"])
    assert out.exit_code == 0, out.output
    assert store_index.qdrant_alias_target() == "m2__v1"
    assert store_index.os_alias_targets() == ["m2__v1"]
    assert wdb.active_generation() == "m2__v1"
    out = runner.invoke(app, ["index", "collections"])
    lines = {line.split()[0]: line for line in out.output.splitlines()}
    assert lines["m2__v1"].endswith("active") and not lines["m1__v1"].endswith("active")

    out = runner.invoke(app, ["index", "activate", "nope__v9"])
    assert out.exit_code == 1 and "unknown generation" in out.output
    assert wdb.active_generation() == "m2__v1"


@pytest.mark.stores
def test_validate_ok_and_mismatch(env):
    from newsrag.stores import qdrant

    env.build(DAY, ["s_s1", "s_s2"])
    make_stage().handle(DAY, [])
    out = runner.invoke(app, ["index", "validate"])
    assert out.exit_code == 0 and out.output.strip() == "ok 2026-04-03 2", out.output
    out = runner.invoke(app, ["index", "validate", "--date", "2026-04-03"])
    assert out.exit_code == 0 and out.output.strip() == "ok 2026-04-03 2"

    from qdrant_client import models as qm

    name = embed.collection_name("bge-m3__v1")
    qdrant().delete(
        name, points_selector=qm.PointIdsList(points=[records.point_id("s_s1#c01")]), wait=True
    )
    out = runner.invoke(app, ["index", "validate"])
    assert out.exit_code == 1
    assert out.output.strip() == "MISMATCH 2026-04-03 q=1 os=2 pg=2"


@pytest.mark.stores
def test_validate_union_dates(env):
    """E-6: dates with `index` done in the ledger and dates found in any store are checked."""
    from qdrant_client import models as qm

    from newsrag.ledger import db
    from newsrag.stores import qdrant

    env.build(DAY, ["s_s1"])
    make_stage().handle(DAY, [])
    empty = date(2026, 4, 9)
    with db.conn() as c:  # `index` done for a date that has no chunks anywhere
        c.execute(
            "insert into date_runs (pub_date, stage, version, status) "
            "values (%s, 'index', 'v', 'done')",
            (empty,),
        )
    stray = date(2026, 4, 10)  # a point in Qdrant only, for a date the ledger does not know
    qdrant().upsert(
        embed.collection_name("bge-m3__v1"),
        points=[
            qm.PointStruct(
                id=records.point_id("stray#c01"),
                vector=[1.0] + [0.0] * (DIM - 1),
                payload={"chunk_id": "stray#c01", "pub_date_int": records.date_int(stray)},
            )
        ],
        wait=True,
    )
    out = runner.invoke(app, ["index", "validate"])
    assert out.exit_code == 1
    assert out.output.splitlines() == [
        "ok 2026-04-03 1",
        "ok 2026-04-09 0",
        "MISMATCH 2026-04-10 q=1 os=0 pg=0",
    ]
    out = runner.invoke(app, ["index", "validate", "--date", "2026-04-09"])
    assert out.exit_code == 0 and out.output.strip() == "ok 2026-04-09 0"


@pytest.mark.stores
def test_validate_without_generation(env):
    out = runner.invoke(app, ["index", "validate"])
    assert out.exit_code == 1 and "no active generation" in out.output


@pytest.mark.stores
def test_entities_pruned(env):
    """E-6: each run upserts the alias table's entities and drops the ones that left it."""
    from newsrag.warehouse import db as wdb

    env.build(DAY, ["s_s1"])
    stage = make_stage()
    stage.handle(DAY, [])
    with wdb.conn() as c:
        assert {r[0] for r in c.execute("select entity_id from entities")} == {"e_1", "e_2", "e_3"}
    env.entities = [dict(e) for e in env.entities if e["entity_id"] != "e_3"]
    env.entities[0]["canonical"] = "కొత్త పేరు"
    env.write_entities()
    stage.handle(DAY, [])
    with wdb.conn() as c:
        rows = dict(c.execute("select entity_id, canonical from entities").fetchall())
    assert rows == {"e_1": "కొత్త పేరు", "e_2": "పేరు 2"}


@pytest.mark.stores
def test_icu_telugu_search_and_district_filter_on_fake_data(env):
    """Telugu BM25 through the ICU analyzer, and a Qdrant district filter, on small fake data."""
    from qdrant_client import models as qm

    from newsrag.embed import index as store_index
    from newsrag.stores import opensearch, qdrant

    env.build(DAY, ["s_s1", "s_s2", "s_s3"])
    make_stage().handle(DAY, [])
    name = embed.collection_name("bge-m3__v1")
    hits = opensearch().search(
        index=name, body={"query": {"match": {"text_te": "వర్షాలు"}}, "size": 10}
    )["hits"]["hits"]
    assert [h["_id"] for h in hits] == ["s_s1#c01"]
    hits = opensearch().search(
        index=name, body={"query": {"match": {"text_en": "budgets"}}, "size": 10}
    )["hits"]["hits"]  # the english analyzer stems
    assert [h["_id"] for h in hits] == ["s_s2#c01"]
    flt = qm.Filter(
        must=[qm.FieldCondition(key="districts_current", match=qm.MatchValue(value="vizianagaram"))]
    )
    found = qdrant().query_points(name, query=[1.0] + [0.0] * (DIM - 1), query_filter=flt, limit=10)
    assert len(found.points) == 3
    nothing = qm.Filter(
        must=[qm.FieldCondition(key="districts_current", match=qm.MatchValue(value="elsewhere"))]
    )
    assert (
        qdrant().query_points(name, query=[1.0] + [0.0] * (DIM - 1), query_filter=nothing).points
        == []
    )
    assert store_index.os_counts("bge-m3__v1") == {DAY: 3}


@pytest.mark.stores
def test_db_init_runs_both_migrations():
    out = runner.invoke(app, ["db", "init"])
    assert out.exit_code == 0, out.output
    assert "ledger schema ledger: up to date" in out.output
    assert "warehouse schema warehouse: up to date" in out.output


# --- E-3: the real index built by E-1 (marked data) --------------------------------------------


def _real_chunks() -> list[dict[str, Any]]:
    from newsrag.chunking.cli import done_chunks

    chunks = done_chunks(None)
    if not chunks:
        pytest.skip("no done chunk run")
    return chunks


def _real_alias_ready() -> None:
    from newsrag.warehouse import db as wdb

    if wdb.active_generation() is None:
        pytest.skip("no active generation: run 'newsrag stage run index' first")


def _telugu_words(text: str) -> list[str]:
    import re

    return [w for w in re.findall(r"[ఀ-౿]+", text) if len(w) >= 5]


@pytest.mark.stores
@pytest.mark.data
def test_telugu_bm25_real_index():
    """E-3 / NFR-2: a Telugu word of a real chunk finds that chunk in the top 10 of the alias."""
    from newsrag.stores import opensearch

    _real_alias_ready()
    chunks = [c for c in _real_chunks() if c["kind"] == "news"]
    document_frequency: dict[str, int] = {}
    for chunk in chunks:
        for word in set(_telugu_words(chunk["text"])):
            document_frequency[word] = document_frequency.get(word, 0) + 1
    tried = 0
    for chunk in chunks[:: max(1, len(chunks) // 25)]:
        rare = sorted(set(_telugu_words(chunk["text"])), key=lambda w: (document_frequency[w], w))
        if not rare:
            continue
        word = rare[0]
        hits = opensearch().search(
            index=embed.alias_name(), body={"query": {"match": {"text_te": word}}, "size": 10}
        )["hits"]["hits"]
        assert chunk["chunk_id"] in [h["_id"] for h in hits], (word, chunk["chunk_id"])
        tried += 1
    assert tried >= 10


@pytest.mark.stores
@pytest.mark.data
def test_district_filter_real_index():
    """E-3 / FR-2: a Qdrant query filtered on `districts_current` returns only that district."""
    from qdrant_client import models as qm

    from newsrag.stores import qdrant

    _real_alias_ready()
    chunks = _real_chunks()
    counts: dict[str, int] = {}
    for chunk in chunks:
        for district in chunk["districts_current"]:
            counts[district] = counts.get(district, 0) + 1
    assert counts, "no chunk has a district"
    district = max(counts, key=lambda d: counts[d])
    flt = qm.Filter(
        must=[qm.FieldCondition(key="districts_current", match=qm.MatchValue(value=district))]
    )
    points = qdrant().scroll(embed.alias_name(), scroll_filter=flt, limit=10_000)[0]
    assert len(points) == counts[district]
    assert all(district in p.payload["districts_current"] for p in points)
    dim = qdrant().get_collection(embed.alias_name()).config.params.vectors.size
    found = (
        qdrant()
        .query_points(
            embed.alias_name(), query=[1.0] + [0.0] * (dim - 1), query_filter=flt, limit=20
        )
        .points
    )
    assert found and all(district in p.payload["districts_current"] for p in found)
