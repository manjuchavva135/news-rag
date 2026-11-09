"""The separate vLLM venv `envs/vllm/` (FR-2, D-3)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from packaging.version import InvalidVersion, Version

from newsrag import config

REPORT_SCRIPT = (
    "import torch, vllm; "
    "print('vllm', vllm.__version__); "
    "print('device capability', tuple(torch.cuda.get_device_capability()))"
)


class InstallError(RuntimeError):
    pass


def env_python() -> Path:
    return config.settings.vllm_env_dir / "bin" / "python"


def installed_version() -> str | None:
    """vLLM's version in the venv, or None when the venv or vLLM is missing/broken."""
    py = env_python()
    if not py.exists():
        return None
    result = subprocess.run(
        [str(py), "-c", "import importlib.metadata as m; print(m.version('vllm'))"],
        capture_output=True,
        text=True,
        env=config.settings.subprocess_env(),
        check=False,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def is_current(version: str | None) -> bool:
    if not version:
        return False
    try:
        return Version(version) >= Version(config.settings.vllm_min_version)
    except InvalidVersion:
        return False


def _uv() -> str:
    uv = os.environ.get("UV") or shutil.which(config.settings.uv_bin)
    if not uv:
        raise InstallError(f"uv not found (setting uv_bin = {config.settings.uv_bin!r})")
    return uv


def _run(cmd: list[str], uv: str) -> None:
    env = config.settings.subprocess_env()
    env.pop("VIRTUAL_ENV", None)  # never install into the main venv by accident
    # `vllm download-kernels` installs its wheels with the uv it finds on PATH
    env["PATH"] = os.pathsep.join([str(Path(uv).parent), env.get("PATH", "")])
    if subprocess.run(cmd, env=env, check=False).returncode != 0:
        raise InstallError(f"failed: {' '.join(cmd)}")


def install() -> tuple[bool, str]:
    """Create/upgrade the venv unless it already has a current vLLM; (kept, report text)."""
    s = config.settings
    kept = is_current(installed_version())
    uv = _uv()
    if not kept:
        _run([uv, "venv", "--clear", "--python", "3.12", str(s.vllm_env_dir)], uv)
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(env_python()),
                s.vllm_spec,
                f"--torch-backend={s.vllm_torch_backend}",
            ],
            uv,
        )
    # Precompiled FlashInfer kernels (flashinfer-cubin + jit-cache): without them, and with no
    # nvcc on the host, vLLM's sampler fails at startup. A no-op when they are already there.
    _run([str(s.vllm_env_dir / "bin" / "vllm"), "download-kernels"], uv)
    result = subprocess.run(
        [str(env_python()), "-c", REPORT_SCRIPT],
        capture_output=True,
        text=True,
        env=s.subprocess_env(),
        check=False,
    )
    if result.returncode != 0:
        raise InstallError(f"vLLM venv check failed:\n{result.stderr.strip()[-2000:]}")
    return kept, result.stdout.strip()
