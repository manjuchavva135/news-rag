"""Stage runner tests (ticket 003). Every test uses a throw-away ledger schema."""

import io
import os
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from _dummy_stages import DummyDate, DummyPage, add_pages, rows
from typer.testing import CliRunner

from newsrag.cli import app
from newsrag.config import settings
from newsrag.ledger import db
from newsrag.stages import runner as runner_mod
from newsrag.stages.base import StageUnavailable
from newsrag.stages.runner import RunFilters, list_eligible, run_stage

pytestmark = pytest.mark.stores

D1 = date(2026, 4, 1)
D2 = date(2026, 4, 2)
cli = CliRunner()


def run(stage, **filters):
    return run_stage(stage, RunFilters(**filters), out=io.StringIO())


def eligible(stage, **filters):
    return list_eligible(stage, RunFilters(**filters))[0]


def register(registry, *stages):
    for stage in stages:
        registry[stage.name] = stage
    return stages


def test_eligibility_requires_dependencies_done(ledger, registry):
    ids = add_pages(3)
    a, b = register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    assert eligible(b) == []
    assert run(b).processed == 0
    assert run(a, page_ids=[ids[0]]).processed == 1
    assert eligible(b) == [ids[0]]
    assert run(a).processed == 2
    assert run(b).processed == 3
    assert sorted(b.calls) == ids
    assert eligible(a) == [] and eligible(b) == []


def test_version_change_makes_stage_and_downstream_eligible(ledger, registry):
    ids = add_pages(2)
    a, b = register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    run(a)
    run(b)
    a.version = "v2"
    assert eligible(a) == ids
    assert eligible(b) == []  # its dependency is not current until a reruns
    run(a)
    assert {r["version"] for r in rows("a").values()} == {"v2"}
    assert eligible(b) == ids
    assert run(b).processed == 2
    assert eligible(b) == []


def test_failure_recorded_and_retried_until_max_attempts(ledger, registry):
    (p1,) = add_pages(1)
    (a,) = register(registry, DummyPage("a", fail={p1}))
    for attempt in range(1, settings.max_attempts + 1):
        result = run(a)
        assert result.failed == 1 and result.failed_items == [p1]
        row = rows("a")[p1]
        assert row["status"] == "failed" and row["attempt"] == attempt
        assert row["error"] == "ValueError: boom" and row["finished_at"] is not None
    assert eligible(a) == []
    assert run(a).failed == 0
    assert len(a.calls) == settings.max_attempts


def test_exception_does_not_stop_run(ledger, registry):
    ids = add_pages(5)
    (a,) = register(registry, DummyPage("a", fail={ids[1]}))
    result = run(a)
    assert (result.processed, result.failed) == (4, 1)
    statuses = {pid: r["status"] for pid, r in rows("a").items()}
    assert statuses[ids[1]] == "failed"
    assert [s for pid, s in statuses.items() if pid != ids[1]] == ["done"] * 4
    done = rows("a")[ids[0]]
    assert done["artifact_path"] == f"/tmp/a/{ids[0]}.json" and done["detail"] == {"n": 1}


def test_pages_file_and_page_filters(ledger, registry, tmp_path):
    ids = add_pages(4)
    (a,) = register(registry, DummyPage("a"))
    pages_file = tmp_path / "pages.txt"
    pages_file.write_text(f"# chosen pages\n{ids[0]}\n\n{ids[2]}\n")
    result = cli.invoke(app, ["stage", "run", "a", "--pages-file", str(pages_file)])
    assert result.exit_code == 0, result.output
    assert "a: done=2 failed=0 skipped=0" in result.output
    assert sorted(a.calls) == [ids[0], ids[2]]

    result = cli.invoke(app, ["stage", "run", "a", "--page", ids[1], "--page", ids[0]])
    assert "a: done=1 failed=0 skipped=1" in result.output  # ids[0] is already done
    assert sorted(rows("a")) == sorted(ids[:3])


def test_date_stage_waits_for_all_pages_of_date(ledger, registry):
    ids = add_pages(2, D1)
    a, d = register(registry, DummyPage("a"), DummyDate("d", depends_on=("a",)))
    run(a, page_ids=[ids[0]])
    assert eligible(d) == []
    run(a)
    assert eligible(d) == [D1.isoformat()]
    assert run(d).processed == 1
    assert d.calls == [(D1, ids)]
    row = rows("d", "date_runs")[D1]
    assert row["status"] == "done" and row["input_versions"]["a"] == "v1"
    assert "pages_fingerprint" in row["input_versions"]
    assert eligible(d) == []


