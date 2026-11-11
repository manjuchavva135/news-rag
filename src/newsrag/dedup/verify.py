"""`newsrag dedup verify` (ticket 017 NFR-1): does a text model agree that merged printings are
the same story?"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from newsrag import config

PROMPT = """Below are two printings of a text from a Telugu newspaper, taken from two different \
editions. Small local edits (a place name, an added or dropped sentence, a different number) are \
allowed.

Printing A, headline: {headline_a}
Printing A: {text_a}

Printing B, headline: {headline_b}
Printing B: {text_b}

Are these the same news story (local edits allowed)? Answer with one word: yes or no."""

Ask = Callable[[str], str]


def collect_pairs(pub_date: date | None = None) -> list[dict[str, Any]]:
    """Every merged pair (canonical printing, another printing) of the done `dedup` artifacts,
    with both texts."""
    from newsrag.dedup import load

    out: list[dict[str, Any]] = []
    for day, path in load.done_dedup(pub_date).items():
        art = load.load_dedup(path)
        wanted = {s["canonical_unit_id"]: s for s in art["stories"] if len(s["printings"]) > 1}
        if not wanted:
            continue
        units, _ = load.date_units(date.fromisoformat(day), load.date_page_rows(day))
        by_id = {u.unit_id: u for u in units}
        for story in art["stories"]:
            if story["canonical_unit_id"] not in wanted:
                continue
            best = by_id.get(story["canonical_unit_id"])
            if best is None:  # the inputs changed after the dedup run
                continue
            for pr in story["printings"]:
                if pr["unit_id"] == best.unit_id or pr["unit_id"] not in by_id:
                    continue
                other = by_id[pr["unit_id"]]
                out.append(
                    {
                        "pub_date": day,
                        "story_id": story["story_id"],
                        "canonical_unit_id": best.unit_id,
                        "unit_id": other.unit_id,
                        "headline_a": best.headline,
                        "text_a": best.text,
                        "headline_b": other.headline,
                        "text_b": other.text,
                    }
                )
    out.sort(key=lambda x: (x["pub_date"], x["canonical_unit_id"], x["unit_id"]))
    return out


def sample(pairs: Sequence[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """`n` pairs drawn with `seed` (all of them when there are fewer)."""
    if len(pairs) <= n:
        return list(pairs)
    return random.Random(seed).sample(list(pairs), n)


def prompt_for(pair: dict[str, Any]) -> str:
    chars = config.settings.dedup_verify_part_chars
    return PROMPT.format(
        headline_a=pair["headline_a"].strip() or "(none)",
        text_a=pair["text_a"][:chars],
        headline_b=pair["headline_b"].strip() or "(none)",
        text_b=pair["text_b"][:chars],
    )


def parse_answer(reply: str) -> str:
    """ "yes" / "no" from the reply's first word, else "unclear"."""
    words = re.findall(r"[A-Za-z]+", reply or "")
    first = words[0].lower() if words else ""
    return first if first in ("yes", "no") else "unclear"


def run_verify(
    pairs: Sequence[dict[str, Any]], n: int, seed: int, ask: Ask | None = None
) -> dict[str, Any]:
    """The report. `ask(prompt) -> reply` defaults to `dedup_verify_model` via `models.use`."""
    s = config.settings
    chosen = sample(pairs, n, seed)
    if chosen and ask is None:
        from newsrag import models

        with models.use(s.dedup_verify_model) as client:

            def ask_model(text: str) -> str:
                return client.chat(text, temperature=0, max_tokens=s.dedup_verify_max_tokens)

            return run_verify(pairs, n, seed, ask_model)

    def one(pair: dict[str, Any]) -> dict[str, Any]:
        assert ask is not None
        reply = ask(prompt_for(pair))
        return {
            "pub_date": pair["pub_date"],
            "story_id": pair["story_id"],
            "canonical_unit_id": pair["canonical_unit_id"],
            "unit_id": pair["unit_id"],
            "answer": parse_answer(reply),
            "reply": reply.strip()[:80],
        }

    with ThreadPoolExecutor(max_workers=max(1, s.dedup_verify_workers)) as pool:
        items = list(pool.map(one, chosen))
    return {
        "model": s.dedup_verify_model,
        "n_requested": n,
        "seed": seed,
        "n_pairs_available": len(pairs),
        "n": len(items),
        "yes": sum(1 for x in items if x["answer"] == "yes"),
        "no": sum(1 for x in items if x["answer"] == "no"),
        "unclear": sum(1 for x in items if x["answer"] == "unclear"),
        "items": items,
    }
