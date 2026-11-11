"""`newsrag stitch verify` (ticket 016 NFR-1): does a text model agree that linked parts belong
to the same story?"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from newsrag import config

PROMPT = """Below are two excerpts from a Telugu newspaper. Part 1 is the headline and the end \
of the first part of an article. Part 2 is the beginning of a text on another page that is \
supposed to be the rest of that article.

Part 1 (headline): {headline}
Part 1 (end): ...{source}

Part 2 (beginning): {continuation}...

Are these two parts of the same news story? Answer with one word: yes or no."""

Ask = Callable[[str], str]


def collect_links(pub_date: date | None = None) -> list[dict[str, Any]]:
    """Every link of the done `stitch` artifacts (of one date, or of all), with the texts."""
    from newsrag.stitch import load
    from newsrag.stitch.merge import link_pairs

    patterns = load.patterns()
    out: list[dict[str, Any]] = []
    for day, path in load.done_stitch(pub_date).items():
        pages = load.date_pages(date.fromisoformat(day))
        out.extend(
            link_pairs(pages, load.group_final_artifacts(pages), load.load_stitch(path), patterns)
        )
    out.sort(key=lambda x: (x["pub_date"], x["source_article_id"], x["continuation_article_id"]))
    return out


def sample(links: Sequence[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """`n` links drawn with `seed` (all of them when there are fewer)."""
    if len(links) <= n:
        return list(links)
    return random.Random(seed).sample(list(links), n)


def prompt_for(link: dict[str, Any]) -> str:
    chars = config.settings.stitch_verify_part_chars
    return PROMPT.format(
        headline=link["source_headline"].strip() or "(none)",
        source=link["source_text"][-chars:],
        continuation=link["continuation_text"][:chars],
    )


def parse_answer(reply: str) -> str:
    """ "yes" / "no" from the reply's first word, else "unclear"."""
    words = re.findall(r"[A-Za-z]+", reply or "")
    first = words[0].lower() if words else ""
    return first if first in ("yes", "no") else "unclear"


def run_verify(
    links: Sequence[dict[str, Any]], n: int, seed: int, ask: Ask | None = None
) -> dict[str, Any]:
    """The report. `ask(prompt) -> reply` defaults to `stitch_verify_model` via `models.use`."""
    s = config.settings
    chosen = sample(links, n, seed)
    if chosen and ask is None:
        from newsrag import models

        with models.use(s.stitch_verify_model) as client:

            def ask_model(text: str) -> str:
                return client.chat(text, temperature=0, max_tokens=s.stitch_verify_max_tokens)

            return run_verify(links, n, seed, ask_model)

    def one(link: dict[str, Any]) -> dict[str, Any]:
        assert ask is not None
        reply = ask(prompt_for(link))
        return {
            "pub_date": link["pub_date"],
            "source_article_id": link["source_article_id"],
            "continuation_article_id": link["continuation_article_id"],
            "method": link["method"],
            "score": link["score"],
            "answer": parse_answer(reply),
            "reply": reply.strip()[:80],
        }

    with ThreadPoolExecutor(max_workers=max(1, config.settings.stitch_verify_workers)) as pool:
        items = list(pool.map(one, chosen))
    return {
        "model": s.stitch_verify_model,
        "n_requested": n,
        "seed": seed,
        "n_links_available": len(links),
        "n": len(items),
        "yes": sum(1 for x in items if x["answer"] == "yes"),
        "no": sum(1 for x in items if x["answer"] == "no"),
        "unclear": sum(1 for x in items if x["answer"] == "unclear"),
        "items": items,
    }