def test_date_stage_depending_on_date_stage(ledger, registry):
    add_pages(1, D1)
    add_pages(1, D2)
    d1, d2 = register(registry, DummyDate("d1"), DummyDate("d2", depends_on=("d1",)))
    assert eligible(d2) == []
    run(d1, pub_date=D1)
    assert eligible(d2) == [D1.isoformat()]
    run(d1)
    run(d2)
    assert eligible(d2) == []
    run(d1, pub_date=D2, force=True)  # upstream date stage reran
    assert eligible(d2) == [D2.isoformat()]


def test_new_page_reruns_date_stage(ledger, registry):
    add_pages(2, D1)
    a, d = register(registry, DummyPage("a"), DummyDate("d", depends_on=("a",)))
    run(a)
    run(d)
    assert eligible(d) == []
    db.upsert_page(
        page_id="2026-04-01_FH_extra_01",
        pub_date=D1,
        edition_code="FH",
        section="extra",
        page_num=1,
        source_path="/src/extra.pdf",
        source_archive="test.rar",
    )
    assert eligible(d) == []  # the new page has not been through `a` yet
    run(a)
    assert eligible(d) == [D1.isoformat()]
    run(d)
    assert len(d.calls[-1][1]) == 3


def test_running_row_never_eligible(ledger, registry):
    ids = add_pages(2)
    (a,) = register(registry, DummyPage("a"))
    with db.conn() as c:
        c.execute(
            "insert into stage_runs (page_id, stage, version, status, attempt, started_at) "
            "values (%s, 'a', 'v0', 'running', 1, now())",
            (ids[0],),
        )
    assert eligible(a) == [ids[1]]
    a.version = "v9"  # even a different version does not free a running row
    assert eligible(a) == [ids[1]]


def test_attempt_reset_on_version_change(ledger, registry):
    (p1,) = add_pages(1)
    (a,) = register(registry, DummyPage("a", fail={p1}))
    run(a)
    run(a)
    assert rows("a")[p1]["attempt"] == 2
    a.version = "v2"
    run(a)
    row = rows("a")[p1]
    assert row["version"] == "v2" and row["attempt"] == 1


def test_dependency_must_be_at_current_version(ledger, registry):
    ids = add_pages(2)
    a, b = register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    run(a)
    a.version = "v2"  # a's rows are done but at v1
    assert eligible(b) == []
    run(a, page_ids=[ids[0]])
    assert eligible(b) == [ids[0]]


def test_version_for_per_item(ledger, registry):
    ids = add_pages(3)
    (a,) = register(registry, DummyPage("a", per_item={ids[1]: "special-v1"}))
    run(a)
    versions = {pid: r["version"] for pid, r in rows("a").items()}
    assert versions == {ids[0]: "v1", ids[1]: "special-v1", ids[2]: "v1"}
    assert eligible(a) == []
    a.per_item[ids[1]] = "special-v2"
    assert eligible(a) == [ids[1]]


def test_hooks_called_once_and_close_on_error(ledger, registry):
    ids = add_pages(6)
    (a,) = register(registry, DummyPage("a", fail={ids[0], ids[3]}, concurrency=3, sleep=0.02))
    result = run(a)
    assert (result.processed, result.failed) == (4, 2)
    assert (a.opened, a.closed) == (1, 1)

    (b,) = register(registry, DummyPage("b", depends_on=("missing_dep",)))
    with pytest.raises(KeyError):
        run(b)
    (c,) = register(registry, DummyPage("c"))
    run(c)
    run(c)  # nothing left to do: open is not called, so neither is close
    assert (c.opened, c.closed) == (1, 1)


def test_preflight_unavailable_exits_2_without_db(registry, monkeypatch):
    class Unavailable(DummyPage):
        def preflight(self):
            raise StageUnavailable("model weights not downloaded")

    register(registry, Unavailable("a"))

    def no_db():
        raise AssertionError("preflight must run before any DB access")

    monkeypatch.setattr(db, "conn", no_db)
    result = cli.invoke(app, ["stage", "run", "a"])
    assert result.exit_code == 2
    assert "model weights not downloaded" in result.output


