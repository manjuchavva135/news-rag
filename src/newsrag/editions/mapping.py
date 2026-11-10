"""The per-year code → edition map and each page's editions (011 FR-3, FR-4, FR-5, FR-6).

Pure functions over plain records; `newsrag.editions.store` reads the ledger and the folio
artifacts and writes `editions.yaml` / `page_editions`.

A *record* is one page whose `folio` stage is done::

    {page_id, pub_date: date, edition_code, section, place_te, place_districts_current,
     place_districts_at_print, place_state, printed_date: str | None}

Pages of a state section (`editions_state_sections`, e.g. `main`) are shared by many editions
and their folio names the printing centre: they never feed the code map and get only a state.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# Keys `editions build` writes; any other key of an entry was added by a person and is kept.
GENERATED_KEYS = (
    "name_te",
    "districts_current",
    "districts_at_print",
    "state",
    "n_pages",
    "agreement",
    "status",
    "names_from_letters",
    "letters_missing",
)
WHOLE_CODES = ("ALL", "FH")  # read from their own folios only (grill D-19)
UNKNOWN = "low_confidence:unknown_edition"
FROM_FOLIO = "edition_from_folio"
LETTERS_PARTIAL = "edition_letters_partial"
DATE_MISMATCH = "date_mismatch"
STATE_PAGE = "state_page"  # informational: a shared state page, no edition of its own


def is_letter_code(code: str) -> bool:
    """A multi-letter code made only of letters, not ALL/FH, without a `-` variant (A-10)."""
    return (
        len(code) > 1
        and re.fullmatch(r"[A-Za-z]+", code) is not None
        and code.upper() not in WHOLE_CODES
    )


def _most_common(values: Iterable[Any]) -> tuple[Any, int]:
    counts = Counter(values)
    if not counts:
        return None, 0
    # highest count first; ties broken by the value's text so the result is stable
    value, n = min(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return value, n


def code_entry(
    records: list[dict[str, Any]], min_agreement: float, min_pages: int
) -> dict[str, Any]:
    """The generated keys of one (year, code) entry, from that code's records."""
    placed = [r for r in records if r.get("place_te")]
    n_pages = len(placed)
    name, n = _most_common(r["place_te"] for r in placed)
    agreement = round(n / n_pages, 2) if n_pages else 0.0
    same = [r for r in placed if r["place_te"] == name]
    current, _ = _most_common(tuple(r.get("place_districts_current") or ()) for r in same)
    at_print, _ = _most_common(tuple(r.get("place_districts_at_print") or ()) for r in same)
    state, _ = _most_common(r.get("place_state") for r in same if r.get("place_state"))
    ok = n_pages >= min_pages and agreement >= min_agreement
    return {
        "name_te": name,
        "districts_current": list(current or ()),
        "districts_at_print": list(at_print or ()),
        "state": state,
        "n_pages": n_pages,
        "agreement": agreement,
        "status": "ok" if ok else "ambiguous",
    }


def build_map(
    records: Iterable[dict[str, Any]],
    min_agreement: float,
    min_pages: int,
    state_sections: Iterable[str] = (),
) -> dict[int, dict[str, dict[str, Any]]]:
    """{year: {code: generated entry}} including the letter rule (FR-3); state pages skipped."""
    by_code: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if is_state_page(r, state_sections):
            continue
        by_code[(r["pub_date"].year, r["edition_code"])].append(r)
    out: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (year, code), recs in sorted(by_code.items()):
        out[year][code] = code_entry(recs, min_agreement, min_pages)
    for year, entries in out.items():
        for code, entry in entries.items():
            if not is_letter_code(code):
                continue
            names: list[str] = []
            missing: list[str] = []
            for letter in code:
                single = entries.get(letter)
                if single and single["status"] == "ok" and single["name_te"]:
                    if single["name_te"] not in names:
                        names.append(single["name_te"])
                elif letter not in missing:
                    missing.append(letter)
            entry["names_from_letters"] = names
            entry["letters_missing"] = missing
    return {y: dict(sorted(e.items())) for y, e in sorted(out.items())}


