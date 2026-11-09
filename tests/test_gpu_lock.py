"""GPU lock and vLLM start/stop, CPU only (ticket 006: FR-3, FR-4, FR-7, NFR-2)."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time

import pytest
from _models_env import SERVE_CODE, wait_for
from typer.testing import CliRunner

from newsrag.cli import app
from newsrag.models import lock
from newsrag.models.lock import LockRecord, gpu_lock, probe, read_record
from newsrag.models.profiles import get_profile
from newsrag.models.serve import ModelStartError, pick_port, run_server

runner = CliRunner()


def dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def write_record(path, **fields) -> None:
    path.write_text(json.dumps(LockRecord(**fields).__dict__))


def test_gpu_lock_holder_recorded(models_env):
    path = models_env.settings.gpu_lock_path
    with gpu_lock("ocr test") as handle:
        record = read_record(path)
        assert record.holder == "ocr test"
        assert record.pid == os.getpid()
        assert record.kind == "lock"
        assert handle.record.started
        handle.update(pgid=1234, base_url="http://127.0.0.1:1/v1")
        assert read_record(path).pgid == 1234
        result = runner.invoke(app, ["models", "status"])
        assert result.exit_code == 0
        assert "holder: ocr test" in result.output
        assert f"pid: {os.getpid()}" in result.output
    assert not path.exists()  # the record is deleted on release
    assert probe(path) is None


def test_status_free_after_release(models_env):
    path = models_env.settings.gpu_lock_path
    with gpu_lock("first"):
        pass
    assert runner.invoke(app, ["models", "status"]).output.strip() == "GPU free"
    # a leftover record from a crashed holder does not make the GPU look busy
    write_record(path, holder="crashed", pid=dead_pid())
    result = runner.invoke(app, ["models", "status"])
    assert result.output.strip() == "GPU free"


def test_reentrant_gpu_lock(models_env):
    path = models_env.settings.gpu_lock_path
    with gpu_lock("outer"):
        fd = lock._held.fd
        with gpu_lock("inner"):
            assert lock._held.fd == fd and lock._held.depth == 2
            assert read_record(path).holder == "outer"
        assert probe(path).holder == "outer"  # still held after the inner scope
    assert probe(path) is None
    assert lock._held is None


def test_second_process_waits_for_lock(models_env):
    code = (
        "from newsrag.models import gpu_lock\n"
        "with gpu_lock('second'):\n"
        "    print('acquired', flush=True)\n"
    )
    with gpu_lock("first holder"):
        proc = models_env.run_python(code)
        line = proc.stdout.readline()
        assert "waiting for GPU lock held by first holder" in line
        time.sleep(1.0)
        assert proc.poll() is None  # still waiting
    out, _ = proc.communicate(timeout=20)
    assert proc.returncode == 0
    assert "acquired" in out


def test_dead_holder_taken_over_quickly(models_env):
    path = models_env.settings.gpu_lock_path
    write_record(path, holder="gone", pid=dead_pid(), pgid=None)
    t0 = time.monotonic()
    with gpu_lock("taker"):
        assert time.monotonic() - t0 < 5
        assert read_record(path).holder == "taker"


def test_dead_holder_process_group_killed(models_env):
    path = models_env.settings.gpu_lock_path
    child = subprocess.Popen(["sleep", "1000"], start_new_session=True)
    try:
        write_record(path, holder="gone", pid=dead_pid(), pgid=child.pid)
        messages = []
        t0 = time.monotonic()
        with gpu_lock("taker", report=messages.append):
            assert child.wait(timeout=5) == -signal.SIGTERM
        assert time.monotonic() - t0 < 5
        assert any("is dead: killing its process group" in m for m in messages)
    finally:
        if child.poll() is None:
            child.kill()


def test_dead_holder_sigkilled_leaves_no_orphan(models_env):
    """A real holder killed with SIGKILL while its 'vLLM' group runs (NFR-2)."""
    code = (
        "import subprocess, time\n"
        "from newsrag.models import gpu_lock\n"
        "with gpu_lock('doomed') as lk:\n"
        "    child = subprocess.Popen(['sleep', '1000'], start_new_session=True)\n"
        "    lk.update(pgid=child.pid)\n"
        "    print(child.pid, flush=True)\n"
        "    time.sleep(1000)\n"
    )
    holder = models_env.run_python(code)
    orphan = int(holder.stdout.readline())
    holder.kill()
    holder.wait()
    assert lock.group_alive(orphan)  # the orphan survives its holder ...
    with gpu_lock("next"):
        assert wait_for(lambda: not lock.group_alive(orphan), 5)  # ... until the next taker


def test_start_failure_releases_lock(models_env, monkeypatch):
    monkeypatch.setenv("FAKE_VLLM_MODE", "exit")
    result = runner.invoke(app, ["models", "serve", "fake-llm"])
    assert result.exit_code == 1
    assert "vllm failed to start fake-llm:" in result.output
    assert "boom: fake vllm failed" in result.output  # the log tail
    assert probe(models_env.settings.gpu_lock_path) is None
    assert not models_env.settings.gpu_lock_path.exists()


def test_start_timeout_kills_group(models_env, monkeypatch):
    s = models_env.settings.model_copy(update={"model_start_timeout_s": 1.0})
    monkeypatch.setattr("newsrag.config.settings", s)
    # a server that never answers: argv has no --served-model-name the fake can match
    monkeypatch.setattr(
        "newsrag.models.serve.vllm_command",
        lambda profile, port: ["sleep", "1000"],
    )
    with gpu_lock("t") as lk:
        with (
            pytest.raises(ModelStartError, match="vllm failed to start fake-llm: no answer"),
            run_server(get_profile("fake-llm"), lock=lk),
        ):
            pass
        pgid = lk.record.pgid
    assert pgid and not lock.group_alive(pgid)


def test_port_busy_picks_next(models_env):
    base = models_env.settings.model_port
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", base))
        busy.listen()
        assert pick_port(base) == base + 1
        with gpu_lock("t") as lk, run_server(get_profile("fake-llm"), base, lock=lk) as server:
            assert server.port == base + 1
            assert server.base_url == f"http://127.0.0.1:{base + 1}/v1"
            record = read_record(models_env.settings.gpu_lock_path)
            assert record.base_url == server.base_url
            assert record.pgid == server.pgid
            pgid = server.pgid
        assert not lock.group_alive(pgid)


def test_pick_port_wraps_in_range(models_env):
    s = models_env.settings
    socks = []
    try:
        for port in range(s.model_port + 5, s.model_port_max + 1):
            sock = socket.socket()
            sock.bind(("127.0.0.1", port))
            sock.listen()
            socks.append(sock)
        assert pick_port(s.model_port + 5) == s.model_port
    finally:
        for sock in socks:
            sock.close()


def test_serve_stops_on_sigterm(models_env):
    proc = models_env.run_python(SERVE_CODE, "fake-llm")
    try:
        line = proc.stdout.readline()
        assert line.startswith("serving fake-llm at http://127.0.0.1:")
        record = read_record(models_env.settings.gpu_lock_path)
        assert record.kind == "serve" and record.profile == "fake-llm"
        pgid = record.pgid
        assert lock.group_alive(pgid)
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=20)
        assert "stopped fake-llm" in out
        assert not lock.group_alive(pgid)
        assert probe(models_env.settings.gpu_lock_path) is None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_vllm_dies_with_parent(models_env):
    """PR_SET_PDEATHSIG: a SIGKILLed `models serve` takes its vLLM down with it."""
    proc = models_env.run_python(SERVE_CODE, "fake-llm")
    line = proc.stdout.readline()
    assert line.startswith("serving fake-llm")
    pgid = read_record(models_env.settings.gpu_lock_path).pgid
    proc.kill()
    proc.wait()
    assert wait_for(lambda: not lock.group_alive(pgid), 10)