def test_on_final_failure_called_at_max_attempts(ledger, registry, monkeypatch):
    monkeypatch.setattr(runner_mod, "settings", settings.model_copy(update={"max_attempts": 2}))
    ids = add_pages(2)
    (a,) = register(registry, DummyPage("a", fail={ids[0]}))
    run(a)
    assert a.final_failures == []
    run(a)
    assert a.final_failures == [(ids[0], "ValueError: boom")]
    run(a)
    assert len(a.final_failures) == 1


def test_force_and_skipped_count(ledger, registry):
    ids = add_pages(3, D1)
    add_pages(2, D2)
    (a,) = register(registry, DummyPage("a"))
    run(a)
    result = run(a, page_ids=[ids[0]])
    assert (result.processed, result.skipped) == (0, 1)
    result = run(a, page_ids=[ids[0]], force=True)
    assert (result.processed, result.skipped) == (1, 0)
    assert rows("a")[ids[0]]["attempt"] == 2  # same version and inputs: attempt counts on
    result = run(a, pub_date=D1)
    assert (result.processed, result.skipped) == (0, 3)
    result = run(a, pub_date=D2, force=True)
    assert result.processed == 2
    assert run(a).skipped == 0  # no filter: nothing is counted as skipped


def test_all_quarantined_date_not_eligible(ledger, registry):
    d1_ids = add_pages(2, D1)
    add_pages(1, D2)
    a, d = register(registry, DummyPage("a"), DummyDate("d"))
    for page_id in d1_ids:
        db.quarantine_page(page_id, "broken pdf")
    assert eligible(d) == [D2.isoformat()]
    assert eligible(a) == ["2026-04-02_FH_main_01"]
    run(d)
    assert [call[0] for call in d.calls] == [D2]


def test_multiple_stage_names_run_in_order(ledger, registry):
    add_pages(2)
    register(registry, DummyPage("a"), DummyPage("b", depends_on=("a",)))
    result = cli.invoke(app, ["stage", "run", "a", "b"])
    assert result.exit_code == 0, result.output
    summary = [line for line in result.output.splitlines() if ": done=" in line]
    assert summary[0].startswith("a: done=2 failed=0 skipped=0")
    assert summary[1].startswith("b: done=2 failed=0 skipped=0")


def test_change_propagates_three_stages(ledger, registry):
    ids = add_pages(2)
    a, b, c = register(
        registry,
        DummyPage("a"),
        DummyPage("b", depends_on=("a",)),
        DummyPage("c", depends_on=("b",)),
    )
    for stage in (a, b, c):
        run(stage)
    assert [eligible(s) for s in (a, b, c)] == [[], [], []]
    run(a, page_ids=[ids[0]], force=True)  # same version, new finished_at
    assert eligible(b) == [ids[0]]
    assert eligible(c) == []  # b is no longer current for that page
    run(b)  # b's version is unchanged, but its inputs are new
    assert eligible(c) == [ids[0]]
    run(c)
    assert [eligible(s) for s in (a, b, c)] == [[], [], []]


def test_open_failure_restores_row_and_exits_2(ledger, registry):
    ids = add_pages(2)

    class BadOpen(DummyPage):
        def open(self):
            self.opened += 1
            raise RuntimeError("GPU busy")

    (a,) = register(registry, DummyPage("a", fail={ids[0]}))
    run(a, page_ids=[ids[0]])  # ids[0]: failed, attempt 1; ids[1]: no row
    before = rows("a")[ids[0]]

    bad = BadOpen("a", concurrency=2)
    registry["a"] = bad
    with pytest.raises(StageUnavailable, match="GPU busy"):
        run(bad)
    after = rows("a")
    assert ids[1] not in after
    for col in ("status", "attempt", "error", "version", "started_at", "finished_at"):
        assert after[ids[0]][col] == before[col]
    assert bad.calls == [] and bad.closed == 1

    result = cli.invoke(app, ["stage", "run", "a"])
    assert result.exit_code == 2 and "GPU busy" in result.output