def merge_entry(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """`new`'s generated keys over `old`, keeping every key a person added (D-8)."""
    merged = dict(new)
    merged.update({k: v for k, v in (old or {}).items() if k not in GENERATED_KEYS})
    return merged


# --- pages ---------------------------------------------------------------------------------------


@dataclass
class PageEdition:
    page_id: str
    edition_names: list[str] = field(default_factory=list)
    districts_current: list[str] = field(default_factory=list)
    districts_at_print: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    source: str = "unknown"
    flags: list[str] = field(default_factory=list)


def _union(lists: Iterable[Iterable[str] | None]) -> list[str]:
    out: list[str] = []
    for values in lists:
        for v in values or ():
            if v and v not in out:
                out.append(v)
    return out


def is_state_page(record: dict[str, Any], state_sections: Iterable[str]) -> bool:
    return record.get("section") in set(state_sections)


def state_page_states(
    records: Iterable[dict[str, Any]], state_sections: Iterable[str]
) -> dict[tuple[int, str], str]:
    """(year, code) -> the most common folio state of that code's state pages."""
    states: dict[tuple[int, str], Counter] = defaultdict(Counter)
    for r in records:
        if is_state_page(r, state_sections) and r.get("place_state"):
            states[(r["pub_date"].year, r["edition_code"])][r["place_state"]] += 1
    return {key: _most_common(c.elements())[0] for key, c in states.items()}


def page_edition(
    record: dict[str, Any],
    year_map: dict[str, dict[str, Any]],
    state_sections: Iterable[str] = (),
    code_state: str | None = None,
) -> PageEdition:
    """One page's editions (FR-4, FR-6, FR-7). A state page gets its state only: its own folio's,
    else `code_state` (see `state_page_states`). Other pages: letters, then the code's own ok
    entry, then the page's folio."""
    code = record["edition_code"]
    entry = year_map.get(code) or {}
    pe = PageEdition(record["page_id"])
    letters_names = entry.get("names_from_letters") or []
    if is_state_page(record, state_sections):
        # The folio names the printing centre: keep its state, never its place.
        pe.states = _union([[record.get("place_state")]]) or _union([[code_state]])
        pe.source = "main"
        pe.flags.append(STATE_PAGE)
        if not pe.states:
            pe.flags.append(UNKNOWN)
    elif is_letter_code(code) and letters_names:
        letters = [
            year_map[ch]
            for ch in dict.fromkeys(code)
            if ch in year_map and year_map[ch].get("status") == "ok"
        ]
        pe.edition_names = list(letters_names)
        pe.districts_current = _union(e.get("districts_current") for e in letters)
        pe.districts_at_print = _union(e.get("districts_at_print") for e in letters)
        pe.states = _union([e.get("state")] for e in letters)
        pe.source = "letters"
        if entry.get("letters_missing"):
            pe.flags.append(LETTERS_PARTIAL)
    elif entry.get("status") == "ok" and entry.get("name_te"):
        pe.edition_names = [entry["name_te"]]
        pe.districts_current = list(entry.get("districts_current") or [])
        pe.districts_at_print = list(entry.get("districts_at_print") or [])
        pe.states = _union([[entry.get("state")]])
        pe.source = "code_map"
    elif record.get("place_te"):
        pe.edition_names = [record["place_te"]]
        pe.districts_current = list(record.get("place_districts_current") or [])
        pe.districts_at_print = list(record.get("place_districts_at_print") or [])
        pe.states = _union([[record.get("place_state")]])
        pe.source = "folio"
        pe.flags.append(FROM_FOLIO)
    elif record.get("place_state"):
        pe.states = [record["place_state"]]
        pe.source = "folio_state"
        pe.flags.append(UNKNOWN)
    else:
        pe.flags.append(UNKNOWN)
    printed = record.get("printed_date")
    if printed and str(printed) != record["pub_date"].isoformat():
        pe.flags.append(DATE_MISMATCH)
    return pe


# --- report --------------------------------------------------------------------------------------


def report_lines(
    editions: dict[int, dict[str, dict[str, Any]]],
    pages: list[tuple[dt.date, str, list[str]]],
    records: list[dict[str, Any]],
    state_sections: Iterable[str] = (),
) -> list[str]:
    """FR-5 / FR-6 report: one summary line per year, then the ambiguous codes, the codes with
    unknown_edition pages and the places the state pages' folios print.
    `pages` = (pub_date, edition_code, flags) of page_editions."""
    unknown: dict[int, Counter] = defaultdict(Counter)
    mismatch: Counter = Counter()
    state_pages: Counter = Counter()
    for pub_date, code, flags in pages:
        if UNKNOWN in flags:
            unknown[pub_date.year][code] += 1
        if DATE_MISMATCH in flags:
            mismatch[pub_date.year] += 1
        if STATE_PAGE in flags:
            state_pages[pub_date.year] += 1
    places: dict[tuple[int, str], Counter] = defaultdict(Counter)
    state_places: dict[int, Counter] = defaultdict(Counter)
    for r in records:
        if not r.get("place_te"):
            continue
        if is_state_page(r, state_sections):
            state_places[r["pub_date"].year][r["place_te"]] += 1
        else:
            places[(r["pub_date"].year, r["edition_code"])][r["place_te"]] += 1
    years = sorted(set(editions) | set(unknown) | set(mismatch) | set(state_pages))
    lines = []
    for year in years:
        entries = editions.get(year, {})
        n_ok = sum(1 for e in entries.values() if e.get("status") == "ok")
        n_amb = sum(1 for e in entries.values() if e.get("status") == "ambiguous")
        lines.append(
            f"{year} codes={len(entries)} ok={n_ok} ambiguous={n_amb} "
            f"unknown_edition={sum(unknown[year].values())} date_mismatch={mismatch[year]} "
            f"state_pages={state_pages[year]}"
        )

    def top(counter: Counter, n: int) -> str:
        items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
        return ", ".join(f"{name}:{k}" for name, k in items) or "-"

    lines.append("ambiguous codes (year code n_pages agreement top-3 places):")
    for year in years:
        for code, e in editions.get(year, {}).items():
            if e.get("status") == "ambiguous":
                lines.append(
                    f"  {year} {code} {e.get('n_pages', 0)} {e.get('agreement', 0):.2f} "
                    f"{top(places[(year, code)], 3)}"
                )
    lines.append("codes with unknown_edition pages (year code pages):")
    for year in years:
        for code, n in sorted(unknown[year].items()):
            lines.append(f"  {year} {code} {n}")
    lines.append("state pages: folio places (year top-5, not used as editions):")
    for year in years:
        if state_places[year]:
            lines.append(f"  {year} {top(state_places[year], 5)}")
    return lines
