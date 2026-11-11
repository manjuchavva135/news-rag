"""The comparison of the old and the new pipeline (ticket 027 FR-4, FR-5).

1. `build_set`: ticket 025's question builder, restricted to new stories with a printing on a
   page the old pipeline also has (`found_in_new` in the page map).
2. `calibrate`: for a sample of those stories, does at least one old chunk on the story's mapped
   page pass the ticket-025 hit rule against the story? Proves the rule can credit the old
   (Sarvam) text; `compare` refuses below `baseline_calibration_min`.
3. `compare`: the retrieval test on `baseline` and on `new` restricted to the mapped pages; new
   beats old when its recall@10 is higher.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.baseline import pages
from newsrag.evals import retrieval

OldChunks = Callable[[Sequence[str]], list[dict[str, Any]]]


class CompareError(RuntimeError):
    """A refusal or a failure of a baseline command (exit 1)."""


def mapping_or_fail(path: Path | None = None) -> dict[str, list[str]]:
    try:
        return pages.read_page_map(path)
    except FileNotFoundError:
        raise CompareError(
            f"no page map ({path or config.settings.baseline_page_map_file}): "
            "run 'newsrag baseline map-pages' first"
        ) from None


def story_filter(mapped: set[str]) -> retrieval.StoryFilter:
    """Keep stories with a printing on a mapped new page."""

    def keep(story: Mapping[str, Any]) -> bool:
        return bool(set(story.get("page_ids") or []) & mapped)

    return keep


# --- build-set --------------------------------------------------------------------------------


def build_set(
    n: int | None = None,
    seed: int | None = None,
    out: Path | None = None,
    mapping: Mapping[str, list[str]] | None = None,
    ask: retrieval.Ask | None = None,
    stories: Sequence[Mapping[str, Any]] | None = None,
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """FR-4: the committed comparison set. Raises CompareError when it is short of `n`."""
    s = config.settings
    n = s.baseline_set_n if n is None else n
    seed = s.baseline_set_seed if seed is None else seed
    out = Path(out or s.baseline_set_file)
    mapping = mapping if mapping is not None else mapping_or_fail()
    mapped = pages.mapped_new_pages(mapping)
    summary = retrieval.build_retrieval_set(
        n=n,
        seed=seed,
        out=out,
        window_n=n,  # every story on a mapped page lies in the old pipeline's window
        story_filter=story_filter(mapped),
        ask=ask,
        stories=stories,
        echo=echo,
    )
    if summary["n"] < n:
        raise CompareError(
            f"short set: {summary['n']} of {n} questions "
            f"({summary['stories_available']} stories on mapped pages)"
        )
    return summary


# --- calibrate --------------------------------------------------------------------------------


def old_chunks_on(old_page_ids: Sequence[str]) -> list[dict[str, Any]]:
    """`page_id`, `text` of every old chunk on these old pages."""
    from psycopg import sql
    from psycopg.rows import dict_row

    from newsrag.baseline import stores

    if not old_page_ids:
        return []
    query = sql.SQL("select page_id, text from {}.chunks where page_id = any(%s)").format(
        sql.Identifier(config.settings.baseline_schema)
    )
    with stores.pg_connect() as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute(query, (list(old_page_ids),))
        return cur.fetchall()


def set_stories(questions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per story of the set: story_id, gold_page_ids, gold_text."""
    seen: dict[str, dict[str, Any]] = {}
    for q in questions:
        seen.setdefault(
            q["story_id"],
            {
                "story_id": q["story_id"],
                "gold_page_ids": list(q["gold_page_ids"]),
                "gold_text": q["gold_text"],
            },
        )
    return [seen[k] for k in sorted(seen)]


def story_passes(
    story: Mapping[str, Any], mapping: Mapping[str, list[str]], old_chunks: OldChunks
) -> bool:
    """At least one old chunk on the story's mapped pages passes the hit rule against it."""
    s = config.settings
    back = pages.reverse(mapping)
    old_ids = sorted({old for new in story["gold_page_ids"] for old in back.get(new, [])})
    gold = retrieval.ngrams(story["gold_text"], s.retrieval_ngram)
    for chunk in old_chunks(old_ids):
        hit_pages = mapping.get(chunk["page_id"], [])
        if retrieval.is_correct(hit_pages, chunk["text"], story["gold_page_ids"], gold):
            return True
    return False


