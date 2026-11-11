"""`newsrag run`: the whole pipeline in one command (ticket 029).

One run is: ``stage requeue-stale``, ``ingest unpack``, ``ingest register`` (skipped when the
unpacked archives did not change), then every stage of the registry in topological order of
``depends_on``, one pass per stage through the ticket-003 runner, so a stage's model is loaded at
most once per pass and never for a stage with nothing to do. Corpus-level steps run right after
the stage they read (``editions build`` + ``editions apply`` after ``folio``, ``aliases build``
after ``enrich_core``) and only when the fingerprint of their input changed. The whole order is
repeated until a cycle finds no work; then ``index validate`` runs (unfiltered runs that reach
``index``).

Every run writes ``<runs_dir>/<UTC timestamp>.json``. Item failures never stop a run; a stage
that cannot run at all does (exit 1). SIGINT/SIGTERM stop claiming, give items in flight
``run_stop_grace_s`` to finish, and exit 130.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TextIO

from newsrag import config
from newsrag.ledger import db
from newsrag.stages.base import DateStage, Stage, write_json_atomic

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

Echo = Callable[[str], None]


class StepFailed(Exception):
    """A whole stage or corpus step could not run: the run stops with exit 1."""

    def __init__(self, step: str, message: str):
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message


# --- order -----------------------------------------------------------------------------------


def stage_order(registry: Mapping[str, Stage]) -> list[str]:
    """Every registered stage, each after all stages it depends on; ties keep registry order."""
    from newsrag.stages.eligibility import validate

    for stage in registry.values():
        validate(stage, registry)  # unknown dependencies, cycles, page -> date dependencies
    done: list[str] = []
    remaining = list(registry)
    while remaining:
        for name in remaining:
            if all(dep in done for dep in registry[name].depends_on):
                done.append(name)
                remaining.remove(name)
                break
    return done


def forget_cached_versions(registry: Mapping[str, Stage]) -> None:
    """Drop `functools.cached_property` versions so the next look recomputes them (FR-7: a corpus
    step may have changed a file a version hashes)."""
    for stage in registry.values():
        if isinstance(getattr(type(stage), "version", None), functools.cached_property):
            stage.__dict__.pop("version", None)


# --- fingerprints ----------------------------------------------------------------------------


def fingerprint(parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(payload.encode()).hexdigest()


def stored_fingerprint(step: str) -> str | None:
    with db.conn() as c:
        row = c.execute("select fingerprint from corpus_steps where step = %s", (step,)).fetchone()
    return row[0] if row else None


def store_fingerprint(step: str, value: str) -> None:
    with db.conn() as c:
        c.execute(
            "insert into corpus_steps (step, fingerprint, finished_at) values (%s, %s, now()) "
            "on conflict (step) do update set fingerprint = excluded.fingerprint, "
            "finished_at = excluded.finished_at",
            (step, value),
        )


def _file_bytes_hash(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def register_fingerprint(source_dir: Path) -> str:
    """The `.unpacked` markers of the stems under `source_dir` and their PDF counts."""
    from newsrag.ingest.unpack import marker_path, read_marker

    parts = []
    if source_dir.is_dir():
        for entry in sorted(source_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            marker = read_marker(marker_path(source_dir, entry.name))
            if marker is None:
                continue
            pdfs = sum(
                1
                for _, _, files in os.walk(entry)
                for name in files
                if name.lower().endswith(".pdf")
            )
            parts.append([entry.name, marker, pdfs])
    return fingerprint(parts)


def page_stage_fingerprint(stage: str) -> str:
    """(page_id, version, finished_at) of the stage's done rows."""
    with db.conn() as c:
        rows = c.execute(
            "select page_id, version, finished_at from stage_runs "
            "where stage = %s and status = 'done' order by page_id",
            (stage,),
        ).fetchall()
    return fingerprint([[r[0], r[1], r[2].isoformat()] for r in rows])


def date_stage_fingerprint(stage: str) -> str:
    """(pub_date, version, finished_at) of the stage's done rows."""
    with db.conn() as c:
        rows = c.execute(
            "select pub_date, version, finished_at from date_runs "
            "where stage = %s and status = 'done' order by pub_date",
            (stage,),
        ).fetchall()
    return fingerprint([[r[0].isoformat(), r[1], r[2].isoformat()] for r in rows])


