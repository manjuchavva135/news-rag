"""Ticket 027 FR-2, FR-5: streaming the old stage-9 bundle into the baseline stores."""

from __future__ import annotations

import io
import json
import secrets
import tarfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.baseline import load
from newsrag.cli import app

runner = CliRunner()
DIM = 4


def _rows() -> dict[str, list[dict[str, Any]]]:
    issue = "ap-bgnoprsvaclt-2026-02-19"
    pages = [f"{issue}#p01", f"{issue}#fea02"]
    articles = [
        {
            "article_id": f"{p}#a00",
            "story_id": f"{p}#a00",
            "page_id": p,
            "issue_id": issue,
            "seq": 0,
            "headline": f"శీర్షిక {i}",
            "pub_date": "2026-02-19",
            "state": "andhra_pradesh",
            "edition": "bgnoprsvaclt",
            "category": "politics",
            "synopsis": None,
            "topics": ["a", "b"],
            "entities": {"people": []},
            "lang": "te",
            "body": "body",
            "char_count": 4,
            "created_at": "2026-07-01 10:00:00+00:00",
        }
        for i, p in enumerate(pages)
    ]
    chunks = [
        {
            "chunk_id": f"{a['article_id']}#c00",
            "article_id": a["article_id"],
            "story_id": a["story_id"],
            "page_id": a["page_id"],
            "issue_id": issue,
            "seq": 0,
            "lang": "te",
            "text": f"తెలుగు వార్త పాఠం {i} Guntur",
            "token_est": 10,
            "created_at": "2026-07-01 10:00:00+00:00",
        }
        for i, a in enumerate(articles)
    ]
    return {
        "issues": [{"issue_id": issue, "created_at": "2026-07-01 10:00:00+00:00"}],
        "pages": [
            {"page_id": p, "issue_id": issue, "page_num": i + 1, "source_path": f"/x/{i}.pdf"}
            for i, p in enumerate(pages)
        ],
        "articles": articles,
        "chunks": chunks,
    }


def _points(rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    import uuid

    return [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, c["chunk_id"])),
            "vector": [0.1 * (i + 1)] * DIM,
            "payload": {
                "chunk_id": c["chunk_id"],
                "story_id": c["story_id"],
                "page_id": c["page_id"],
            },
        }
        for i, c in enumerate(rows["chunks"])
    ]


def make_bundle(
    path: Path, counts: dict[str, int] | None = None, crops: bool = True, extra: bool = True
) -> Path:
    """A small bundle in the export_snapshot layout (manifest first, tables parents first)."""
    rows = _rows()
    points = _points(rows)
    manifest = {
        "created_at": "2026-07-01T10:00:00+00:00",
        "embed_model": "BAAI/bge-m3",
        "embed_dim": DIM,
        "counts": {t: len(r) for t, r in rows.items()}
        | {"answer_feedback": 1, "qdrant_points": len(points)},
    }
    manifest["counts"].update(counts or {})

    def add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    def lines(items: list[dict[str, Any]]) -> bytes:
        return "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items).encode()

    with tarfile.open(path, "w:gz") as tar:
        add(tar, "manifest.json", json.dumps(manifest).encode())
        for table, items in rows.items():
            add(tar, f"postgres/{table}.jsonl", lines(items))
        if extra:
            add(tar, "postgres/answer_feedback.jsonl", lines([{"id": 1, "question": "q"}]))
        add(tar, "qdrant/points.jsonl", lines(points))
        if crops:
            add(tar, "crops/ap-bgnoprsvaclt-2026-02-19_p01_a00.png", b"\x89PNG fake")
    return path


class FakeSink:
    def __init__(self, existing: list[str] | None = None, lose_point: bool = False) -> None:
        self._existing = existing or []
        self.dropped = False
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.points: list[dict[str, Any]] = []
        self.indexed = 0
        self.lose_point = lose_point

    def existing(self) -> list[str]:
        return list(self._existing)

    def drop(self) -> None:
        self.dropped = True

    def create_tables(self) -> None:
        pass

    def write_rows(self, table: str, rows: Any) -> int:
        self.tables[table] = list(rows)
        return len(self.tables[table])

    def write_points(self, points: list[dict[str, Any]]) -> None:
        self.points.extend(points)

    def build_index(self) -> None:
        self.indexed = len(self.tables.get("chunks", []))

    def counts(self) -> dict[str, int]:
        out = {t: len(self.tables.get(t, [])) for t in load.OLD_TABLES}
        out["qdrant_points"] = len(self.points) - (1 if self.lose_point else 0)
        out["opensearch_docs"] = self.indexed
        return out


@pytest.fixture
def bdir(tmp_path, monkeypatch):
    """baseline_dir and the report files in tmp_path."""
    base = tmp_path / "baseline"
    current = config.settings.model_copy(
        update={
            "baseline_dir": base,
            "reports_dir": tmp_path / "reports",
            "baseline_page_map_file": tmp_path / "reports" / "baseline-page-map.csv",
        }
    )
    monkeypatch.setattr(config, "settings", current)
    return base


def _files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


def test_streams_without_extracting(tmp_path, bdir, monkeypatch):
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    bundle = make_bundle(bundles / "rag-snapshot-x.tar.gz")
    modes: list[str] = []
    real_open = tarfile.open

    def spy(*args: Any, **kwargs: Any) -> tarfile.TarFile:
        modes.append(kwargs.get("mode") or (args[1] if len(args) > 1 else "r"))
        return real_open(*args, **kwargs)

    monkeypatch.setattr(load.tarfile, "open", spy)
    before = _files(tmp_path)
    sink = FakeSink()
    result = load.load_bundle(bundle, sink=sink, echo=lambda _: None)
    assert modes == ["r|gz"]
    assert result["mismatches"] == {}
    # the only new file is manifest-loaded.json: nothing extracted next to the bundle or elsewhere
    assert _files(tmp_path) - before == {bdir / "manifest-loaded.json"}
    assert [len(sink.tables[t]) for t in load.OLD_TABLES] == [1, 2, 2, 2]
    assert len(sink.points) == 2 and len(sink.points[0]["vector"]) == DIM
    assert sink.tables["articles"][0]["topics"] == ["a", "b"]


