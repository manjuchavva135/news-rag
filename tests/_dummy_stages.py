"""Dummy stages for the runner tests. Also a script: two copies of it run in parallel in
`test_concurrent_two_processes_never_double_process` (NEWSRAG_LEDGER_SCHEMA picks the schema)."""

from __future__ import annotations

import sys
import threading
import time
from datetime import date

from newsrag.ledger import db
from newsrag.stages.base import DateStage, PageRow, PageStage, StageResult


class DummyPage(PageStage):
    def __init__(
        self,
        name: str,
        version: str = "v1",
        depends_on: tuple[str, ...] = (),
        fail: set[str] | frozenset[str] = frozenset(),
        sleep: float = 0.0,
        concurrency: int = 1,
        per_item: dict[str, str] | None = None,
        block: threading.Event | None = None,
    ):
        self.name = name
        self.version = version
        self.depends_on = depends_on
        self.fail = set(fail)
        self.sleep = sleep
        self.concurrency = concurrency
        self.per_item = per_item or {}
        self.block = block
        self.calls: list[str] = []
        self.opened = 0
        self.closed = 0
        self.final_failures: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def version_for(self, item: PageRow) -> str:
        return self.per_item.get(item.page_id, self.version)

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1

    def on_final_failure(self, item: PageRow, error: str) -> None:
        self.final_failures.append((item.page_id, error))

    def handle(self, page: PageRow) -> StageResult:
        with self.lock:
            self.calls.append(page.page_id)
        if self.block is not None:
            self.block.wait(30)
        if self.sleep:
            time.sleep(self.sleep)
        if page.page_id in self.fail:
            raise ValueError("boom")
        return StageResult(artifact_path=f"/tmp/{self.name}/{page.page_id}.json", detail={"n": 1})


class DummyDate(DateStage):
    def __init__(self, name: str, version: str = "v1", depends_on: tuple[str, ...] = ()):
        self.name = name
        self.version = version
        self.depends_on = depends_on
        self.calls: list[tuple[date, list[str]]] = []

    def handle(self, pub_date: date, pages: list[PageRow]) -> StageResult:
        self.calls.append((pub_date, [p.page_id for p in pages]))
        return StageResult(detail={"pages": len(pages)})


def add_pages(n: int, pub_date: date = date(2026, 4, 1), prefix: str | None = None) -> list[str]:
    """Register n dummy pages for one date; returns their page_ids."""
    prefix = prefix or f"{pub_date.isoformat()}_FH_main"
    ids = []
    for i in range(1, n + 1):
        page_id = f"{prefix}_{i:02d}"
        db.upsert_page(
            page_id=page_id,
            pub_date=pub_date,
            edition_code="FH",
            section="main",
            page_num=i,
            source_path=f"/src/{page_id}.pdf",
            source_archive="test.rar",
        )
        ids.append(page_id)
    return ids


def rows(stage: str, table: str = "stage_runs") -> dict:
    key = "pub_date" if table == "date_runs" else "page_id"
    with db.conn() as c:
        found = db.fetch_all(c, f"select * from {table} where stage = %s", (stage,))
    return {r[key]: r for r in found}


def _main() -> None:
    """argv: calls_file. Runs stage `dummy` with 2 threads, appending each handled page_id."""
    from newsrag.stages.runner import RunFilters, run_stage

    calls_file = sys.argv[1]

    class Recording(DummyPage):
        def handle(self, page: PageRow) -> StageResult:
            with open(calls_file, "a", encoding="utf-8") as fh:
                fh.write(page.page_id + "\n")
            time.sleep(0.01)
            return StageResult()

    stage = Recording("dummy", concurrency=2)
    result = run_stage(stage, RunFilters(), registry={"dummy": stage})
    print(f"processed={result.processed} failed={result.failed}")


if __name__ == "__main__":
    _main()
