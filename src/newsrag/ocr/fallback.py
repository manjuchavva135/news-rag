"""The Sarvam fallback rules (ticket 010): which pages `ocr_final` re-reads with Sarvam.

`ocr_fallback_file` holds ``rules:``, a list of mappings. Each rule may set ``years``,
``sections``, ``page_nums``, ``edition_codes`` (which pages) and ``labels`` (which blocks). A key
that is omitted, ``null`` or ``[]`` means "any". Rules are normalised (empty keys dropped, lists
sorted and de-duplicated) before matching, hashing and comparing.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml

from newsrag import config
from newsrag.stages.base import PageRow

Rule = dict[str, list[Any]]

INT_KEYS = ("years", "page_nums")
STR_KEYS = ("sections", "edition_codes", "labels")
RULE_KEYS = (*INT_KEYS, *STR_KEYS)


class RulesError(ValueError):
    """The rules file is missing or malformed."""


def normalise_rule(raw: Any, where: str = "rule") -> Rule:
    """One rule with empty keys dropped and every list sorted and de-duplicated."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RulesError(f"{where}: a rule must be a mapping, got {type(raw).__name__}")
    out: Rule = {}
    for key, value in raw.items():
        key = str(key)
        if key not in RULE_KEYS:
            raise RulesError(f"{where}: unknown key {key!r} (allowed: {', '.join(RULE_KEYS)})")
        if value is None:
            continue
        values = value if isinstance(value, list | tuple) else [value]
        if not values:
            continue
        try:
            if key in INT_KEYS:
                if any(isinstance(v, bool) for v in values):
                    raise ValueError
                cleaned: list[Any] = sorted({int(v) for v in values})
            else:
                cleaned = sorted({str(v) for v in values})
        except (TypeError, ValueError):
            kind = "whole numbers" if key in INT_KEYS else "strings"
            raise RulesError(f"{where}: {key} must be a list of {kind}") from None
        out[key] = cleaned
    return dict(sorted(out.items()))


def parse_rules(data: Any, source: str = "rules") -> list[Rule]:
    if data is None:
        return []
    if not isinstance(data, dict) or "rules" not in data:
        raise RulesError(f"{source}: expected a mapping with a `rules:` list")
    rules = data["rules"]
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise RulesError(f"{source}: `rules` must be a list")
    return [normalise_rule(r, f"{source}: rule {i + 1}") for i, r in enumerate(rules)]


_cache_lock = threading.Lock()
_cache: dict[tuple[str, int, int], list[Rule]] = {}


def load_rules(path: Path | None = None) -> list[Rule]:
    """The normalised rules of `ocr_fallback_file` (re-read when the file changes)."""
    path = Path(path if path is not None else config.settings.ocr_fallback_file)
    try:
        stat = path.stat()
    except FileNotFoundError:
        raise RulesError(f"ocr_fallback_file {path} not found") from None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RulesError(f"{path}: not valid YAML: {exc}") from None
    rules = parse_rules(data, str(path))
    with _cache_lock:
        _cache.clear()
        _cache[key] = rules
    return rules


def page_numbers(page: PageRow) -> range:
    """The page numbers a page covers (a spread covers page_num..page_end)."""
    end = page.page_end if page.page_end is not None else page.page_num
    return range(page.page_num, max(end, page.page_num) + 1)


def matches(rule: Rule, page: PageRow) -> bool:
    if "years" in rule and page.pub_date.year not in rule["years"]:
        return False
    if "sections" in rule and page.section not in rule["sections"]:
        return False
    if "edition_codes" in rule and page.edition_code not in rule["edition_codes"]:
        return False
    return "page_nums" not in rule or any(n in rule["page_nums"] for n in page_numbers(page))


def matching_rules(rules: Iterable[Rule], page: PageRow) -> list[Rule]:
    """The rules a page matches, canonical: de-duplicated and sorted by their JSON."""
    found = {canonical_json(r): r for r in rules if matches(r, page)}
    return [found[k] for k in sorted(found)]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rules_hash(rules: Sequence[Rule], extra: dict[str, Any] | None = None) -> str:
    """First 8 hex of sha1 of the canonical JSON of the matched rules (+ `extra` settings)."""
    payload: Any = list(rules) if extra is None else {"rules": list(rules), "settings": extra}
    return hashlib.sha1(canonical_json(payload).encode()).hexdigest()[:8]


def default_labels() -> tuple[str, ...]:
    s = config.settings
    return tuple(sorted({*s.text_labels, *s.title_labels}))


def labels_for(rules: Iterable[Rule]) -> frozenset[str]:
    """Block labels to re-read on a page matching `rules` (a rule without labels: the default)."""
    out: set[str] = set()
    for rule in rules:
        out.update(rule.get("labels") or default_labels())
    return frozenset(out)


def preview_counts(pages: Iterable[PageRow], rules: Sequence[Rule]) -> list[tuple[int, str, int]]:
    """(year, section, n) for every year and section with n > 0 matching pages, sorted."""
    counts: Counter[tuple[int, str]] = Counter()
    if not rules:
        return []
    for page in pages:
        if page.quarantine_reason is None and any(matches(r, page) for r in rules):
            counts[(page.pub_date.year, page.section)] += 1
    return [(year, section, n) for (year, section), n in sorted(counts.items()) if n > 0]


def registered_pages() -> list[PageRow]:
    """Every non-quarantined registered page, from the ledger."""
    from newsrag.ledger import db

    with db.conn() as c:
        rows = db.fetch_all(
            c, "select * from pages where quarantine_reason is null order by pub_date, page_id"
        )
    return [PageRow.from_row(r) for r in rows]
