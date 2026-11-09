"""The only way to call Surya or indic-ocr (FR-2).

- Surya: a stdin/stdout worker (`surya_worker.py`) in `envs/surya`, started with
  `settings.subprocess_env()` in its own process group, with PR_SET_PDEATHSIG so it dies with us
  (the launcher and spawner thread of ticket 006). The client waits for the worker's
  `model loaded in <s> s` line on stderr, then sends one JSON line per request and reads one
  JSON line back. A reply `{"error": ...}` raises `WorkerError` (the worker keeps running); a
  worker that dies fails the request with `WorkerDied` and is restarted; a request slower than
  `ocr_request_timeout_s` kills the worker's process group, restarts it and raises
  `WorkerTimeout`.
- indic-ocr's text model: a vLLM server from `envs/vllm` (`newsrag.ocr.text.TextOcr`), restarted
  when it dies.

All text coming back is NFC-normalised here.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import subprocess
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from newsrag import config
from newsrag.models.lock import kill_group
from newsrag.models.serve import DIE_WITH_PARENT, _spawn
from newsrag.ocr.text import TextOcr

HERE = Path(__file__).resolve().parent
SURYA_WORKER = HERE / "surya_worker.py"
LOADED_PREFIX = "model loaded in "
TAIL_LINES = 20


class WorkerError(RuntimeError):
    """The worker answered a request with {"error": ...}."""


class WorkerDied(RuntimeError):
    """The worker exited (EOF on stdout or a non-zero exit) while answering."""


class WorkerTimeout(RuntimeError):
    """The worker did not answer within `ocr_request_timeout_s`."""


class WorkerStartError(RuntimeError):
    """The worker exited or stayed silent before printing `model loaded in <s> s`."""


@dataclass
class WorkerSpec:
    """How to start one worker: argv (python first) and extra environment."""

    name: str
    argv: list[str]
    extra_env: dict[str, str] = field(default_factory=dict)


def _env_python(env_dir: Path) -> str:
    return str(Path(env_dir) / "bin" / "python")


def surya_spec() -> WorkerSpec:
    s = config.settings
    return WorkerSpec(
        "surya",
        [
            _env_python(s.surya_env_dir),
            "-I",
            str(SURYA_WORKER),
            "--device",
            s.ocr_device,
            "--threshold",
            str(s.ocr_layout_threshold),
            "--checkpoint",
            s.surya_layout_checkpoint,
            "--order-checkpoint",
            s.surya_order_checkpoint,
        ],
        {"HF_HUB_OFFLINE": "1"},  # installed by `ocr install`; never re-resolve at run time
    )


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


class Worker:
    """One worker process. Thread-safe: one request at a time."""

    def __init__(self, spec: WorkerSpec):
        self.spec = spec
        self.proc: subprocess.Popen | None = None
        self.load_s: float | None = None
        self.starts = 0
        self._lines: queue.Queue[str | None] | None = None
        self._tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
        self._loaded = threading.Event()
        self._lock = threading.RLock()

    # --- lifecycle ---

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def log_path(self) -> Path:
        return Path(config.settings.logs_dir) / f"ocr-{self.spec.name}.log"

    def start(self) -> None:
        with self._lock:
            self._kill()
            env = config.settings.subprocess_env()
            env.update(self.spec.extra_env)
            env.pop("VIRTUAL_ENV", None)  # the worker runs in its own venv, not ours
            self.log_path().parent.mkdir(parents=True, exist_ok=True)
            self._tail.clear()
            self._loaded.clear()
            self.load_s = None
            argv = [sys.executable, "-I", "-c", DIE_WITH_PARENT, str(os.getpid()), *self.spec.argv]
            proc = _spawn(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,  # its own process group: a timeout kills all of it
            )
            self.proc = proc
            self.starts += 1
            self._lines = queue.Queue()
            threading.Thread(
                target=self._read_stdout, args=(proc, self._lines), daemon=True
            ).start()
            threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()
            deadline = time.monotonic() + config.settings.ocr_start_timeout_s
            while not self._loaded.wait(0.1):
                if proc.poll() is not None:
                    time.sleep(0.2)  # let the stderr reader catch the last lines
                    raise WorkerStartError(
                        f"{self.spec.name} worker exited {proc.returncode} before loading its "
                        f"model:\n{self.tail()}"
                    )
                if time.monotonic() > deadline:
                    self._kill()
                    raise WorkerStartError(
                        f"{self.spec.name} worker did not load its model within "
                        f"{config.settings.ocr_start_timeout_s:.0f} s:\n{self.tail()}"
                    )

    def stop(self) -> None:
        with self._lock:
            proc = self.proc
            if proc is None:
                return
            if proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.close()  # a clean EOF: the worker leaves its read loop
                except OSError:
                    pass
                try:
                    proc.wait(timeout=config.settings.ocr_stop_timeout_s)
                except subprocess.TimeoutExpired:
                    pass
            self._kill()

    def _kill(self) -> None:
        proc = self.proc
        if proc is None:
            return
        kill_group(proc.pid, grace_s=config.settings.ocr_stop_timeout_s)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass
        self.proc = None

    def _restart_after_failure(self) -> None:
        try:
            self.start()
        except Exception as exc:  # noqa: BLE001 - the next request tries again
            print(f"ocr: restarting {self.spec.name} worker failed: {exc}", file=sys.stderr)

    # --- I/O threads ---

    @staticmethod
    def _read_stdout(proc: subprocess.Popen, lines: queue.Queue) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.put(raw.decode("utf-8", "replace"))
        lines.put(None)

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        with open(self.log_path(), "a", encoding="utf-8") as log:
            log.write(f"--- {self.spec.name} worker pid {proc.pid} started ---\n")
            log.flush()
            for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                log.write(line + "\n")
                log.flush()
                if proc is self.proc:
                    self._tail.append(line)
                if line.startswith(LOADED_PREFIX) and proc is self.proc:
                    try:
                        self.load_s = float(line[len(LOADED_PREFIX) :].split()[0])
                    except (ValueError, IndexError):
                        self.load_s = None
                    self._loaded.set()

    def tail(self) -> str:
        return "\n".join(self._tail) or "(no stderr)"

    # --- requests ---

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if not self.alive():
                self.start()
            proc, lines = self.proc, self._lines
            assert proc is not None and proc.stdin is not None and lines is not None
            try:
                proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass  # it died; the EOF below says so
            timeout = config.settings.ocr_request_timeout_s
            try:
                line = lines.get(timeout=timeout)
            except queue.Empty:
                self._kill()
                self._restart_after_failure()
                raise WorkerTimeout(
                    f"{self.spec.name} worker gave no answer within {timeout:.0f} s; restarted"
                ) from None
            if line is None:
                try:
                    code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    code = None
                time.sleep(0.1)
                tail = self.tail()
                self._kill()
                self._restart_after_failure()
                raise WorkerDied(f"{self.spec.name} worker died (exit {code}); restarted:\n{tail}")
            try:
                reply = json.loads(line)
            except ValueError:
                raise WorkerError(f"{self.spec.name} worker sent a non-JSON line: {line[:200]!r}")
            if not isinstance(reply, dict):
                raise WorkerError(f"{self.spec.name} worker sent {type(reply).__name__}")
            if "error" in reply:
                raise WorkerError(f"{self.spec.name}: {reply['error']}")
            return reply


class OcrClient:
    """Surya layout (worker) + indic-ocr text (vLLM), both loaded for the life of the client.

    Thread-safe: page threads share it; Surya answers one page at a time, vLLM batches the
    blocks of all pages in flight.
    """

    def __init__(self, surya: WorkerSpec, text: TextOcr):
        self.surya = Worker(surya)
        self.text = text

    def start(self, lock: Any = None) -> None:
        """Start both at once (they fit on the GPU together, D-8). `lock`: the GPU lock handle
        the caller holds, which records the vLLM server."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.surya.start), pool.submit(self.text.start, lock)]
            errors = [f.exception() for f in futures]
        failed = [e for e in errors if e is not None]
        if failed:
            self.stop()
            raise failed[0]

    def stop(self) -> None:
        self.surya.stop()
        self.text.stop()

    def layout(self, image: str | Path) -> dict[str, Any]:
        """Surya regions in reading order: {"regions": [...], "width", "height"}."""
        reply = self.surya.request({"op": "layout", "image": str(image)})
        for region in reply.get("regions", []):
            region["label"] = nfc(str(region.get("label", "")))
        return reply

    def blocks(self, image: str | Path, blocks: list[dict[str, Any]]) -> list[str]:
        """indic-ocr text for each block ({"bbox", "type": "text"|"table"}), same order, NFC."""
        return [nfc(t) for t in self.text.read(image, blocks)]

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
