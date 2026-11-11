"""Asking the local model whether two names are the same entity (ticket 022 FR-3).

`decide` is the one request (with the format retries); `Verdicts` is the cache file
`<work_dir>/aliases/<version>/verdicts.jsonl`, keyed by the sorted name pair.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.aliases.names import NO_EXAMPLE
from newsrag.models.client import parse_json_reply

PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "alias_v1.md"
PAIR_MARKER = "<!-- pair -->"

REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["same", "confidence"],
}


@dataclass(frozen=True)
class Verdict:
    """The model's answer for one pair. `same` is `None` when no valid reply came."""

    same: bool | None
    confidence: float | None

    @property
    def valid(self) -> bool:
        return self.same is not None


INVALID = Verdict(None, None)

# decide(type, name_a, name_b, examples_a, examples_b) -> Verdict
Decide = Callable[[str, str, str, Sequence[str], Sequence[str]], Verdict]


def prompt_text() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def prompt_hash() -> str:
    return hashlib.sha1(prompt_text().encode("utf-8")).hexdigest()[:8]


def build_messages(
    kind: str, name_a: str, name_b: str, examples_a: Sequence[str], examples_b: Sequence[str]
) -> list[dict[str, str]]:
    system, _, pair = prompt_text().partition(PAIR_MARKER)

    def lines(items: Sequence[str]) -> str:
        return "\n".join(f"- {x}" for x in items) if items else NO_EXAMPLE

    user = (
        pair.replace("<<TYPE>>", "person" if kind == "person" else "organisation")
        .replace("<<NAME_A>>", name_a)
        .replace("<<NAME_B>>", name_b)
        .replace("<<EXAMPLES_A>>", lines(examples_a))
        .replace("<<EXAMPLES_B>>", lines(examples_b))
    )
    return [
        {"role": "system", "content": system.strip()},
        {"role": "user", "content": user.strip()},
    ]


def parse_verdict(reply: str) -> Verdict:
    """The verdict of one reply; `ValueError` when it is not `{"same": bool, "confidence": 0-1}`."""
    data = parse_json_reply(reply)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")  # noqa: TRY004
    same, conf = data.get("same"), data.get("confidence")
    if not isinstance(same, bool):
        raise ValueError(f"'same' is not true or false: {same!r}")  # noqa: TRY004
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        raise ValueError(f"'confidence' is not a number from 0 to 1: {conf!r}")
    return Verdict(same, float(conf))


def decide_with(client: Any) -> Decide:
    """A `Decide` that asks `client` (temperature 0, the reply schema, `alias_format_retries`
    more tries on an unusable reply). A transport failure raises `core.TransportFailed`."""
    from newsrag.enrich import core

    def decide(
        kind: str, name_a: str, name_b: str, examples_a: Sequence[str], examples_b: Sequence[str]
    ) -> Verdict:
        s = config.settings
        messages = build_messages(kind, name_a, name_b, examples_a, examples_b)
        extra = {
            "max_tokens": s.alias_max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": REPLY_SCHEMA},
            },
        }
        for _ in range(s.alias_format_retries + 1):
            try:
                reply = core.ask(client, messages, **extra)
            except core.TransportFailed:
                raise
            except Exception:  # noqa: BLE001 - e.g. a 400: asking again would not help
                return INVALID
            try:
                return parse_verdict(reply)
            except ValueError:
                continue
        return INVALID

    return decide


class Verdicts:
    """The verdict cache (a JSON-lines file; the last line of a pair wins). A verdict made with
    another prompt text is not used: the pair is asked again."""

    def __init__(self, path: Path, prompt: str | None = None) -> None:
        self.path = Path(path)
        self.prompt = prompt or prompt_hash()
        self._data: dict[tuple[str, str, str], Verdict] = {}
        self._lock = threading.Lock()
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("prompt") != self.prompt:
                    continue
                self._data[row["type"], row["a"], row["b"]] = Verdict(
                    row["same"], row["confidence"]
                )

    def get(self, kind: str, a: str, b: str) -> Verdict | None:
        return self._data.get((kind, a, b))

    def add(self, kind: str, a: str, b: str, verdict: Verdict) -> None:
        row = {
            "type": kind,
            "a": a,
            "b": b,
            "same": verdict.same,
            "confidence": verdict.confidence,
            "prompt": self.prompt,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._data[kind, a, b] = verdict

    def __len__(self) -> int:
        return len(self._data)
