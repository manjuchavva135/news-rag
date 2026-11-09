"""The shared stage runner (ticket 003 FR-5..FR-7, FR-10).

``run_stage`` lists eligible items, then worker threads claim them one at a time. Each claim is a
short transaction committed before ``handle`` runs: a per-(stage, item) advisory lock taken with
try-lock semantics (another process holding it means "skip"), eligibility checked again, and the
stage row upserted as ``running``. After ``handle`` the row becomes ``done`` or ``failed``.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Any, TextIO

from psycopg import sql
from psycopg.types.json import Jsonb

from newsrag.config import settings
from newsrag.ledger import db
from newsrag.stages.base import (
    DateStage,
    PageRow,
    PageStage,
    Stage,
    StageResult,
    StageUnavailable,
)
from newsrag.stages.eligibility import DateContext, PageContext, next_attempt, validate

RUN_COLUMNS = (
    "version",
    "input_versions",
    "status",
    "attempt",
    "artifact_path",
    "detail",
    "error",
    "started_at",
    "finished_at",
)
_json = partial(Jsonb, dumps=partial(json.dumps, default=str))


def _registry() -> Mapping[str, Stage]:
    from newsrag.stages.registry import REGISTRY

    return REGISTRY


@dataclass
class RunFilters:
    page_ids: Sequence[str] | None = None
    pub_date: date | None = None
    limit: int | None = None
    force: bool = False
    workers: int | None = None

    @property
    def filtered(self) -> bool:
        return self.page_ids is not None or self.pub_date is not None


@dataclass
class RunResult:
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    seconds: float = 0.0
    failed_items: list[str] = field(default_factory=list)


def read_pages_file(path: str | Path) -> list[str]:
    """One page_id per line; blank lines and `#` lines are skipped."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def first_line(text: str) -> str:
    lines = str(text).strip().splitlines()
    return lines[0] if lines else ""


# --- loading ledger state ---


def _table(stage: Stage) -> tuple[str, str]:
    return ("date_runs", "pub_date") if isinstance(stage, DateStage) else ("stage_runs", "page_id")


def _load_pages(
    c: Any, page_ids: Sequence[str] | None = None, dates: Sequence[date] | None = None
) -> list[PageRow]:
    clauses, params = [], []
    if page_ids is not None:
        clauses.append("page_id = any(%s)")
        params.append(list(page_ids))
    if dates is not None:
        clauses.append("pub_date = any(%s)")
        params.append(list(dates))
    where = f"where {' and '.join(clauses)}" if clauses else ""
    rows = db.fetch_all(c, f"select * from pages {where} order by pub_date, page_id", params)
    return [PageRow.from_row(r) for r in rows]


def _load_page_rows(
    c: Any,
    stages: Sequence[str],
    page_ids: Sequence[str] | None = None,
    dates: Sequence[date] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    if not stages:
        return {}
    clauses, params = ["r.stage = any(%s)"], [list(stages)]
    if page_ids is not None:
        clauses.append("r.page_id = any(%s)")
        params.append(list(page_ids))
    if dates is not None:
        clauses.append("p.pub_date = any(%s)")
        params.append(list(dates))
    rows = db.fetch_all(
        c,
        "select r.* from stage_runs r join pages p using (page_id) where " + " and ".join(clauses),
        params,
    )
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["page_id"], {})[row["stage"]] = row
    return out


