"""What the quality report reads (ticket 030): the inputs of bars 1-3 from the ledger and the stores,
the eval files of bars 4-7 and the numbers of the stats sections.

Every function here only reads. A function returns None when its input does not exist yet; the
builder turns an exception into "not available" (sections) or a FAIL (bars).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from newsrag import config

# Filename rejects of `ingest register` that count as quarantined files (bar 1; FR-5: test and
# dummy files are not newspaper pages and are left out).
COUNTED_REJECTS = ("no_date", "bad_filename", "bad_date")
GROUPING_BAKEOFF_NAME = "group-bakeoff.md"  # newsrag.grouping.bakeoff.REPORT_NAME
WINNER_RE = re.compile(r"^winner:\s*(.+?)\s*$", re.MULTILINE)
READ_THREADS = 16  # artifact files read at once (bar 3)


# --- files --------------------------------------------------------------------------------------


def read_json(path: Path) -> Any | None:
    """The parsed file, None when it is missing or not JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def file_sha1(path: Path) -> str | None:
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def ocr_verdicts_file() -> Path:
    return Path(config.settings.ocr_check_dir) / "verdicts.json"


def grouping_bakeoff_file() -> Path:
    return Path(config.settings.reports_dir) / GROUPING_BAKEOFF_NAME


# --- bar 1: quarantine --------------------------------------------------------------------------


def reject_key(path: str) -> tuple[Any, ...]:
    """(date folder, edition, section, page) of a rejected file when its folders give a date and
    its name parses; otherwise the path itself (FR-5: de-duplicated where parseable)."""
    from newsrag.ingest import filenames as fn

    p = Path(path)
    folders = p.parent.parts
    when = fn._last_match(fn.DATE_DIR, folders) or fn._last_match(fn.MONTH_DIR, folders)
    m = fn.FILENAME.match(fn.clean_name(p.name))
    if when is None or m is None:
        return ("path", path)
    return ("page", when.group(0).lower(), m["edition"].upper(), m["section"].upper(), m["page"])


YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def reject_in_corpus(path: str) -> bool:
    """A rejected file counts unless its folders name a year before `corpus_from_date`'s year
    (ticket 031); a file whose folders name no year always counts."""
    start = config.settings.corpus_from_date
    if start is None:
        return True
    years = YEAR_RE.findall(str(Path(path).parent))
    return not years or int(years[-1]) >= start.year


def count_rejects(paths: Iterable[str]) -> int:
    return len({reject_key(p) for p in paths})


def pages_input() -> dict[str, Any]:
    """Bar 1: registered pages, pages with a quarantine_reason and the counted filename rejects
    (quarantine rows without a page, reason not `test_or_dummy`)."""
    from newsrag.ledger import db
    from newsrag.stages.runner import in_corpus

    scope, params = in_corpus()
    with db.conn() as c:
        row = db.fetch_one(
            c,
            "select count(*) as registered, count(quarantine_reason) as quarantined from pages "
            f"where {scope}",
            params,
        )
        rejects = db.fetch_all(
            c,
            "select q.source_path, q.reason from quarantine q where q.reason = any(%s) "
            "and not exists (select 1 from pages p where p.source_path = q.source_path) "
            "order by q.source_path",
            [list(COUNTED_REJECTS)],
        )
    assert row is not None
    rejects = [r for r in rejects if reject_in_corpus(r["source_path"])]
    return {
        "registered": int(row["registered"]),
        "quarantined_pages": int(row["quarantined"]),
        "rejects": count_rejects(r["source_path"] for r in rejects),
        "reject_files": [{"path": r["source_path"], "reason": r["reason"]} for r in rejects],
    }


# --- bars 2 and 3: stages current in the ledger --------------------------------------------------


def _snapshot(stage_name: str) -> Any:
    from newsrag.stages.eligibility import validate
    from newsrag.stages.registry import REGISTRY
    from newsrag.stages.runner import _Snapshot

    stage = REGISTRY[stage_name]
    return _Snapshot(stage, REGISTRY, validate(stage, REGISTRY))


def expected_generation() -> str:
    return f"{config.settings.embed_model}__v1"


