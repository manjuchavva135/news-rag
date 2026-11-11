"""Numbers of `newsrag judge stats` (ticket 013) as a dict, for the command and the quality report
(ticket 030)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

JUDGE_LOW = "low_confidence:judge_low"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(pages: list[dict], original_scores: dict[str, float | None]) -> dict[str, Any]:
    """Statistics of `group_final` artifacts (each with a `page_id`); `original_scores` are the
    `judge` scores of the first groupings. Percentages are None when there is no page."""
    n = len(pages)
    kept = [p["judge_score"] for p in pages if p.get("judge_score") is not None]
    low = sum(1 for p in pages if JUDGE_LOW in (p.get("page_flags") or []))
    retried = [p for p in pages if p.get("retried")]
    gains = [
        p["judge_score"] - original_scores[p["page_id"]]
        for p in retried
        if p.get("judge_score") is not None and original_scores.get(p["page_id"]) is not None
    ]
    flags: Counter = Counter(f for p in pages for f in p.get("page_flags") or [])
    return {
        "pages": n,
        "scored_pages": len(kept),
        "mean_score": _mean(kept),
        "judge_low": low,
        "judge_low_pct": low / n * 100 if n else None,
        "retried": len(retried),
        "retried_pct": len(retried) / n * 100 if n else None,
        "retry_gain": _mean(gains),
        "page_flags": dict(sorted(flags.items())),
    }


def stats(page_ids: list[str] | None = None) -> dict[str, Any]:
    """`summarize` over the done `group_final` artifacts (of `page_ids`, default all)."""
    from newsrag.grouping.input import load_artifact
    from newsrag.ledger import db

    where = "stage = %s and status = 'done' and artifact_path is not null"
    params: list = ["group_final"]
    if page_ids is not None:
        where += " and page_id = any(%s)"
        params.append(list(page_ids))
    with db.conn() as c:
        rows = db.fetch_all(
            c,
            f"select page_id, artifact_path from stage_runs where {where} order by page_id",
            params,
        )
        judged = db.fetch_all(
            c,
            "select page_id, artifact_path from stage_runs where stage = 'judge' "
            "and status = 'done' and artifact_path is not null and page_id = any(%s)",
            [[r["page_id"] for r in rows]],
        )
    pages = [{**load_artifact(r["artifact_path"]), "page_id": r["page_id"]} for r in rows]
    original = {r["page_id"]: load_artifact(Path(r["artifact_path"])).get("score") for r in judged}
    return summarize(pages, original)