def _load_date_rows(
    c: Any, stages: Sequence[str], dates: Sequence[date] | None = None
) -> dict[date, dict[str, dict[str, Any]]]:
    if not stages:
        return {}
    clauses, params = ["stage = any(%s)"], [list(stages)]
    if dates is not None:
        clauses.append("pub_date = any(%s)")
        params.append(list(dates))
    rows = db.fetch_all(c, "select * from date_runs where " + " and ".join(clauses), params)
    out: dict[date, dict[str, dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["pub_date"], {})[row["stage"]] = row
    return out


class _Snapshot:
    """Ledger state needed to judge one stage for a set of pages or dates."""

    def __init__(self, stage: Stage, registry: Mapping[str, Stage], ancestors: list[str]):
        self.stage = stage
        self.registry = registry
        self.page_stage_names = [
            n for n in [*ancestors, stage.name] if isinstance(registry[n], PageStage)
        ]
        self.date_stage_names = [
            n for n in [*ancestors, stage.name] if isinstance(registry[n], DateStage)
        ]

    def page_contexts(
        self, c: Any, page_ids: Sequence[str] | None = None, dates: Sequence[date] | None = None
    ) -> list[PageContext]:
        pages = _load_pages(c, page_ids, dates)
        rows = _load_page_rows(c, self.page_stage_names, page_ids, dates)
        return [PageContext(self.registry, p, rows.get(p.page_id, {})) for p in pages]

    def date_contexts(self, c: Any, dates: Sequence[date] | None = None) -> list[DateContext]:
        pages = _load_pages(c, None, dates)
        by_date: dict[date, list[PageRow]] = {}
        for page in pages:
            by_date.setdefault(page.pub_date, []).append(page)
        wanted = sorted(by_date)
        page_rows = _load_page_rows(c, self.page_stage_names, None, wanted)
        date_rows = _load_date_rows(c, self.date_stage_names, wanted)
        return [
            DateContext(self.registry, d, by_date[d], page_rows, date_rows.get(d, {}))
            for d in wanted
        ]


# --- claiming and recording ---


@dataclass
class _Claim:
    key: str
    item: Any  # PageRow or date
    pages: list[PageRow] | None  # DateStage only
    prev: dict[str, Any] | None
    started_at: Any
    attempt: int


def _try_lock(c: Any, stage: Stage, key: str) -> bool:
    row = c.execute(
        "select pg_try_advisory_xact_lock(hashtext(%s), hashtext(%s))",
        (f"{db.schema()}:{stage.name}", key),
    ).fetchone()
    return bool(row[0])


def _claim(
    stage: Stage, snapshot: _Snapshot, key: str, max_attempts: int, force: bool
) -> _Claim | None:
    table, key_col = _table(stage)
    with db.conn() as c:
        if not _try_lock(c, stage, key):
            return None  # another runner is claiming this item right now
        if isinstance(stage, PageStage):
            contexts = snapshot.page_contexts(c, page_ids=[key])
            if not contexts:
                return None
            ctx = contexts[0]
            item: Any = ctx.page
            pages = None
            row_key: Any = key
        else:
            pub_date = date.fromisoformat(key)
            contexts = snapshot.date_contexts(c, dates=[pub_date])
            if not contexts:
                return None
            ctx = contexts[0]
            item = pub_date
            pages = list(ctx.active)
            row_key = pub_date
        if not ctx.eligible(stage, max_attempts, force):
            return None
        version = stage.version_for(item)
        inputs = ctx.inputs(stage)
        prev = (
            ctx.rows.get(stage.name)
            if isinstance(ctx, PageContext)
            else ctx.date_rows.get(stage.name)
        )
        attempt = next_attempt(prev, version, inputs)
        query = sql.SQL(
            "insert into {table} ({key_col}, stage, version, input_versions, status, attempt, "
            "started_at) values (%s, %s, %s, %s, 'running', %s, clock_timestamp()) "
            "on conflict ({key_col}, stage) do update set version = excluded.version, "
            "input_versions = excluded.input_versions, status = 'running', "
            "attempt = excluded.attempt, artifact_path = null, detail = null, error = null, "
            "started_at = excluded.started_at, finished_at = null returning started_at"
        ).format(table=sql.Identifier(table), key_col=sql.Identifier(key_col))
        started_at = c.execute(
            query, (row_key, stage.name, version, _json(inputs), attempt)
        ).fetchone()[0]
    return _Claim(key, item, pages, dict(prev) if prev else None, started_at, attempt)


def _row_key(stage: Stage, claim: _Claim) -> Any:
    return claim.item if isinstance(stage, DateStage) else claim.key


def _finish(stage: Stage, claim: _Claim, status: str, **values: Any) -> bool:
    """Write the end state of a claim; only if the row is still the one we claimed."""
    table, key_col = _table(stage)
    sets = [sql.SQL("status = %s"), sql.SQL("finished_at = clock_timestamp()")]
    params: list[Any] = [status]
    for col, value in values.items():
        sets.append(sql.SQL("{} = %s").format(sql.Identifier(col)))
        params.append(_json(value) if col == "detail" else value)
    query = sql.SQL(
        "update {table} set {sets} where {key_col} = %s and stage = %s "
        "and status = 'running' and started_at = %s"
    ).format(
        table=sql.Identifier(table), sets=sql.SQL(", ").join(sets), key_col=sql.Identifier(key_col)
    )
    with db.conn() as c:
        cur = c.execute(query, [*params, _row_key(stage, claim), stage.name, claim.started_at])
        return cur.rowcount == 1


def _restore(stage: Stage, claim: _Claim) -> None:
    """Put a claimed row back the way it was before the claim (attempt not used)."""
    table, key_col = _table(stage)
    ident = {"table": sql.Identifier(table), "key_col": sql.Identifier(key_col)}
    guard = [_row_key(stage, claim), stage.name, claim.started_at]
    with db.conn() as c:
        if claim.prev is None:
            c.execute(
                sql.SQL(
                    "delete from {table} where {key_col} = %s and stage = %s and started_at = %s"
                ).format(**ident),
                guard,
            )
            return
        sets = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(col)) for col in RUN_COLUMNS
        )
        values = [
            _json(claim.prev[col])
            if col in ("input_versions", "detail") and claim.prev[col] is not None
            else claim.prev[col]
            for col in RUN_COLUMNS
        ]
        c.execute(
            sql.SQL(
                "update {table} set {sets} where {key_col} = %s and stage = %s and started_at = %s"
            ).format(sets=sets, **ident),
            [*values, *guard],
        )


