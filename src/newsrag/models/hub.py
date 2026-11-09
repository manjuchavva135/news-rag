"""Hugging Face downloads into `settings.hf_home` (FR-2, FR-6)."""

from __future__ import annotations

import sys
from pathlib import Path

from newsrag import config
from newsrag.models.profiles import Profile

# Never needed by vLLM or sentence-transformers; `*.bin` is skipped when safetensors exist.
IGNORE_PATTERNS = ["original/*", "onnx/*", "*.onnx", "*.pth", "*.pt", "*.msgpack", "*.h5", "*.ot"]


def hub_args() -> tuple[Path, str | None]:
    """(cache_dir, token) taken from `settings.subprocess_env()` so HF_HOME/HF_TOKEN match."""
    env = config.settings.subprocess_env()
    return Path(env["HF_HOME"]) / "hub", env.get("HF_TOKEN")


def _ignore_patterns(hf_id: str, token: str | None) -> list[str]:
    from huggingface_hub import HfApi

    files = HfApi().list_repo_files(hf_id, token=token)
    patterns = list(IGNORE_PATTERNS)
    if any(f.endswith(".safetensors") for f in files):
        patterns.append("*.bin")
    return patterns


def local_snapshot(profile: Profile) -> Path | None:
    """The cached snapshot of a profile, or None when it was never downloaded."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    cache_dir, token = hub_args()
    try:
        return Path(
            snapshot_download(
                profile.hf_id, cache_dir=cache_dir, token=token, local_files_only=True
            )
        )
    except LocalEntryNotFoundError:
        return None


def download(profile: Profile, *, quiet: bool = False) -> Path:
    """Download (or verify) a profile's weights; return the snapshot directory.

    Without network access an already-complete cache is used as it is.
    """
    from huggingface_hub import snapshot_download

    cache_dir, token = hub_args()
    try:
        patterns = _ignore_patterns(profile.hf_id, token)
        path = snapshot_download(
            profile.hf_id,
            cache_dir=cache_dir,
            token=token,
            ignore_patterns=patterns,
            max_workers=8,
            tqdm_class=_silent_tqdm() if quiet else None,
        )
    except Exception as exc:  # network down, hub unreachable: fall back to the cache
        cached = local_snapshot(profile)
        if cached is None:
            raise
        print(f"using cached {profile.hf_id} ({type(exc).__name__}: {exc})", file=sys.stderr)
        return cached
    return Path(path)


def _silent_tqdm():
    from tqdm.auto import tqdm

    class Silent(tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)

    return Silent
