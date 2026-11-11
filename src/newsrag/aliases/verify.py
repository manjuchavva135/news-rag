"""`newsrag aliases verify` (ticket 022 NFR-1): does a second model agree that random pairs of
names of one auto-merged group are the same entity?"""

from __future__ import annotations

import bisect
import json
import random
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from newsrag import config
from newsrag.aliases import version_dir
from newsrag.aliases.judge import Decide
from newsrag.aliases.names import NameInfo, examples


def pair_count(size: int) -> int:
    return size * (size - 1) // 2


def unrank(size: int, index: int) -> tuple[int, int]:
    """The `index`-th pair `(i, j)`, `i < j`, of `size` items in lexicographic order."""
    for i in range(size - 1):
        row = size - 1 - i
        if index < row:
            return i, i + 1 + index
        index -= row
    raise IndexError(index)


def sample_pairs(groups: Sequence[Sequence[str]], n: int, seed: int) -> list[tuple[str, str]]:
    """`n` distinct pairs of names of one group, drawn with `seed` (any two names of a group, so a
    transitive merge is tested too); all the pairs when there are fewer than `n`."""
    groups = [sorted(g) for g in sorted(groups) if len(g) >= 2]
    cumulative: list[int] = []
    total = 0
    for g in groups:
        total += pair_count(len(g))
        cumulative.append(total)
    picks = range(total) if total <= n else sorted(random.Random(seed).sample(range(total), n))
    out = []
    for index in picks:
        g = bisect.bisect_right(cumulative, index)
        before = cumulative[g - 1] if g else 0
        i, j = unrank(len(groups[g]), index - before)
        out.append((groups[g][i], groups[g][j]))
    return out


def run_verify(
    groups: Sequence[Sequence[str]],
    names: Mapping[str, NameInfo],
    texts: Mapping[str, str],
    n: int,
    seed: int,
    decide: Decide | None = None,
) -> dict[str, Any]:
    """The report. `decide` defaults to the judge model `alias_verify_model` via `models.use`."""
    s = config.settings
    pairs = [(a, b) for a, b in sample_pairs(groups, n, seed) if a in names and b in names]
    if pairs and decide is None:
        from newsrag.aliases.build import model_decider

        with model_decider(s.alias_verify_model) as model:
            return run_verify(groups, names, texts, n, seed, model)

    def one(pair: tuple[str, str]) -> dict[str, Any]:
        assert decide is not None
        a, b = names[pair[0]], names[pair[1]]
        ex = [examples(i, texts, s.alias_examples, s.alias_example_chars) for i in (a, b)]
        verdict = decide(a.type, a.display, b.display, ex[0], ex[1])
        return {
            "type": a.type,
            "name_a": a.display,
            "name_b": b.display,
            "same": verdict.same,
            "confidence": verdict.confidence,
        }

    with ThreadPoolExecutor(max_workers=max(1, s.alias_concurrency)) as pool:
        items = list(pool.map(one, pairs))
    return {
        "model": s.alias_verify_model,
        "n_requested": n,
        "seed": seed,
        "groups": len([g for g in groups if len(g) >= 2]),
        "n": len(items),
        "same": sum(1 for x in items if x["same"] is True),
        "items": items,
    }


def load_auto_groups() -> list[list[str]]:
    """The auto-merged groups (name keys) of the last build."""
    path = version_dir() / "stats.json"
    return json.loads(path.read_text(encoding="utf-8"))["auto_groups"]
