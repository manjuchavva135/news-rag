"""What of a thread goes to the model with a new question. Pure functions.

- History: the earlier questions and answers in full, newest first, until the token budget is
  used; older turns are dropped. The newest turn is always sent (its answer is cut to fit).
- Sources: a chunk keeps one number for the whole thread, so `[3]` in an earlier answer and in
  the new one is the same article. The sources cited earlier in the thread are added to the
  fresh hits of the question (newest turn first, up to a limit).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from newsrag import config
from newsrag.answer.threads import Turn

_CITE = re.compile(r"\[(\d+)\]")


@dataclass
class Source:
    n: int
    chunk_id: str
    story_id: str
    headline: str
    pub_date: str
    editions: list[str]
    page_id: str
    text: str
    carried: bool = False  # from an earlier turn of the thread, not a hit of this question

    def header(self) -> str:
        editions = ", ".join(self.editions) or "-"
        return f"[{self.n}] {self.headline} | {self.pub_date} | {editions} | page {self.page_id}"

    def block(self) -> str:
        return f"{self.header()}\n{self.text}"

    def record(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "chunk_id": self.chunk_id,
            "story_id": self.story_id,
            "headline": self.headline,
            "pub_date": self.pub_date,
            "editions": self.editions,
            "page_id": self.page_id,
            "text": self.text,
            "carried": self.carried,
        }


def est_tokens(text: str) -> int:
    """Estimated tokens of `text`: its characters / `answer_chars_per_token`, rounded up."""
    return math.ceil(len(text) / max(config.settings.answer_chars_per_token, 0.1))


@dataclass
class History:
    messages: list[dict[str, str]]  # alternating user / assistant, oldest first
    kept: int  # turns sent
    dropped: int  # older turns left out
    tokens: int  # estimated


def history(turns: Sequence[Turn], budget_tokens: int) -> History:
    """The earlier turns as chat messages, as many of the newest as fit `budget_tokens`."""
    kept: list[tuple[str, str]] = []
    used = 0
    for turn in reversed(turns):
        cost = est_tokens(turn.question) + est_tokens(turn.answer)
        answer, cut = turn.answer, False
        if used + cost > budget_tokens:
            if kept:
                break
            cut = True
            # the newest turn alone is over the budget: its answer is cut to what is left
            room = max(0, budget_tokens - est_tokens(turn.question))
            answer = answer[: int(room * config.settings.answer_chars_per_token)]
            cost = est_tokens(turn.question) + est_tokens(answer)
        kept.append((turn.question, answer))
        used += cost
        if cut:
            break
    messages: list[dict[str, str]] = []
    for question, answer in reversed(kept):
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": answer or "-"})
    return History(messages, len(kept), len(turns) - len(kept), used)


def cited_numbers(answer: str, shown: Sequence[int]) -> list[int]:
    """The source numbers the answer cites, in order of first use, limited to `shown`."""
    allowed, out = set(shown), []
    for m in _CITE.finditer(answer):
        n = int(m.group(1))
        if n in allowed and n not in out:
            out.append(n)
    return out


def _from_record(rec: Mapping[str, Any], carried: bool) -> Source:
    return Source(
        n=int(rec["n"]),
        chunk_id=str(rec["chunk_id"]),
        story_id=str(rec.get("story_id") or ""),
        headline=str(rec.get("headline") or ""),
        pub_date=str(rec.get("pub_date") or ""),
        editions=list(rec.get("editions") or []),
        page_id=str(rec.get("page_id") or ""),
        text=str(rec.get("text") or ""),
        carried=carried,
    )


def sources_for(
    hits: Sequence[Any],
    meta: Mapping[str, Mapping[str, Any]],
    turns: Sequence[Turn],
    carry: int | None = None,
) -> list[Source]:
    """The numbered sources of a new question: its hits in rank order, then up to `carry`
    sources cited in earlier turns (newest turn first) that are not among the hits."""
    s = config.settings
    carry = s.answer_carry_sources if carry is None else carry
    numbers: dict[str, int] = {}  # chunk_id -> its number in the thread
    records: dict[int, Mapping[str, Any]] = {}
    for turn in turns:
        for rec in turn.sources:
            numbers.setdefault(str(rec["chunk_id"]), int(rec["n"]))
            records.setdefault(int(rec["n"]), rec)
    next_n = max(records, default=0) + 1

    out: list[Source] = []
    fresh: set[str] = set()
    for hit in hits:
        if hit.chunk_id in fresh:
            continue
        fresh.add(hit.chunk_id)
        n = numbers.get(hit.chunk_id)
        if n is None:
            n, next_n = next_n, next_n + 1
        m = meta.get(hit.story_id, {})
        out.append(
            Source(
                n=n,
                chunk_id=hit.chunk_id,
                story_id=hit.story_id,
                headline=" ".join((hit.headline or "").split()),
                pub_date=str(hit.pub_date),
                editions=list(m.get("editions") or []),
                page_id=str(m.get("page_id") or (hit.page_ids[0] if hit.page_ids else "")),
                text=(hit.text or "")[: s.answer_source_chars],
            )
        )
    carried = 0
    for turn in reversed(turns):
        for n in turn.cited:
            rec = records.get(n)
            if carried >= carry:
                return out
            if rec is None or str(rec["chunk_id"]) in fresh:
                continue
            fresh.add(str(rec["chunk_id"]))
            out.append(_from_record(rec, carried=True))
            carried += 1
    return out
