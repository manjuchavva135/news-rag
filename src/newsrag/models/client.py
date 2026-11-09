"""`use(profile)`: one model at a time, as an OpenAI-compatible client or an embedder (FR-4, FR-7)."""

from __future__ import annotations

import base64
import contextlib
import gc
import io
import json
import re
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from newsrag import config
from newsrag.models import fit, hub
from newsrag.models.lock import Reporter, gpu_lock, pid_alive, probe
from newsrag.models.profiles import Profile, get_profile
from newsrag.models.serve import _answers, run_server


class ModelBusyError(RuntimeError):
    """A different profile is already loaded by this process."""

    def __init__(self, held: str, wanted: str) -> None:
        super().__init__(
            f"model {held} is loaded in this process; cannot use {wanted} at the same time"
        )
        self.held = held
        self.wanted = wanted


# --- clients ---------------------------------------------------------------------------------

ImageLike = Any  # PIL.Image.Image | bytes | str | Path


def _image_url(image: ImageLike) -> str:
    """A data: URL for a PIL image, raw PNG/JPEG bytes or a path."""
    if isinstance(image, str) and image.startswith(("data:", "http://", "https://")):
        return image
    if isinstance(image, (str, Path)):
        data = Path(image).read_bytes()
    elif isinstance(image, (bytes, bytearray)):
        data = bytes(image)
    else:  # PIL image
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        data = buf.getvalue()
    mime = "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def build_messages(
    messages: str | Sequence[dict], images: Sequence[ImageLike] | None = None
) -> list[dict]:
    """Normalise to a message list; images go before the text of the last user message."""
    msgs = [{"role": "user", "content": messages}] if isinstance(messages, str) else list(messages)
    msgs = [dict(m) for m in msgs]
    if images:
        idx = max(i for i, m in enumerate(msgs) if m.get("role") == "user")
        content = msgs[idx].get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        image_parts = [{"type": "image_url", "image_url": {"url": _image_url(i)}} for i in images]
        msgs[idx]["content"] = image_parts + list(parts)
    return msgs


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_json_reply(text: str) -> Any:
    """Parse a model reply as JSON, allowing one surrounding ``` fence."""
    match = _FENCE.match(text)
    body = match.group(1) if match else text.strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model reply is not JSON ({exc}): {text[:200]!r}") from exc


class ModelClient:
    """An OpenAI-compatible client for one vLLM-served profile; `model=<profile>` on every call."""

    def __init__(self, base_url: str, profile: str) -> None:
        from openai import OpenAI

        self.base_url = base_url
        self.profile = profile
        self.raw = OpenAI(
            base_url=base_url, api_key="EMPTY", timeout=config.settings.model_request_timeout_s
        )

    def chat(
        self,
        messages: str | Sequence[dict],
        images: Sequence[ImageLike] | None = None,
        **overrides: Any,
    ) -> str:
        params = {"temperature": 0, **overrides}
        resp = self.raw.chat.completions.create(
            model=self.profile, messages=build_messages(messages, images), **params
        )
        return resp.choices[0].message.content or ""

    def chat_json(
        self,
        messages: str | Sequence[dict],
        images: Sequence[ImageLike] | None = None,
        schema: dict | None = None,
        **overrides: Any,
    ) -> Any:
        """`chat`, parsed as JSON; with `schema`, vLLM constrains the reply to it."""
        if schema is not None:
            overrides.setdefault(
                "response_format",
                {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}},
            )
        return parse_json_reply(self.chat(messages, images, **overrides))


class Embedder:
    """A sentence-transformers model for one `embed` profile."""

    def __init__(self, profile: Profile, model: Any) -> None:
        self.profile = profile
        self.model = model

    @property
    def dim(self) -> int:
        return int(self.profile.dim or 0)

    def embed(
        self, texts: str | Sequence[str], kind: str = "doc", batch_size: int = 64
    ) -> np.ndarray:
        """L2-normalised float32 vectors, one row per text, with the profile's prefix."""
        if kind not in ("doc", "query"):
            raise ValueError(f"kind must be 'doc' or 'query', not {kind!r}")
        prefix = self.profile.doc_prefix if kind == "doc" else self.profile.query_prefix
        items = [texts] if isinstance(texts, str) else list(texts)
        vectors = self.model.encode(
            [prefix + t for t in items],
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=False,
            show_progress_bar=False,
        )
        vectors = np.asarray(vectors, dtype=np.float32).reshape(len(items), -1)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-12)


