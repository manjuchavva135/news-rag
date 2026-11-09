"""Does a profile fit the GPU? (FR-8)

weights   = total size of the repo's safetensors files (x 0.5 for `dtype: fp8` on bf16 weights)
kv_one_seq = layers x KV heads x head dim x 2 (K and V) x 2 bytes x max_model_len
budget    = gpu_memory_gb x gpu_memory_utilization (the whole GPU for embed profiles)
fit       = weights + kv_one_seq + fit_overhead_gb <= budget

GB here means 2**30 bytes, the unit nvidia-smi and vLLM report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from newsrag import config
from newsrag.models import hub
from newsrag.models.profiles import Profile

GB = 2**30
FP8_DTYPES = ("fp8", "float8", "float8_e4m3fn")
HALF_DTYPES = ("bfloat16", "float16")


@dataclass(frozen=True)
class FitResult:
    profile: str
    weights_gb: float
    kv_one_seq_gb: float
    overhead_gb: float
    budget_gb: float

    @property
    def fits(self) -> bool:
        return self.weights_gb + self.kv_one_seq_gb + self.overhead_gb <= self.budget_gb

    def line(self) -> str:
        return (
            f"{self.profile} weights={self.weights_gb:.1f} kv_one_seq={self.kv_one_seq_gb:.1f} "
            f"budget={self.budget_gb:.1f} fit={'yes' if self.fits else 'no'}"
        )

    def numbers(self) -> str:
        return (
            f"weights={self.weights_gb:.1f} GB + kv_one_seq={self.kv_one_seq_gb:.1f} GB + "
            f"overhead={self.overhead_gb:.1f} GB > budget={self.budget_gb:.1f} GB"
        )


class ModelDoesNotFitError(RuntimeError):
    def __init__(self, result: FitResult) -> None:
        super().__init__(f"profile {result.profile} does not fit the GPU: {result.numbers()}")
        self.result = result


def _repo_files(profile: Profile) -> tuple[dict[str, int], dict]:
    """(file name -> size in bytes, config.json) from the Hub, or from the cache when offline."""
    cache_dir, token = hub.hub_args()
    try:
        from huggingface_hub import HfApi, hf_hub_download

        info = HfApi().model_info(profile.hf_id, files_metadata=True, token=token)
        sizes = {s.rfilename: s.size or 0 for s in info.siblings or []}
        cfg_path = hf_hub_download(profile.hf_id, "config.json", cache_dir=cache_dir, token=token)
    except Exception:
        snapshot = hub.local_snapshot(profile)
        if snapshot is None:
            raise
        sizes = {
            str(p.relative_to(snapshot)): p.stat().st_size
            for p in snapshot.rglob("*")
            if p.is_file()
        }
        cfg_path = snapshot / "config.json"
    return sizes, json.loads(Path(cfg_path).read_text(encoding="utf-8"))


def weights_bytes(sizes: dict[str, int]) -> int:
    def total(suffix: str) -> int:
        return sum(
            size
            for name, size in sizes.items()
            if name.endswith(suffix) and not name.startswith(("original/", "onnx/"))
        )

    return total(".safetensors") or total(".bin")


def kv_bytes_per_token(cfg: dict) -> int:
    text = cfg.get("text_config") or cfg
    layers = text["num_hidden_layers"]
    heads = text["num_attention_heads"]
    kv_heads = text.get("num_key_value_heads") or heads
    head_dim = text.get("head_dim") or text["hidden_size"] // heads
    return layers * kv_heads * head_dim * 2 * 2


def estimate_from(profile: Profile, sizes: dict[str, int], cfg: dict) -> FitResult:
    s = config.settings
    weights = weights_bytes(sizes)
    text = cfg.get("text_config") or {}
    stored = str(
        cfg.get("torch_dtype") or cfg.get("dtype") or text.get("torch_dtype") or text.get("dtype")
    )
    if profile.dtype.lower() in FP8_DTYPES and stored in HALF_DTYPES:
        weights //= 2
    kv = kv_bytes_per_token(cfg) * profile.max_model_len if profile.is_vllm else 0
    utilization = profile.gpu_memory_utilization if profile.is_vllm else 1.0
    return FitResult(
        profile=profile.name,
        weights_gb=weights / GB,
        kv_one_seq_gb=kv / GB,
        overhead_gb=s.fit_overhead_gb,
        budget_gb=s.gpu_memory_gb * utilization,
    )


def estimate(profile: Profile) -> FitResult:
    sizes, cfg = _repo_files(profile)
    return estimate_from(profile, sizes, cfg)


def require_fit(profile: Profile) -> FitResult:
    result = estimate(profile)
    if not result.fits:
        raise ModelDoesNotFitError(result)
    return result
