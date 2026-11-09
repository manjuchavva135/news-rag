"""District / mandal reference list and the place-name resolver (ticket 018).

``data/geo/`` holds, per state, an ordered list of district-list periods (``periods.yaml``), one
district file per period and one ``mandals.yaml`` giving every mandal's district in every period
file of its state. ``resolve(name, on_date)`` turns a place name as printed into
``Place`` matches carrying district ids for today's districts and for the districts in force on
``on_date``.

Loading touches only the YAML files; the index is built once and cached.
"""

from __future__ import annotations

import datetime as dt
import functools
import itertools
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

STATES = ("andhra_pradesh", "telangana")
KINDS = ("district", "town", "mandal")
PACKAGED_ALIASES = Path(__file__).with_name("aliases.yaml")

# Suffixes dropped from a name before matching (after spaces and dots are removed).
_SUFFIXES = ("జిల్లా", "district", "మండలం", "mandal")
# Spaces, dots, joiners and dashes are ignored when matching.
_DROP = re.compile("[\\s.\u200b\u200c\u200d\u00a0\\-\u2013\u2014_'\u2019]+")


@dataclass(frozen=True)
class Place:
    kind: str  # "district" | "mandal" | "town"
    id: str
    name_en: str
    name_te: str
    state: str
    districts_current: list[str] = field(default_factory=list)
    districts_at_date: list[str] = field(default_factory=list)


class GeoDataError(Exception):
    """The geo files are missing or cannot be read."""


def normalise(name: str) -> str:
    """NFC, Latin lower-cased, no spaces/dots/joiners, without a district/mandal suffix."""
    text = unicodedata.normalize("NFC", str(name)).lower()
    text = _DROP.sub("", text)
    changed = True
    while changed and text:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
    return text


def _as_date(value: Any) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GeoDataError(f"missing geo file: {path}") from exc
    except yaml.YAMLError as exc:
        raise GeoDataError(f"bad YAML in {path}: {exc}") from exc


@dataclass
class Period:
    state: str
    file: str
    valid_from: dt.date
    valid_to: dt.date | None


@dataclass
class GeoData:
    """The raw files, as loaded (also what `check` validates)."""

    periods: dict[str, list[Period]]
    district_files: dict[str, dict[str, Any]]  # file -> parsed YAML
    mandals: list[dict[str, Any]]
    aliases: list[dict[str, Any]]


def load_data(geo_dir: Path, aliases_path: Path | None = None) -> GeoData:
    geo_dir = Path(geo_dir)
    raw = _read_yaml(geo_dir / "periods.yaml") or {}
    if not isinstance(raw, dict):
        raise GeoDataError(f"{geo_dir / 'periods.yaml'}: top level is not a mapping")
    periods: dict[str, list[Period]] = {}
    district_files: dict[str, dict[str, Any]] = {}
    for state, entries in raw.items():
        plist = []
        for entry in entries or []:
            plist.append(
                Period(
                    state=str(state),
                    file=str(entry["file"]),
                    valid_from=_as_date(entry["valid_from"]),
                    valid_to=_as_date(entry.get("valid_to")),
                )
            )
            district_files[str(entry["file"])] = _read_yaml(geo_dir / str(entry["file"])) or {}
        plist.sort(key=lambda p: p.valid_from)
        periods[str(state)] = plist
    mandal_doc = _read_yaml(geo_dir / "mandals.yaml") or {}
    mandals = list(mandal_doc.get("mandals") or []) if isinstance(mandal_doc, dict) else []
    aliases_path = Path(aliases_path) if aliases_path is not None else PACKAGED_ALIASES
    aliases = list(_read_yaml(aliases_path) or []) if aliases_path.exists() else []
    return GeoData(periods, district_files, mandals, aliases)


