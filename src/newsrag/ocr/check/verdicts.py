"""The reader's verdicts and sign-off (ticket 009 FR-2, FR-3, FR-4).

``<ocr_check_dir>/verdicts.json``::

    {"pages": {<page_id>: {"verdict", "problems": [...], "note", "updated_at"}},
     "signoff": {"name", "failing_page_types": [] | [{years, sections, page_nums,
                 edition_codes, note}], "signed_at"} | null}

Every change is written at once (atomically). Changing a page's verdict, problems or note after
sign-off clears the sign-off (D-4): it must match the verdicts it covers.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from newsrag.stages.base import write_json_atomic

VERDICTS = ("good", "acceptable", "bad")
PROBLEMS = (
    "headlines",
    "body text",
    "numbers",
    "names",
    "reading order",
    "tables",
    "old scan quality",
    "other",
)
# A failing-page-type row has the shape of a ticket-010 `ocr_fallback.yaml` rule.
ROW_FIELDS = ("years", "sections", "page_nums", "edition_codes")
INT_FIELDS = ("years", "page_nums")

MSG_NAME = "enter your name"
MSG_BOTH = "choose either no page types or rows"
MSG_NEITHER = "tick the box or add a row"
MSG_NARROW = "a row must narrow the pages"


class FormError(ValueError):
    """A form post is rejected; the message is shown to the reader."""


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def empty() -> dict[str, Any]:
    return {"pages": {}, "signoff": None}


def load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty()
    data.setdefault("pages", {})
    data.setdefault("signoff", None)
    return data


def _split(text: str) -> list[str]:
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def row_to_json(row: Mapping[str, str]) -> dict[str, Any] | None:
    """One sign-off form row as stored JSON; None for a row left completely blank.

    `years` / `page_nums` become int lists, `sections` / `edition_codes` string lists, and a
    blank field (the note too) is left out.
    """
    if not any(str(row.get(k) or "").strip() for k in (*ROW_FIELDS, "note")):
        return None
    out: dict[str, Any] = {}
    for field in ROW_FIELDS:
        values = _split(row.get(field, ""))
        if not values:
            continue
        if field in INT_FIELDS:
            try:
                out[field] = [int(v) for v in values]
            except ValueError:
                raise FormError(f"{field}: whole numbers only, comma-separated") from None
        else:
            out[field] = values
    if not out:
        raise FormError(MSG_NARROW)
    note = str(row.get("note") or "").strip()
    if note:
        out["note"] = note
    return out


def missing_verdicts(state: Mapping[str, Any], page_ids: Sequence[str]) -> list[str]:
    pages = state.get("pages") or {}
    return [p for p in page_ids if (pages.get(p) or {}).get("verdict") not in VERDICTS]


class Store:
    """verdicts.json for one list of pages. Safe to use from the server's threads."""

    def __init__(self, path: Path, page_ids: Sequence[str]) -> None:
        self.path = Path(path)
        self.page_ids = list(page_ids)
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            return load(self.path)

    def set_verdict(
        self, page_id: str, verdict: str, problems: Sequence[str], note: str
    ) -> dict[str, Any]:
        if page_id not in self.page_ids:
            raise FormError(f"{page_id} is not one of the check pages")
        if verdict not in VERDICTS:
            raise FormError(f"choose a verdict: {' / '.join(VERDICTS)}")
        unknown = [p for p in problems if p not in PROBLEMS]
        if unknown:
            raise FormError(f"unknown problem type: {', '.join(unknown)}")
        entry = {
            "verdict": verdict,
            "problems": [p for p in PROBLEMS if p in set(problems)],
            "note": str(note or "").strip(),
        }
        with self._lock:
            state = load(self.path)
            old = state["pages"].get(page_id) or {}
            changed = any(old.get(k) != v for k, v in entry.items())
            if changed or not old:
                state["pages"][page_id] = {**entry, "updated_at": now()}
                if changed:
                    state["signoff"] = None
                write_json_atomic(self.path, state)
            return state

    def sign_off(
        self, name: str, no_page_types: bool, rows: Sequence[Mapping[str, str]]
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise FormError(MSG_NAME)
        given = [r for r in rows if any(str(v or "").strip() for v in r.values())]
        if no_page_types and given:
            raise FormError(MSG_BOTH)
        if not no_page_types and not given:
            raise FormError(MSG_NEITHER)
        failing = [j for j in (row_to_json(r) for r in given) if j is not None]
        with self._lock:
            state = load(self.path)
            missing = missing_verdicts(state, self.page_ids)
            if missing:
                raise FormError(
                    f"every page needs a verdict before sign-off ({len(missing)} missing)"
                )
            state["signoff"] = {"name": name, "failing_page_types": failing, "signed_at": now()}
            write_json_atomic(self.path, state)
            return state


def summary_lines(state: Mapping[str, Any], page_ids: Sequence[str]) -> list[str]:
    """`ocr check summary` (FR-4): one line per page, the sign-off, the failing page types."""
    pages = state.get("pages") or {}
    lines = []
    for page_id in page_ids:
        entry = pages.get(page_id) or {}
        problems = ",".join(entry.get("problems") or []) or "-"
        lines.append(f"{page_id} {entry.get('verdict') or '-'} {problems}")
    signoff = state.get("signoff")
    if signoff:
        lines.append(f"SIGNED OFF by {signoff['name']} at {signoff['signed_at']}")
    else:
        lines.append("NOT SIGNED OFF")
    failing = (signoff or {}).get("failing_page_types") or []
    lines += [json.dumps(row, ensure_ascii=False) for row in failing] or ["none"]
    return lines
