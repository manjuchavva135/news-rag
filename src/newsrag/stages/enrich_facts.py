"""Stage `enrich_facts` (ticket 021): the amounts, counts and percentages of every story as facts.

A date stage on top of `enrich_places`, run like it: one client opened once per `stage run`,
`enrich_concurrency` parallel requests inside `handle`, the date fails when more than
`enrich_fail_ratio` of its stories could not be asked. The model finds the numbers; the code
validates and parses them (`newsrag.enrich.facts`).
"""

from __future__ import annotations

import sys
from datetime import date

from newsrag import config
from newsrag.stages.base import (
    PageRow,
    StageResult,
    artifact_path,
    versioned,
    write_json_atomic,
)
from newsrag.stages.enrich_places import EnrichPlacesStage, geo_files

# every setting the stage reads that changes what it writes (the geo files are hashed below)
VERSION_SETTINGS = [
    "enrich_model",
    "enrich_input_chars",
    "enrich_max_tokens",
    "enrich_max_facts",
    "enrich_facts_scale_window",
    "enrich_format_retries",
]


class EnrichFactsStage(EnrichPlacesStage):
    """Model use and `preflight` (the model and the geo files) are those of `enrich_places`."""

    name = "enrich_facts"
    depends_on = ("enrich_places",)

    @property
    def version(self) -> str:  # type: ignore[override]
        from newsrag.enrich.facts import PROMPT_FILE

        return versioned(
            f"{config.settings.enrich_model}-facts-p1",
            VERSION_SETTINGS,
            [PROMPT_FILE, *geo_files()],
        )

    def handle(self, pub_date: date, pages: list[PageRow]) -> StageResult:
        from newsrag import enrich
        from newsrag.dedup import load
        from newsrag.enrich import facts

        if self._client is None:
            raise RuntimeError("enrich_facts: the model is not open (call open() first)")
        day = pub_date.isoformat()
        path = load.done_dedup(pub_date).get(day)
        if path is None:
            raise FileNotFoundError(f"no done dedup artifact for {day}")
        stories = load.load_dedup(path)["stories"]
        # the event date of a story is `enrich_core`'s; a date without it uses the print date
        core_path = enrich.done_artifacts("enrich_core", pub_date).get(day)
        event_dates: dict[str, str] = {}
        if core_path is not None:
            for story_id, record in (enrich.read_artifact(core_path).get("stories") or {}).items():
                event_dates[story_id] = record.get("event_date") or day
        records, summary = facts.enrich_facts(self._client, stories, pub_date, event_dates)
        out = write_json_atomic(
            artifact_path(self.name, self.version, day),
            {"pub_date": day, "stories": records},
        )
        print(
            f"enrich_facts {day}: {summary['stories']} stories in "
            f"{summary['request_seconds']:.1f} s ({summary['stories_per_s']} stories/s), "
            f"{summary['facts']} facts, {summary['dropped_non_verbatim']} non-verbatim / "
            f"{summary['dropped_unparsed']} unparsed dropped, {summary['enrich_failed']} failed",
            file=sys.stderr,
            flush=True,
        )
        total, failed = summary["stories"], summary["enrich_failed"]
        if total and failed / total > config.settings.enrich_fail_ratio:
            raise RuntimeError(
                f"{failed} of {total} stories could not be enriched "
                f"(more than {config.settings.enrich_fail_ratio:.0%}); artifact kept at {out}"
            )
        return StageResult(str(out), summary)
