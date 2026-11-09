"""Unpack and register (ticket 004). Register tests need the stores; unpack tests do not."""

from __future__ import annotations

import json
import os
import stat
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsrag.cli import app
from newsrag.config import settings
from newsrag.ingest import register as register_mod
from newsrag.ingest import unpack as unpack_mod
from newsrag.ingest.unpack import MARKER, UNRAR_MISSING

cli = CliRunner()


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """archive_dir, source_dir and reports_dir under tmp_path, wired into the ingest settings."""
    archive_dir = tmp_path / "data"
    source_dir = tmp_path / "raw" / "source"
    reports_dir = tmp_path / "raw" / "reports"
    archive_dir.mkdir()
    patched = settings.model_copy(
        update={"archive_dir": archive_dir, "source_dir": source_dir, "reports_dir": reports_dir}
    )
    monkeypatch.setattr(unpack_mod, "settings", patched)
    monkeypatch.setattr(register_mod, "settings", patched)
    return archive_dir, source_dir, reports_dir


def _snapshot(folder: Path) -> list[tuple[str, int, int]]:
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir())


def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


# --- unpack -------------------------------------------------------------------------------


def test_zip_unpack_then_skip(dirs):
    archive_dir, source_dir, _ = dirs
    _make_zip(
        archive_dir / "2024.zip",
        {
            "2024/December-2024/05-12-2024/main/out/05B-MAIN_01.pdf": b"%PDF-1 a",
            "2024/December-2024/05-12-2024/main/out/05B-MAIN_02R.pdf": b"%PDF-1 b",
        },
    )
    (archive_dir / "vinayaka.jpg").write_bytes(b"jpg")
    before = _snapshot(archive_dir)

    result = cli.invoke(app, ["ingest", "unpack"])
    assert result.exit_code == 0, result.output
    out = source_dir / "2024" / "2024" / "December-2024" / "05-12-2024" / "main" / "out"
    assert (out / "05B-MAIN_01.pdf").read_bytes() == b"%PDF-1 a"
    marker = json.loads((source_dir / "2024" / MARKER).read_text())
    assert marker["size"] == (archive_dir / "2024.zip").stat().st_size
    assert not (source_dir / "vinayaka").exists()

    again = cli.invoke(app, ["ingest", "unpack", "--archive", "2024.zip"])
    assert again.exit_code == 0
    assert "2024.zip: skipped (already unpacked)" in again.output
    assert _snapshot(archive_dir) == before


def test_zip_changed_archive_is_extracted_again(dirs):
    archive_dir, source_dir, _ = dirs
    zpath = archive_dir / "2025.zip"
    _make_zip(zpath, {"2025/a.pdf": b"one"})
    assert cli.invoke(app, ["ingest", "unpack"]).exit_code == 0
    _make_zip(zpath, {"2025/a.pdf": b"two, longer"})
    result = cli.invoke(app, ["ingest", "unpack"])
    assert result.exit_code == 0
    assert "skipped" not in result.output
    assert (source_dir / "2025" / "2025" / "a.pdf").read_bytes() == b"two, longer"


def test_zip_unsafe_member_refused(dirs):
    archive_dir, source_dir, _ = dirs
    _make_zip(archive_dir / "evil.zip", {"ok.pdf": b"x", "../escaped.pdf": b"x"})
    result = cli.invoke(app, ["ingest", "unpack"])
    assert result.exit_code == 1
    assert "unzip failed for" in result.output and "unsafe member path" in result.output
    assert not (source_dir / "escaped.pdf").exists()
    assert not (source_dir / "evil" / MARKER).exists()


def test_zip_corrupt_fails_and_others_continue(dirs):
    archive_dir, source_dir, _ = dirs
    (archive_dir / "bad.zip").write_bytes(b"this is not a zip")
    _make_zip(archive_dir / "good.zip", {"g.pdf": b"x"})
    result = cli.invoke(app, ["ingest", "unpack"])
    assert result.exit_code == 1
    assert f"unzip failed for {archive_dir / 'bad.zip'}:" in result.output
    assert not (source_dir / "bad" / MARKER).exists()
    assert (source_dir / "good" / MARKER).exists()