def index_version_prefix() -> str:
    return f"index-v1-{config.settings.embed_model}"


def indexed_input() -> dict[str, Any]:
    """Bar 2: non-quarantined pages, those whose date has `index` current (done at the current
    version with current inputs, so not eligible again) with an `index-v1-<embed_model>` version,
    the active generation and the result of `index validate`."""
    from newsrag.embed import generations
    from newsrag.ledger import db
    from newsrag.warehouse import db as wdb

    snap = _snapshot("index")
    prefix = index_version_prefix()
    with db.conn() as c:
        contexts = snap.date_contexts(c)
    pages = indexed = 0
    missing_dates = []
    for ctx in contexts:
        n = len(ctx.active)
        if not n:
            continue
        pages += n
        row = ctx.date_rows.get("index")
        if ctx.current("index") and str(row["version"]).startswith(prefix):
            indexed += n
        else:
            missing_dates.append(ctx.pub_date.isoformat())
    active = wdb.active_generation()
    validate_ok, error, mismatches = False, None, []
    if active is None:
        error = "no active generation"
    else:
        try:
            results = generations.validate(active)
            mismatches = [generations.line(r) for r in results if not (r[1] == r[2] == r[3])]
            validate_ok = bool(results) and not mismatches
            if not results:
                error = "no indexed date"
        except Exception as exc:  # noqa: BLE001 - a store down is a failed validate
            error = f"{type(exc).__name__}: {exc}"
    return {
        "pages": pages,
        "indexed_pages": indexed,
        "dates_not_indexed": missing_dates,
        "active_generation": active,
        "expected_generation": expected_generation(),
        "validate_ok": validate_ok,
        "validate_error": error,
        "validate_mismatches": mismatches,
    }


def _load_artifacts(paths: list[str]) -> list[dict[str, Any]]:
    from newsrag.grouping.input import load_artifact

    with ThreadPoolExecutor(READ_THREADS) as pool:
        return list(pool.map(load_artifact, paths))


def grouping_input() -> dict[str, Any]:
    """Bar 3: `grouping.stats.summarize` over the non-quarantined pages whose `group_final` is
    current; the first `judge` score of each, for the retry gain."""
    from newsrag.grouping.stats import summarize
    from newsrag.ledger import db

    snap = _snapshot("group_final")
    with db.conn() as c:
        contexts = snap.page_contexts(c)
    current = [
        ctx
        for ctx in contexts
        if not ctx.page.quarantine_reason
        and ctx.current("group_final")
        and ctx.rows["group_final"].get("artifact_path")
    ]
    finals = _load_artifacts([ctx.rows["group_final"]["artifact_path"] for ctx in current])
    judged = [
        ctx
        for ctx in current
        if (ctx.rows.get("judge") or {}).get("status") == "done"
        and ctx.rows["judge"].get("artifact_path")
    ]
    firsts = _load_artifacts([ctx.rows["judge"]["artifact_path"] for ctx in judged])
    pages = [{**art, "page_id": ctx.page.page_id} for ctx, art in zip(current, finals, strict=True)]
    original = {ctx.page.page_id: art.get("score") for ctx, art in zip(judged, firsts, strict=True)}
    return summarize(pages, original)


# --- stats sections -----------------------------------------------------------------------------