def test_skips_crops(tmp_path, bdir):
    bundle = make_bundle(tmp_path / "b.tar.gz")
    sink = FakeSink()
    result = load.load_bundle(bundle, sink=sink, echo=lambda _: None)
    assert result["skipped"]["crops"] == 1
    assert result["skipped"]["postgres/answer_feedback.jsonl"] == 1
    assert "answer_feedback" not in sink.tables
    assert all("crops" not in str(p.get("payload")) for p in sink.points)


def test_refuses_without_force(tmp_path, bdir):
    bundle = make_bundle(tmp_path / "b.tar.gz")
    sink = FakeSink(existing=["schema old"])
    with pytest.raises(load.LoadError, match="already loaded .*schema old.*--force"):
        load.load_bundle(bundle, sink=sink, echo=lambda _: None)
    assert not sink.dropped and not sink.tables
    load.load_bundle(bundle, force=True, sink=sink, echo=lambda _: None)
    assert sink.dropped and len(sink.tables["chunks"]) == 2


def test_count_mismatch_exits_1(tmp_path, bdir, monkeypatch):
    bundle = make_bundle(tmp_path / "b.tar.gz", counts={"chunks": 3})
    monkeypatch.setattr(load, "StoreSink", FakeSink)
    result = runner.invoke(app, ["baseline", "load", str(bundle)])
    assert result.exit_code == 1, result.output
    assert "manifest counts:" in result.output and "loaded counts:" in result.output
    assert "mismatch chunks: manifest=3 loaded=2" in result.output
    assert not (bdir / "manifest-loaded.json").exists()
    # a point lost on the way is a mismatch too
    monkeypatch.setattr(load, "StoreSink", lambda: FakeSink(lose_point=True))
    ok_bundle = make_bundle(tmp_path / "c.tar.gz")
    result = runner.invoke(app, ["baseline", "load", str(ok_bundle)])
    assert result.exit_code == 1
    assert "mismatch qdrant_points: manifest=2 loaded=1" in result.output


def test_manifest_loaded_written(tmp_path, bdir):
    bundle = make_bundle(tmp_path / "b.tar.gz")
    load.load_bundle(bundle, sink=FakeSink(), echo=lambda _: None)
    data = json.loads((bdir / "manifest-loaded.json").read_text())
    assert data["bundle"] == str(bundle.resolve())
    assert data["counts"]["chunks"] == 2 and data["counts"]["qdrant_points"] == 2
    assert data["loaded"]["opensearch_docs"] == 2


def test_missing_manifest_and_bundle(tmp_path, bdir):
    with pytest.raises(load.LoadError, match="bundle not found"):
        load.load_bundle(tmp_path / "nope.tar.gz", sink=FakeSink())
    path = tmp_path / "empty.tar.gz"
    with tarfile.open(path, "w:gz"):
        pass
    with pytest.raises(load.LoadError, match="no manifest.json"):
        load.load_bundle(path, sink=FakeSink(), echo=lambda _: None)


def test_ddl_columns_match_old_restore_spec():
    # the column lists of the old restore_snapshot (`_pg_load` specs), plus created_at
    assert load.COLUMNS["chunks"] == [
        "chunk_id",
        "article_id",
        "story_id",
        "page_id",
        "issue_id",
        "seq",
        "lang",
        "text",
        "token_est",
        "created_at",
    ]
    assert load.COLUMNS["pages"] == ["page_id", "issue_id", "page_num", "source_path", "created_at"]
    assert "category" in load.COLUMNS["articles"] and "body" in load.COLUMNS["articles"]


# --- against the real baseline stores (a throw-away schema / collection) ----------------------


@pytest.mark.stores
def test_load_into_baseline_stores(tmp_path, bdir, monkeypatch):
    from newsrag.baseline import stores

    if not all(stores.ping_all().values()):
        pytest.skip("baseline stores not running (newsrag baseline up)")
    name = f"test_{secrets.token_hex(6)}"
    monkeypatch.setattr(
        config,
        "settings",
        config.settings.model_copy(update={"baseline_schema": name, "baseline_collection": name}),
    )
    bundle = make_bundle(tmp_path / "b.tar.gz")
    sink = load.StoreSink()
    try:
        result = load.load_bundle(bundle, sink=sink, echo=lambda _: None)
        assert result["mismatches"] == {}, result
        assert result["loaded"] == {
            "issues": 1,
            "pages": 2,
            "articles": 2,
            "chunks": 2,
            "qdrant_points": 2,
            "opensearch_docs": 2,
        }
        assert sorted(sink.existing()) == sorted(
            [f"schema {name}", f"qdrant collection {name}", f"opensearch index {name}"]
        )
        doc = stores.opensearch().get(index=name, id="ap-bgnoprsvaclt-2026-02-19#p01#a00#c00")
        src = doc["_source"]
        assert src["headline_te"] == "శీర్షిక 0" and "Guntur" in src["text_te"]
        assert src["page_ids"] == ["ap-bgnoprsvaclt-2026-02-19#p01"]
        with pytest.raises(load.LoadError, match="already loaded"):
            load.load_bundle(bundle, sink=load.StoreSink(), echo=lambda _: None)
        again = load.load_bundle(bundle, force=True, sink=load.StoreSink(), echo=lambda _: None)
        assert again["mismatches"] == {}
    finally:
        sink.drop()
