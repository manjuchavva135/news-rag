"""Stage `index` (ticket 024): the chunks of a print date embedded and written to the three stores.

A date stage on top of `chunk`. It embeds `prefix + "\\n" + text` of every chunk with the
`embed_model` profile and writes, for the current generation `<embed_model>__<tag>`:

- Qdrant `chunks__G`: vector + payload; OpenSearch `chunks__G`: BM25 text (ICU for Telugu);
- Postgres `warehouse`: stories, printings, chunks, entities, places, facts.

Re-indexing a date replaces its old rows in all three. The first generation ever written is
activated automatically; `newsrag index activate` switches later ones.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from newsrag import config, embed
from newsrag.stages.base import (
    DateStage,
    PageRow,
    StageResult,
    StageUnavailable,
    versioned,
)

# every setting the stage reads that changes what it writes
VERSION_SETTINGS = ["embed_model", "index_generation_tag", "index_script_share"]


class Embedder(Protocol):
    """What the stage needs of `newsrag.models.Embedder` (tests inject a fake one)."""

    @property
    def dim(self) -> int: ...

    def embed(self, texts: Any, kind: str = "doc", batch_size: int = 64) -> np.ndarray: ...


class IndexStage(DateStage):
    name = "index"
    depends_on = ("chunk",)

    def __init__(self, embedder: Embedder | None = None, generation: str | None = None) -> None:
        self._injected = embedder
        self._embedder: Embedder | None = embedder
        self._generation = generation
        self._stack: contextlib.ExitStack | None = None
        self._entities_synced: tuple[str, str] | None = None  # (warehouse schema, file digest)

    @property
    def version(self) -> str:  # type: ignore[override]
        return versioned(f"index-v1-{config.settings.embed_model}", VERSION_SETTINGS)

    @property
    def models_needed(self) -> tuple[str, ...]:  # type: ignore[override]
        return (config.settings.embed_model,)

    @property
    def generation(self) -> str:
        return self._generation or embed.generation_name()

    # --- lifecycle ---------------------------------------------------------------------------

    def preflight(self) -> None:
        from newsrag import aliases
        from newsrag.models import get_profile
        from newsrag.stores import ping_all
        from newsrag.warehouse import db as wdb

        s = config.settings
        if self._injected is None:
            try:
                profile = get_profile(s.embed_model)
            except KeyError:
                raise StageUnavailable(f"embed_model {s.embed_model} not in models.yaml") from None
            if profile.kind != "embed":
                raise StageUnavailable(f"embed_model {profile.name} is not an embed profile")
        if not aliases.entities_path().is_file():
            raise StageUnavailable("run 'newsrag aliases build' first")
        down = [name for name, ok in ping_all(s.stores_ping_timeout_s).items() if not ok]
        if down:
            raise StageUnavailable(f"stores down ({', '.join(down)}): run 'newsrag stores up'")
        if not wdb.is_initialised():
            raise StageUnavailable("run 'newsrag db init' first")

    def open(self) -> None:
        """Load the embed model once per `stage run` (the GPU lock is held until `close`)."""
        if self._injected is not None:
            return
        from newsrag import models

        self.close()
        self._stack = contextlib.ExitStack()
        try:
            self._embedder = self._stack.enter_context(models.use(config.settings.embed_model))
            # CUDA kernels and memory pools are set up by the first call: keep that out of the
            # first date's chunks/s
            self._embedder.embed(["warm-up"], "doc", 1)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        stack, self._stack = self._stack, None
        if self._injected is None:
            self._embedder = None
        if stack is not None:
            stack.close()

    # --- one date ----------------------------------------------------------------------------

    def embed_chunks(self, chunks: list[dict[str, Any]]) -> np.ndarray:
        """Vectors of `prefix + "\\n" + text` of every chunk, L2-normalised. The model adds the
        profile's `doc_prefix` itself, once."""
        if self._embedder is None:
            raise RuntimeError("index: the embedding model is not open (call open() first)")
        texts = [f"{c['prefix']}\n{c['text']}" for c in chunks]
        if not texts:
            return np.zeros((0, self._embedder.dim), dtype=np.float32)
        return self._embedder.embed(texts, "doc", config.settings.embed_batch_size)

    def handle(self, pub_date: date, pages: list[PageRow]) -> StageResult:
        from newsrag import aliases, enrich
        from newsrag.dedup import load as dedup_load
        from newsrag.embed import generations
        from newsrag.embed import index as store_index
        from newsrag.warehouse import rows as wrows
        from newsrag.warehouse import write as wwrite

        day = pub_date.isoformat()
        generation = self.generation
        chunks = self.load_chunks(pub_date)
        dedup_path = dedup_load.done_dedup(pub_date).get(day)
        if dedup_path is None:
            raise FileNotFoundError(f"no done dedup artifact for {day}")
        stories = dedup_load.load_dedup(dedup_path)["stories"]
        entities = aliases.load_entities()
        name_to_entity = entities["name_to_entity"]
        story_ids = {c["story_id"] for c in chunks}
        page_ids = {
            p
            for s in stories
            if s["story_id"] in story_ids
            for printing in s.get("printings") or []
            for p in [printing["page_id"], *(x["page_id"] for x in printing.get("parts") or [])]
        }
        date_rows = wrows.build_date_rows(
            pub_date,
            generation,
            stories,
            enrich.load(pub_date),
            chunks,
            name_to_entity,
            wwrite.load_pages(pub_date, page_ids),
        )

        # one-off work (collections, the entity table) is not part of the date's throughput
        store_index.ensure_collection(generation, self._dim())
        store_index.ensure_index(generation)
        self._sync_entities(entities["entities"])
        started = time.monotonic()
        vectors = self.embed_chunks(chunks)
        if len(vectors) and vectors.shape[1] != self._dim():
            raise ValueError(f"embedder gave {vectors.shape[1]} dimensions, expected {self._dim()}")
        embedded = time.monotonic()
        # the three stores do not depend on each other: write them at the same time
        timings: dict[str, float] = {}

        def timed(label: str, write: Any, *args: Any) -> Any:
            begin = time.monotonic()
            try:
                return write(*args)
            finally:
                timings[label] = time.monotonic() - begin

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(
                    timed, "postgres", wwrite.replace_date, pub_date, generation, date_rows
                ),
                pool.submit(
                    timed,
                    "qdrant",
                    store_index.qdrant_replace_date,
                    generation,
                    pub_date,
                    chunks,
                    vectors,
                ),
                pool.submit(
                    timed, "opensearch", store_index.os_replace_date, generation, pub_date, chunks
                ),
            ]
            results = [f.exception() or f.result() for f in futures]
        for result in results:
            if isinstance(result, BaseException):
                raise result
        deleted = results[0]
        finished = time.monotonic()
        seconds = finished - started
        activated = generations.activate_if_first(generation)

        rate = len(chunks) / seconds if chunks and seconds > 0 else 0.0
        print(
            f"index {day}: {len(chunks)} chunks in {seconds:.2f}s ({rate:.0f} chunks/s)",
            file=sys.stderr,
            flush=True,
        )
        return StageResult(
            None,
            {
                "generation": generation,
                "chunks": len(chunks),
                "stories": len(date_rows.stories),
                "removed_stories": deleted["removed"],
                "facts": len(date_rows.facts),
                "activated": activated,
                "seconds": round(seconds, 2),
                "embed_seconds": round(embedded - started, 2),
                "postgres_seconds": round(timings["postgres"], 2),
                "qdrant_seconds": round(timings["qdrant"], 2),
                "opensearch_seconds": round(timings["opensearch"], 2),
                "chunks_per_s": round(rate, 1),
            },
        )

    # --- helpers -----------------------------------------------------------------------------

    def _dim(self) -> int:
        if self._embedder is None:
            raise RuntimeError("index: the embedding model is not open (call open() first)")
        return int(self._embedder.dim)

    def load_chunks(self, pub_date: date) -> list[dict[str, Any]]:
        """The chunks of the date's done `chunk` run."""
        from newsrag.ledger import db

        with db.conn() as c:
            row = db.fetch_one(
                c,
                "select artifact_path from date_runs where stage = 'chunk' and status = 'done' "
                "and pub_date = %s",
                (pub_date,),
            )
        if row is None or not row["artifact_path"] or not Path(row["artifact_path"]).is_file():
            raise FileNotFoundError(f"no done chunk artifact for {pub_date.isoformat()}")
        data = json.loads(Path(row["artifact_path"]).read_text(encoding="utf-8"))
        return list(data["chunks"])

    def _sync_entities(self, entities: list[dict[str, Any]]) -> None:
        """FR-6: upsert the alias table's entities and prune the others, once per changed table."""
        from newsrag import aliases
        from newsrag.warehouse import db as wdb
        from newsrag.warehouse import write as wwrite

        stat = aliases.entities_path().stat()
        marker = (wdb.schema(), f"{stat.st_mtime_ns}:{stat.st_size}")
        if self._entities_synced == marker:
            return
        wwrite.sync_entities(entities)
        self._entities_synced = marker
