"""Stage `chunk` (ticket 023): the retrieval chunks of every story, table and notice of a date.

A date stage on top of `enrich_facts`. It needs the alias table (ticket 022) and the tokenizer of
the configured embed model. The version holds the embed model, not the alias / edition lookups:
each date's detail stores a `lookup_hash` and `newsrag.chunking.stale_dates()` finds the dates
whose lookups changed.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from typing import Any

from newsrag import config
from newsrag.stages.base import (
    DateStage,
    PageRow,
    StageResult,
    StageUnavailable,
    artifact_path,
    versioned,
    write_json_atomic,
)

# every setting the stage reads that changes what it writes
VERSION_SETTINGS = ["embed_model", "chunk_max_tokens"]


class ChunkStage(DateStage):
    name = "chunk"
    depends_on = ("enrich_facts",)

    def __init__(self) -> None:
        # a tokenizer-like callable `(text, add_special_tokens=True) -> obj.input_ids`; tests
        # inject a small one, otherwise `open()` loads the embed model's own
        self.tokenizer: Any = None

    @property
    def version(self) -> str:  # type: ignore[override]
        return versioned(f"chunk-v1-{config.settings.embed_model}", VERSION_SETTINGS)

    def preflight(self) -> None:
        from newsrag import aliases
        from newsrag.models import get_profile

        s = config.settings
        try:
            profile = get_profile(s.embed_model)
        except KeyError:
            raise StageUnavailable(f"embed_model {s.embed_model} not in models.yaml") from None
        if profile.kind != "embed":
            raise StageUnavailable(f"embed_model {profile.name} is not an embed profile")
        if not aliases.entities_path().is_file():
            raise StageUnavailable("run 'newsrag aliases build' first")

    def open(self) -> None:
        self._tokenizer()

    def _tokenizer(self) -> Any:
        if self.tokenizer is None:
            from transformers import AutoTokenizer

            from newsrag.models import get_profile

            profile = get_profile(config.settings.embed_model)
            self.tokenizer = AutoTokenizer.from_pretrained(
                profile.hf_id, cache_dir=str(config.settings.hf_home)
            )
        return self.tokenizer

    def handle(self, pub_date: date, pages: list[PageRow]) -> StageResult:
        from newsrag.chunking import build, load
        from newsrag.chunking.split import Chunker
        from newsrag.models import get_profile

        started = time.monotonic()
        tokenizer = self._tokenizer()
        profile = get_profile(config.settings.embed_model)

        def count(text: str) -> int:
            return len(tokenizer(text, add_special_tokens=True).input_ids)

        chunker = Chunker(count, profile.doc_prefix, config.settings.chunk_max_tokens)
        inputs = load.load_inputs(pub_date)
        result = build.build_date(inputs, chunker)
        day = pub_date.isoformat()
        path = write_json_atomic(
            artifact_path(self.name, self.version, day),
            {"pub_date": day, "chunks": result.chunks},
        )
        seconds = time.monotonic() - started
        tokens = [c["n_tokens"] for c in result.chunks]
        print(
            f"chunk {day}: {result.stories} stories -> {len(result.chunks)} chunks "
            f"({result.table_chunks} table), max {max(tokens, default=0)} tokens, "
            f"{result.empty_stories} empty, {result.unmapped_names} unmapped names "
            f"in {seconds:.1f} s",
            file=sys.stderr,
            flush=True,
        )
        return StageResult(
            str(path),
            {
                "stories": result.stories,
                "chunks": len(result.chunks),
                "table_chunks": result.table_chunks,
                "empty_stories": result.empty_stories,
                "unmapped_names": result.unmapped_names,
                "max_tokens": max(tokens, default=0),
                "lookup_hash": result.lookup_hash,
                "seconds": round(seconds, 2),
            },
        )
