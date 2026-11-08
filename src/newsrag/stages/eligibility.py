"""Eligibility rules (ticket 003 FR-4), as pure functions over rows loaded from the ledger.

A stage row ("run row") is a dict with at least ``status``, ``version``, ``input_versions``,
``attempt`` and ``finished_at``.

- A stage is **current** for an item when its row is ``done`` at the stage's current
  ``version_for(item)``, its recorded ``input_versions`` equal the inputs it would record now, and
  every dependency is current too. Only a current dependency satisfies a stage.
- A stage is **eligible** for an item when the item is not quarantined, every dependency is
  current, and its own row is absent, ``failed`` with attempts left, or ``failed``/``done`` at a
  different version or with different inputs. ``running`` rows are never eligible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from newsrag.stages.base import DateStage, PageRow, PageStage, Stage

Row = Mapping[str, Any]
FINGERPRINT_KEY = "pages_fingerprint"


def ts(value: datetime | None) -> str | None:
    """Canonical text form of a timestamp, as stored in ``input_versions``."""
    return value.astimezone(UTC).isoformat() if value is not None else None


def ancestors(name: str, registry: Mapping[str, Stage]) -> list[str]:
    """Every stage `name` depends on, directly or not (dependencies first, `name` excluded)."""
    order: list[str] = []
    visiting: set[str] = set()

    def visit(current: str, chain: tuple[str, ...]) -> None:
        if current in order:
            return
        if current in visiting:
            raise ValueError(f"dependency cycle: {' -> '.join((*chain, current))}")
        if current not in registry:
            raise KeyError(f"stage {chain[-1]} depends on unknown stage {current}")
        visiting.add(current)
        for dep in registry[current].depends_on:
            visit(dep, (*chain, current))
        visiting.discard(current)
        order.append(current)

    if name not in registry:
        raise KeyError(f"unknown stage {name}")
    for dep in registry[name].depends_on:
        visit(dep, (name,))
    return order


def validate(stage: Stage, registry: Mapping[str, Stage]) -> list[str]:
    """Check the dependency graph of `stage`; return its ancestors."""
    found = ancestors(stage.name, registry)
    if isinstance(stage, PageStage):
        for dep in found:
            if not isinstance(registry[dep], PageStage):
                raise TypeError(
                    f"page stage {stage.name} cannot depend on date stage {dep} "
                    "(directly or through another stage)"
                )
    return found


def row_wants_run(row: Row | None, version: str, inputs: Any, max_attempts: int) -> bool:
    """The part of eligibility that looks only at the stage's own row."""
    if row is None:
        return True
    status = row["status"]
    if status == "running":
        return False
    changed = row["version"] != version or row["input_versions"] != inputs
    if status == "failed":
        return changed or row["attempt"] < max_attempts
    return changed  # done


def next_attempt(prev: Row | None, version: str, inputs: Any) -> int:
    """Attempt number for a new claim: reset to 1 when the version or the inputs changed."""
    if prev is None or prev["version"] != version or prev["input_versions"] != inputs:
        return 1
    return int(prev["attempt"]) + 1


class PageContext:
    """Eligibility of page stages for one page, given all its stage rows."""

    def __init__(self, registry: Mapping[str, Stage], page: PageRow, rows: Mapping[str, Row]):
        self.registry = registry
        self.page = page
        self.rows = rows
        self._current: dict[str, bool] = {}

    def inputs(self, stage: Stage) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for dep in stage.depends_on:
            row = self.rows.get(dep)
            out[dep] = [row["version"], ts(row["finished_at"])] if row is not None else None
        return out

    def current(self, name: str) -> bool:
        if name not in self._current:
            self._current[name] = False  # guards against cycles while computing
            stage = self.registry[name]
            row = self.rows.get(name)
            self._current[name] = (
                row is not None
                and row["status"] == "done"
                and row["version"] == stage.version_for(self.page)
                and row["input_versions"] == self.inputs(stage)
                and all(self.current(dep) for dep in stage.depends_on)
            )
        return self._current[name]

    def eligible(self, stage: Stage, max_attempts: int, force: bool = False) -> bool:
        if self.page.quarantine_reason:
            return False
        if not all(self.current(dep) for dep in stage.depends_on):
            return False
        if force:
            return True
        return row_wants_run(
            self.rows.get(stage.name),
            stage.version_for(self.page),
            self.inputs(stage),
            max_attempts,
        )


class DateContext:
    """Eligibility of date stages for one print date."""

    def __init__(
        self,
        registry: Mapping[str, Stage],
        pub_date: date,
        pages: list[PageRow],
        page_rows: Mapping[str, Mapping[str, Row]],
        date_rows: Mapping[str, Row],
    ):
        self.registry = registry
        self.pub_date = pub_date
        self.active = sorted((p for p in pages if not p.quarantine_reason), key=lambda p: p.page_id)
        self.page_rows = page_rows
        self.date_rows = date_rows
        self._pages = {
            p.page_id: PageContext(registry, p, page_rows.get(p.page_id, {})) for p in self.active
        }
        self._current: dict[str, bool] = {}

    def _page_deps(self, stage: Stage) -> list[str]:
        return [d for d in stage.depends_on if isinstance(self.registry[d], PageStage)]

    def fingerprint(self, stage: Stage) -> str:
        entries: list[list[Any]] = []
        page_deps = self._page_deps(stage)
        for page in self.active:
            entries.append(["page", page.page_id, page.revision])
            rows = self.page_rows.get(page.page_id, {})
            for dep in page_deps:
                row = rows.get(dep)
                entries.append(
                    [page.page_id, dep, row and row["version"], row and ts(row["finished_at"])]
                )
        payload = json.dumps(sorted(entries, key=json.dumps), separators=(",", ":"))
        return hashlib.sha1(payload.encode()).hexdigest()

    def inputs(self, stage: Stage) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for dep in stage.depends_on:
            dep_stage = self.registry[dep]
            if isinstance(dep_stage, DateStage):
                row = self.date_rows.get(dep)
                out[dep] = [row["version"], ts(row["finished_at"])] if row is not None else None
            else:
                out[dep] = dep_stage.version
        out[FINGERPRINT_KEY] = self.fingerprint(stage)
        return out

    def dep_satisfied(self, dep: str) -> bool:
        if isinstance(self.registry[dep], DateStage):
            return self.current(dep)
        return bool(self.active) and all(ctx.current(dep) for ctx in self._pages.values())

    def current(self, name: str) -> bool:
        if name not in self._current:
            self._current[name] = False
            stage = self.registry[name]
            row = self.date_rows.get(name)
            self._current[name] = (
                row is not None
                and row["status"] == "done"
                and row["version"] == stage.version_for(self.pub_date)
                and row["input_versions"] == self.inputs(stage)
                and all(self.dep_satisfied(dep) for dep in stage.depends_on)
            )
        return self._current[name]

    def eligible(self, stage: Stage, max_attempts: int, force: bool = False) -> bool:
        if not self.active:
            return False
        if not all(self.dep_satisfied(dep) for dep in stage.depends_on):
            return False
        if force:
            return True
        return row_wants_run(
            self.date_rows.get(stage.name),
            stage.version_for(self.pub_date),
            self.inputs(stage),
            max_attempts,
        )
