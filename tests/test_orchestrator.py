"""`newsrag run` (ticket 029) with fake stages, a throw-away ledger schema and temp dirs."""

from __future__ import annotations

import functools
import io
import json
import threading
import time
import zipfile
from datetime import date
from pathlib import Path

import pytest
from _dummy_stages import DummyDate, DummyPage, add_pages, rows

from newsrag import config
from newsrag import orchestrator as orch_mod
from newsrag.ledger import db
from newsrag.orchestrator import (
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    CorpusStep,
    Orchestrator,
    RunOptions,
    page_stage_fingerprint,
    stage_order,
    stored_fingerprint,
)
from newsrag.stages.base import PageRow, StageResult, StageUnavailable

pytestmark = pytest.mark.stores

D1 = date(2026, 4, 1)
D2 = date(2026, 4, 2)


@pytest.fixture
def env(tmp_path, monkeypatch, ledger):
    """Settings pointing every path the orchestrator touches into tmp_path."""
    archive_dir = tmp_path / "data"
    archive_dir.mkdir()
    patched = config.settings.model_copy(
        update={
            "archive_dir": archive_dir,
            "source_dir": tmp_path / "raw" / "source",
            "reports_dir": tmp_path / "raw" / "reports",
            "runs_dir": tmp_path / "raw" / "reports" / "runs",
            "alias_overrides_file": tmp_path / "overrides.yaml",
            "editions_file": tmp_path / "editions.yaml",
            "run_stop_grace_s": 5,
        }
    )
    monkeypatch.setattr(config, "settings", patched)
    monkeypatch.setattr(orch_mod, "models_loaded", list)
    return tmp_path


def register(registry, *stages):
    for stage in stages:
        registry[stage.name] = stage
    return stages


def make(registry, steps=(), stale=None, validate=None, stop=None, **options) -> Orchestrator:
    return Orchestrator(
        RunOptions(**options),
        registry=registry,
        corpus_steps=list(steps),
        stale_dates=stale or (list),
        validate_index=validate or (lambda echo: True),
        stop_event=stop,
        out=io.StringIO(),
    )


def last_run_file(env: Path) -> dict:
    files = sorted((env / "raw" / "reports" / "runs").glob("*.json"))
    assert files, "no run file written"
    return json.loads(files[-1].read_text())


class Flaky(DummyPage):
    """Fails the first attempt of every page in `flaky`, then succeeds."""

    def __init__(self, name, flaky, **kw):
        super().__init__(name, **kw)
        self.flaky = set(flaky)

    def handle(self, page: PageRow) -> StageResult:
        if page.page_id in self.flaky:
            self.flaky.discard(page.page_id)
            with self.lock:
                self.calls.append(page.page_id)
            raise ValueError("first attempt fails")
        return super().handle(page)


class Counting(CorpusStep):
    def __init__(self, name, after, fingerprint, log=None, fail=False, on_run=None):
        self.runs = 0
        self.log = log
        self.fail = fail
        self.on_run = on_run

        def run(echo):
            self.runs += 1
            if self.log is not None:
                self.log.append(name)
            if self.on_run:
                self.on_run()
            if self.fail:
                raise RuntimeError("step broke")
            return {"n": self.runs}

        super().__init__(name, after, fingerprint, run)


def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _member(day: date, page: int) -> str:
    month = day.strftime("%B-%Y")
    return (
        f"{day.year}/{month}/{day.strftime('%d-%m-%Y')}/main/out/{day.day:02d}B-MAIN_{page:02d}.pdf"
    )


# --- order -----------------------------------------------------------------------------------


def test_stage_order_is_topological(registry):
    register(
        registry,
        DummyPage("c", depends_on=("b",)),
        DummyPage("a"),
        DummyPage("b", depends_on=("a",)),
        DummyDate("d", depends_on=("c",)),
    )
    assert stage_order(registry) == ["a", "b", "c", "d"]