def test_two_archives_share_stem(dirs):
    archive_dir, source_dir, _ = dirs
    _make_zip(archive_dir / "2025.zip", {"a.pdf": b"x"})
    (archive_dir / "2025.rar").write_bytes(b"Rar!")
    result = cli.invoke(app, ["ingest", "unpack"])
    assert result.exit_code == 1
    assert "two archives share stem 2025" in result.output
    assert not (source_dir / "2025" / MARKER).exists()


def test_unknown_archive_name(dirs):
    result = cli.invoke(app, ["ingest", "unpack", "--archive", "1999.zip"])
    assert result.exit_code == 2
    assert "no archive named 1999.zip" in result.output


def test_missing_unrar(dirs, monkeypatch):
    archive_dir, _, _ = dirs
    (archive_dir / "2020.rar").write_bytes(b"Rar!")
    monkeypatch.setattr(unpack_mod.shutil, "which", lambda name: None)
    result = cli.invoke(app, ["ingest", "unpack"])
    assert result.exit_code == 2
    assert UNRAR_MISSING in result.output
    assert UNRAR_MISSING == "unrar not found: run  sudo apt install unrar"


def test_missing_unrar_not_needed_for_zip(dirs, monkeypatch):
    archive_dir, _, _ = dirs
    _make_zip(archive_dir / "2024.zip", {"a.pdf": b"x"})
    monkeypatch.setattr(unpack_mod.shutil, "which", lambda name: None)
    assert cli.invoke(app, ["ingest", "unpack"]).exit_code == 0


def test_unrar_failure(dirs, tmp_path, monkeypatch):
    archive_dir, source_dir, _ = dirs
    (archive_dir / "2020.rar").write_bytes(b"Rar! good")
    (archive_dir / "2026.rar").write_bytes(b"Rar! bad")
    fake = tmp_path / "bin" / "unrar"
    fake.parent.mkdir()
    fake.write_text(
        "#!/bin/sh\n"
        '# args: x -o+ -idq <rar> <target>/\nrar="$4"; target="$5"\n'
        'case "$rar" in *2026.rar) echo "first line" >&2; '
        'echo "ERROR: checksum error in 01FH-MAIN_01.pdf" >&2; exit 3;; esac\n'
        'mkdir -p "$target" && echo x > "${target}01FH-MAIN_01.pdf"\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(
        unpack_mod, "settings", unpack_mod.settings.model_copy(update={"unrar_bin": str(fake)})
    )
    result = cli.invoke(app, ["ingest", "unpack"])
    assert result.exit_code == 1, result.output
    expected = f"unrar failed for {archive_dir / '2026.rar'}: ERROR: checksum error in"
    assert expected in result.output
    assert (source_dir / "2020" / MARKER).exists()
    assert (source_dir / "2020" / "01FH-MAIN_01.pdf").exists()
    assert not (source_dir / "2026" / MARKER).exists()


# --- register -----------------------------------------------------------------------------


def _stem(source_dir: Path, stem: str, files: list[str], marker: bool = True) -> list[Path]:
    root = source_dir / stem
    root.mkdir(parents=True, exist_ok=True)
    if marker:
        (root / MARKER).write_text(json.dumps({"size": 1, "mtime_ns": 1}))
    paths = []
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF")
        paths.append(path)
    return paths


def _register(dirs):
    _, source_dir, reports_dir = dirs
    lines: list[str] = []
    reports = register_mod.register_all(
        source_dir=source_dir, reports_dir=reports_dir, echo=lines.append
    )
    return reports, lines


def _pages():
    from newsrag.ledger import db

    with db.conn() as c:
        return {r["page_id"]: r for r in db.fetch_all(c, "select * from pages order by page_id")}