# --- listing ---


def list_eligible(
    stage: Stage,
    filters: RunFilters | None = None,
    registry: Mapping[str, Stage] | None = None,
) -> tuple[list[str], int, list[str]]:
    """(eligible keys in order, number of filtered items not eligible, unknown page ids)."""
    filters = filters or RunFilters()
    registry = registry if registry is not None else _registry()
    snapshot = _Snapshot(stage, registry, validate(stage, registry))
    max_attempts = settings.max_attempts
    unknown: list[str] = []
    with db.conn() as c:
        if isinstance(stage, PageStage):
            dates = [filters.pub_date] if filters.pub_date else None
            contexts = snapshot.page_contexts(c, filters.page_ids, dates)
            if filters.page_ids is not None:
                known = {ctx.page.page_id for ctx in contexts}
                unknown = [p for p in dict.fromkeys(filters.page_ids) if p not in known]
            keys = [
                ctx.page.page_id
                for ctx in contexts
                if ctx.eligible(stage, max_attempts, filters.force)
            ]
            considered = len(contexts) + len(unknown)
        else:
            dates = None
            if filters.page_ids is not None:
                pages = _load_pages(c, filters.page_ids)
                known = {p.page_id for p in pages}
                unknown = [p for p in dict.fromkeys(filters.page_ids) if p not in known]
                dates = sorted({p.pub_date for p in pages})
            if filters.pub_date is not None:
                dates = [d for d in (dates or [filters.pub_date]) if d == filters.pub_date]
            contexts = snapshot.date_contexts(c, dates)
            keys = [
                ctx.pub_date.isoformat()
                for ctx in contexts
                if ctx.eligible(stage, max_attempts, filters.force)
            ]
            considered = len(contexts) + len(unknown)
    skipped = considered - len(keys) if filters.filtered else 0
    return keys, skipped, unknown