def test_real_registry_order():
    from newsrag.stages.registry import REGISTRY

    order = stage_order(REGISTRY)
    for name in order:
        for dep in REGISTRY[name].depends_on:
            assert order.index(dep) < order.index(name)
    assert order[:5] == ["render", "ocr", "ocr_split", "ocr_final", "folio"]
    assert order[-2:] == ["chunk", "index"]


# --- E-2 -------------------------------------------------------------------------------------


def test_runs_until_no_work(env, registry):
    ids = add_pages(3)
    register(
        registry,
        Flaky("a", flaky={ids[0]}),
        DummyPage("b", depends_on=("a",)),
        DummyDate("d", depends_on=("b",)),
    )
    o = make(registry)
    assert o.run() == EXIT_OK
    assert all(r["status"] == "done" for r in rows("a").values())
    assert sorted(rows("b")) == ids
    assert list(rows("d", "date_runs")) == [D1]
    assert o.record["cycles"] == 3  # cycle 2 retries ids[0] and its downstream, 3 finds nothing
    run = last_run_file(env)
    assert run["stages"]["a"] == {**run["stages"]["a"], "processed": 3, "failed": 1}


def test_model_loaded_once_per_pass(env, registry):
    ids = add_pages(5)
    (a,) = register(registry, Flaky("a", flaky={ids[2]}, concurrency=4))
    assert make(registry).run() == EXIT_OK
    stats = last_run_file(env)["stages"]["a"]
    assert stats["passes"] == 2  # all 5 pages, then the retry
    assert a.opened == 2 and a.closed == 2


def test_corpus_steps_after_inputs_done(env, registry):
    add_pages(3)
    log: list[str] = []

    class Logging(DummyPage):
        def handle(self, page):
            log.append(self.name)
            return super().handle(page)

    register(registry, Logging("a"), Logging("b", depends_on=("a",)))
    step = Counting("step", "a", lambda: page_stage_fingerprint("a"), log=log)
    assert make(registry, [step]).run() == EXIT_OK
    assert log == ["a", "a", "a", "step", "b", "b", "b"]
    assert stored_fingerprint("step") == page_stage_fingerprint("a")


def test_run_file_written(env, registry):
    add_pages(2)
    register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    assert make(registry, tag="full").run() == EXIT_OK
    run = last_run_file(env)
    for key in (
        "started_at",
        "finished_at",
        "stages",
        "archives_unpacked",
        "pages_registered",
        "status",
        "models_loaded",
        "failed_items",
    ):
        assert key in run
    assert run["tag"] == "full" and run["interrupted"] is False and run["failed_stage"] is None
    assert run["stages"]["a"]["processed"] == 2 and run["stages"]["b"]["processed"] == 2
    assert {"processed", "failed", "seconds"} <= set(run["stages"]["a"])
    assert run["status"]["a"]["done"] == 2 and run["status"]["b"]["eligible"] == 0
    assert run["models_loaded"] == []


def test_permanently_failed_items_listed(env, registry):
    ids = add_pages(2)
    register(registry, DummyPage("a", fail={ids[1]}))
    assert make(registry).run() == EXIT_OK  # item failures never stop the run
    run = last_run_file(env)
    assert run["failed_items"] == {"a": [ids[1]]}
    assert run["stages"]["a"]["failed"] == config.settings.max_attempts


def test_interrupt_writes_run_file_and_exits_130(env, registry):
    add_pages(4)
    stop = threading.Event()

    class Interrupting(DummyPage):
        def handle(self, page):
            stop.set()  # as the SIGINT handler would
            return super().handle(page)

    a, b = register(registry, Interrupting("a"), DummyPage("b", depends_on=("a",)))
    assert make(registry, stop=stop).run() == EXIT_INTERRUPTED
    run = last_run_file(env)
    assert run["interrupted"] is True and run["exit_code"] == EXIT_INTERRUPTED
    assert len(a.calls) == 1  # no new items claimed after the stop
    assert a.closed == 1 and b.opened == 0
    assert rows("a")[a.calls[0]]["status"] == "done"  # the item in flight finished


