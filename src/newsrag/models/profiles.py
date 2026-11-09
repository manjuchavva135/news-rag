"""Model profiles from `models.yaml` (FR-1)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from newsrag import config

VLLM_KINDS = ("vlm", "llm")


class Profile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    hf_id: str
    family: str
    kind: Literal["vlm", "llm", "embed"]
    dtype: str = "bfloat16"
    # vLLM kinds
    max_model_len: int | None = None
    gpu_memory_utilization: float | None = None
    extra_args: tuple[str, ...] = ()
    # embed kind
    dim: int | None = None
    query_prefix: str = ""
    doc_prefix: str = ""

    @model_validator(mode="after")
    def _check_kind_fields(self) -> Profile:
        if self.is_vllm:
            missing = [
                f for f in ("max_model_len", "gpu_memory_utilization") if not getattr(self, f)
            ]
            if missing:
                raise ValueError(f"profile {self.name}: {self.kind} needs {', '.join(missing)}")
        elif self.dim is None:
            raise ValueError(f"profile {self.name}: embed needs dim")
        return self

    @property
    def is_vllm(self) -> bool:
        return self.kind in VLLM_KINDS


class UnknownProfileError(KeyError):
    def __str__(self) -> str:
        return str(self.args[0])


def load_profiles(path: Path | None = None) -> dict[str, Profile]:
    """All profiles of `models.yaml`, by name, in file order."""
    path = Path(path) if path is not None else config.settings.models_file
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise TypeError(f"{path}: expected a top-level `profiles:` list")
    profiles: dict[str, Profile] = {}
    for entry in entries:
        profile = Profile.model_validate(entry)
        if profile.name in profiles:
            raise ValueError(f"{path}: profile {profile.name} is listed twice")
        profiles[profile.name] = profile
    return profiles


def get_profile(name: str) -> Profile:
    profiles = load_profiles()
    if name not in profiles:
        known = ", ".join(profiles) or "none"
        raise UnknownProfileError(
            f"unknown model profile {name!r} (in {config.settings.models_file}: {known})"
        )
    return profiles[name]
