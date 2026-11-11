"""Candidate pairs from the spelling rules (ticket 022 FR-2): pure functions, no model.

Rule (a): identical folded forms. Rule (b): Jaro-Winkler on the folded forms, only inside blocks
(names that share their last word or their first three letters). Rule (d): a shared surname
word plus several common stories. A pair is recorded under the strongest rule that matches.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from itertools import combinations

from rapidfuzz.distance import JaroWinkler

from newsrag.aliases import fold, fold_tokens
from newsrag.aliases.names import NameInfo

RULE_ORDER = ("a", "b", "d")  # strongest first
AUTO_RULES = ("a", "b")  # only spelling rules may be applied without a person looking (A-47)
MIN_SURNAME_CHARS = 3
Pair = tuple[str, str]  # (name key a, name key b), a < b


def pair_of(a: str, b: str) -> Pair:
    return (a, b) if a < b else (b, a)


def candidate_pairs(
    names: Mapping[str, NameInfo], jw_threshold: float, min_shared_stories: int
) -> dict[Pair, str]:
    """`{(name_key_a, name_key_b): rule}` for names of the same type, `a < b`."""
    by_type: dict[str, list[str]] = defaultdict(list)
    for nk, info in names.items():
        by_type[info.type].append(nk)
    found: dict[Pair, str] = {}

    def add(a: str, b: str, rule: str) -> None:
        pair = pair_of(a, b)
        old = found.get(pair)
        if old is None or RULE_ORDER.index(rule) < RULE_ORDER.index(old):
            found[pair] = rule

    for keys in by_type.values():
        folded = {nk: fold(names[nk].key) for nk in keys}
        tokens = {nk: fold_tokens(names[nk].key) for nk in keys}
        keys = [nk for nk in keys if folded[nk]]

        same_fold: dict[str, list[str]] = defaultdict(list)
        for nk in keys:
            same_fold[folded[nk]].append(nk)
        for group in same_fold.values():
            for a, b in combinations(group, 2):
                add(a, b, "a")

        blocks: dict[tuple[str, str], list[str]] = defaultdict(list)
        for nk in keys:
            blocks["last", tokens[nk][-1]].append(nk)
            blocks["head", folded[nk][:3]].append(nk)
        seen: set[Pair] = set()
        for block in blocks.values():
            for a, b in combinations(block, 2):
                pair = pair_of(a, b)
                if pair in seen:
                    continue
                seen.add(pair)
                if folded[a] != folded[b] and (
                    JaroWinkler.similarity(folded[a], folded[b], score_cutoff=jw_threshold)
                    >= jw_threshold
                ):
                    add(a, b, "b")

        surnames: dict[str, list[str]] = defaultdict(list)
        for nk in keys:
            if len(tokens[nk][-1]) >= MIN_SURNAME_CHARS:
                surnames[tokens[nk][-1]].append(nk)
        for group in surnames.values():
            for a, b in combinations(group, 2):
                if len(names[a].stories & names[b].stories) >= min_shared_stories:
                    add(a, b, "d")
    return dict(sorted(found.items()))


def priority(pair: Pair, names: Mapping[str, NameInfo]) -> tuple:
    """Sort key for the LLM batches: the pairs of the most frequent names first."""
    a, b = names[pair[0]], names[pair[1]]
    return (-max(a.count, b.count), -(a.count + b.count), pair)