def corpus() -> dict[str, Any] | None:
    """Per year: non-quarantined pages (ledger), stories and chunks of the active generation
    (warehouse); plus the render counts (ticket 005)."""
    from newsrag.ledger import db
    from newsrag.stages.render import stats as render_stats
    from newsrag.stages.runner import in_corpus
    from newsrag.warehouse import db as wdb

    scope, params = in_corpus()
    with db.conn() as c:
        pages = c.execute(
            "select extract(year from pub_date)::int, count(*) from pages "
            f"where quarantine_reason is null and {scope} group by 1",
            params,
        ).fetchall()
    if not pages:
        return None
    per_year: dict[str, dict[str, int]] = {
        str(y): {"pages": int(n), "stories": 0, "chunks": 0} for y, n in pages
    }
    active = wdb.active_generation()
    if active is not None:
        with wdb.conn() as c:
            stories = c.execute(
                "select extract(year from s.pub_date)::int, count(*) from stories s where exists "
                "(select 1 from chunks k where k.generation = %s and k.story_id = s.story_id) "
                "group by 1",
                (active,),
            ).fetchall()
            chunks = c.execute(
                "select extract(year from pub_date)::int, count(*) from chunks "
                "where generation = %s group by 1",
                (active,),
            ).fetchall()
        for key, rows in (("stories", stories), ("chunks", chunks)):
            for y, n in rows:
                per_year.setdefault(str(y), {"pages": 0, "stories": 0, "chunks": 0})[key] = int(n)
    return {
        "generation": active,
        "per_year": dict(sorted(per_year.items())),
        "render": render_stats(),
    }


def dedup() -> dict[str, Any] | None:
    from newsrag.dedup.stats import stats

    return stats()


def continuations() -> dict[str, Any] | None:
    from newsrag.stitch.stats import stats

    return stats()


def low_confidence() -> dict[str, Any] | None:
    """Stories of the active generation per `low_confidence_reasons` entry."""
    from newsrag.warehouse import db as wdb

    active = wdb.active_generation()
    if active is None:
        return None
    live = "exists (select 1 from chunks k where k.generation = %s and k.story_id = s.story_id)"
    with wdb.conn() as c:
        total = c.execute(
            f"select count(*), count(*) filter (where s.low_confidence) from stories s "
            f"where {live}",
            (active,),
        ).fetchone()
        rows = c.execute(
            "select r, count(distinct s.story_id) "
            f"from stories s, unnest(s.low_confidence_reasons) as r where {live} "
            "group by r order by 2 desc, r",
            (active,),
        ).fetchall()
    return {
        "generation": active,
        "stories": int(total[0]),
        "low_confidence_stories": int(total[1]),
        "reasons": {str(r): int(n) for r, n in rows},
    }


def editions() -> dict[str, Any] | None:
    from newsrag.editions.stats import stats

    counts = stats()
    return {str(y): n for y, n in counts.items()} if counts else None


def aliases() -> dict[str, Any] | None:
    from newsrag.aliases.stats import stats

    numbers = stats()
    if numbers is None:
        return None
    # the counts only: stats.json also lists the auto-merged groups themselves
    return {k: v for k, v in numbers.items() if isinstance(v, (int, float))}


def _bakeoff(path: Path) -> dict[str, Any] | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return {"file": str(path), "winner": WINNER_RE.findall(text), "text": text}


def bakeoffs() -> dict[str, Any] | None:
    """The embedding (ticket 026) and grouping (ticket 014) bake-off reports with their winners."""
    out = {
        "embedding": _bakeoff(config.settings.embed_bakeoff_report_file),
        "grouping": _bakeoff(grouping_bakeoff_file()),
    }
    return out if any(out.values()) else None


def ocr_fallback() -> dict[str, Any] | None:
    """The current Sarvam rules and the `ocr_final` pages whose blocks were sent to Sarvam."""
    from newsrag.ledger import db
    from newsrag.ocr import fallback

    rules = fallback.load_rules()
    with db.conn() as c:
        sent = c.execute(
            "select count(*) from stage_runs where stage = 'ocr_final' and status = 'done' "
            "and (detail->>'fallback')::boolean and coalesce((detail->>'n_sent')::int, 0) > 0"
        ).fetchone()[0]
    return {"rules": rules, "n_rules": len(rules), "pages_sent": int(sent)}


def runs() -> list[dict[str, Any]] | None:
    """Each run file of `newsrag run` (ticket 029): start, duration, tag, models, interrupted."""
    files = sorted(Path(config.settings.runs_dir).glob("*.json"))
    out = []
    for path in files:
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        out.append(
            {
                "file": path.name,
                "started_at": data.get("started_at"),
                "seconds": data.get("seconds"),
                "tag": data.get("tag"),
                "models_loaded": data.get("models_loaded") or [],
                "interrupted": data.get("interrupted"),
            }
        )
    return out or None
