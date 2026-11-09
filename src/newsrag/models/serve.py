"""Start and stop a vLLM server for one profile (FR-3)."""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from newsrag import config
from newsrag.models.lock import GpuLockHandle, kill_group
from newsrag.models.profiles import Profile

HOST = "127.0.0.1"  # D-6: the model API is never open on the network
FAILURE_TAIL_LINES = 20
READY_POLL_S = 2.0
# Runs as the child before vLLM: PR_SET_PDEATHSIG (SIGTERM when our process dies, kept across
# exec), exit if the parent is already gone, then exec vLLM in place (same PID and group).
# A launcher instead of preexec_fn, which is unsafe when use() runs in a threaded stage.
# PR_SET_PDEATHSIG fires when the *thread* that forked exits, so servers are always started
# from one long-lived spawner thread, never from a short-lived stage worker thread.
DIE_WITH_PARENT = (
    "import ctypes, os, signal, sys; "
    "ctypes.CDLL('libc.so.6', use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0); "
    "os.getppid() == int(sys.argv[1]) or sys.exit(70); "
    "os.execvp(sys.argv[2], sys.argv[2:])"
)


_spawner: ThreadPoolExecutor | None = None
_spawner_mutex = threading.Lock()


def _spawn(*args, **kwargs) -> subprocess.Popen:
    """subprocess.Popen, run on the process-lifetime spawner thread."""
    global _spawner
    with _spawner_mutex:
        if _spawner is None:
            _spawner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="newsrag-vllm-spawn")
    return _spawner.submit(subprocess.Popen, *args, **kwargs).result()


class ModelStartError(RuntimeError):
    """vLLM exited early or did not answer in time; the message ends with the log tail."""


@dataclass
class VllmServer:
    profile: str
    port: int
    proc: subprocess.Popen
    log_path: Path

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}/v1"

    @property
    def pgid(self) -> int:
        return self.proc.pid  # started with start_new_session: its own group leader


def vllm_bin() -> Path:
    return config.settings.vllm_env_dir / "bin" / "vllm"


def vllm_command(profile: Profile, port: int) -> list[str]:
    return [
        str(vllm_bin()),
        "serve",
        profile.hf_id,
        "--host",
        HOST,
        "--port",
        str(port),
        "--served-model-name",
        profile.name,
        "--dtype",
        profile.dtype,
        "--max-model-len",
        str(profile.max_model_len),
        "--gpu-memory-utilization",
        str(profile.gpu_memory_utilization),
        *profile.extra_args,
    ]


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def pick_port(preferred: int | None = None) -> int:
    """`preferred` if free, else the next free port of model_port..model_port_max (wrapping)."""
    s = config.settings
    preferred = s.model_port if preferred is None else preferred
    in_range = list(range(s.model_port, s.model_port_max + 1))
    if preferred in in_range:
        i = in_range.index(preferred)
        order = in_range[i:] + in_range[:i]
    else:
        order = [preferred, *in_range]
    for port in order:
        if port_free(port):
            return port
    raise ModelStartError(f"no free port in {s.model_port}-{s.model_port_max}")


def _tail(path: Path, n: int = FAILURE_TAIL_LINES) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return "(no log)"
    return "\n".join(lines[-n:])


def _answers(base_url: str, profile: str, timeout_s: float = 5.0) -> bool:
    """True when GET /v1/models answers and lists `profile`."""
    try:
        resp = httpx.get(f"{base_url}/models", timeout=timeout_s)
        resp.raise_for_status()
        return any(m.get("id") == profile for m in resp.json().get("data", []))
    except (httpx.HTTPError, ValueError):
        return False


def _stop(server: VllmServer) -> None:
    kill_group(server.pgid)
    with contextlib.suppress(subprocess.TimeoutExpired):
        server.proc.wait(timeout=5)


@contextlib.contextmanager
def run_server(
    profile: Profile, port: int | None = None, lock: GpuLockHandle | None = None
) -> Iterator[VllmServer]:
    """Start vLLM for `profile`, wait until it answers, stop it when the scope exits.

    The caller holds the GPU lock; `lock` gets the server's pgid and base_url recorded.
    """
    s = config.settings
    port = pick_port(port)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = s.logs_dir / f"vllm-{profile.name}-{ts}.log"
    cmd = vllm_command(profile, port)
    env = s.subprocess_env()
    env["HF_HUB_OFFLINE"] = "1"  # weights were downloaded first; never block on the network
    env["VLLM_NO_USAGE_STATS"] = "1"
    env["DO_NOT_TRACK"] = "1"

    with log_path.open("ab") as log:
        log.write(("$ " + " ".join(cmd) + "\n").encode())
        log.flush()
        proc = _spawn(
            [sys.executable, "-I", "-c", DIE_WITH_PARENT, str(os.getpid()), *cmd],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    server = VllmServer(profile=profile.name, port=port, proc=proc, log_path=log_path)
    try:
        if lock is not None:
            lock.update(pgid=server.pgid, base_url=server.base_url, profile=profile.name)
        deadline = time.monotonic() + s.model_start_timeout_s
        while not _answers(server.base_url, profile.name):
            code = proc.poll()
            if code is not None:
                reason = f"vllm exited with code {code}"
                break
            if time.monotonic() >= deadline:
                reason = f"no answer within {s.model_start_timeout_s:.0f} s"
                break
            time.sleep(READY_POLL_S)
        else:
            reason = None
        if reason is not None:
            _stop(server)
            raise ModelStartError(
                f"vllm failed to start {profile.name}: {reason} (log {log_path})\n{_tail(log_path)}"
            )
        yield server
    finally:
        _stop(server)
