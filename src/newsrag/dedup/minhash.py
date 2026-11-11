"""Near-duplicate candidates: MinHash over character shingles (ticket 017 FR-2)."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping

from datasketch import MinHash, MinHashLSH


def normalise(headline: str, text: str) -> str:
    """headline + "\\n" + text, NFC, with Unicode punctuation (P*) and whitespace removed.

    Combining marks (M*) stay: dropping Telugu vowel signs would make different words equal.
    """
    joined = unicodedata.normalize("NFC", f"{headline or ''}\n{text or ''}")
    return "".join(
        ch for ch in joined if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )


def shingles(norm: str, size: int) -> set[str]:
    """Character `size`-shingles; a text shorter than `size` is its own single shingle."""
    if not norm:
        return set()
    if len(norm) < size:
        return {norm}
    return {norm[i : i + size] for i in range(len(norm) - size + 1)}


def signature(norm: str, num_perm: int, size: int) -> MinHash | None:
    """The MinHash of the text, or None when nothing is left of it."""
    grams = shingles(norm, size)
    if not grams:
        return None
    sig = MinHash(num_perm=num_perm)
    sig.update_batch([g.encode("utf-8") for g in sorted(grams)])
    return sig


def candidate_pairs(
    signatures: Mapping[str, MinHash], num_perm: int, lsh_threshold: float
) -> list[tuple[str, str]]:
    """Ordered (a < b) pairs of ids the LSH puts together, sorted."""
    lsh = MinHashLSH(threshold=lsh_threshold, num_perm=num_perm)
    ids = sorted(signatures)
    for key in ids:
        lsh.insert(key, signatures[key])
    pairs: set[tuple[str, str]] = set()
    for key in ids:
        for other in lsh.query(signatures[key]):
            if other != key:
                pairs.add((key, other) if key < other else (other, key))
    return sorted(pairs)


def jaccard(a: MinHash, b: MinHash) -> float:
    """Estimated Jaccard similarity of the two shingle sets."""
    return float(a.jaccard(b))