class GeoIndex:
    """An in-memory index over the geo files: name -> (kind, id), plus per-period districts."""

    def __init__(self, data: GeoData) -> None:
        self.periods = data.periods
        self.file_state = {p.file: p.state for plist in data.periods.values() for p in plist}
        self.current_file = {s: plist[-1].file for s, plist in data.periods.items() if plist}
        # district id -> {file: entry}
        self.districts: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.district_state: dict[str, str] = {}
        # town id -> entry (from the latest file naming it) and its state
        self.towns: dict[str, dict[str, Any]] = {}
        self.town_state: dict[str, str] = {}
        for state, plist in data.periods.items():
            for period in plist:
                doc = data.district_files.get(period.file) or {}
                for d in doc.get("districts") or []:
                    self.districts[d["id"]][period.file] = d
                    self.district_state[d["id"]] = state
                    hq = d.get("hq") or {}
                    if hq.get("id"):
                        self.towns[hq["id"]] = hq
                        self.town_state[hq["id"]] = state
        self.mandals: dict[str, dict[str, Any]] = {m["id"]: m for m in data.mandals}
        # (file, district id) -> mandal ids
        self.mandals_in: dict[tuple[str, str], list[str]] = defaultdict(list)
        for m in data.mandals:
            for f, did in (m.get("districts") or {}).items():
                if did:
                    self.mandals_in[(f, did)].append(m["id"])
        self.names: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for did, by_file in self.districts.items():
            for d in by_file.values():
                self._add(d.get("name_en"), "district", did)
                self._add(d.get("name_te"), "district", did)
        for tid, t in self.towns.items():
            self._add(t.get("name_en"), "town", tid)
            self._add(t.get("name_te"), "town", tid)
        for mid, m in self.mandals.items():
            self._add(m.get("name_en"), "mandal", mid)
            self._add(m.get("name_te"), "mandal", mid)
        for a in data.aliases:
            if self._exists(a.get("kind"), a.get("id")):
                self._add(a.get("alias"), a["kind"], a["id"])

    def _add(self, name: Any, kind: str, ident: str) -> None:
        if name:
            key = normalise(str(name))
            if key:
                self.names[key].add((kind, ident))

    def _exists(self, kind: Any, ident: Any) -> bool:
        if kind == "district":
            return ident in self.districts
        if kind == "town":
            return ident in self.towns
        if kind == "mandal":
            return ident in self.mandals
        return False

    # --- periods ---

    def period_file(self, state: str, on_date: dt.date | None) -> str:
        plist = self.periods[state]
        if on_date is None:
            return plist[-1].file
        for p in plist:
            if p.valid_from <= on_date and (p.valid_to is None or on_date <= p.valid_to):
                return p.file
        if on_date < plist[0].valid_from:
            return plist[0].file
        # in a gap or after a closed last period: the latest period starting before the date
        started = [p for p in plist if p.valid_from <= on_date]
        return (started[-1] if started else plist[-1]).file

    # --- districts of a mandal / town / district ---

    def mandal_district(
        self, mandal_id: str, file: str, _seen: frozenset = frozenset()
    ) -> str | None:
        m = self.mandals.get(mandal_id)
        if m is None or mandal_id in _seen:
            return None
        did = (m.get("districts") or {}).get(file)
        if did:
            return did
        parent = m.get("split_from")
        if parent:
            return self.mandal_district(parent, file, _seen | {mandal_id})
        return None

    def _mandal_place(self, mid: str, on_date: dt.date | None) -> Place:
        m = self.mandals[mid]
        state = m["state"]
        cur = self.mandal_district(mid, self.current_file[state])
        at = self.mandal_district(mid, self.period_file(state, on_date))
        return Place(
            "mandal",
            mid,
            m["name_en"],
            m["name_te"],
            state,
            [cur] if cur else [],
            [at] if at else [],
        )

    def _town_place(self, tid: str, on_date: dt.date | None) -> Place:
        t = self.towns[tid]
        state = self.town_state[tid]
        mid = t.get("mandal_id")
        cur = self.mandal_district(mid, self.current_file[state]) if mid else None
        at = self.mandal_district(mid, self.period_file(state, on_date)) if mid else None
        return Place(
            "town",
            tid,
            t["name_en"],
            t["name_te"],
            state,
            [cur] if cur else [],
            [at] if at else [],
        )

    def _district_place(self, did: str, on_date: dt.date | None) -> Place:
        state = self.district_state[did]
        by_file = self.districts[did]
        cur_file = self.current_file[state]
        at_file = self.period_file(state, on_date)
        if at_file in by_file:
            entry = by_file[at_file]
            mids = self.mandals_in[(at_file, did)]
            at = [did]
            current = sorted({d for mid in mids if (d := self.mandal_district(mid, cur_file))})
        else:
            # Not a district on that date: describe it through its mandals in the period nearest
            # in time (as first formed for a later district, as last drawn for an abolished one).
            order = [p.file for p in self.periods[state]]
            files = [f for f in order if f in by_file]
            later = [f for f in files if order.index(f) > order.index(at_file)]
            near = later[0] if later else files[-1]
            entry = by_file[near]
            mids = self.mandals_in[(near, did)]
            at = sorted({d for mid in mids if (d := self.mandal_district(mid, at_file))})
            current = (
                [did]
                if cur_file in by_file
                else sorted({d for mid in mids if (d := self.mandal_district(mid, cur_file))})
            )
        return Place("district", did, entry["name_en"], entry["name_te"], state, current, at)

    def resolve(self, name: str, on_date: dt.date | None = None) -> list[Place]:
        # copies, so a caller changing a list never changes the cache
        return [
            replace(
                p,
                districts_current=list(p.districts_current),
                districts_at_date=list(p.districts_at_date),
            )
            for p in self._resolve_cached(normalise(name), on_date)
        ]

    @functools.lru_cache(maxsize=65536)  # noqa: B019 - one index per process
    def _resolve_cached(self, key: str, on_date: dt.date | None) -> tuple[Place, ...]:
        if not key:
            return ()
        hits = sorted(self.names.get(key, ()), key=lambda h: (KINDS.index(h[0]), h[1]))
        out = []
        for kind, ident in hits:
            if kind == "district":
                out.append(self._district_place(ident, on_date))
            elif kind == "town":
                out.append(self._town_place(ident, on_date))
            else:
                out.append(self._mandal_place(ident, on_date))
        return tuple(out)