def test_interrupt_grace_marks_unfinished_items(env, registry, monkeypatch):
    add_pages(1)
    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"run_stop_grace_s": 0.3})
    )
    stop = threading.Event()
    release = threading.Event()

    class Slow(DummyPage):
        def handle(self, page):
            stop.set()
            release.wait(5)
            return super().handle(page)

    register(registry, Slow("a"))
    try:
        assert make(registry, stop=stop).run() == EXIT_INTERRUPTED
    finally:
        release.set()
    (row,) = rows("a").values()
    assert row["status"] == "failed" and row["error"] == "interrupted"


def test_signal_handler_sets_stop_event():
    import os
    import signal

    stop = threading.Event()
    with orch_mod.stop_on_signals(stop, lambda m: None):
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(50):
            if stop.is_set():
                break
            time.sleep(0.01)
    assert stop.is_set()


def test_requeue_stale_first(env, registry):
    (p1,) = add_pages(1)
    (a,) = register(registry, DummyPage("a"))
    with db.conn() as c:
        c.execute(
            "insert into stage_runs (page_id, stage, version, input_versions, status, attempt, "
            "started_at) values (%s, 'a', 'v1', '{}', 'running', 1, now() - interval '3 hours')",
            (p1,),
        )
    assert make(registry).run() == EXIT_OK
    assert rows("a")[p1]["status"] == "done" and a.calls == [p1]
    assert last_run_file(env)["requeued_stale"] == 1


def test_zero_eligible_stage_not_opened(env, registry):
    add_pages(2)
    a, b = register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    assert make(registry).run() == EXIT_OK
    assert a.opened == 1 and b.opened == 1
    started = time.monotonic()
    assert make(registry).run() == EXIT_OK
    assert time.monotonic() - started < 10
    assert a.opened == 1 and b.opened == 1  # nothing to do: no open(), no model
    run = last_run_file(env)
    assert run["stages"]["a"]["passes"] == 0 and run["models_loaded"] == []


def test_corpus_step_skipped_when_fingerprint_same(env, registry):
    add_pages(2)
    register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    step = Counting("step", "a", lambda: page_stage_fingerprint("a"))
    make(registry, [step]).run()
    make(registry, [step]).run()
    assert step.runs == 1
    registry["a"].version = "v2"  # new folio rows -> new fingerprint
    make(registry, [step]).run()
    assert step.runs == 2


def test_register_skipped_when_unchanged(env, registry, monkeypatch):
    from newsrag.ingest import register as register_mod

    _make_zip(env / "data" / "2024.zip", {_member(D1, 1): b"%PDF a", _member(D1, 2): b"%PDF b"})
    calls = []
    real = register_mod.register_all

    def spy(**kw):
        calls.append(kw)
        return real(**kw)

    monkeypatch.setattr(register_mod, "register_all", spy)
    o = make(registry)
    assert o.run() == EXIT_OK
    run = last_run_file(env)
    assert run["archives_unpacked"] == ["2024.zip"] and run["pages_registered"] == 2
    assert len(calls) == 1
    started = time.monotonic()
    o2 = make(registry)
    o2._ingest()
    assert time.monotonic() - started < 5
    assert len(calls) == 1 and o2.record["register"] == "skipped"
    assert o2.record["archives_unpacked"] == [] and o2.record["pages_registered"] == 0


def test_stage_unavailable_stops_run_exit_1(env, registry):
    add_pages(2)

    class Unavailable(DummyPage):
        def preflight(self):
            raise StageUnavailable("model weights missing")

    a, _, c = register(
        registry,
        DummyPage("a"),
        Unavailable("b", depends_on=("a",)),
        DummyPage("c", depends_on=("b",)),
    )
    assert make(registry).run() == EXIT_FAILED
    run = last_run_file(env)
    assert run["failed_stage"] == "b" and "model weights missing" in run["error"]
    assert len(a.calls) == 2 and c.calls == []


def test_open_failure_stops_run_exit_1(env, registry):
    add_pages(1)

    class NoModel(DummyPage):
        def open(self):
            raise RuntimeError("vLLM did not start")

    register(registry, NoModel("a"))
    assert make(registry).run() == EXIT_FAILED
    assert last_run_file(env)["failed_stage"] == "a"