# --- corpus steps ----------------------------------------------------------------------------


@dataclass
class CorpusStep:
    """A corpus-level step run after stage `after` when `fingerprint()` changed."""

    name: str
    after: str
    fingerprint: Callable[[], str]
    run: Callable[[Echo], dict[str, Any]]


def _editions_fingerprint() -> str:
    return page_stage_fingerprint("folio")


def _editions_run(echo: Echo) -> dict[str, Any]:
    from newsrag.editions import store

    path = config.settings.editions_file
    before = _file_bytes_hash(path)
    written, built = store.build()
    for year, codes in built.items():
        n_ok = sum(1 for e in codes.values() if e["status"] == "ok")
        echo(f"editions build: {year}: {len(codes)} codes, {n_ok} ok")
    rows = store.apply()
    echo(f"editions apply: page_editions: {rows} rows")
    changed = _file_bytes_hash(written) != before
    if changed:
        try:
            shown = Path(written).relative_to(config.settings.repo_root)
        except ValueError:
            shown = written
        echo(f"{shown} changed: commit it")
    return {"page_editions": rows, "editions_yaml_changed": changed}


def _aliases_fingerprint() -> str:
    return fingerprint(
        [
            date_stage_fingerprint("enrich_core"),
            _file_bytes_hash(config.settings.alias_overrides_file),
        ]
    )


def _aliases_run(echo: Echo) -> dict[str, Any]:
    from newsrag.aliases import build as builder
    from newsrag.aliases.cli import stats_line

    try:
        result = builder.build()
    except LookupError as exc:
        raise StepFailed("aliases build", str(exc)) from exc
    echo(f"aliases build: asked={result.asked} {stats_line(result.stats)}")
    return {"asked": result.asked, **result.stats}


def default_corpus_steps() -> list[CorpusStep]:
    return [
        CorpusStep("editions build", "folio", _editions_fingerprint, _editions_run),
        CorpusStep("aliases build", "enrich_core", _aliases_fingerprint, _aliases_run),
    ]


def default_stale_dates() -> list[date]:
    from newsrag.chunking import stale_dates

    return stale_dates()


def default_validate_index(echo: Echo) -> bool:
    """`index validate` over the active generation; False on a mismatch or no generation."""
    from newsrag.embed import generations
    from newsrag.warehouse import db as wdb

    generation = wdb.active_generation()
    if generation is None:
        echo("index validate: no active generation")
        return False
    results = generations.validate(generation, None)
    bad = [r for r in results if not (r[1] == r[2] == r[3])]
    for result in bad:
        echo(f"index validate: {generations.line(result)}")
    echo(f"index validate: {len(results) - len(bad)} dates ok, {len(bad)} mismatched")
    return not bad


def models_loaded() -> list[str]:
    """Every model profile and in-process GPU user this process loaded (ticket 006)."""
    from newsrag.models import loaded_profiles, lock_holders

    return list(dict.fromkeys([*loaded_profiles(), *lock_holders()]))


# --- the run ---------------------------------------------------------------------------------


@dataclass
class RunOptions:
    until: str | None = None
    only: Sequence[str] | None = None
    pages_file: Path | None = None
    dates: Sequence[date] | None = None
    dry_run: bool = False
    tag: str | None = None

    @property
    def filtered(self) -> bool:
        return self.pages_file is not None or self.dates is not None


@dataclass
class StageStats:
    processed: int = 0
    failed: int = 0
    seconds: float = 0.0
    passes: int = 0
    failed_keys: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "failed": self.failed,
            "seconds": round(self.seconds, 3),
            "passes": self.passes,
        }