def calibrate(
    n: int | None = None,
    seed: int | None = None,
    set_file: Path | None = None,
    mapping: Mapping[str, list[str]] | None = None,
    old_chunks: OldChunks | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    """FR-4 / FR-5: write `baseline-calibration.json` {calibration (percent), n, sampled_from,
    at} and return it."""
    s = config.settings
    n = s.baseline_calibration_n if n is None else n
    seed = s.baseline_calibration_seed if seed is None else seed
    set_file = Path(set_file or s.baseline_set_file)
    if not set_file.is_file():
        raise CompareError(f"no comparison set ({set_file}): run 'newsrag baseline build-set'")
    mapping = mapping if mapping is not None else mapping_or_fail()
    old_chunks = old_chunks or old_chunks_on
    stories = set_stories(retrieval.read_set(set_file))
    sample = random.Random(seed).sample(stories, min(n, len(stories)))
    passed = sum(1 for story in sample if story_passes(story, mapping, old_chunks))
    report = {
        "calibration": round(100 * passed / len(sample), 1) if sample else 0.0,
        "n": len(sample),
        "passed": passed,
        "sampled_from": str(set_file),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    out = Path(out or s.baseline_calibration_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


# --- compare ----------------------------------------------------------------------------------


def run_paths(compare_file: Path) -> tuple[Path, Path, Path]:
    """(old run JSON, new run JSON, restrict-pages file) next to the compare file."""
    stem = compare_file.with_suffix("")
    return (
        Path(f"{stem}-old.json"),
        Path(f"{stem}-new.json"),
        Path(f"{stem}-pages.txt"),
    )


def compare(
    set_file: Path | None = None,
    mapping: Mapping[str, list[str]] | None = None,
    search_fn: Callable[..., list[Any]] | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    """FR-4: refuse without (or below) calibration; else run both stores and write the result."""
    s = config.settings
    cal_file = Path(s.baseline_calibration_file)
    if not cal_file.is_file():
        raise CompareError("run 'newsrag baseline calibrate' first")
    calibration = float(json.loads(cal_file.read_text(encoding="utf-8"))["calibration"])
    if calibration < 100 * s.baseline_calibration_min:
        raise CompareError(
            f"calibration below {100 * s.baseline_calibration_min:g}%: {calibration:g}"
        )
    set_file = Path(set_file or s.baseline_set_file)
    mapping = mapping if mapping is not None else mapping_or_fail()
    out = Path(out or s.baseline_compare_file)
    old_out, new_out, pages_file = run_paths(out)
    pages_file.parent.mkdir(parents=True, exist_ok=True)
    pages_file.write_text(
        "".join(f"{p}\n" for p in sorted(pages.mapped_new_pages(mapping))), encoding="utf-8"
    )
    old = retrieval.run_retrieval(
        set_file=set_file, store="baseline", out=old_out, search_fn=search_fn
    )
    new = retrieval.run_retrieval(
        set_file=set_file, store="new", restrict_pages=pages_file, out=new_out, search_fn=search_fn
    )
    if old["n"] != new["n"]:
        raise CompareError(f"runs scored different question counts: old {old['n']} new {new['n']}")

    def part(r: Mapping[str, Any]) -> dict[str, Any]:
        return {"recall_at_10": r["recall_at_10"], "mrr": r["mrr"], "n": r["n"]}

    result = {
        "set": str(set_file),
        "calibration": calibration,
        "old": part(old),
        "new": part(new),
        "new_beats_old": new["recall_at_10"] > old["recall_at_10"],
    }
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def compare_line(result: Mapping[str, Any]) -> str:
    return (
        f"old recall@10={result['old']['recall_at_10']:.4f} "
        f"new recall@10={result['new']['recall_at_10']:.4f} "
        f"new_beats_old={str(result['new_beats_old']).lower()}"
    )