def load_index(geo_dir: Path, aliases_path: Path | None = None) -> GeoIndex:
    return GeoIndex(load_data(geo_dir, aliases_path))


@functools.lru_cache(maxsize=1)
def default_index() -> GeoIndex:
    from newsrag.config import settings

    return load_index(settings.geo_dir, settings.geo_aliases_file)


def resolve(name: str, on_date: dt.date | None = None) -> list[Place]:
    """Every district, town and mandal the printed name can mean ([] when none)."""
    if isinstance(on_date, dt.datetime):
        on_date = on_date.date()
    return default_index().resolve(name, on_date)


# --- check -------------------------------------------------------------------------------------


def check(geo_dir: Path, aliases_path: Path | None = None) -> list[str]:
    """Every problem found in the geo files ([] = valid)."""
    try:
        data = load_data(geo_dir, aliases_path)
    except (GeoDataError, KeyError, TypeError, ValueError) as exc:
        return [str(exc)]
    problems: list[str] = []
    for state, plist in data.periods.items():
        if state not in STATES:
            problems.append(f"periods.yaml: unknown state {state!r}")
        if not plist:
            problems.append(f"periods.yaml: {state} has no period")
            continue
        if plist[-1].valid_to is not None:
            problems.append(f"periods.yaml: {state}'s last period has a valid_to (none is current)")
        for a, b in itertools.pairwise(plist):
            if a.valid_to is None or a.valid_to >= b.valid_from:
                problems.append(f"periods.yaml: {a.file} overlaps {b.file}")
    districts_by_file: dict[str, set[str]] = {}
    for f, doc in data.district_files.items():
        if not isinstance(doc, dict):
            problems.append(f"{f}: top level is not a mapping")
            districts_by_file[f] = set()
            continue
        if not doc.get("as_of"):
            problems.append(f"{f}: no as_of")
        if not doc.get("source_urls"):
            problems.append(f"{f}: no source_urls")
        ids = set()
        for d in doc.get("districts") or []:
            did = d.get("id")
            if not did:
                problems.append(f"{f}: a district has no id")
                continue
            if did in ids:
                problems.append(f"{f}: district {did} listed twice")
            ids.add(did)
            for key in ("name_en", "name_te"):
                if not str(d.get(key) or "").strip():
                    problems.append(f"{f}: district {did} has no {key}")
            hq = d.get("hq") or {}
            for key in ("id", "name_en", "name_te", "mandal_id"):
                if not str(hq.get(key) or "").strip():
                    problems.append(f"{f}: district {did}: hq has no {key}")
        districts_by_file[f] = ids
    files_of = {s: [p.file for p in plist] for s, plist in data.periods.items()}
    mandal_ids: dict[str, dict[str, Any]] = {}
    per_district: dict[tuple[str, str], int] = defaultdict(int)
    for m in data.mandals:
        mid = m.get("id")
        if not mid:
            problems.append("mandals.yaml: a mandal has no id")
            continue
        if mid in mandal_ids:
            problems.append(f"mandals.yaml: mandal {mid} listed twice")
        mandal_ids[mid] = m
        for key in ("name_en", "name_te"):
            if not str(m.get(key) or "").strip():
                problems.append(f"mandals.yaml: mandal {mid} has no {key}")
        state = m.get("state")
        if state not in files_of:
            problems.append(f"mandals.yaml: mandal {mid} has unknown state {state!r}")
            continue
        dists = m.get("districts") or {}
        files = files_of[state]
        seen_value = False
        for i, f in enumerate(files):
            if f not in dists:
                problems.append(f"mandals.yaml: mandal {mid} has no district for {f}")
                continue
            did = dists[f]
            if did is None:
                # allowed only before the mandal existed (FR-6), never for the current period
                if seen_value or i == len(files) - 1:
                    problems.append(f"mandals.yaml: mandal {mid} has no district for {f}")
                continue
            seen_value = True
            if did not in districts_by_file.get(f, set()):
                problems.append(f"mandals.yaml: mandal {mid}: district {did} is not in {f}")
            per_district[(f, did)] += 1
        for f in dists:
            if f not in files:
                problems.append(f"mandals.yaml: mandal {mid}: {f} is not a period file of {state}")
    for m in data.mandals:
        parent = m.get("split_from")
        if parent and parent not in mandal_ids:
            problems.append(f"mandals.yaml: mandal {m.get('id')}: split_from {parent} not found")
    town_ids = set()
    for f, doc in data.district_files.items():
        for d in (doc.get("districts") or []) if isinstance(doc, dict) else []:
            did = d.get("id")
            if did and per_district.get((f, did), 0) < 1:
                problems.append(f"{f}: district {did} has no mandal")
            hq = d.get("hq") or {}
            if hq.get("id"):
                town_ids.add(hq["id"])
            mid = hq.get("mandal_id")
            if mid and mid not in mandal_ids:
                problems.append(f"{f}: district {did}: hq.mandal_id {mid} not found")
            elif mid and (mandal_ids[mid].get("districts") or {}).get(f) != did:
                problems.append(f"{f}: district {did}: hq mandal {mid} is not in {did}")
    all_districts = set().union(*districts_by_file.values()) if districts_by_file else set()
    for a in data.aliases:
        kind, ident = a.get("kind"), a.get("id")
        target = {"district": all_districts, "town": town_ids, "mandal": set(mandal_ids)}.get(kind)
        if not str(a.get("alias") or "").strip():
            problems.append(f"aliases: entry {a} has no alias")
        if target is None:
            problems.append(f"aliases: {a.get('alias')!r} has unknown kind {kind!r}")
        elif ident not in target:
            problems.append(f"aliases: {a.get('alias')!r} points at missing {kind} {ident!r}")
    return problems


