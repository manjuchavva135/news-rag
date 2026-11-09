"""Extract the delivered archives into ``<source_dir>/<stem>/`` (ticket 004, FR-1, FR-2, FR-8).

ZIPs are extracted with :mod:`zipfile`, RARs with ``unrar``. The archives themselves are only ever
opened for reading. A ``.unpacked`` marker (the archive's size and mtime) is written last, so a
folder without one is incomplete and is extracted again (overwriting) next time.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from newsrag.config import settings

ARCHIVE_SUFFIXES = (".zip", ".rar")
MARKER = ".unpacked"
UNRAR_MISSING = "unrar not found: run  sudo apt install unrar"

Echo = Callable[[str], None]


class UnpackError(Exception):
    """One archive could not be extracted; the message is what the user sees."""


@dataclass
class Outcome:
    archive: Path
    status: str  # unpacked | skipped | failed
    message: str = ""


def find_archives(archive_dir: Path) -> list[Path]:
    return sorted(
        p for p in archive_dir.iterdir() if p.is_file() and p.suffix.lower() in ARCHIVE_SUFFIXES
    )


def marker_path(source_dir: Path, stem: str) -> Path:
    return source_dir / stem / MARKER


def signature(archive: Path) -> dict:
    st = archive.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def read_marker(path: Path) -> dict | None:
    """The marker's content, or None when it is missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "size" not in data or "mtime_ns" not in data:
        return None
    return data


def is_unpacked(archive: Path, source_dir: Path) -> bool:
    marker = read_marker(marker_path(source_dir, archive.stem))
    if marker is None:
        return False
    sig = signature(archive)
    return marker["size"] == sig["size"] and marker["mtime_ns"] == sig["mtime_ns"]


def write_marker(archive: Path, source_dir: Path) -> None:
    path = marker_path(source_dir, archive.stem)
    data = {
        "archive": archive.name,
        **signature(archive),
        "mtime": datetime.fromtimestamp(archive.stat().st_mtime, UTC).isoformat(),
        "unpacked_at": datetime.now(UTC).isoformat(),
    }
    tmp = path.with_name(MARKER + ".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def _safe_member(name: str) -> bool:
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute() or (pure.parts and ":" in pure.parts[0]):
        return False
    return ".." not in pure.parts


def extract_zip(archive: Path, target: Path, workers: int) -> int:
    """Extract every member into ``target``; returns the number of files written."""
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise UnpackError(f"unzip failed for {archive}: {exc}") from exc
    unsafe = [i.filename for i in infos if not _safe_member(i.filename)]
    if unsafe:
        raise UnpackError(f"unzip failed for {archive}: unsafe member path {unsafe[0]!r}")
    files = [i for i in infos if not i.is_dir()]
    # Create every folder first so parallel extraction never races on makedirs.
    for info in infos:
        rel = PurePosixPath(info.filename.replace("\\", "/"))
        folder = rel if info.is_dir() else rel.parent
        (target / folder).mkdir(parents=True, exist_ok=True)

    chunks = [files[i :: max(1, workers)] for i in range(max(1, workers))]

    def run(chunk: list[zipfile.ZipInfo]) -> int:
        with zipfile.ZipFile(archive) as zf:
            for info in chunk:
                zf.extract(info, target)
        return len(chunk)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return sum(pool.map(run, chunks))
    except (OSError, zipfile.BadZipFile, EOFError, zipfile.LargeZipFile) as exc:
        raise UnpackError(f"unzip failed for {archive}: {exc}") from exc


def extract_rar(archive: Path, target: Path, unrar_bin: str) -> None:
    proc = subprocess.run(
        [unrar_bin, "x", "-o+", "-idq", str(archive), f"{target}/"],
        env=settings.subprocess_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        lines = [line for line in proc.stderr.splitlines() if line.strip()]
        last = lines[-1].strip() if lines else f"exit code {proc.returncode}"
        raise UnpackError(f"unrar failed for {archive}: {last}")


def unpack_all(
    only: str | None = None,
    *,
    archive_dir: Path | None = None,
    source_dir: Path | None = None,
    echo: Echo = print,
) -> int:
    """Extract every archive (or the one named). Returns the exit code: 0, 1 or 2."""
    archive_dir = Path(archive_dir or settings.archive_dir)
    source_dir = Path(source_dir or settings.source_dir)
    archives = find_archives(archive_dir)
    if only is not None:
        archives = [a for a in archives if a.name == only]
        if not archives:
            echo(f"no archive named {only} in {archive_dir}")
            return 2

    by_stem: dict[str, list[Path]] = {}
    for archive in find_archives(archive_dir):
        by_stem.setdefault(archive.stem, []).append(archive)

    outcomes: list[Outcome] = []
    todo: list[Path] = []
    for archive in archives:
        if len(by_stem[archive.stem]) > 1:
            outcomes.append(
                Outcome(
                    archive,
                    "failed",
                    f"two archives share stem {archive.stem}: "
                    + ", ".join(a.name for a in by_stem[archive.stem]),
                )
            )
        elif is_unpacked(archive, source_dir):
            outcomes.append(Outcome(archive, "skipped"))
        else:
            todo.append(archive)

    if any(a.suffix.lower() == ".rar" for a in todo) and shutil.which(settings.unrar_bin) is None:
        echo(UNRAR_MISSING)
        return 2

    for outcome in outcomes:
        if outcome.status == "skipped":
            echo(f"{outcome.archive.name}: skipped (already unpacked)")
        else:
            echo(outcome.message)

    for archive in todo:
        target = source_dir / archive.stem
        target.mkdir(parents=True, exist_ok=True)
        marker_path(source_dir, archive.stem).unlink(missing_ok=True)
        echo(f"{archive.name}: extracting into {target} ...")
        started = time.monotonic()
        try:
            if archive.suffix.lower() == ".zip":
                count = extract_zip(archive, target, settings.unpack_workers)
                what = f"{count} files"
            else:
                extract_rar(archive, target, settings.unrar_bin)
                what = "done"
        except UnpackError as exc:
            echo(str(exc))
            outcomes.append(Outcome(archive, "failed", str(exc)))
            continue
        write_marker(archive, source_dir)
        echo(f"{archive.name}: unpacked ({what}, {time.monotonic() - started:.1f} s)")
        outcomes.append(Outcome(archive, "unpacked"))

    return 1 if any(o.status == "failed" for o in outcomes) else 0
