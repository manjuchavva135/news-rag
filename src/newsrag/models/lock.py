"""The exclusive GPU lock (FR-4, FR-7, NFR-2).

One file, `settings.gpu_lock_path`, is both the lock (``fcntl.flock``) and the record of who
holds it (JSON: holder, pid, pid_start, pgid, profile, base_url, kind, started). The record is
written in place while the lock is held and the file is deleted on release, *before* unlocking;
a taker therefore checks after ``flock`` that the path still names the inode it locked, and
retries otherwise.

A holder killed with SIGKILL releases the flock (the kernel closes its descriptor) but leaves its
record behind. The next taker sees a record whose PID is dead and kills the process group it
names (an orphaned vLLM) before writing its own record.

Inside one process the lock is re-entrant: nested ``gpu_lock`` scopes share the descriptor of
the outermost one, which alone takes and releases the flock.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from newsrag import config

POLL_INTERVAL_S = 0.25
KILL_WAIT_S = 5.0  # after SIGKILL, wait this long for the group to disappear

Reporter = Callable[[str], None]


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass
class LockRecord:
    holder: str
    pid: int
    pid_start: int | None = None
    pgid: int | None = None
    profile: str | None = None
    base_url: str | None = None
    kind: str = "lock"  # "lock" (in-process GPU user), "use" (use() started vLLM), "serve"
    started: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def describe(self) -> str:
        return f"{self.holder} (pid {self.pid})"

    def lines(self) -> list[str]:
        return [f"{k}: {'-' if v is None else v}" for k, v in asdict(self).items()]


# --- /proc helpers -------------------------------------------------------------------------


def _proc_stat(pid: int) -> tuple[str, int, int] | None:
    """(state, pgrp, starttime) of a process from /proc, or None when it does not exist."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    fields = text[text.rindex(")") + 2 :].split()
    # fields[0] is field 3 (state); pgrp is field 5; starttime is field 22.
    return fields[0], int(fields[2]), int(fields[19])


def pid_start(pid: int) -> int | None:
    stat = _proc_stat(pid)
    return stat[2] if stat else None


def pid_alive(pid: int, start: int | None = None) -> bool:
    """True when `pid` runs (not a zombie) and, if `start` is given, is the same process."""
    stat = _proc_stat(pid)
    if stat is None or stat[0] in ("Z", "X"):
        return False
    return start is None or stat[2] == start


def group_alive(pgid: int) -> bool:
    """True when any non-zombie process is in process group `pgid`."""
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            stat = _proc_stat(int(entry.name))
            if stat and stat[1] == pgid and stat[0] not in ("Z", "X"):
                return True
    return False