def test_stop_event_grace_marks_interrupted(ledger, registry):
    ids = add_pages(3)
    gate = threading.Event()
    (a,) = register(registry, DummyPage("a", block=gate))
    stop = threading.Event()
    box = {}

    def target():
        box["result"] = run_stage(a, RunFilters(), stop_event=stop, stop_grace_s=0.3)

    thread = threading.Thread(target=target)
    thread.start()
    deadline = time.monotonic() + 10
    while not a.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    stop.set()
    thread.join(10)
    gate.set()
    result = box["result"]
    assert (result.processed, result.failed) == (0, 1)
    found = rows("a")
    assert list(found) == [ids[0]]  # no new items claimed after the stop
    assert found[ids[0]]["status"] == "failed"
    assert found[ids[0]]["error"] == "interrupted" and found[ids[0]]["attempt"] == 1
    time.sleep(0.1)  # the abandoned handle finishing late must not overwrite the row
    assert rows("a")[ids[0]]["status"] == "failed"


def test_concurrent_two_processes_never_double_process(ledger, tmp_path):
    ids = add_pages(50)
    calls_file = tmp_path / "calls.txt"
    script = Path(__file__).parent / "_dummy_stages.py"
    env = {**os.environ, "NEWSRAG_LEDGER_SCHEMA": ledger}
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(calls_file)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = [p.communicate(timeout=120)[0] for p in procs]
    assert [p.returncode for p in procs] == [0, 0], outputs
    calls = calls_file.read_text().split()
    assert len(calls) == 50 and sorted(set(calls)) == ids
    found = rows("dummy")
    assert len(found) == 50
    assert {r["status"] for r in found.values()} == {"done"}
    assert {r["attempt"] for r in found.values()} == {1}
    counts = [int(out.split("processed=")[1].split()[0]) for out in outputs]
    print("per-process processed:", counts)
    assert sum(counts) == 50 and all(n > 0 for n in counts)  # both really worked


def test_status_output(ledger, registry):
    ids = add_pages(3)
    register(registry, DummyPage("dummy", fail={ids[2]}))
    cli.invoke(app, ["stage", "run", "dummy"])
    result = cli.invoke(app, ["stage", "status"])
    assert result.exit_code == 0
    import re

    assert re.search(r"dummy\s+done=2 failed=1 running=0 eligible=1", result.output)
    result = cli.invoke(app, ["stage", "failures", "dummy"])
    lines = result.output.splitlines()
    assert len(lines) == 1 and lines[0].endswith("boom")
    assert lines[0].split("\t")[:2] == [ids[2], "1"]


def test_requeue_stale(ledger, registry):
    ids = add_pages(2)
    register(registry, DummyPage("a"))
    with db.conn() as c:
        for page_id, age in ((ids[0], timedelta(hours=3)), (ids[1], timedelta(minutes=10))):
            c.execute(
                "insert into stage_runs (page_id, stage, version, status, attempt, started_at) "
                "values (%s, 'a', 'v1', 'running', 1, now() - %s)",
                (page_id, age),
            )
    result = cli.invoke(app, ["stage", "requeue-stale", "--older-than", "2h"])
    assert result.exit_code == 0 and "requeued 1" in result.output
    found = rows("a")
    assert found[ids[0]]["status"] == "failed"
    assert found[ids[0]]["error"] == "stale: requeued after 2h"
    assert found[ids[0]]["attempt"] == 1
    assert found[ids[1]]["status"] == "running"


def test_non_tty_output(ledger, registry):
    ids = add_pages(2)
    (a,) = register(registry, DummyPage("a", fail={ids[1]}))
    out = io.StringIO()
    run_stage(a, RunFilters(), out=out)
    text = out.getvalue()
    assert f"done {ids[0]}" in text.splitlines()
    assert f"FAILED {ids[1]}: ValueError: boom" in text.splitlines()
    assert "\x1b" not in text and "\r" not in text


def test_reset_deletes_rows(ledger, registry):
    ids = add_pages(2, D1)
    add_pages(1, D2)
    (a,) = register(registry, DummyPage("a"))
    run(a)
    result = cli.invoke(app, ["stage", "reset", "a", "--page", ids[0]])
    assert result.exit_code == 0 and "deleted 1 rows" in result.output
    assert cli.invoke(app, ["stage", "reset", "a", "--date", "2026-04-02"]).exit_code == 0
    assert sorted(rows("a")) == [ids[1]]
    assert cli.invoke(app, ["stage", "reset", "a"]).exit_code != 0  # needs exactly one choice
    cli.invoke(app, ["stage", "reset", "a", "--all"])
    assert rows("a") == {}
