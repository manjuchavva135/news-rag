"""Romanized Telugu -> Telugu script for search (ticket 034 FR-2).

`to_telugu(query, ask)` asks `enrich_model` (through `ask(prompt) -> reply`) to rewrite a query
typed in Latin letters in Telugu script, so BM25 can match the Telugu-script stories. The model
is never held together with the query embedder: callers transliterate first, release the model,
then search.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.embed.records import script_shares

PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "translit_v1.md"
THINK_OFF = {"chat_template_kwargs": {"enable_thinking": False}}
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "telugu",
        "schema": {
            "type": "object",
            "properties": {"telugu": {"type": "string"}},
            "required": ["telugu"],
            "additionalProperties": False,
        },
    },
}

Ask = Callable[[str], str]


@dataclass
class Translit:
    query: str  # what was typed
    text: str  # the Telugu-script query, or `query` when every try failed
    failed: bool


def prompt(query: str) -> str:
    return PROMPT_FILE.read_text("utf-8").replace("{query}", query)


def parse(reply: str) -> str | None:
    """The Telugu-script text, None when the reply is unusable."""
    from newsrag.models.client import parse_json_reply

    try:
        data = parse_json_reply(reply)
    except ValueError:
        return None
    text = data.get("telugu") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        return None
    telugu, _ = script_shares(text)
    if telugu < config.settings.retrieval_lang_share:
        return None
    return " ".join(text.split())


def to_telugu(query: str, ask: Ask) -> Translit:
    """At most `retrieval_max_tries` calls in total; then the query is kept as typed."""
    for _ in range(max(1, config.settings.retrieval_max_tries)):
        try:
            text = parse(ask(prompt(query)))
        except Exception:  # noqa: BLE001, S112 - a failed request counts as a try
            continue
        if text is not None:
            return Translit(query, text, False)
    return Translit(query, query, True)


def model_ask(client: Any) -> Ask:
    s = config.settings

    def ask(text: str) -> str:
        return client.chat(
            text,
            temperature=0,
            max_tokens=s.retrieval_max_tokens,
            response_format=RESPONSE_FORMAT,
            extra_body=THINK_OFF,
        )

    return ask


@contextlib.contextmanager
def enrich_ask() -> Iterator[Ask]:
    """`ask` on `enrich_model` for the scope (the model is released when it ends)."""
    from newsrag import models

    with models.use(config.settings.enrich_model) as client:
        yield model_ask(client)


def to_telugu_many(queries: Sequence[str], ask: Ask | None = None) -> list[Translit]:
    """Every query transliterated, in order; opens `enrich_model` only when `ask` is None."""
    if not queries:
        return []
    with contextlib.ExitStack() as stack:
        if ask is None:
            ask = stack.enter_context(enrich_ask())
        workers = max(1, config.settings.retrieval_concurrency)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(lambda q: to_telugu(q, ask), queries))
