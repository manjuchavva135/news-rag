"""Stage `enrich_places` (ticket 020): the places of every story and their districts.

A date stage on top of `enrich_core`, run like it: one client opened once per `stage run`,
`enrich_concurrency` parallel requests inside `handle`, the date fails when more than
`enrich_fail_ratio` of its stories could not be asked. Places are resolved to districts with
`newsrag.geo.resolve` (today's districts and the districts on the print date).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from newsrag import config
from newsrag.stages.base import (
    PageRow,
    StageResult,
    artifact_path,
    versioned,
    write_json_atomic,
)
from newsrag.stages.enrich_core import EnrichCoreStage

# every setting the stage reads that changes what it writes (the geo files are hashed below)
VERSION_SETTINGS = [
    "enrich_model",
    "enrich_input_chars",
    "enrich_max_tokens",
    "enrich_max_places",
    "enrich_format_retries",
]


def geo_files() -> list[Path]:
    """The reference data a resolution depends on: the district / mandal files and the aliases."""
    from newsrag.geo.resolve import PACKAGED_ALIASES

    s = config.settings
    aliases = Path(s.geo_aliases_file) if s.geo_aliases_file else PACKAGED_ALIASES
    return [*sorted(Path(s.geo_dir).glob("*.yaml")), *([aliases] if aliases.is_file() else [])]


class EnrichPlacesStage(EnrichCoreStage):
    """Model use (`preflight`, `open`, `close`, `models_needed`) is that of `enrich_core`."""

    name = "enrich_places"
    depends_on = ("enrich_core",)

    def preflight(self) -> None:
        """The model checks of `enrich_core`, and the geo files must load (before any request)."""
        from newsrag import geo
        from newsrag.stages.base import StageUnavailable

        super().preflight()
        try:
            geo.resolve("")
        except Exception as exc:  # noqa: BLE001 - GeoDataError, a bad YAML, a missing key
            raise StageUnavailable(f"geo data: {exc}") from None

    @property
    def version(self) -> str:  # type: ignore[override]
        from newsrag.enrich.places import PROMPT_FILE

        return versioned(
            f"{config.settings.enrich_model}-places-p1",
            VERSION_SETTINGS,
            [PROMPT_FILE, *geo_files()],
        )

    def handle(self, pub_date: date, pages: list[PageRow]) -> StageResult:
        from newsrag.dedup import load
        from newsrag.enrich import places

        if self._client is None:
            raise RuntimeError("enrich_places: the model is not open (call open() first)")
        paths = load.done_dedup(pub_date)
        path = paths.get(pub_date.isoformat())
        if path is None:
            raise FileNotFoundError(f"no done dedup artifact for {pub_date.isoformat()}")
        stories = load.load_dedup(path)["stories"]
        records, summary = places.enrich_places(self._client, stories, pub_date)
        out = write_json_atomic(
            artifact_path(self.name, self.version, pub_date.isoformat()),
            {"pub_date": pub_date.isoformat(), "stories": records},
        )
        print(
            f"enrich_places {pub_date.isoformat()}: {summary['stories']} stories in "
            f"{summary['request_seconds']:.1f} s ({summary['stories_per_s']} stories/s), "
            f"{summary['places']} places, {summary['resolved']} resolved / "
            f"{summary['ambiguous']} ambiguous / {summary['unresolved']} unresolved, "
            f"{summary['enrich_failed']} failed, {summary['dropped_places']} dropped",
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