# --- use() -----------------------------------------------------------------------------------


@dataclass
class _Loaded:
    profile: str
    obj: Any
    stack: contextlib.ExitStack
    depth: int = 1


_mutex = threading.Lock()
_loaded: _Loaded | None = None
_history: list[str] = []
_history_mutex = threading.Lock()


def loaded_profiles() -> list[str]:
    """Profiles started or loaded by this process, in order; a reused `models serve` is
    listed as ``"<profile> (reused)"``."""
    with _history_mutex:
        return list(_history)


def _remember(name: str) -> None:
    with _history_mutex:
        if name not in _history:
            _history.append(name)


def _running_serve(profile: str) -> str | None:
    """base_url of a live `models serve` of `profile` holding the lock, else None."""
    record = probe()
    if (
        record is not None
        and record.kind == "serve"
        and record.profile == profile
        and record.base_url
        and pid_alive(record.pid, record.pid_start)
        and _answers(record.base_url, profile)
    ):
        return record.base_url
    return None


def _load_sentence_transformer(path: Path, device: str, dtype: str) -> Any:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(str(path), device=device)
    if device == "cuda" and dtype in ("float16", "bfloat16"):
        import torch

        model = model.to(getattr(torch, dtype))
    return model


def _free(model_holder: dict) -> None:
    model_holder.clear()
    gc.collect()
    with contextlib.suppress(Exception):
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _open(profile: Profile, device: str, report: Reporter | None) -> _Loaded:
    stack = contextlib.ExitStack()
    try:
        if profile.is_vllm:
            base_url = _running_serve(profile.name)
            if base_url is not None:
                _remember(f"{profile.name} (reused)")
                return _Loaded(profile.name, ModelClient(base_url, profile.name), stack)
            hub.download(profile)
            fit.require_fit(profile)
            handle = stack.enter_context(
                gpu_lock(f"use {profile.name}", profile=profile.name, kind="use", report=report)
            )
            server = stack.enter_context(run_server(profile, lock=handle))
            obj: Any = ModelClient(server.base_url, profile.name)
        else:
            path = hub.download(profile, quiet=True)
            if device == "cuda":
                fit.require_fit(profile)
                stack.enter_context(gpu_lock(profile.name, profile=profile.name, report=report))
            holder = {"model": _load_sentence_transformer(path, device, profile.dtype)}
            stack.callback(_free, holder)
            obj = Embedder(profile, holder["model"])
        _remember(profile.name)
        return _Loaded(profile.name, obj, stack)
    except BaseException:
        stack.close()
        raise


@contextlib.contextmanager
def use(name: str, device: str = "cuda", report: Reporter | None = None) -> Iterator[Any]:
    """Open a model for the scope: a `ModelClient` (vlm/llm) or an `Embedder` (embed).

    Re-entrant in one process for the same profile (reference-counted; the model is released
    when the last scope exits); a different profile raises `ModelBusyError`. `device="cpu"` is
    for embed profiles only and takes no GPU lock.
    """
    global _loaded
    if device not in ("cuda", "cpu"):
        raise ValueError(f"device must be 'cuda' or 'cpu', not {device!r}")
    profile = get_profile(name)
    if device == "cpu" and profile.kind != "embed":
        raise ValueError(
            f"device='cpu' is only allowed for embed profiles ({name} is {profile.kind})"
        )
    with _mutex:
        if _loaded is not None:
            if _loaded.profile != name:
                raise ModelBusyError(_loaded.profile, name)
            _loaded.depth += 1
            entry = _loaded
        else:
            entry = _loaded = _open(profile, device, report)
    try:
        yield entry.obj
    finally:
        with _mutex:
            entry.depth -= 1
            if entry.depth == 0:
                _loaded = None
                entry.stack.close()