# --- running ---


class _Output:
    """Progress bar on a TTY, otherwise one line per item."""

    def __init__(self, stage_name: str, total: int, out: TextIO):
        self.out = out
        self.tty = bool(getattr(out, "isatty", lambda: False)())
        self.lock = threading.Lock()
        self.progress = None
        if self.tty and total:
            from rich.console import Console
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                TaskProgressColumn,
                TextColumn,
                TimeElapsedColumn,
                TimeRemainingColumn,
            )

            self.progress = Progress(
                TextColumn(f"[bold]{stage_name}"),
                BarColumn(),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TextColumn("{task.fields[rate]}"),
                TimeElapsedColumn(),
                TextColumn("ETA"),
                TimeRemainingColumn(),
                console=Console(file=out),
            )
            self.task = self.progress.add_task(stage_name, total=total, rate="")
            self.started = time.monotonic()
            self.progress.start()

    def item(self, key: str, error: str | None) -> None:
        with self.lock:
            if self.progress is None:
                line = f"done {key}" if error is None else f"FAILED {key}: {first_line(error)}"
                print(line, file=self.out, flush=True)
                return
            if error is not None:
                self.progress.console.print(f"FAILED {key}: {first_line(error)}", markup=False)
            self.progress.advance(self.task)
            finished = self.progress.tasks[0].completed
            elapsed = max(time.monotonic() - self.started, 1e-9)
            self.progress.update(self.task, rate=f"{finished / elapsed:.2f}/s")

    def stop(self) -> None:
        if self.progress is not None:
            self.progress.stop()


class _Run:
    def __init__(
        self,
        stage: Stage,
        snapshot: _Snapshot,
        keys: list[str],
        filters: RunFilters,
        stop_event: threading.Event,
        output: _Output,
    ):
        self.stage = stage
        self.snapshot = snapshot
        self.keys: Iterator[str] = iter(keys)
        self.filters = filters
        self.stop_event = stop_event
        self.halt = threading.Event()  # open() failed
        self.output = output
        self.max_attempts = settings.max_attempts
        self.lock = threading.Lock()
        self.result = RunResult()
        self.claimed = 0
        self.inflight: dict[str, _Claim] = {}
        self.abandoned = False
        self.open_state = "new"  # new | open | failed
        self.open_error: BaseException | None = None
        self.open_lock = threading.Lock()

    def stopping(self) -> bool:
        return self.stop_event.is_set() or self.halt.is_set()

    def _next_key(self) -> str | None:
        with self.lock:
            if self.filters.limit is not None and self.claimed >= self.filters.limit:
                return None
            key = next(self.keys, None)
            if key is not None:
                self.claimed += 1  # reserve a slot; released if the claim fails
            return key

    def _release_slot(self, count_skipped: bool) -> None:
        with self.lock:
            self.claimed -= 1
            if count_skipped:
                self.result.skipped += 1

    def _ensure_open(self) -> bool:
        with self.open_lock:
            if self.open_state == "new":
                try:
                    self.stage.open()
                    self.open_state = "open"
                except BaseException as exc:  # noqa: BLE001 - any open failure stops the run
                    self.open_state = "failed"
                    self.open_error = exc
                    self.halt.set()
            return self.open_state == "open"

    def worker(self) -> None:
        while not self.stopping():
            key = self._next_key()
            if key is None:
                return
            claim = _claim(self.stage, self.snapshot, key, self.max_attempts, self.filters.force)
            if claim is None:
                self._release_slot(count_skipped=self.filters.filtered)
                continue
            if self.stopping():  # stop arrived while claiming: give the item back
                _restore(self.stage, claim)
                self._release_slot(count_skipped=False)
                return
            if not self._ensure_open():
                _restore(self.stage, claim)
                self._release_slot(count_skipped=False)
                return
            with self.lock:
                self.inflight[key] = claim
            self._process(claim)

    def _process(self, claim: _Claim) -> None:
        error: str | None = None
        try:
            if isinstance(self.stage, PageStage):
                result = self.stage.handle(claim.item)
            else:
                result = self.stage.handle(claim.item, claim.pages or [])
            if result is None:
                result = StageResult()
        except Exception as exc:  # noqa: BLE001 - one item's failure never stops the run
            error = f"{type(exc).__name__}: {exc}"
        with self.lock:
            if self.abandoned or claim.key not in self.inflight:
                return  # already marked interrupted after the stop grace period
            del self.inflight[claim.key]
        if error is None:
            _finish(
                self.stage,
                claim,
                "done",
                artifact_path=result.artifact_path,
                detail=result.detail,
                error=None,
            )
            with self.lock:
                self.result.processed += 1
        else:
            self._record_failure(claim, error)
        self.output.item(claim.key, error)

    def _record_failure(self, claim: _Claim, error: str) -> None:
        _finish(self.stage, claim, "failed", error=error)
        with self.lock:
            self.result.failed += 1
            self.result.failed_items.append(claim.key)
        if claim.attempt >= self.max_attempts:
            try:
                self.stage.on_final_failure(claim.item, error)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"{self.stage.name}: on_final_failure({claim.key}) raised {exc!r}",
                    file=sys.stderr,
                )

    def interrupt_inflight(self) -> None:
        with self.lock:
            self.abandoned = True
            claims = list(self.inflight.values())
            self.inflight.clear()
        for claim in claims:
            self._record_failure(claim, "interrupted")
            self.output.item(claim.key, "interrupted")