def test_index_validate_failure_exits_1(env, registry):
    add_pages(1)
    register(registry, DummyPage("index"))
    seen = []
    o = make(registry, validate=lambda echo: seen.append(1) or False)
    assert o.run() == EXIT_FAILED
    assert seen == [1] and last_run_file(env)["index_validate"] == "failed"
    # filtered runs and --until another stage skip it
    assert make(registry, validate=lambda e: seen.append(2), dates=[D1]).run() == EXIT_OK
    assert seen == [1]


def test_only_and_until_semantics(env, registry):
    ids = add_pages(2)
    (a, *_) = register(
        registry,
        Flaky("a", flaky={ids[0]}),
        DummyPage("b", depends_on=("a",)),
        DummyPage("c", depends_on=("b",)),
    )
    step = Counting("step", "a", lambda: page_stage_fingerprint("a"))
    assert make(registry, [step], until="b").run() == EXIT_OK
    assert sorted(rows("b")) == ids and rows("c") == {}
    assert step.runs == 2  # after cycle 1, and after the retry of ids[0] changed `a`'s rows

    a.version = "v2"
    step2 = Counting("step2", "a", lambda: page_stage_fingerprint("a"))
    o = make(registry, [step2], only=["c", "a"])
    assert o.run() == EXIT_OK
    assert o.plan() == ["a", "c"]
    assert step2.runs == 0  # no corpus steps with --only
    assert o.record["cycles"] == 1  # no repeat
    assert {r["version"] for r in rows("a").values()} == {"v2"}
    assert rows("c") == {}  # b is not current at the new a version and was not run

    assert make(registry, until="nope").run() == 2
    assert make(registry, only=["nope"]).run() == 2


def test_dry_run_changes_nothing(env, registry):
    ids = add_pages(3)
    a, b = register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    make(registry, only=["a"], pages_file=None).run()
    before = (rows("a"), rows("b"))
    runs_before = sorted((env / "raw" / "reports" / "runs").glob("*.json"))
    o = make(registry, dry_run=True)
    assert o.run() == EXIT_OK
    assert o.out.getvalue().splitlines() == ["a  eligible=0", "b  eligible=3"]
    assert (rows("a"), rows("b")) == before
    assert sorted((env / "raw" / "reports" / "runs").glob("*.json")) == runs_before
    assert len(b.calls) == 0 and len(a.calls) == len(ids)


def test_filters_pages_file_and_dates(env, registry, tmp_path):
    d1 = add_pages(2, D1)
    d2 = add_pages(2, D2)
    (a,) = register(registry, DummyPage("a"))
    pages = tmp_path / "pages.txt"
    pages.write_text(f"# sample\n{d1[0]}\n{d2[0]}\n")
    make(registry, pages_file=pages, dates=[D2]).run()
    assert a.calls == [d2[0]]
    make(registry, dates=[D1]).run()
    assert sorted(a.calls) == sorted([d2[0], *d1])


# --- E-5 -------------------------------------------------------------------------------------


def test_incremental_archive(env, registry):
    register(registry, DummyPage("render"), DummyDate("dstage", depends_on=("render",)))
    _make_zip(env / "data" / "2024.zip", {_member(D1, 1): b"%PDF a", _member(D1, 2): b"%PDF b"})
    assert make(registry).run() == EXIT_OK
    old = rows("render")
    old_date = rows("dstage", "date_runs")
    assert len(old) == 2 and list(old_date) == [D1]

    # a later archive: one more page for D1 and one page of a new date D2
    _make_zip(env / "data" / "2025.zip", {_member(D1, 3): b"%PDF c", _member(D2, 1): b"%PDF d"})
    assert make(registry).run() == EXIT_OK
    run = last_run_file(env)
    assert run["archives_unpacked"] == ["2025.zip"] and run["pages_registered"] == 2
    new = rows("render")
    assert len(new) == 4
    assert run["stages"]["render"]["processed"] == 2  # exactly the N new pages
    for page_id, row in old.items():  # old pages untouched
        assert new[page_id]["finished_at"] == row["finished_at"]
    dates = rows("dstage", "date_runs")
    assert set(dates) == {D1, D2}
    assert dates[D1]["finished_at"] > old_date[D1]["finished_at"]  # D1 gained a page: reruns


