"""The names of the `enrich_core` artifacts and their example sentences (ticket 022 FR-1, FR-3)."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from newsrag.aliases import key as name_key
from newsrag.aliases import normalise

_SPACES = re.compile(r"\s+")
SENTENCE_END = ".!?।"
NO_EXAMPLE = "(none)"


@dataclass
class NameInfo:
    """One distinct name (`type|normalised name`) with where it was seen."""

    name_key: str
    type: str
    key: str  # the normalised name
    spellings: Counter[str] = field(default_factory=Counter)  # NFC + collapsed spaces, case kept
    count: int = 0  # mentions
    stories: set[str] = field(default_factory=set)
    org_types: Counter[str] = field(default_factory=Counter)

    @property
    def display(self) -> str:
        """The most frequent spelling (tie: the smallest)."""
        return min(self.spellings, key=lambda s: (-self.spellings[s], s))


def clean(name: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFC", name)).strip()


def collect(artifacts: Iterable[Mapping[str, Any]]) -> dict[str, NameInfo]:
    """Every distinct name of the `enrich_core` artifacts, by name key, sorted by name key.

    People are type `person`; organisations are type `org` and count their sub-type.
    """
    out: dict[str, NameInfo] = {}
    for art in artifacts:
        for story_id, record in sorted((art.get("stories") or {}).items()):
            for kind, field_name, sub in (
                ("person", "people", None),
                ("org", "organisations", "type"),
            ):
                for item in record.get(field_name) or []:
                    spelling = clean(item.get("name") or "")
                    if not spelling:
                        continue
                    nk = name_key(kind, spelling)
                    info = out.get(nk)
                    if info is None:
                        info = out[nk] = NameInfo(nk, kind, normalise(spelling))
                    info.spellings[spelling] += 1
                    info.count += 1
                    info.stories.add(story_id)
                    if sub is not None:
                        info.org_types[item.get(sub) or "other"] += 1
    return dict(sorted(out.items()))


def example_for(text: str, spellings: Iterable[str], limit: int) -> str | None:
    """The sentence of `text` that holds one of `spellings`, cut to `limit` characters around the
    name; `None` when the name is not in the text."""
    flat = _SPACES.sub(" ", unicodedata.normalize("NFC", text))
    best: tuple[int, str] | None = None
    for spelling in spellings:
        at = flat.find(spelling)
        if at >= 0 and (best is None or at < best[0]):
            best = (at, spelling)
    if best is None:
        return None
    at, spelling = best
    end_at = at + len(spelling)
    start = max(
        (i + 2 for i in (flat.rfind(m + " ", 0, at) for m in SENTENCE_END) if i >= 0), default=0
    )
    ends = [i for i in (flat.find(m + " ", end_at) for m in SENTENCE_END) if i >= 0]
    end = min(ends) + 1 if ends else len(flat)
    sentence = flat[start:end].strip()
    if len(sentence) > limit:
        mid = (at - start) + len(spelling) // 2
        lo = max(0, min(mid - limit // 2, len(sentence) - limit))
        sentence = sentence[lo : lo + limit].strip()
    return sentence


def examples(info: NameInfo, texts: Mapping[str, str], n: int, limit: int) -> list[str]:
    """Up to `n` distinct example sentences of a name, one per story (stories in id order)."""
    out: list[str] = []
    spellings = sorted(info.spellings, key=lambda s: (-info.spellings[s], s))
    for story_id in sorted(info.stories):
        text = texts.get(story_id)
        if not text:
            continue
        sentence = example_for(text, spellings, limit)
        if sentence and sentence not in out:
            out.append(sentence)
            if len(out) >= n:
                break
    return out
