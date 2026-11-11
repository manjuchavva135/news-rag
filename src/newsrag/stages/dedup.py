"""Stage `dedup` (ticket 017): one canonical story per group of near-identical printings.

A date stage: it runs once the date has `stitch` done and every non-quarantined page has
`transcribe` done. It reads the stitched articles and the ticket-015 items, finds duplicate
printings (MinHash + BGE-M3 cosine), keeps stable story ids in `ledger.story_ids` and writes
`{pub_date, stories, excluded}`. The embedding model is opened once per `stage run` (`open`).
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Sequence
from datetime import date

import numpy as np

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

# every setting the stage reads
VERSION_SETTINGS = [
    "dedup_jaccard",
    "dedup_cosine",
    "dedup_device",
    "dedup_model",
    "dedup_num_perm",
    "dedup_shingle_size",
    "dedup_lsh_threshold",
    "dedup_embed_batch",
    "dedup_kinds",
]


class DedupStage(DateStage):
    name = "dedup"
    depends_on = ("stitch", "transcribe")

    def __init__(self) -> None:
        self._stack: contextlib.ExitStack | None = None
        self._embedder = None

    @property
    def version(self) -> str:  # type: ignore[override]
        return versioned("dedup-v1", VERSION_SETTINGS)

    @property
    def models_needed(self) -> tuple[str, ...]:  # type: ignore[override]
        return () if config.settings.dedup_device == "cpu" else (config.settings.dedup_model,)

    def preflight(self) -> None:
        from newsrag.models import get_profile

        s = config.settings
        if s.dedup_device not in ("cuda", "cpu"):
            raise StageUnavailable(f"dedup_device must be cuda or cpu, not {s.dedup_device!r}")
        try:
            profile = get_profile(s.dedup_model)
        except KeyError as exc:
            raise StageUnavailable(str(exc)) from None
        if profile.kind != "embed":
            raise StageUnavailable(f"dedup_model {profile.name} is not an embed profile")

    def open(self) -> None:
        """Load the embedding model (CPU without the GPU lock when `dedup_device` is cpu)."""
        from newsrag import models

        self.close()
        self._stack = contextlib.ExitStack()
        try:
            s = config.settings
            self._embedder = self._stack.enter_context(
                models.use(s.dedup_model, device=s.dedup_device)
            )
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        stack, self._stack, self._embedder = self._stack, None, None
        if stack is not None:
            stack.close()

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if self._embedder is None:  # handle() called outside a `stage run`
            self.open()
        assert self._embedder is not None
        return self._embedder.embed(list(texts), "doc", config.settings.dedup_embed_batch)

    def handle(self, pub_date: date, pages: list[PageRow]) -> StageResult:
        from newsrag.dedup import cluster, ids, load

        started = time.monotonic()
        active = [p for p in pages if not p.quarantine_reason]
        units, excluded = load.date_units(pub_date, active)
        params = cluster.Params.from_settings()
        pairs = cluster.duplicate_pairs(units, self.embed, params) if units else []
        clusters = cluster.cluster_units(units, pairs)
        story_ids = ids.sync(pub_date, [[u.unit_id for u in c] for c in clusters])
        artifact = cluster.build_artifact(
            pub_date.isoformat(),
            clusters,
            {c[0].unit_id: sid for c, sid in zip(clusters, story_ids, strict=True)},
            excluded,
        )
        path = write_json_atomic(
            artifact_path(self.name, self.version, pub_date.isoformat()), artifact
        )
        seconds = time.monotonic() - started
        print(
            f"dedup {pub_date.isoformat()}: {len(units)} units, {len(clusters)} stories "
            f"in {seconds:.1f} s",
            file=sys.stderr,
            flush=True,
        )
        return StageResult(
            str(path),
            {
                "units": len(units),
                "stories": len(clusters),
                "merged_units": len(units) - len(clusters),
                "pairs": len(pairs),
                "excluded": excluded,
                "seconds": round(seconds, 2),
            },
        )