def _quarantine():
    from newsrag.ledger import db

    with db.conn() as c:
        return {
            r["source_path"]: r["reason"]
            for r in db.fetch_all(c, "select source_path, reason from quarantine")
        }


DAY = "2020/April-2020/01-04-2020/main/out"


@pytest.mark.stores
def test_register_newer_revision(ledger, dirs):
    from newsrag.ledger import db

    _, source_dir, _ = dirs
    (old,) = _stem(source_dir, "2020", [f"{DAY}/01FH-MAIN_02.pdf"])
    _register(dirs)
    pid = "2020-04-01_FH_main_02"
    assert _pages()[pid]["source_path"] == str(old)
    with db.conn() as c:
        c.execute(
            "insert into stage_runs (page_id, stage, version, status) values (%s, 'x', 'v', 'done')",
            (pid,),
        )
    db.quarantine_page(pid, "corrupt_pdf")

    (new,) = _stem(source_dir, "2020", [f"{DAY}/01FH-MAIN_02R.pdf"])
    reports, _ = _register(dirs)
    page = _pages()[pid]
    assert page["source_path"] == str(new)
    assert page["revision"] == 1
    assert page["quarantine_reason"] is None
    assert str(old) not in _quarantine()
    with db.conn() as c:
        n = db.fetch_one(c, "select count(*) as n from stage_runs where page_id = %s", (pid,))
    assert n["n"] == 0
    assert reports["2020"]["superseded"] == [{"path": str(old), "winner": str(new)}]


@pytest.mark.stores
def test_register_quarantine(ledger, dirs):
    _, source_dir, _ = dirs
    paths = _stem(
        source_dir,
        "2020",
        [
            "xyz.pdf",
            "2020/April-2020/01-04-2020/01FH-DUMMY_01.pdf",
            "2020/April-2020/01-04-2020/01FH-MAIN_01TEST.pdf",
            "2025/April-2025/26-30/31FH-MAIN_01.pdf",
            "2020/April-2020/01-04-2020/01FH-MAIN_01.pdf",
        ],
    )
    reports, _ = _register(dirs)
    q = _quarantine()
    assert [q.get(str(p)) for p in paths[:4]] == [
        "no_date",
        "test_or_dummy",
        "test_or_dummy",
        "bad_date",
    ]
    assert {p["source_path"] for p in _pages().values()} == {str(paths[4])}
    report = reports["2020"]
    assert report["registered"] == 1 and len(report["quarantined"]) == 4
    assert report["quarantine_pct"] == 80.0


@pytest.mark.stores
def test_register_is_idempotent_and_reports(ledger, dirs):
    _, source_dir, reports_dir = dirs
    _stem(
        source_dir,
        "2024",
        [
            "2024/December-2024/31-12-2024/main/out/31B-MAIN_01.pdf",
            "2024/December-2024/31-12-2024/main/out/31B-MAIN_01R.pdf",
            "2024/December-2024/31-12-2024/main/out/31B-MAIN_01R(EPAPER).pdf",
            "2024/December-2024/31-12-2024/main/out/31B-MAIN_02.pdf",
            "2024/December-2024/31-12-2024/main/out/02B-MAIN_03.pdf",  # day mismatch
            "2024/December-2024/31-12-2024/main/out/18V_10.pdf",
        ],
    )
    # 2025 holds a newer revision of a 2024 page (year-end overlap) and a page of its own.
    _stem(
        source_dir,
        "2025",
        [
            "2024/December-2024/31-12-2024/main/out/31B-MAIN_02RR.pdf",
            "2025/January-2025/01-01-2025/main/out/01B-MAIN_01.pdf",
            "2025/January-2025/01-01-2025/main/out/01B-MAIN_08-09.pdf",
        ],
    )
    reports, lines = _register(dirs)
    first = _pages()
    assert sorted(first) == [
        "2024-12-31_B_main_01",
        "2024-12-31_B_main_02",
        "2024-12-31_B_main_03",
        "2025-01-01_B_main_01",
        "2025-01-01_B_main_08-09",
    ]
    assert first["2024-12-31_B_main_01"]["source_path"].endswith("31B-MAIN_01R.pdf")
    assert first["2024-12-31_B_main_02"]["source_archive"] == "2025"
    r24, r25 = reports["2024"], reports["2025"]
    assert (r24["source_pdfs"], r24["registered"], len(r24["superseded"])) == (6, 2, 3)
    assert len(r24["quarantined"]) == 1 and r24["day_mismatch_warnings"] == 1
    assert (r25["source_pdfs"], r25["registered"], len(r25["superseded"])) == (3, 3, 0)
    for r in (r24, r25):
        assert r["registered"] + len(r["superseded"]) + len(r["quarantined"]) == r["source_pdfs"]
    on_disk = json.loads((reports_dir / "register-2024.json").read_text())
    assert on_disk == r24
    assert "2024: pdfs=6 registered=2 superseded=3 quarantined=1 (33.33%)" in lines

    _register(dirs)
    assert _pages() == first  # same rows, same updated_at: nothing was written