def kill_group(pgid: int, grace_s: float | None = None) -> None:
    """SIGTERM a process group, then SIGKILL it if it outlives `grace_s`."""
    if pgid <= 1 or pgid == os.getpgrp():
        return
    grace_s = config.settings.model_stop_timeout_s if grace_s is None else grace_s
    for sig, wait_s in ((signal.SIGTERM, grace_s), (signal.SIGKILL, KILL_WAIT_S)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if not group_alive(pgid):
                return
            time.sleep(POLL_INTERVAL_S)


# --- the lock file --------------------------------------------------------------------------


def _parse(text: str) -> LockRecord | None:
    try:
        data = json.loads(text)
        return LockRecord(**data)
    except (ValueError, TypeError):
        return None


def read_record(path: Path | None = None) -> LockRecord | None:
    """The record in the lock file, or None (no file, empty or unreadable)."""
    path = Path(path) if path is not None else config.settings.gpu_lock_path
    try:
        return _parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _read_fd(fd: int) -> LockRecord | None:
    size = os.fstat(fd).st_size
    return _parse(os.pread(fd, size, 0).decode("utf-8", "replace")) if size else None


def _write_fd(fd: int, record: LockRecord) -> None:
    data = (json.dumps(asdict(record), indent=1) + "\n").encode()
    os.ftruncate(fd, 0)
    os.pwrite(fd, data, 0)


def probe(path: Path | None = None) -> LockRecord | None:
    """The record of the current holder, or None when the GPU is free.

    Free means a non-blocking flock succeeds, whatever file is left behind (FR-7).
    """
    path = Path(path) if path is not None else config.settings.gpu_lock_path
    try:
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _read_fd(fd) or LockRecord(holder="unknown holder", pid=0)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def _acquire_fd(path: Path, holder: str, report: Reporter) -> int:
    """Open and flock the lock file, waiting (and reporting) while someone else holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    next_report = 0.0
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            other = _read_fd(fd)
            os.close(fd)
            now = time.monotonic()
            if now >= next_report:
                who = other.describe() if other else "unknown holder"
                report(f"waiting for GPU lock held by {who}")
                next_report = now + config.settings.gpu_lock_report_s
            time.sleep(POLL_INTERVAL_S)
            continue
        try:
            same = os.stat(path).st_ino == os.fstat(fd).st_ino
        except FileNotFoundError:
            same = False
        if same:
            return fd
        os.close(fd)  # the previous holder deleted the file between our open and flock


def _recover_dead_holder(fd: int, report: Reporter) -> None:
    """Kill the process group of a holder that died without releasing (NFR-2)."""
    stale = _read_fd(fd)
    if stale is None or stale.pid == os.getpid() or pid_alive(stale.pid, stale.pid_start):
        return
    if stale.pgid and group_alive(stale.pgid):
        report(
            f"GPU lock holder {stale.describe()} is dead: killing its process group {stale.pgid}"
        )
        kill_group(stale.pgid)


@dataclass
class _Held:
    fd: int
    path: Path
    record: LockRecord
    depth: int = 1


_mutex = threading.Lock()
_held: _Held | None = None
_holders: list[str] = []  # holder names of in-process GPU users (kind "lock"), first use first


class GpuLockHandle:
    """What `gpu_lock` yields: the record, and a way to add pgid / base_url to it."""

    def __init__(self, held: _Held) -> None:
        self._held = held

    @property
    def record(self) -> LockRecord:
        return self._held.record

    def update(self, **fields: object) -> None:
        with _mutex:
            for name, value in fields.items():
                setattr(self._held.record, name, value)
            _write_fd(self._held.fd, self._held.record)


@contextlib.contextmanager
def gpu_lock(
    holder: str,
    *,
    profile: str | None = None,
    kind: str = "lock",
    report: Reporter | None = None,
) -> Iterator[GpuLockHandle]:
    """Hold the exclusive GPU lock for the scope; `holder` is free text shown to waiters."""
    global _held
    report = report or _stderr
    with _mutex:
        if _held is not None:
            _held.depth += 1
            held = _held
        else:
            path = Path(config.settings.gpu_lock_path)
            fd = _acquire_fd(path, holder, report)
            try:
                _recover_dead_holder(fd, report)
                pid = os.getpid()
                record = LockRecord(
                    holder=holder, pid=pid, pid_start=pid_start(pid), profile=profile, kind=kind
                )
                _write_fd(fd, record)
            except BaseException:
                os.close(fd)
                raise
            held = _held = _Held(fd=fd, path=path, record=record)
            if kind == "lock" and holder not in _holders:
                _holders.append(holder)
    try:
        yield GpuLockHandle(held)
    finally:
        with _mutex:
            held.depth -= 1
            if held.depth == 0:
                _release(held)
                _held = None


def _release(held: _Held) -> None:
    try:
        if os.stat(held.path).st_ino == os.fstat(held.fd).st_ino:
            os.unlink(held.path)
    except FileNotFoundError:
        pass
    finally:
        fcntl.flock(held.fd, fcntl.LOCK_UN)
        os.close(held.fd)


def held_by_this_process() -> LockRecord | None:
    """The record of the lock this process holds, if any."""
    with _mutex:
        return _held.record if _held is not None else None


def lock_holders() -> list[str]:
    """Holder names this process took the GPU lock for as an in-process GPU user (kind "lock",
    e.g. ``ocr``, ``indic-ocr``), in order of first use. With `loaded_profiles()` this is every
    model this process loaded (ticket 029 `models_loaded`)."""
    with _mutex:
        return list(_holders)
