"""Name alias table (ticket 022): which spellings of a person or organisation are one entity.

- `names`: collecting the names of the `enrich_core` artifacts and their example sentences.
- `rules`: the spelling rules that propose candidate pairs.
- `judge`: asking the local model whether a pair is the same entity (cached verdicts).
- `group`: overrides, union-find grouping, entities and persistent entity ids.
- `build`, `verify`, `cli`: the `newsrag aliases` commands.

This module holds the helpers other tickets use to match names: `key(type, name)` and
`lookup(type, name)`. Importing the package touches neither the database nor the GPU.
"""

from __future__ import annotations

import functools
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

TYPES = ("person", "org")
VERSION_SUFFIX = "alias-p1"

_SPACES = re.compile(r"\s+")
_TELUGU_RUN = re.compile("[ఀ-౿‌‍]*[ఀ-౿][ఀ-౿‌‍]*")


def normalise(name: str) -> str:
    """NFC, runs of whitespace collapsed to one space, trimmed, Latin letters lower-cased."""
    text = _SPACES.sub(" ", unicodedata.normalize("NFC", name)).strip()
    return "".join(c.lower() if c.isalpha() and ord(c) < 0x250 else c for c in text)


def key(type: str, name: str) -> str:
    """The lookup key of a name, `"<type>|<normalised name>"` (the `entity_ids.name_key`)."""
    return f"{type}|{normalise(name)}"


def split_key(name_key: str) -> tuple[str, str]:
    kind, _, name = name_key.partition("|")
    return kind, name


def script(name: str) -> str:
    """`te` when the name has a Telugu letter, else `en` when it has a Latin letter, else `other`."""
    if any("ఀ" <= c <= "౿" for c in name):
        return "te"
    if any(c.isalpha() and ord(c) < 0x250 for c in name):
        return "en"
    return "other"


@functools.lru_cache(maxsize=200_000)
def iso(text: str) -> str:
    """Telugu to Latin in ISO 15919 (other characters are kept as they are), NFC."""
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate

    def one(match: re.Match[str]) -> str:
        return transliterate(match.group(0), sanscript.TELUGU, sanscript.ISO)

    return unicodedata.normalize("NFC", _TELUGU_RUN.sub(one, unicodedata.normalize("NFC", text)))


# English digraphs mapped before comparing (FR-8), in this order.
DIGRAPHS = (
    ("ch", "c"),
    ("th", "t"),
    ("dh", "d"),
    ("sh", "s"),
    ("aa", "a"),
    ("ee", "i"),
    ("oo", "u"),
    ("w", "v"),
)
_ANUSVARA = re.compile("(?:ṁ|m̐|ṁ)(.?)")  # ṁ and the chandrabindu m̐


def _anusvara(match: re.Match[str]) -> str:
    following = match.group(1)
    labial = not following.isalpha() or following in "pbm"
    return ("m" if labial else "n") + following


@functools.lru_cache(maxsize=200_000)
def fold_tokens(name: str) -> tuple[str, ...]:
    """The comparison form of a name, one string per word: Telugu to ISO 15919, lower-cased,
    diacritics stripped (ā→a, ḍ→d, ṇ→n, ś/ṣ→s), the anusvara written n (m before p, b, m or at
    the end), English digraphs mapped, anything that is not a letter or digit removed."""
    text = unicodedata.normalize("NFC", iso(name)).lower()
    text = _ANUSVARA.sub(_anusvara, unicodedata.normalize("NFD", text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    tokens = []
    for raw in re.split(r"[\s.]+", text):
        for old, new in DIGRAPHS:
            raw = raw.replace(old, new)
        word = "".join(c for c in raw if c.isalnum())
        if word:
            tokens.append(word)
    return tuple(tokens)


def fold(name: str) -> str:
    """`fold_tokens` joined: the key of rule (a) and the string rule (b) compares."""
    return "".join(fold_tokens(name))


def version() -> str:
    from newsrag import config

    return f"{config.settings.enrich_model}-{VERSION_SUFFIX}"


def version_dir() -> Path:
    from newsrag import config

    return config.settings.alias_dir / version()


def entities_path() -> Path:
    return version_dir() / "entities.json"


_loaded: dict[Path, tuple[tuple[int, int], dict[str, Any]]] = {}


def load_entities(path: Path | None = None) -> dict[str, Any]:
    """The `entities.json` of the current version (cached until the file changes)."""
    path = Path(path) if path is not None else entities_path()
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _loaded.get(path)
    if cached is None or cached[0] != stamp:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached = _loaded[path] = (stamp, data)
    return cached[1]


def lookup(type: str, name: str, path: Path | None = None) -> str | None:
    """The entity id of a name (`None` when the name is not in the alias table)."""
    return load_entities(path)["name_to_entity"].get(key(type, name))