# --- E-6 (FR-7) ------------------------------------------------------------------------------


def test_filtered_run_runs_changed_corpus_steps(env, registry):
    add_pages(2, D1)
    add_pages(1, D2)
    register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    step = Counting("step", "a", lambda: page_stage_fingerprint("a"))
    assert make(registry, [step], dates=[D1]).run() == EXIT_OK
    assert step.runs == 1  # filtered, but the input changed
    assert make(registry, [step], dates=[D1]).run() == EXIT_OK
    assert step.runs == 1  # unchanged now
    assert make(registry, [step], dates=[D2]).run() == EXIT_OK
    assert step.runs == 2


def test_stale_dates_reset_only(env, registry):
    add_pages(1, D1)
    add_pages(1, D2)
    register(registry, DummyPage("a"), DummyDate("chunk", depends_on=("a",)))
    first = Counting("step", "a", lambda: "fp-1")
    make(registry, [first]).run()
    done = rows("chunk", "date_runs")
    assert set(done) == {D1, D2}

    second = Counting("step", "a", lambda: "fp-2")
    assert make(registry, [second], stale=lambda: [D1]).run() == EXIT_OK
    after = rows("chunk", "date_runs")
    assert after[D1]["finished_at"] > done[D1]["finished_at"]  # reset and chunked again
    assert after[D2]["finished_at"] == done[D2]["finished_at"]  # left alone
    assert last_run_file(env)["chunk_dates_reset"] == 1


def test_versions_reevaluated(env, registry):
    add_pages(2)
    data = env / "lookup.txt"
    data.write_text("one")

    class Reading(DummyPage):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            del self.__dict__["version"]  # use the cached property below

        @functools.cached_property
        def version(self):  # type: ignore[override]
            return "v-" + data.read_text()

    register(registry, DummyPage("a"), Reading("b", depends_on=("a",)))
    make(registry).run()
    assert {r["version"] for r in rows("b").values()} == {"v-one"}

    # the corpus step after `a` rewrites the file `b`'s version hashes: `b` reruns this run
    step = Counting("step", "a", lambda: "changed", on_run=lambda: data.write_text("two"))
    assert make(registry, [step]).run() == EXIT_OK
    assert {r["version"] for r in rows("b").values()} == {"v-two"}


def test_overrides_in_fingerprint(env):
    overrides = config.settings.alias_overrides_file
    base = orch_mod._aliases_fingerprint()
    overrides.write_text("same: []\n")
    with_file = orch_mod._aliases_fingerprint()
    overrides.write_text("same: [[a, b]]\n")
    changed = orch_mod._aliases_fingerprint()
    assert len({base, with_file, changed}) == 3


def test_fingerprint_after_success(env, registry):
    add_pages(1)
    register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    broken = Counting("step", "a", lambda: "fp", fail=True)
    assert make(registry, [broken]).run() == EXIT_FAILED
    run = last_run_file(env)
    assert run["failed_stage"] == "step" and stored_fingerprint("step") is None
    assert rows("b") == {}  # the run stopped before the stages after the step

    # interrupted during the step: it finishes, the fingerprint is not stored
    stop = threading.Event()
    interrupted = Counting("step", "a", lambda: "fp", on_run=stop.set)
    assert make(registry, [interrupted], stop=stop).run() == EXIT_INTERRUPTED
    assert interrupted.runs == 1 and stored_fingerprint("step") is None

    fixed = Counting("step", "a", lambda: "fp")
    assert make(registry, [fixed]).run() == EXIT_OK
    assert fixed.runs == 1 and stored_fingerprint("step") == "fp"


