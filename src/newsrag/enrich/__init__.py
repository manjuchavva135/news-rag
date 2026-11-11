"""Story enrichment (tickets 019, 020, 021, 023): metadata of every canonical story.

- `validate`: checking one model reply (names, categories, event dates).
- `core`: the prompt, the requests and the per-date run of stage `enrich_core`.
- `verify`, `cli`: the quality check (`enrich verify-core`) and the statistics (`enrich stats`).
- `load(pub_date)`: the merged result of the enrich stages for one date (below).

Each enrich stage writes its own artifact `{pub_date, stories:{<story_id>: {...}}}`; later stages
never rewrite earlier ones. Importing this package touches neither the database nor the GPU.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

# The stages whose artifacts `load` merges, in merge order (flags are kept in this order).
STAGES = ("enrich_core", "enrich_places", "enrich_facts")


def done_artifacts(stage: str, pub_date: date | str | None = None) -> dict[str, Path]:
    """Print date (ISO) -> the artifact of the done `stage` run (of one date, or of every date)."""
    from newsrag.ledger import db

    where, params = "stage = %s and status = 'done' and artifact_path is not null", [stage]
    if pub_date is not None:
        day = date.fromisoformat(pub_date) if isinstance(pub_date, str) else pub_date
        where += " and pub_date = %s"
        params.append(day)  # type: ignore[arg-type]
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            f"select pub_date, artifact_path from date_runs where {where} order by pub_date",
            params,
        )
    return {
        r["pub_date"].isoformat(): Path(r["artifact_path"])
        for r in rows
        if Path(r["artifact_path"]).is_file()
    }


def read_artifact(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def merge(artifacts: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge enrich artifacts (stage name -> artifact) into `{story_id: record}`.

    `flags` is the ordered, de-duplicated union over the artifacts (in `STAGES` order); every
    other key must belong to one stage: a key that two stages both set raises `ValueError`.
    """
    out: dict[str, dict[str, Any]] = {}
    owner: dict[tuple[str, str], str] = {}
    for stage in sorted(artifacts, key=lambda n: STAGES.index(n) if n in STAGES else len(STAGES)):
        for story_id, record in (artifacts[stage].get("stories") or {}).items():
            merged = out.setdefault(story_id, {"flags": []})
            for key, value in record.items():
                if key == "flags":
                    merged["flags"].extend(f for f in value if f not in merged["flags"])
                    continue
                if key in merged:
                    raise ValueError(
                        f"story {story_id}: key {key!r} is set by both "
                        f"{owner[(story_id, key)]} and {stage}"
                    )
                owner[(story_id, key)] = stage
                merged[key] = value
    for record in out.values():  # flags last
        record["flags"] = record.pop("flags")
    return out


def load(pub_date: date | str) -> dict[str, dict[str, Any]]:
    """The enrichment of one date, `{story_id: record}`: the done `enrich_core`, `enrich_places`
    and `enrich_facts` artifacts merged. A stage that has not run for the date is skipped."""
    artifacts = {}
    for stage in STAGES:
        path = done_artifacts(stage, pub_date).get(
            pub_date if isinstance(pub_date, str) else pub_date.isoformat()
        )
        if path is not None:
            artifacts[stage] = read_artifact(path)
    return merge(artifacts)
