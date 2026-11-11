"""`newsrag enrich verify-core` (ticket 019 NFR-1): does a second model agree with the extracted
category and names of a random sample of enriched stories?"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from newsrag import config
from newsrag.enrich import core
from newsrag.models.client import parse_json_reply

Ask = Callable[[str], str]


def collect_stories(pub_date: str | None = None) -> list[dict[str, Any]]:
    """Every story of the done `enrich_core` artifacts (of one date, or all), with its text from
    the date's `dedup` artifact, sorted by (date, story id)."""
    from newsrag import enrich
    from newsrag.dedup import load

    out: list[dict[str, Any]] = []
    for day, path in enrich.done_artifacts("enrich_core", pub_date).items():
        records = enrich.read_artifact(path).get("stories") or {}
        dedup_path = load.done_dedup(date.fromisoformat(day)).get(day)
        if dedup_path is None:
            continue
        texts = {s["story_id"]: s for s in load.load_dedup(dedup_path)["stories"]}
        for story_id, record in sorted(records.items()):
            story = texts.get(story_id)
            if story is None:  # the dedup run changed after the enrichment
                continue
            out.append(
                {
                    "pub_date": day,
                    "story_id": story_id,
                    "headline": story.get("headline") or "",
                    "text": story.get("text") or "",
                    "category": record["category"],
                    "people": record["people"],
                    "organisations": record["organisations"],
                }
            )
    return out


def sample(stories: Sequence[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """`n` stories drawn with `seed` (all of them when there are fewer)."""
    if len(stories) <= n:
        return list(stories)
    return random.Random(seed).sample(list(stories), n)


def judge_prompt(story: dict[str, Any]) -> str:
    text = core.JUDGE_PROMPT_FILE.read_text(encoding="utf-8")

    def dump(items: list[dict[str, Any]]) -> str:
        return json.dumps(items, ensure_ascii=False) if items else "(none)"

    return (
        text.replace("<<RULES>>", core.category_rules())
        .replace("<<CATEGORY>>", story["category"])
        .replace("<<PEOPLE>>", dump(story["people"]))
        .replace("<<ORGANISATIONS>>", dump(story["organisations"]))
        .replace("<<STORY>>", core.model_input(story["headline"], story["text"]))
    )


def parse_verdict(reply: str) -> tuple[bool, bool]:
    """(category_ok, names_ok) from the judge's reply; an unparseable reply is (False, False)."""
    try:
        data = parse_json_reply(reply)
    except ValueError:
        return False, False
    if not isinstance(data, dict):
        return False, False
    return data.get("category_ok") is True, data.get("names_ok") is True


def run_verify(
    stories: Sequence[dict[str, Any]], n: int, seed: int, ask: Ask | None = None
) -> dict[str, Any]:
    """The report. `ask(prompt) -> reply` defaults to `enrich_verify_model` via `models.use`."""
    s = config.settings
    chosen = sample(stories, n, seed)
    if chosen and ask is None:
        from newsrag import models

        with models.use(s.enrich_verify_model) as client:

            def ask_model(prompt: str) -> str:
                return client.chat(prompt, temperature=0, max_tokens=s.enrich_verify_max_tokens)

            return run_verify(stories, n, seed, ask_model)

    def one(story: dict[str, Any]) -> dict[str, Any]:
        assert ask is not None
        reply = ask(judge_prompt(story))
        category_ok, names_ok = parse_verdict(reply)
        return {
            "pub_date": story["pub_date"],
            "story_id": story["story_id"],
            "category": story["category"],
            "people": [p["name"] for p in story["people"]],
            "organisations": [o["name"] for o in story["organisations"]],
            "category_ok": category_ok,
            "names_ok": names_ok,
            "reply": reply.strip()[:120],
        }

    with ThreadPoolExecutor(max_workers=max(1, s.enrich_verify_workers)) as pool:
        items = list(pool.map(one, chosen))
    return {
        "model": s.enrich_verify_model,
        "n_requested": n,
        "seed": seed,
        "n_stories_available": len(stories),
        "n": len(items),
        "category_ok": sum(1 for x in items if x["category_ok"]),
        "names_ok": sum(1 for x in items if x["names_ok"]),
        "items": items,
    }
