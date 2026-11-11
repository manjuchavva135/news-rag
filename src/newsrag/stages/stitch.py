"""Stage `stitch` (ticket 016): link the parts of stories that continue on another page.

A date stage: it runs once every non-quarantined page of the date has `group_final`. It reads
the `group_final` artifacts (and the `ocr_final` text of marker blocks), never changes them, and
writes `{pub_date, links, unmatched, stats}`; `newsrag.stitch.stitched_articles` merges the
linked parts at read time. CPU only.
"""

from __future__ import annotations

import functools
from datetime import date
from pathlib import Path

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

# every setting the matching reads (the ticket names the first two and the marker file)
VERSION_SETTINGS = [
    "stitch_min_score",
    "stitch_ambiguity_margin",
    "stitch_page_ref_bonus",
    "stitch_kinds",
    "stitch_tail_chars",
    "stitch_token_min_chars",
    "stitch_attach_px",
]


class StitchStage(DateStage):
    name = "stitch"
    depends_on = ("group_final",)

    @functools.cached_property
    def version(self) -> str:  # type: ignore[override]
        return versioned("stitch-v1", VERSION_SETTINGS, [Path(config.settings.continuations_file)])

    def preflight(self) -> None:
        path = Path(config.settings.continuations_file)
        if not path.is_file():
            raise StageUnavailable(f"continuation patterns missing: {path}")

    def handle(self, pub_date: date, pages: list[PageRow]) -> StageResult:
        from newsrag.stitch import load
        from newsrag.stitch.match import Params, stitch_date

        infos = [load.page_info(p) for p in pages]
        groups = load.group_final_artifacts(infos)
        missing = [p.page_id for p in infos if p.page_id not in groups]
        if missing:
            raise FileNotFoundError(f"no done group_final artifact for {missing}")
        artifact = stitch_date(
            pub_date, infos, groups, load.OcrBlocks(), load.patterns(), Params.from_settings()
        )
        path = write_json_atomic(
            artifact_path(self.name, self.version, pub_date.isoformat()), artifact
        )
        detail = {
            "links": len(artifact["links"]),
            "unmatched": len(artifact["unmatched"]),
            **artifact["stats"],
        }
        return StageResult(str(path), detail)
