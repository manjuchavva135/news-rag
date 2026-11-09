"""Shared fixture for the CPU model-runner tests: settings in tmp_path, a fake vLLM, fake profiles."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from newsrag import config
from newsrag.models import client, fit, hub

FAKE_VLLM = Path(__file__).parent / "_fake_vllm.py"

PROFILES_YAML = """\
profiles:
  - name: fake-llm
    hf_id: fake/llm
    family: qwen
    kind: llm
    max_model_len: 1024
    gpu_memory_utilization: 0.5
  - name: fake-vlm
    hf_id: fake/vlm
    family: gemma
    kind: vlm
    max_model_len: 1024
    gpu_memory_utilization: 0.5
    extra_args: ["--limit-mm-per-prompt", '{"image": 1}']
  - name: fake-embed
    hf_id: fake/embed
    family: e5
    kind: embed
    dim: 3
    query_prefix: "query: "
    doc_prefix: "passage: "
"""


# `newsrag models serve <profile>` in a subprocess, with downloads and the fit check stubbed out.
SERVE_CODE = (
    "import sys\n"
    "from newsrag.models import fit, hub\n"
    "hub.download = lambda profile, quiet=False: None\n"
    "fit.require_fit = lambda profile: None\n"
    "from newsrag.cli import app\n"
    "app(['models', 'serve', sys.argv[1]])\n"
)


def free_port_range(width: int = 10) -> int:
    """A base port such that base..base+width are currently free."""
    for _ in range(50):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            base = sock.getsockname()[1]
        if base + width < 65535 and all(_free(p) for p in range(base, base + width + 1)):
            return base
    raise RuntimeError("no free port range")


def _free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@dataclass
class ModelsEnv:
    root: Path
    fake_dir: Path
    settings: config.Settings

    def env(self) -> dict[str, str]:
        """Environment for a subprocess that should see the same settings."""
        s = self.settings
        env = dict(os.environ)
        env.update(
            NEWSRAG_GPU_LOCK_PATH=str(s.gpu_lock_path),
            NEWSRAG_LOGS_DIR=str(s.logs_dir),
            NEWSRAG_MODELS_FILE=str(s.models_file),
            NEWSRAG_VLLM_ENV_DIR=str(s.vllm_env_dir),
            NEWSRAG_MODEL_PORT=str(s.model_port),
            NEWSRAG_MODEL_PORT_MAX=str(s.model_port_max),
            NEWSRAG_MODEL_START_TIMEOUT_S=str(s.model_start_timeout_s),
            NEWSRAG_MODEL_STOP_TIMEOUT_S=str(s.model_stop_timeout_s),
            NEWSRAG_ENV_FILE="/dev/null",
            NEWSRAG_PIPELINE_FILE="/dev/null",
        )
        return env

    def starts(self) -> list[Path]:
        return sorted(self.fake_dir.glob("start-*.json"))

    def requests(self) -> list[Path]:
        return sorted(self.fake_dir.glob("req-*.json"), key=lambda p: int(p.stem.split("-")[1]))

    def run_python(self, code: str, *args: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-c", code, *args],
            env=self.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )


def wait_for(predicate, timeout_s: float = 10.0, interval_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


@pytest.fixture
def models_env(tmp_path, monkeypatch) -> ModelsEnv:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    vllm = venv / "bin" / "vllm"
    vllm.write_text(f"#!{sys.executable}\n" + FAKE_VLLM.read_text())
    vllm.chmod(0o755)
    models_file = tmp_path / "models.yaml"
    models_file.write_text(PROFILES_YAML)
    fake_dir = tmp_path / "fake"
    monkeypatch.setenv("FAKE_VLLM_DIR", str(fake_dir))
    monkeypatch.delenv("FAKE_VLLM_MODE", raising=False)
    base = free_port_range()
    settings = config.settings.model_copy(
        update={
            "gpu_lock_path": tmp_path / ".gpu.lock",
            "logs_dir": tmp_path / "logs",
            "models_file": models_file,
            "vllm_env_dir": venv,
            "model_port": base,
            "model_port_max": base + 10,
            "model_start_timeout_s": 20.0,
            "model_stop_timeout_s": 5.0,
        }
    )
    monkeypatch.setattr(config, "settings", settings)
    monkeypatch.setattr(hub, "download", lambda profile, quiet=False: tmp_path / "weights")
    monkeypatch.setattr(fit, "require_fit", lambda profile: None)
    monkeypatch.setattr(client, "_history", [])
    monkeypatch.setattr(client, "_loaded", None)
    return ModelsEnv(root=tmp_path, fake_dir=fake_dir, settings=settings)