def run_stage(
    stage: Stage,
    filters: RunFilters | None = None,
    stop_event: threading.Event | None = None,
    stop_grace_s: float = 600,
    *,
    registry: Mapping[str, Stage] | None = None,
    out: TextIO | None = None,
) -> RunResult:
    """Run one stage over its eligible items. Raises StageUnavailable from preflight/open."""
    started = time.monotonic()
    filters = filters or RunFilters()
    stop_event = stop_event or threading.Event()
    registry = registry if registry is not None else _registry()
    out = out or sys.stdout

    stage.preflight()  # before touching the DB
    snapshot = _Snapshot(stage, registry, validate(stage, registry))
    keys, skipped, unknown = list_eligible(stage, filters, registry)
    for page_id in unknown:
        print(f"{stage.name}: unknown page {page_id}", file=sys.stderr)
    total = min(len(keys), filters.limit) if filters.limit is not None else len(keys)
    output = _Output(stage.name, total, out)
    run = _Run(stage, snapshot, keys, filters, stop_event, output)
    run.result.skipped = skipped

    workers = max(1, filters.workers or stage.concurrency or 1)
    threads = [
        threading.Thread(target=run.worker, name=f"{stage.name}-{i}", daemon=True)
        for i in range(min(workers, max(total, 1)))
    ]
    try:
        for thread in threads:
            thread.start()
        deadline: float | None = None
        while True:
            alive = [t for t in threads if t.is_alive()]
            if not alive:
                break
            if stop_event.is_set() and deadline is None:
                deadline = time.monotonic() + stop_grace_s
            if deadline is not None and time.monotonic() >= deadline:
                run.interrupt_inflight()
                break
            try:
                alive[0].join(timeout=0.2)
            except KeyboardInterrupt:
                stop_event.set()
    finally:
        output.stop()
        if run.open_state != "new":
            try:
                stage.close()
            except Exception as exc:  # noqa: BLE001
                print(f"{stage.name}: close() raised {exc!r}", file=sys.stderr)

    if run.open_state == "failed":
        raise StageUnavailable(f"{stage.name}: open() failed: {run.open_error}")
    run.result.seconds = time.monotonic() - started
    return run.result


# --- status, failures, maintenance ---