@pytest.mark.stores
def test_register_skips_stem_without_marker(ledger, dirs):
    _, source_dir, _ = dirs
    _stem(source_dir, "2025", ["2025/January-2025/01-01-2025/01B-MAIN_01.pdf"], marker=False)
    reports, lines = _register(dirs)
    assert reports == {}
    assert "skipping 2025: not fully unpacked" in lines
    assert _pages() == {}


@pytest.mark.stores
def test_register_source_missing_and_back(ledger, dirs):
    _, source_dir, _ = dirs
    (path,) = _stem(source_dir, "2020", [f"{DAY}/01FH-MAIN_05.pdf"])
    _register(dirs)
    pid = "2020-04-01_FH_main_05"
    path.rename(path.with_suffix(".bak"))
    _register(dirs)
    assert _pages()[pid]["quarantine_reason"] == "source_missing"
    assert _quarantine()[str(path)] == "source_missing"
    path.with_suffix(".bak").rename(path)
    _register(dirs)
    assert _pages()[pid]["quarantine_reason"] is None
    assert str(path) not in _quarantine()


@pytest.mark.stores
def test_register_keeps_quarantine_when_winner_unchanged(ledger, dirs):
    from newsrag.ledger import db

    _, source_dir, _ = dirs
    (path,) = _stem(source_dir, "2020", [f"{DAY}/01FH-MAIN_06.pdf"])
    _register(dirs)
    db.quarantine_page("2020-04-01_FH_main_06", "corrupt_pdf")
    _register(dirs)
    assert _pages()["2020-04-01_FH_main_06"]["quarantine_reason"] == "corrupt_pdf"
    assert _quarantine()[str(path)] == "corrupt_pdf"


@pytest.mark.stores
def test_register_clears_parse_quarantine_once_it_parses(ledger, dirs):
    from newsrag.ledger import db

    _, source_dir, _ = dirs
    (path,) = _stem(source_dir, "2020", [f"{DAY}/01FH-MAIN_07.pdf"])
    with db.conn() as c:
        c.execute(
            "insert into quarantine (source_path, source_archive, reason) "
            "values (%s, '2020', 'bad_filename')",
            (str(path),),
        )
    _register(dirs)
    assert str(path) not in _quarantine()
    assert "2020-04-01_FH_main_07" in _pages()


@pytest.mark.stores
def test_register_cli_without_ledger_schema(dirs, monkeypatch):
    from newsrag.ledger import db

    with db.use_schema(f"test_missing_{os.getpid()}"):
        _stem(dirs[1], "2020", [f"{DAY}/01FH-MAIN_01.pdf"])
        result = cli.invoke(app, ["ingest", "register"])
    assert result.exit_code == 2
    assert "ledger init" in result.output
