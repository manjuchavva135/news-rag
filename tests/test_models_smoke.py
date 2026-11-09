"""Real models on the GPU (ticket 006, E-2 / E-5). Needs weights, envs/vllm and an idle GPU."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.cli import app
from newsrag.models import lock, use

pytestmark = pytest.mark.gpu

NEWSRAG = Path(sys.executable).parent / "newsrag"
SERVE_PROFILE = "qwen3-30b-a3b"


def compute_pids() -> list[int]:
    out = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [int(p) for p in out.split()]


def _start_serve(profile: str) -> tuple[subprocess.Popen, list[str]]:
    """`newsrag models serve <profile>` in a subprocess; returns it and its output lines."""
    proc = subprocess.Popen(
        [str(NEWSRAG), "models", "serve", profile],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []

    def pump():
        lines.extend(proc.stdout)

    threading.Thread(target=pump, daemon=True).start()
    return proc, lines


def test_reuse_running_serve():
    proc, lines = _start_serve(SERVE_PROFILE)
    try:
        deadline = time.monotonic() + config.settings.model_start_timeout_s + 600
        while not any(line.startswith(f"serving {SERVE_PROFILE}") for line in lines):
            assert proc.poll() is None, "".join(lines)
            assert time.monotonic() < deadline, "serve did not start"
            time.sleep(2)
        record = lock.read_record()
        pids_before = compute_pids()
        with use(SERVE_PROFILE) as model:
            assert model.base_url == record.base_url
            assert "OK" in model.chat("Reply with the word OK.", max_tokens=16)
            assert compute_pids() == pids_before  # no second vLLM
        assert proc.poll() is None  # the server was not stopped
        assert lock.group_alive(record.pgid)
        assert lock.probe().holder == f"models serve {SERVE_PROFILE}"
    finally:
        proc.terminate()
        proc.wait(timeout=180)
    assert lock.probe() is None


@pytest.mark.parametrize(
    ("profile", "expect"),
    [
        ("qwen3-vl-32b", "news 2026"),
        ("gemma3-27b", "news 2026"),
        ("qwen3-30b-a3b", "ok"),
        ("bge-m3", "dim=1024"),
    ],
)
def test_smoke_profile(profile, expect):
    result = CliRunner().invoke(app, ["models", "smoke", profile])
    assert result.exit_code == 0, result.output
    assert expect in result.output.lower()
    assert "latency=" in result.output
    assert lock.probe() is None