def counts(geo_dir: Path) -> dict[str, int]:
    """Districts per period file (key `<state>_<file stem>`) and mandals per state."""
    data = load_data(geo_dir, None)
    out: dict[str, int] = {}
    for state, plist in data.periods.items():
        for p in plist:
            doc = data.district_files.get(p.file) or {}
            out[f"{state}_{Path(p.file).stem}"] = len(doc.get("districts") or [])
    for state in data.periods:
        out[f"{state}_mandals"] = sum(1 for m in data.mandals if m.get("state") == state)
    return out


def summary_line(geo_dir: Path) -> str:
    """`ap_current=<n> ap_2014_2022=<n> ts=<n> ap_mandals=<n> ts_mandals=<n>`."""
    data = load_data(geo_dir, None)

    def n_districts(f: str) -> int:
        return len((data.district_files.get(f) or {}).get("districts") or [])

    ap = data.periods.get("andhra_pradesh", [])
    ts = data.periods.get("telangana", [])
    old = next((p.file for p in ap if Path(p.file).stem == "ap_districts_2014_2022"), None)
    n_ap_m = sum(1 for m in data.mandals if m.get("state") == "andhra_pradesh")
    n_ts_m = sum(1 for m in data.mandals if m.get("state") == "telangana")
    return (
        f"ap_current={n_districts(ap[-1].file) if ap else 0} "
        f"ap_2014_2022={n_districts(old) if old else 0} "
        f"ts={n_districts(ts[-1].file) if ts else 0} "
        f"ap_mandals={n_ap_m} ts_mandals={n_ts_m}"
    )