class Orchestrator:
    """One `newsrag run`. Everything it touches can be swapped for tests."""

    def __init__(
        self,
        options: RunOptions,
        *,
        registry: Mapping[str, Stage] | None = None,
        corpus_steps: Sequence[CorpusStep] | None = None,
        stale_dates: Callable[[], list[date]] | None = None,
        validate_index: Callable[[Echo], bool] | None = None,
        stop_event: threading.Event | None = None,
        out: TextIO | None = None,
    ):
        if registry is None:
            from newsrag.stages.registry import REGISTRY

            registry = REGISTRY
        self.options = options
        self.registry = registry
        self.corpus_steps = list(default_corpus_steps() if corpus_steps is None else corpus_steps)
        self.stale_dates = stale_dates or default_stale_dates
        self.validate_index = validate_index or default_validate_index
        self.stop_event = stop_event or threading.Event()
        self.out = out or sys.stdout
        self.stats: dict[str, StageStats] = {}
        self.corpus: dict[str, dict[str, Any]] = {}
        self.record: dict[str, Any] = {}

    def echo(self, message: str) -> None:
        print(message, file=self.out, flush=True)

    # --- plan ---

    def plan(self) -> list[str]:
        order = stage_order(self.registry)
        if self.options.only:
            unknown = [n for n in self.options.only if n not in self.registry]
            if unknown:
                raise KeyError(", ".join(unknown))
            return [n for n in order if n in self.options.only]
        if self.options.until:
            if self.options.until not in self.registry:
                raise KeyError(self.options.until)
            return order[: order.index(self.options.until) + 1]
        return order

    def _steps_after(self, stage: str, plan: list[str]) -> list[CorpusStep]:
        """Corpus steps that follow `stage` and feed a stage still in the plan."""
        if self.options.only or stage == plan[-1]:
            return []
        return [s for s in self.corpus_steps if s.after == stage]

    def filters(self) -> Any:
        from newsrag.stages.runner import RunFilters, read_pages_file

        page_ids: list[str] | None = None
        if self.options.pages_file is not None:
            page_ids = read_pages_file(self.options.pages_file)
        if self.options.dates is not None:
            with db.conn() as c:
                rows = c.execute(
                    "select page_id from pages where pub_date = any(%s) order by page_id",
                    (list(self.options.dates),),
                ).fetchall()
            on_dates = [r[0] for r in rows]
            if page_ids is None:
                page_ids = on_dates
            else:
                wanted = set(on_dates)
                page_ids = [p for p in page_ids if p in wanted]
        return RunFilters(page_ids=page_ids)

    # --- steps ---

    def _ingest(self) -> None:
        from newsrag.ingest.register import register_all
        from newsrag.ingest.unpack import find_archives, is_unpacked, unpack_all

        s = config.settings
        archive_dir, source_dir = Path(s.archive_dir), Path(s.source_dir)
        archives = find_archives(archive_dir) if archive_dir.is_dir() else []
        todo = [a for a in archives if not is_unpacked(a, source_dir)]
        code = unpack_all(archive_dir=archive_dir, source_dir=source_dir, echo=self.echo)
        unpacked = [a.name for a in todo if is_unpacked(a, source_dir)]
        self.record["archives_unpacked"] = unpacked
        self.record["unpack_exit_code"] = code
        if code != 0:
            self.echo(f"ingest unpack: exit {code} (see above); continuing with what is unpacked")

        before = _count_pages()
        fp = register_fingerprint(source_dir)
        if fp == stored_fingerprint("register"):
            self.echo("ingest register: skipped (unpacked archives unchanged)")
            self.record["register"] = "skipped"
        else:
            register_all(source_dir=source_dir, reports_dir=Path(s.reports_dir), echo=self.echo)
            store_fingerprint("register", fp)
            self.record["register"] = "ran"
        self.record["pages_registered"] = _count_pages() - before

    def _run_corpus_step(self, step: CorpusStep) -> bool:
        """Run `step` if its input changed. True when it ran."""
        fp = step.fingerprint()
        if fp == stored_fingerprint(step.name):
            self.corpus.setdefault(step.name, {"ran": 0})
            return False
        self.echo(f"{step.name}: input changed, running")
        started = time.monotonic()
        try:
            detail = step.run(self.echo) or {}
        except StepFailed:
            raise
        except Exception as exc:
            raise StepFailed(step.name, f"{type(exc).__name__}: {exc}") from exc
        entry = self.corpus.setdefault(step.name, {"ran": 0})
        entry["ran"] += 1
        entry["seconds"] = round(entry.get("seconds", 0) + time.monotonic() - started, 3)
        entry["detail"] = detail
        if self.stop_event.is_set():
            self.echo(f"{step.name}: interrupted, fingerprint not stored")
        else:
            store_fingerprint(step.name, fp)
        self._after_corpus_step()
        return True

    def _after_corpus_step(self) -> None:
        """Reset `chunk` for the dates whose lookups changed; re-read every version (FR-7)."""
        forget_cached_versions(self.registry)
        if "chunk" not in self.registry:
            return
        from newsrag.stages.runner import reset_stage

        stale = self.stale_dates()
        for day in stale:
            reset_stage(self.registry["chunk"], pub_date=day)
        if stale:
            self.echo(f"chunk: reset {len(stale)} dates with changed lookups")
        self.record["chunk_dates_reset"] = self.record.get("chunk_dates_reset", 0) + len(stale)

    def _run_pass(self, name: str, filters: Any) -> bool:
        """One pass of stage `name`. True when it processed or failed any item."""
        from newsrag.stages.runner import list_eligible, run_stage

        stage = self.registry[name]
        try:
            keys, _, _ = list_eligible(stage, filters, self.registry)
            if not keys:
                return False
            self.echo(f"{name}: {len(keys)} eligible")
            result = run_stage(
                stage,
                filters,
                stop_event=self.stop_event,
                stop_grace_s=config.settings.run_stop_grace_s,
                registry=self.registry,
                out=self.out,
            )
        except Exception as exc:
            raise StepFailed(name, f"{type(exc).__name__}: {exc}") from exc
        stats = self.stats.setdefault(name, StageStats())
        stats.processed += result.processed
        stats.failed += result.failed
        stats.seconds += result.seconds
        stats.passes += 1
        stats.failed_keys.extend(result.failed_items)
        self.echo(
            f"{name}: done={result.processed} failed={result.failed} in {result.seconds:.1f}s"
        )
        return result.processed + result.failed > 0

    def _cycles(self, plan: list[str], filters: Any) -> None:
        max_cycles = config.settings.max_attempts + 2
        for cycle in range(1, max_cycles + 1):
            worked = False
            for name in plan:
                if self.stop_event.is_set():
                    return
                worked |= self._run_pass(name, filters)
                for step in self._steps_after(name, plan):
                    if self.stop_event.is_set():
                        return
                    worked |= self._run_corpus_step(step)
            self.record["cycles"] = cycle
            if self.options.only or not worked:
                return
        self.echo(f"stopping after {max_cycles} cycles that all found work")

    # --- dry run ---

    def dry_run(self) -> int:
        from newsrag.stages.runner import list_eligible

        try:
            plan = self.plan()
        except KeyError as exc:
            self.echo(f"unknown stage {exc.args[0]}; known stages: {', '.join(self.registry)}")
            return EXIT_USAGE
        filters = self.filters()
        width = max(len(n) for n in plan) if plan else 0
        for name in plan:
            keys, _, _ = list_eligible(self.registry[name], filters, self.registry)
            self.echo(f"{name:<{width}}  eligible={len(keys)}")
        return EXIT_OK

    # --- whole run ---

    def run(self) -> int:
        if self.options.dry_run:
            return self.dry_run()
        try:
            plan = self.plan()
        except KeyError as exc:
            self.echo(f"unknown stage {exc.args[0]}; known stages: {', '.join(self.registry)}")
            return EXIT_USAGE

        from newsrag.stages.runner import requeue_stale

        started = datetime.now(UTC)
        clock = time.monotonic()
        o = self.options
        self.record.update(
            {
                "started_at": started.isoformat(),
                "tag": o.tag,
                "options": {
                    "until": o.until,
                    "only": list(o.only) if o.only else None,
                    "pages_file": str(o.pages_file) if o.pages_file else None,
                    "dates": [d.isoformat() for d in o.dates] if o.dates is not None else None,
                },
                "plan": plan,
                "interrupted": False,
                "failed_stage": None,
                "error": None,
            }
        )
        self.stats = {name: StageStats() for name in plan}
        code = EXIT_OK
        try:
            older = config.settings.run_requeue_older_than
            self.record["requeued_stale"] = requeue_stale(older)
            if not self.stop_event.is_set():
                self._ingest()
            filters = self.filters()
            if not self.stop_event.is_set():
                self._cycles(plan, filters)
            if (
                not self.stop_event.is_set()
                and not o.filtered
                and not o.only
                and plan
                and plan[-1] == "index"
            ):
                ok = self.validate_index(self.echo)
                self.record["index_validate"] = "ok" if ok else "failed"
                if not ok:
                    code = EXIT_FAILED
            if self.record.get("unpack_exit_code", 0) != 0 and code == EXIT_OK:
                code = EXIT_FAILED
        except StepFailed as exc:
            self.echo(f"run stopped: {exc}")
            self.record["failed_stage"] = exc.step
            self.record["error"] = exc.message
            code = EXIT_FAILED
        except Exception as exc:  # noqa: BLE001 - e.g. stores down during ingest
            self.echo(f"run stopped: {type(exc).__name__}: {exc}")
            self.record["failed_stage"] = "ingest"
            self.record["error"] = f"{type(exc).__name__}: {exc}"
            code = EXIT_FAILED
        if self.stop_event.is_set():
            self.record["interrupted"] = True
            code = EXIT_INTERRUPTED
        self.record["finished_at"] = datetime.now(UTC).isoformat()
        self.record["seconds"] = round(time.monotonic() - clock, 3)
        self.record["exit_code"] = code
        path = self.write_run_file(started)
        self.echo(f"run file: {path}")
        return code

    def write_run_file(self, started: datetime) -> Path:
        self.record["stages"] = {name: s.as_json() for name, s in self.stats.items()}
        self.record["corpus_steps"] = self.corpus
        self.record["failed_items"] = self._permanently_failed()
        self.record["models_loaded"] = models_loaded()
        try:
            self.record["status"] = _status_snapshot(self.registry)
        except Exception as exc:  # noqa: BLE001 - stores down: the run file is still written
            self.record["status"] = {"error": f"{type(exc).__name__}: {exc}"}
        runs_dir = Path(config.settings.runs_dir)
        path = runs_dir / f"{started.strftime('%Y%m%dT%H%M%S.%fZ')}.json"
        write_json_atomic(path, self.record)
        return path

    def _permanently_failed(self) -> dict[str, list[str]]:
        """Items this run failed that have now failed `max_attempts` times."""
        out: dict[str, list[str]] = {}
        limit = config.settings.max_attempts
        try:
            with db.conn() as c:
                for name, stats in self.stats.items():
                    if not stats.failed_keys:
                        continue
                    if isinstance(self.registry[name], DateStage):
                        keys = [date.fromisoformat(k) for k in stats.failed_keys]
                        query = (
                            "select pub_date::text from date_runs where stage = %s and "
                            "status = 'failed' and attempt >= %s and pub_date = any(%s) "
                            "order by 1"
                        )
                    else:
                        keys = list(stats.failed_keys)
                        query = (
                            "select page_id from stage_runs where stage = %s and "
                            "status = 'failed' and attempt >= %s and page_id = any(%s) "
                            "order by 1"
                        )
                    found = [r[0] for r in c.execute(query, (name, limit, keys)).fetchall()]
                    if found:
                        out[name] = found
        except Exception:  # noqa: BLE001 - stores down: keep what this run saw
            return {n: s.failed_keys for n, s in self.stats.items() if s.failed_keys}
        return out


def _count_pages() -> int:
    with db.conn() as c:
        return int(c.execute("select count(*) from pages").fetchone()[0])


def _status_snapshot(registry: Mapping[str, Stage]) -> dict[str, dict[str, int]]:
    from newsrag.stages.runner import stage_counts

    return {name: stage_counts(stage) for name, stage in registry.items()}


@contextmanager
def stop_on_signals(stop_event: threading.Event, echo: Echo) -> Iterator[None]:
    """SIGINT/SIGTERM set `stop_event` (main thread only); the old handlers come back after."""

    def handler(signum: int, _frame: Any) -> None:
        if not stop_event.is_set():
            echo(
                f"{signal.Signals(signum).name}: stopping - no new items; items in flight get "
                f"{config.settings.run_stop_grace_s:.0f}s to finish"
            )
        stop_event.set()

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {sig: signal.signal(sig, handler) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)
