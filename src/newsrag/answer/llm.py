"""The two answer providers, both with reasoning.

- `LocalLLM`: a vLLM profile behind `models.ModelClient`. A thinking model replies with its
  reasoning in `reasoning_content` or as `...</think>` in front of the answer; both are split
  off. A model that cannot think ignores the switch and just answers.
- `ClaudeLLM`: Claude on Bedrock's native Anthropic invoke endpoint (bedrock-runtime, a Bedrock
  API key as bearer token: the path the old system uses; the account has no other endpoint in
  its region), with extended thinking.

`complete(system, messages, max_tokens, thinking)` returns a `Reply`; `AnswerUnavailable` when
the provider cannot answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from newsrag import config

PROVIDERS = ("local", "claude")
CLAUDE_THINKING = ("budget", "adaptive", "off")


class AnswerUnavailable(RuntimeError):
    """The provider is not configured, unreachable, or returned no answer."""


@dataclass
class Reply:
    text: str
    reasoning: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class LLM(Protocol):
    provider: str
    model: str

    def complete(
        self, system: str, messages: Sequence[dict], max_tokens: int, thinking: bool
    ) -> Reply: ...


def split_reasoning(content: str) -> tuple[str, str]:
    """(reasoning, answer) of a reply that may start with `<think>...</think>` (the opening tag
    is missing with some chat templates)."""
    head, sep, tail = (content or "").partition("</think>")
    if not sep:
        if head.lstrip().startswith("<think>"):  # cut off while still reasoning
            return head.replace("<think>", "").strip(), ""
        return "", head.strip()
    return head.replace("<think>", "").strip(), tail.strip()


class LocalLLM:
    provider = "local"

    def __init__(self, client: Any) -> None:
        self.client = client  # a models.ModelClient
        self.model = client.profile

    def complete(
        self, system: str, messages: Sequence[dict], max_tokens: int, thinking: bool
    ) -> Reply:
        s = config.settings
        resp = self.client.raw.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, *messages],
            max_tokens=max_tokens,
            temperature=s.answer_thinking_temperature if thinking else s.answer_temperature,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": thinking},
                "repetition_penalty": s.answer_repetition_penalty,
            },
        )
        msg = resp.choices[0].message
        reasoning, text = split_reasoning(msg.content or "")
        reasoning = (getattr(msg, "reasoning_content", None) or reasoning or "").strip()
        if not text:
            raise AnswerUnavailable(
                f"{self.model} returned no answer (finish_reason "
                f"{resp.choices[0].finish_reason}): raise answer_max_tokens if it only reasoned"
            )
        usage = resp.usage
        return Reply(
            text,
            reasoning,
            {
                "input_tokens": getattr(usage, "prompt_tokens", None),
                "output_tokens": getattr(usage, "completion_tokens", None),
            },
        )


def claude_payload(
    system: str, messages: Sequence[dict], max_tokens: int, thinking: bool
) -> dict[str, Any]:
    """The invoke body. No sampling parameters: thinking does not allow them and the newer
    models reject them."""
    s = config.settings
    mode = s.answer_claude_thinking if thinking else "off"
    if mode not in CLAUDE_THINKING:
        raise AnswerUnavailable(
            f"answer_claude_thinking must be one of {', '.join(CLAUDE_THINKING)}, not {mode!r}"
        )
    payload: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
    }
    if mode == "budget":  # max_tokens covers the reasoning and the answer
        payload["max_tokens"] = max_tokens + s.answer_claude_thinking_budget
        payload["thinking"] = {"type": "enabled", "budget_tokens": s.answer_claude_thinking_budget}
    elif mode == "adaptive":
        payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        payload["output_config"] = {"effort": s.answer_claude_effort}
    return payload


def parse_claude(data: dict[str, Any]) -> Reply:
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    reasoning = "\n".join(
        b.get("thinking", "") for b in blocks if b.get("type") == "thinking"
    ).strip()
    stop = data.get("stop_reason")
    if stop == "refusal":
        raise AnswerUnavailable("the model declined to answer this question")
    if not text:
        raise AnswerUnavailable(f"the model returned no answer (stop_reason {stop})")
    usage = data.get("usage") or {}
    return Reply(
        text,
        reasoning,
        {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
    )


class ClaudeLLM:
    provider = "claude"

    def __init__(self) -> None:
        s = config.settings
        if s.bedrock_api_key is None:
            raise AnswerUnavailable(
                "provider claude needs a Bedrock API key: set AWS_BEARER_TOKEN_BEDROCK in .env"
            )
        self.model = s.answer_claude_model
        self._key = s.bedrock_api_key.get_secret_value()
        self._url = f"{s.answer_claude_base_url.rstrip('/')}/model/{self.model}/invoke"

    def complete(
        self, system: str, messages: Sequence[dict], max_tokens: int, thinking: bool
    ) -> Reply:
        import httpx

        payload = claude_payload(system, messages, max_tokens, thinking)
        try:
            resp = httpx.post(
                self._url,
                headers={"Authorization": f"Bearer {self._key}"},
                json=payload,
                timeout=config.settings.answer_claude_timeout_s,
            )
        except httpx.HTTPError as e:
            raise AnswerUnavailable(f"Bedrock unreachable: {type(e).__name__}: {e}") from e
        if resp.status_code != 200:
            raise AnswerUnavailable(f"Bedrock returned {resp.status_code}: {resp.text[:300]}")
        return parse_claude(resp.json())
