"""One turn of a thread: rewrite the follow-up into a standalone query, search, answer with the
thread's history and sources, store the turn.

`session(provider)` opens the model (and, next to a local LLM, the query embedder on the CPU,
because the GPU holds one model at a time) for a run of turns; `ask(...)` is one turn. Tests
pass their own `llm`, `store`, `search_fn`, `story_meta` and `covered_years`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.answer import context as ctx
from newsrag.answer import llm as llm_mod
from newsrag.answer.threads import Turn

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
SYSTEM_PROMPT = PROMPTS / "answer_chat_v1.md"
REWRITE_PROMPT = PROMPTS / "answer_rewrite_v1.md"
NO_SOURCES = "(no sources were found for this question)"


@dataclass
class Answer:
    thread_id: str
    turn_no: int
    question: str
    search_query: str
    answer: str
    reasoning: str  # shown on request, never stored
    sources: list[ctx.Source]
    cited: list[int]
    history_turns: int  # earlier turns sent to the model
    dropped_turns: int  # earlier turns left out (over answer_history_tokens)
    usage: dict[str, Any] = field(default_factory=dict)


# --- prompts ------------------------------------------------------------------------------------


def system_prompt(today: date, covered_years: Sequence[int]) -> str:
    years = ", ".join(str(y) for y in covered_years) or "-"
    template = SYSTEM_PROMPT.read_text("utf-8")
    return template.replace("{today}", today.isoformat()).replace("{covered_years}", years)


def answer_language(question: str) -> str:
    """The language line of a question: Telugu script decides for Telugu; a question in Latin
    letters is English unless the model sees that it is Telugu typed in Latin letters."""
    letters = [ch for ch in question if ch.isalpha()]
    telugu = sum(1 for ch in letters if "\u0c00" <= ch <= "\u0c7f")
    if letters and telugu / len(letters) >= 0.5:
        return "Telugu"
    return "English (Telugu if the question is Telugu typed in Latin letters)"


def question_message(question: str, sources: Sequence[ctx.Source]) -> str:
    blocks = "\n\n".join(src.block() for src in sources) or NO_SOURCES
    return (
        f"Question:\n{question}\n\nSources:\n{blocks}\n\n"
        f"Write the answer in {answer_language(question)}; the sources being in Telugu does "
        "not change that."
    )


def rewrite_prompt(question: str, messages: Sequence[Mapping[str, str]]) -> str:
    lines = [f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in messages]
    return (
        REWRITE_PROMPT.read_text("utf-8")
        .replace("{conversation}", "\n".join(lines))
        .replace("{question}", question)
    )


def rewrite_query(question: str, turns: Sequence[Turn], llm: llm_mod.LLM) -> str:
    """The standalone search query of a follow-up; the question as typed on the first turn and
    whenever the rewrite fails or looks wrong (empty, several lines, too long)."""
    s = config.settings
    if not turns:
        return question
    recent = ctx.history(turns, s.answer_rewrite_history_tokens)
    try:
        reply = llm.complete(
            "You rewrite questions into search queries.",
            [{"role": "user", "content": rewrite_prompt(question, recent.messages)}],
            max_tokens=s.answer_rewrite_max_tokens,
            thinking=False,
        )
    except Exception:  # noqa: BLE001 - a failed rewrite must not block the answer
        return question
    query = reply.text.strip().strip("\"'`")
    if not query or "\n" in query or len(query) > s.answer_rewrite_max_chars:
        return question
    return query


# --- one turn -----------------------------------------------------------------------------------


def ask(
    question: str,
    thread_id: str | None = None,
    *,
    llm: llm_mod.LLM,
    store: Any = None,
    search_fn: Callable[..., list[Any]] | None = None,
    story_meta: Callable[[Sequence[str]], dict[str, dict[str, Any]]] | None = None,
    covered_years: Sequence[int] | None = None,
    today: date | None = None,
) -> Answer:
    """Answer `question` in thread `thread_id` (a new thread when None) and store the turn."""
    s = config.settings
    question = question.strip()
    if not question:
        raise ValueError("empty question")
    if store is None:
        from newsrag.answer.threads import PgStore

        store = PgStore()
    turns = store.load_turns(thread_id) if thread_id else []
    if search_fn is None:
        from newsrag import search as search_mod

        search_fn = search_mod.search
    if story_meta is None or covered_years is None:
        from newsrag.evals import customer

        story_meta = story_meta or customer.load_story_meta
        covered_years = customer.load_covered_years() if covered_years is None else covered_years

    query = rewrite_query(question, turns, llm)
    hits = list(search_fn(query, k=s.answer_k))
    sources = ctx.sources_for(hits, story_meta(sorted({h.story_id for h in hits})), turns)
    past = ctx.history(turns, s.answer_history_tokens)
    reply = llm.complete(
        system_prompt(today or datetime.now(UTC).date(), covered_years),
        [*past.messages, {"role": "user", "content": question_message(question, sources)}],
        max_tokens=s.answer_max_tokens,
        thinking=s.answer_thinking,
    )
    cited = ctx.cited_numbers(reply.text, [src.n for src in sources])
    if thread_id is None:
        thread_id = store.create_thread(question)
    turn_no = store.add_turn(
        thread_id,
        Turn(
            turn_no=0,
            question=question,
            answer=reply.text,
            search_query=query,
            sources=[src.record() for src in sources],
            cited=cited,
            provider=llm.provider,
            model=llm.model,
            usage=reply.usage,
        ),
    )
    return Answer(
        thread_id=thread_id,
        turn_no=turn_no,
        question=question,
        search_query=query,
        answer=reply.text,
        reasoning=reply.reasoning,
        sources=sources,
        cited=cited,
        history_turns=past.kept,
        dropped_turns=past.dropped,
        usage=reply.usage,
    )


# --- the models of a run ------------------------------------------------------------------------


def local_profile() -> str:
    s = config.settings
    return s.answer_chat_model or s.answer_model


def embed_device(provider: str) -> str:
    device = config.settings.answer_embed_device
    if device == "auto":
        return "cpu" if provider == "local" else "cuda"
    if device not in ("cpu", "cuda"):
        raise ValueError(f"answer_embed_device must be auto, cpu or cuda, not {device!r}")
    return device


@contextlib.contextmanager
def session(provider: str | None = None) -> Iterator[llm_mod.LLM]:
    """The LLM of `provider` (default `answer_provider`) for a run of `ask` calls.

    local: `models.use(<profile>)` (a running `newsrag models serve <profile>` is reused,
    else vLLM is started and stopped here). The query embedder runs on the CPU next to it."""
    from newsrag import models
    from newsrag import search as search_mod
    from newsrag.search.hybrid import profile_of
    from newsrag.warehouse import db as wdb

    provider = provider or config.settings.answer_provider
    if provider not in llm_mod.PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(llm_mod.PROVIDERS)}")
    device = embed_device(provider)
    with contextlib.ExitStack() as stack:
        llm: llm_mod.LLM
        if provider == "claude":
            llm = llm_mod.ClaudeLLM()
        else:
            llm = llm_mod.LocalLLM(stack.enter_context(models.use(local_profile())))
        stack.callback(search_mod.close)
        if device == "cpu":
            generation = wdb.active_generation()
            if generation is None:
                raise search_mod.StoreNotConfigured(
                    "no active generation: run 'newsrag stage run index' first"
                )
            profile = profile_of(generation)
            embedder = stack.enter_context(models.cpu_embedder(profile))
            stack.enter_context(search_mod.use_embedder(profile, embedder))
        yield llm
