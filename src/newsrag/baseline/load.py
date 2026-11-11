"""Load the old stage-9 export bundle into the baseline stores (ticket 027 FR-2).

The bundle (`/home/admin1/RAG/pipeline/stage09_validate/run.py` `export_snapshot`) is a tar.gz
of `manifest.json`, `postgres/<table>.jsonl`, `qdrant/points.jsonl` (id, vector, payload) and
optional `crops/`. It is read as a stream (`tarfile` mode `r|gz`): members one by one, nothing
extracted to disk, `crops/` skipped. Rows go to schema `old` of the baseline Postgres (only
`issues`, `pages`, `articles`, `chunks`), points to Qdrant `chunks_old` as stored, and the
OpenSearch index `chunks_old` is built afterwards from `old.chunks` text and
`old.articles.headline` with ticket 024's mapping and script rule.

The writes go through a `Sink` so the tests can load a synthetic bundle without stores.
"""

from __future__ import annotations

import itertools
import json
import tarfile
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import IO, Any, Protocol

from newsrag import config

OLD_TABLES = ("issues", "pages", "articles", "chunks")
DDL_FILE = Path(__file__).parent / "sql" / "old_warehouse.sql"
POINTS_MEMBER = "qdrant/points.jsonl"
MANIFEST_MEMBER = "manifest.json"
# loaded count name -> manifest count name
COUNT_KEYS = {
    "issues": "issues",
    "pages": "pages",
    "articles": "articles",
    "chunks": "chunks",
    "qdrant_points": "qdrant_points",
    "opensearch_docs": "chunks",  # one document per old chunk
}


class LoadError(RuntimeError):
    """The bundle can't be loaded (exit 1)."""


class Sink(Protocol):
    def existing(self) -> list[str]: ...
    def drop(self) -> None: ...
    def create_tables(self) -> None: ...
    def write_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int: ...
    def write_points(self, points: list[dict[str, Any]]) -> None: ...
    def build_index(self) -> None: ...
    def counts(self) -> dict[str, int]: ...