def test_editions_step_reports_changed_yaml(env, monkeypatch):
    from newsrag.editions import store

    path = config.settings.editions_file

    def build():
        path.write_text("2024: {}\n")
        return path, {}

    monkeypatch.setattr(store, "build", build)
    monkeypatch.setattr(store, "apply", lambda: 0)
    lines: list[str] = []
    detail = orch_mod._editions_run(lines.append)
    assert detail["editions_yaml_changed"] is True
    assert any(line.endswith("changed: commit it") for line in lines)
    lines.clear()
    assert orch_mod._editions_run(lines.append)["editions_yaml_changed"] is False
    assert not any("commit it" in line for line in lines)


def test_lock_holders_recorded(models_env):
    from newsrag.models import gpu_lock, lock_holders

    with gpu_lock("unit-test-holder"):
        pass
    assert "unit-test-holder" in lock_holders()


def test_cli_registered():
    from typer.testing import CliRunner

    from newsrag.cli import app

    result = CliRunner().invoke(app, ["run", "--help"])
    assert result.exit_code == 0 and "--dry-run" in result.output


# --- ticket 031 ------------------------------------------------------------------------------


def test_run_file_keys(env, registry):
    """Run files carry `start`, `end` (ISO 8601, +00:00) and `tag`."""
    from datetime import datetime

    add_pages(1)
    register(registry, DummyPage("a"))
    assert make(registry, tag="full").run() == EXIT_OK
    run = last_run_file(env)
    assert run["tag"] == "full"
    for key in ("start", "end"):
        assert run[key].endswith("+00:00")
        datetime.fromisoformat(run[key])
    assert run["end"] >= run["start"]


class FailingDate(DummyDate):
    def __init__(self, name, fail_dates, **kw):
        super().__init__(name, **kw)
        self.fail_dates = set(fail_dates)

    def handle(self, pub_date, pages):
        if pub_date in self.fail_dates:
            raise RuntimeError(f"date {pub_date} broke\nsecond line")
        return super().handle(pub_date, pages)


def test_quarantine_failed_date_stage(env, registry):
    """`stage quarantine-failed <date stage>` quarantines every page of a date that failed
    max_attempts times; a rerun then leaves nothing eligible."""
    from newsrag.stages.runner import list_eligible, quarantine_failed

    bad = add_pages(2, D1)
    good = add_pages(1, D2)
    register(registry, DummyPage("a"), FailingDate("d", {D1}, depends_on=("a",)))
    assert make(registry, tag="full").run() == EXIT_OK
    assert rows("d", "date_runs")[D1]["status"] == "failed"
    assert sorted(quarantine_failed(registry["d"])) == sorted(bad)
    with db.conn() as c:
        reasons = dict(c.execute("select page_id, quarantine_reason from pages").fetchall())
    assert reasons[bad[0]] == f"stage_failed:d: RuntimeError: date {D1} broke"
    assert reasons[good[0]] is None
    assert quarantine_failed(registry["d"]) == []  # already quarantined
    assert make(registry, tag="full").run() == EXIT_OK
    assert list_eligible(registry["d"], registry=registry)[0] == []


def test_quarantine_failed_page_stage(env, registry):
    from newsrag.stages.runner import quarantine_failed

    ids = add_pages(2)
    register(registry, DummyPage("a", fail={ids[1]}))
    assert make(registry).run() == EXIT_OK
    assert quarantine_failed(registry["a"]) == [ids[1]]
    with db.conn() as c:
        reason = c.execute(
            "select quarantine_reason from pages where page_id = %s", (ids[1],)
        ).fetchone()[0]
    assert reason.startswith("stage_failed:a: ")


def test_corpus_from_date_hides_older_pages(env, registry, monkeypatch):
    """Pages printed before `corpus_from_date` are not run, counted or listed."""
    from newsrag.stages.runner import stage_counts

    old = add_pages(1, date(2025, 6, 1))
    new = add_pages(1, D1)
    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"corpus_from_date": D1})
    )
    a, d = register(registry, DummyPage("a"), DummyDate("d", depends_on=("a",)))
    assert make(registry, tag="full").run() == EXIT_OK
    assert a.calls == new and old[0] not in rows("a")
    assert [day for day, _ in d.calls] == [D1]
    assert stage_counts(a) == {"done": 1, "failed": 0, "running": 0, "eligible": 0}