def stage_counts(stage: Stage) -> dict[str, int]:
    """done / failed / running counts (rows of quarantined pages not counted) and eligible."""
    counts = {"done": 0, "failed": 0, "running": 0}
    with db.conn() as c:
        if isinstance(stage, PageStage):
            rows = c.execute(
                "select r.status, count(*) from stage_runs r join pages p using (page_id) "
                "where r.stage = %s and p.quarantine_reason is null group by r.status",
                (stage.name,),
            ).fetchall()
        else:
            rows = c.execute(
                "select status, count(*) from date_runs where stage = %s group by status",
                (stage.name,),
            ).fetchall()
    for status, count in rows:
        counts[status] = count
    counts["eligible"] = len(list_eligible(stage)[0])
    return counts


def status_lines(registry: Mapping[str, Stage] | None = None) -> list[str]:
    registry = registry if registry is not None else _registry()
    width = max((len(name) for name in registry), default=0)
    lines = []
    for name, stage in registry.items():
        n = stage_counts(stage)
        lines.append(
            f"{name:<{width}}  done={n['done']} failed={n['failed']} "
            f"running={n['running']} eligible={n['eligible']}"
        )
    return lines


def failure_lines(stage: Stage) -> list[str]:
    table, key_col = _table(stage)
    with db.conn() as c:
        rows = c.execute(
            sql.SQL(
                "select {key_col}, attempt, error from {table} "
                "where stage = %s and status = 'failed' order by {key_col}"
            ).format(table=sql.Identifier(table), key_col=sql.Identifier(key_col)),
            (stage.name,),
        ).fetchall()
    return [f"{key}\t{attempt}\t{first_line(error or '')}" for key, attempt, error in rows]


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> timedelta:
    """`90s`, `30m`, `2h`, `1d`."""
    match = _DURATION_RE.match(text)
    if not match:
        raise ValueError(f"bad duration {text!r}: use e.g. 90s, 30m, 2h, 1d")
    return timedelta(seconds=int(match.group(1)) * _UNITS[match.group(2)])


def requeue_stale(older_than: str) -> int:
    """Turn `running` rows started before now - older_than into `failed`. Returns the count."""
    limit = parse_duration(older_than)
    error = f"stale: requeued after {older_than.strip()}"
    total = 0
    with db.conn() as c:
        for table in ("stage_runs", "date_runs"):
            cur = c.execute(
                sql.SQL(
                    "update {} set status = 'failed', error = %s, finished_at = clock_timestamp() "
                    "where status = 'running' and started_at < now() - %s"
                ).format(sql.Identifier(table)),
                (error, limit),
            )
            total += cur.rowcount
    return total


def reset_stage(
    stage: Stage, page_id: str | None = None, pub_date: date | None = None, all_rows: bool = False
) -> int:
    """Delete the matching rows of one stage so they run again. Returns the count."""
    if sum(x is not None and x is not False for x in (page_id, pub_date, all_rows)) != 1:
        raise ValueError("give exactly one of --page, --date, --all")
    with db.conn() as c:
        if isinstance(stage, DateStage):
            if page_id is not None:
                cur = c.execute(
                    "delete from date_runs where stage = %s and pub_date = "
                    "(select pub_date from pages where page_id = %s)",
                    (stage.name, page_id),
                )
            elif pub_date is not None:
                cur = c.execute(
                    "delete from date_runs where stage = %s and pub_date = %s",
                    (stage.name, pub_date),
                )
            else:
                cur = c.execute("delete from date_runs where stage = %s", (stage.name,))
        elif page_id is not None:
            cur = c.execute(
                "delete from stage_runs where stage = %s and page_id = %s", (stage.name, page_id)
            )
        elif pub_date is not None:
            cur = c.execute(
                "delete from stage_runs r using pages p where r.page_id = p.page_id "
                "and r.stage = %s and p.pub_date = %s",
                (stage.name, pub_date),
            )
        else:
            cur = c.execute("delete from stage_runs where stage = %s", (stage.name,))
        stage.on_reset(c, page_id, pub_date, all_rows)
        return cur.rowcount