def table_columns(ddl: str) -> dict[str, list[str]]:
    """{table: [columns in DDL order]} of the CREATE TABLE statements in `ddl`."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for raw in ddl.splitlines():
        line = raw.split("--", 1)[0].strip()
        if line.upper().startswith("CREATE TABLE"):
            current = line.split()[-2] if line.endswith("(") else line.split()[5]
            out[current] = []
        elif current and line.startswith(")"):
            current = None
        elif current and line:
            word = line.split()[0]
            keyword = word.upper() in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT")
            if word[0].isalpha() and not keyword:
                out[current].append(word)
    return out


DDL = DDL_FILE.read_text(encoding="utf-8")
COLUMNS = table_columns(DDL)


def jsonl(stream: IO[bytes]) -> Iterator[dict[str, Any]]:
    """JSON lines of a stream-mode tar member (not seekable, so no TextIOWrapper)."""
    for line in stream:
        if line.strip():
            yield json.loads(line.decode("utf-8"))


def batched(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    it = iter(items)
    while batch := list(islice(it, max(1, size))):
        yield batch


def open_stream(path: Path) -> tarfile.TarFile:
    """The bundle as a forward-only stream: nothing is extracted to disk."""
    return tarfile.open(path, mode="r|gz")


def member_name(member: tarfile.TarInfo) -> str:
    name = member.name
    while name.startswith("./"):
        name = name[2:]
    return name


def load_bundle(
    path: Path,
    force: bool = False,
    sink: Sink | None = None,
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """FR-2: stream the bundle into the sink; return {manifest, loaded, mismatches, skipped}.

    Refuses (LoadError) when the targets already exist, unless `force` (drops them first)."""
    path = Path(path)
    if not path.is_file():
        raise LoadError(f"bundle not found: {path}")
    sink = sink if sink is not None else StoreSink()
    existing = sink.existing()
    if existing and not force:
        raise LoadError(
            f"baseline already loaded ({', '.join(existing)}): pass --force to drop and reload"
        )
    if existing:
        echo(f"dropping {', '.join(existing)}")
        sink.drop()
    sink.create_tables()

    batch = config.settings.baseline_load_batch
    manifest: dict[str, Any] | None = None
    skipped: dict[str, int] = {}
    seen_tables: list[str] = []
    with open_stream(path) as tar:
        for member in tar:
            name = member_name(member)
            if name == "crops" or name.startswith("crops/"):
                skipped["crops"] = skipped.get("crops", 0) + (1 if member.isfile() else 0)
                continue
            if not member.isfile():
                continue
            stream = tar.extractfile(member)
            if stream is None:
                continue
            if name == MANIFEST_MEMBER:
                manifest = json.loads(stream.read().decode("utf-8"))
                echo(f"manifest: {json.dumps(manifest.get('counts', {}), sort_keys=True)}")
            elif name.startswith("postgres/") and name.endswith(".jsonl"):
                table = name[len("postgres/") : -len(".jsonl")]
                if table not in OLD_TABLES:
                    skipped[name] = 1
                    continue
                written = sink.write_rows(table, jsonl(stream))
                seen_tables.append(table)
                echo(f"loaded {table}: {written}")
            elif name == POINTS_MEMBER:
                total = 0
                for points in batched(jsonl(stream), batch):
                    sink.write_points(points)
                    total += len(points)
                echo(f"loaded qdrant points: {total}")
            else:
                skipped[name] = 1
    if manifest is None:
        raise LoadError(f"no {MANIFEST_MEMBER} in {path}")

    echo("building the OpenSearch index from old.chunks ...")
    sink.build_index()
    loaded = sink.counts()
    expected = manifest.get("counts") or {}
    mismatches = {
        key: {"manifest": expected.get(src), "loaded": loaded.get(key)}
        for key, src in COUNT_KEYS.items()
        if expected.get(src) != loaded.get(key)
    }
    result = {
        "bundle": str(path),
        "manifest": manifest,
        "loaded": loaded,
        "mismatches": mismatches,
        "skipped": skipped,
    }
    if not mismatches:
        write_loaded_manifest(path, manifest, loaded)
    return result


def write_loaded_manifest(path: Path, manifest: dict[str, Any], loaded: dict[str, int]) -> Path:
    """`<baseline_dir>/manifest-loaded.json`: the manifest counts and the bundle (ticket 031)."""
    out = Path(config.settings.baseline_dir) / "manifest-loaded.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "bundle": str(Path(path).resolve()),
        "counts": manifest.get("counts") or {},
        "loaded": loaded,
        "embed_model": manifest.get("embed_model"),
        "embed_dim": manifest.get("embed_dim"),
        "created_at": manifest.get("created_at"),
        "loaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    return out


# --- the real stores --------------------------------------------------------------------------


def _copy_value(value: Any) -> Any:
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return value


class StoreSink:
    """Schema `old` in the baseline Postgres, Qdrant and OpenSearch `chunks_old`."""

    def __init__(self) -> None:
        s = config.settings
        self.schema = s.baseline_schema
        self.collection = s.baseline_collection
        self._dim: int | None = None

    # Postgres
    def _conn(self) -> Any:
        from newsrag.baseline import stores

        return stores.pg_connect()

    def existing(self) -> list[str]:
        from newsrag.baseline import stores

        found = []
        with self._conn() as c:
            row = c.execute(
                "select 1 from information_schema.schemata where schema_name = %s", (self.schema,)
            ).fetchone()
            if row:
                found.append(f"schema {self.schema}")
        if stores.qdrant().collection_exists(self.collection):
            found.append(f"qdrant collection {self.collection}")
        if stores.opensearch().indices.exists(index=self.collection):
            found.append(f"opensearch index {self.collection}")
        return found

    def drop(self) -> None:
        from psycopg import sql

        from newsrag.baseline import stores

        with self._conn() as c:
            c.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema))
            )
        if stores.qdrant().collection_exists(self.collection):
            stores.qdrant().delete_collection(self.collection)
        if stores.opensearch().indices.exists(index=self.collection):
            stores.opensearch().indices.delete(index=self.collection)

    def create_tables(self) -> None:
        from psycopg import sql

        with self._conn() as c:
            c.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            c.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            c.execute(DDL)

    def write_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        from psycopg import sql

        it = iter(rows)
        first = next(it, None)
        if first is None:
            return 0
        columns = [col for col in COLUMNS[table] if col in first]
        statement = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
            sql.Identifier(self.schema),
            sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(col) for col in columns),
        )
        n = 0
        with self._conn() as c, c.cursor() as cur, cur.copy(statement) as copy:
            for row in itertools.chain([first], it):
                copy.write_row([_copy_value(row.get(col)) for col in columns])
                n += 1
        return n

    # Qdrant
    def write_points(self, points: list[dict[str, Any]]) -> None:
        from qdrant_client import models as qm

        from newsrag.baseline import stores

        client = stores.qdrant()
        if self._dim is None:
            self._dim = len(points[0]["vector"])
            if not client.collection_exists(self.collection):
                client.create_collection(
                    self.collection,
                    vectors_config=qm.VectorParams(size=self._dim, distance=qm.Distance.COSINE),
                )
        client.upsert(
            self.collection,
            points=[
                qm.PointStruct(id=p["id"], vector=p["vector"], payload=p.get("payload") or {})
                for p in points
            ],
            wait=True,
        )

    # OpenSearch
    def build_index(self) -> None:
        from opensearchpy import helpers
        from psycopg import sql

        from newsrag.baseline import stores
        from newsrag.embed import index as new_index
        from newsrag.embed import records

        client = stores.opensearch()
        if not client.indices.exists(index=self.collection):
            client.indices.create(
                index=self.collection,
                body={"settings": new_index.os_settings(), "mappings": new_index.os_mappings()},
            )
        share = config.settings.index_script_share
        query = sql.SQL(
            "select c.chunk_id, c.story_id, c.page_id, c.seq, c.text, a.headline, a.pub_date "
            "from {s}.chunks c left join {s}.articles a on a.article_id = c.article_id"
        ).format(s=sql.Identifier(self.schema))

        def actions() -> Iterator[dict[str, Any]]:
            with self._conn() as c, c.cursor(name="baseline_index") as cur:
                cur.itersize = config.settings.baseline_load_batch
                cur.execute(query)
                for chunk_id, story_id, page_id, seq, text, headline, pub_date in cur:
                    chunk = {
                        "chunk_id": chunk_id,
                        "story_id": story_id,
                        "page_ids": [page_id],
                        "seq": seq,
                        "text": text,
                        "headline": headline or "",
                        "pub_date": pub_date.isoformat() if pub_date else None,
                    }
                    yield {
                        "_op_type": "index",
                        "_index": self.collection,
                        "_id": chunk_id,
                        "_source": records.os_document(chunk, share),
                    }

        helpers.bulk(client, actions(), chunk_size=max(1, config.settings.baseline_load_batch))
        client.indices.refresh(index=self.collection)

    def counts(self) -> dict[str, int]:
        from psycopg import sql

        from newsrag.baseline import stores

        out: dict[str, int] = {}
        with self._conn() as c:
            for table in OLD_TABLES:
                q = sql.SQL("select count(*) from {}.{}").format(
                    sql.Identifier(self.schema), sql.Identifier(table)
                )
                out[table] = int(c.execute(q).fetchone()[0])
        q_client = stores.qdrant()
        out["qdrant_points"] = (
            int(q_client.count(self.collection, exact=True).count)
            if q_client.collection_exists(self.collection)
            else 0
        )
        os_client = stores.opensearch()
        out["opensearch_docs"] = (
            int(os_client.count(index=self.collection)["count"])
            if os_client.indices.exists(index=self.collection)
            else 0
        )
        return out


def old_page_ids() -> list[str]:
    """Every page id of `old.pages`."""
    from psycopg import sql

    from newsrag.baseline import stores

    with stores.pg_connect() as c:
        q = sql.SQL("select page_id from {}.pages order by page_id").format(
            sql.Identifier(config.settings.baseline_schema)
        )
        return [row[0] for row in c.execute(q).fetchall()]
