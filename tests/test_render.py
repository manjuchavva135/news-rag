"""Ticket 005: the `render` stage and the dev sample. Uses tiny generated PDFs, no archive data."""

from __future__ import annotations

import io
import os
import stat
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

import newsrag.sample as sample_mod
import newsrag.stages.base as base_mod
import newsrag.stages.render as render_mod
from newsrag.config import settings
from newsrag.stages.base import PageRow, StageUnavailable
from newsrag.stages.render import (
    POPPLER_MISSING,
    PopplerRejected,
    RenderStage,
    png_size,
    poppler_missing,
)

needs_poppler = pytest.mark.skipif(
    bool(poppler_missing()), reason="poppler-utils (pdftoppm, pdfinfo) not installed"
)


def make_pdf(path: Path, pages: int = 1, size: tuple[int, int] = (612, 792)) -> Path:
    """A minimal valid PDF with `pages` pages of `size` points, each with one black square."""
    w, h = size
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(pages))
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>",
    ]
    for i in range(pages):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] /Contents {4 + 2 * i} 0 R >>"
        )
        content = "0 0 0 rg 20 20 100 100 re f"
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{off:010d} 00000 n \n" for off in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    return path


def junk_pdf(path: Path) -> Path:
    path.write_bytes(b"\x00junk\xff\x01pdf")  # 10 bytes, not a PDF
    return path


def fake_tool(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


def page_row(page_id: str, source: Path, **kw) -> PageRow:
    values = {
        "page_id": page_id,
        "pub_date": date(2025, 3, 4),
        "edition_code": "B",
        "section": "main",
        "page_num": 3,
        "page_end": None,
        "source_path": str(source),
        "source_archive": "2025.zip",
        "revision": 1,
    }
    values.update(kw)
    return PageRow(**values)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """images_dir and dev_sample_file under tmp_path; `env.set(**kw)` changes more settings."""

    class Env:
        images = tmp_path / "images"
        sample_file = tmp_path / "data" / "dev-sample.txt"
        tmp = tmp_path

        def set(self, **kw):
            current = render_mod.settings
            patched = current.model_copy(update=kw)
            for mod in (render_mod, base_mod, sample_mod):
                monkeypatch.setattr(mod, "settings", patched)
            return patched

    e = Env()
    e.set(images_dir=e.images, dev_sample_file=e.sample_file)
    return e


# --- unit: the stage without the ledger ---


@needs_poppler
def test_render_one_page(env):
    pdf = make_pdf(env.tmp / "a.pdf")
    result = RenderStage().handle(page_row("2025-03-04_B_main_03", pdf))
    final = env.images / "2025-03-04_B_main_03.png"
    assert result.artifact_path == str(final)
    assert final.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.detail == {
        "width": 2550,  # 8.5 in at 300 DPI
        "height": 3300,
        "bytes": final.stat().st_size,
        "pdf_pages": 1,
        "multi_page": False,
        "small_render": False,
    }
    assert png_size(final) == (2550, 3300)
    assert not list(env.images.glob(".tmp-*"))


@needs_poppler
def test_multi_page_and_small_render(env):
    pdf = make_pdf(env.tmp / "b.pdf", pages=3, size=(200, 300))
    detail = RenderStage().handle(page_row("p2", pdf)).detail
    assert detail["pdf_pages"] == 3
    assert detail["multi_page"] is True
    assert detail["width"] < 1500 and detail["small_render"] is True


@needs_poppler
def test_creates_images_dir(env):
    assert not env.images.exists()
    RenderStage().handle(page_row("p3", make_pdf(env.tmp / "c.pdf", size=(100, 100))))
    assert (env.images / "p3.png").is_file()


@needs_poppler
def test_corrupt_pdf_is_rejected(env):
    with pytest.raises(PopplerRejected, match=r"^render_failed: \S"):
        RenderStage().handle(page_row("bad", junk_pdf(env.tmp / "bad.pdf")))


def test_partial_png_not_left(env):
    # pdftoppm writes half a PNG under the temp name, then fails: nothing may be left behind.
    fake = fake_tool(
        env.tmp / "pdftoppm",
        'for last; do :; done\nprintf "\\211PNG half" > "$last.png"\necho "Syntax Error: boom" >&2\n'
        "exit 1",
    )
    env.set(pdftoppm_bin=str(fake))
    with pytest.raises(PopplerRejected, match="render_failed: Syntax Error: boom"):
        RenderStage().handle(page_row("p4", make_pdf(env.tmp / "d.pdf")))
    assert not (env.images / "p4.png").exists()
    assert list(env.images.iterdir()) == []


def test_timeout_is_rejected(env):
    fake = fake_tool(env.tmp / "pdftoppm", "sleep 30")
    env.set(pdftoppm_bin=str(fake), render_timeout_s=1)
    with pytest.raises(PopplerRejected, match="^render_failed: timeout$"):
        RenderStage().handle(page_row("p5", make_pdf(env.tmp / "e.pdf")))
    assert list(env.images.iterdir()) == []


def test_output_write_error_is_not_rejected(env):
    fake = fake_tool(
        env.tmp / "pdftoppm", 'echo "Could not write image to x.png; exiting" >&2\nexit 1'
    )
    env.set(pdftoppm_bin=str(fake))
    with pytest.raises(RuntimeError) as info:
        RenderStage().handle(page_row("p6", make_pdf(env.tmp / "f.pdf")))
    assert not isinstance(info.value, PopplerRejected)


def test_disk_full_is_not_rejected(env):
    fake = fake_tool(env.tmp / "pdftoppm", 'echo "Syntax Error: something" >&2\nexit 1')
    env.set(pdftoppm_bin=str(fake), render_min_free_bytes=10**18)
    with pytest.raises(RuntimeError, match="bytes free") as info:
        RenderStage().handle(page_row("p7", make_pdf(env.tmp / "g.pdf")))
    assert not isinstance(info.value, PopplerRejected)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_images_dir_is_not_rejected(env):
    env.images.mkdir()
    env.images.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(PermissionError):
            RenderStage().handle(page_row("p8", make_pdf(env.tmp / "h.pdf")))
    finally:
        env.images.chmod(0o755)


def test_missing_source_is_not_rejected(env):
    with pytest.raises(FileNotFoundError):
        RenderStage().handle(page_row("p9", env.tmp / "gone.pdf"))


def test_on_final_failure_quarantines_only_rejections(monkeypatch):
    calls = []
    monkeypatch.setattr(render_mod.db, "quarantine_page", lambda p, r: calls.append((p, r)))
    stage, page = RenderStage(), page_row("p", Path("/x.pdf"))
    stage.on_final_failure(page, "RuntimeError: pdftoppm exited 1: /x is not writable")
    stage.on_final_failure(page, "PermissionError: /x is not writable")
    stage.on_final_failure(page, "interrupted")
    assert calls == []
    stage.on_final_failure(page, "PopplerRejected: render_failed: timeout")
    assert calls == [("p", "render_failed: timeout")]


def test_version_ends_with_config_hash(env):
    v300 = RenderStage().version
    assert v300.startswith("pdftoppm-300dpi-v1-") and len(v300.rsplit("-", 1)[1]) == 8
    env.set(render_dpi=400)
    v400 = RenderStage().version
    assert v400.startswith("pdftoppm-400dpi-v1-") and v400 != v300


def test_default_concurrency_from_settings(env):
    assert RenderStage().concurrency == settings.render_workers
    env.set(render_workers=7)
    assert RenderStage().concurrency == 7


def test_preflight_missing_poppler(monkeypatch):
    monkeypatch.setattr(render_mod.shutil, "which", lambda name: None)
    with pytest.raises(StageUnavailable, match="poppler not found"):
        RenderStage().preflight()


def test_missing_poppler_cli_exits_2_before_db(monkeypatch):
    from newsrag.cli import app
    from newsrag.ledger import db

    monkeypatch.setattr(render_mod.shutil, "which", lambda name: None)

    def no_db():
        raise AssertionError("the DB was touched before preflight")

    monkeypatch.setattr(db, "conn", no_db)
    result = CliRunner().invoke(app, ["stage", "run", "render"])
    assert result.exit_code == 2
    assert POPPLER_MISSING in result.output
    assert POPPLER_MISSING == "poppler not found: run  sudo apt install poppler-utils"


# --- unit: choosing the dev sample ---


def synthetic_pages(skip: set[str] = frozenset()) -> list[PageRow]:
    pages = []
    for year, n_days in ((2024, 10), (2025, 20)):
        for day in range(1, n_days + 1):
            d = date(year, 12 if year == 2024 else 3, day)
            for ed in ("B", "G", "BGNOP"):
                for num in (1, 2, 3):
                    pid = f"{d}_{ed}_main_{num:02d}"
                    pages.append(
                        page_row(pid, Path("/x.pdf"), pub_date=d, edition_code=ed, page_num=num)
                    )
        if year == 2025 and "all" not in skip:
            pages.append(
                page_row(
                    "2025-03-05_ALL_main_04",
                    Path("/x.pdf"),
                    pub_date=date(2025, 3, 5),
                    edition_code="ALL",
                    page_num=4,
                )
            )
        if year == 2025 and "spread" not in skip:
            pages.append(
                page_row(
                    "2025-03-06_BG_main_06-07",
                    Path("/x.pdf"),
                    pub_date=date(2025, 3, 6),
                    edition_code="BG",
                    page_num=6,
                    page_end=7,
                )
            )
    return pages


def test_choose_strata_and_determinism():
    pages = synthetic_pages()
    ids, short = sample_mod.choose(pages, seed=42)
    assert short == []
    assert len(ids) == 20 and ids == sorted(ids) and len(set(ids)) == 20
    assert sample_mod.choose(list(reversed(pages)), seed=42)[0] == ids  # input order is irrelevant
    by_id = {p.page_id: p for p in pages}
    for year, expected in ((2024, 8), (2025, 12)):
        assert sum(by_id[i].pub_date.year == year for i in ids) == expected
    fronts = [i for i in ids if by_id[i].page_num == 1]
    assert sum(by_id[i].pub_date.year == 2024 for i in fronts) >= 3
    assert sum(by_id[i].pub_date.year == 2025 for i in fronts) >= 4
    assert "2025-03-05_ALL_main_04" in ids and "2025-03-06_BG_main_06-07" in ids
    singles = [i for i in ids if by_id[i].edition_code in ("B", "G") and by_id[i].page_num != 1]
    combined = [i for i in ids if by_id[i].edition_code == "BGNOP" and by_id[i].page_num != 1]
    assert len(singles) >= 6 and len(combined) >= 5


def test_choose_shortfall_is_filled_and_reported():
    pages = synthetic_pages(skip={"all", "spread"})
    ids, short = sample_mod.choose(pages, seed=42)
    assert short == ["# short: 2025 all", "# short: 2025 spread"]
    assert len(ids) == 20
    assert sum(i.startswith("2025") for i in ids) == 12


def test_choose_strata_predicates():
    p = Path("/x.pdf")
    assert sample_mod.PREDICATES["single"](page_row("a", p, edition_code="B", page_num=2))
    assert not sample_mod.PREDICATES["single"](page_row("a", p, edition_code="B", page_num=1))
    assert not sample_mod.PREDICATES["single"](page_row("a", p, edition_code="BG", page_num=2))
    assert sample_mod.PREDICATES["combined"](page_row("a", p, edition_code="BG", page_num=2))
    assert not sample_mod.PREDICATES["combined"](page_row("a", p, edition_code="ALL", page_num=2))
    assert not sample_mod.PREDICATES["combined"](page_row("a", p, edition_code="B-VK", page_num=2))
    assert sample_mod.PREDICATES["all"](page_row("a", p, edition_code="ALL", page_num=1))
    assert sample_mod.PREDICATES["spread"](page_row("a", p, page_num=6, page_end=7))


# --- with a throw-away ledger ---


def add_page(page_id: str, source: Path, **kw) -> None:
    from newsrag.ledger import db

    row = page_row(page_id, source, **kw)
    db.upsert_page(
        page_id=row.page_id,
        pub_date=row.pub_date,
        edition_code=row.edition_code,
        section=row.section,
        page_num=row.page_num,
        page_end=row.page_end,
        source_path=row.source_path,
        source_archive=row.source_archive,
    )


def page_state(page_id: str) -> tuple[str | None, dict | None]:
    from newsrag.ledger import db

    with db.conn() as c:
        p = db.fetch_one(c, "select quarantine_reason from pages where page_id = %s", (page_id,))
        r = db.fetch_one(
            c, "select * from stage_runs where page_id = %s and stage = 'render'", (page_id,)
        )
    return p["quarantine_reason"], r


@pytest.fixture
def render_reg(registry):
    registry["render"] = RenderStage()
    return registry


def run_render(registry, times: int = 1):
    from newsrag.stages.runner import RunFilters, run_stage

    for _ in range(times):
        run_stage(registry["render"], RunFilters(workers=2), registry=registry, out=io.StringIO())


@pytest.mark.stores
@needs_poppler
def test_corrupt_pdf_quarantined_after_3_runs_and_reset_clears(env, ledger, render_reg):
    from newsrag.ledger import db
    from newsrag.stages.runner import reset_stage, stage_counts

    bad = junk_pdf(env.tmp / "bad.pdf")
    assert bad.stat().st_size == 10
    add_page("bad", bad)
    add_page("good", make_pdf(env.tmp / "good.pdf", size=(100, 100)))
    run_render(render_reg)
    reason, row = page_state("bad")
    assert reason is None and row["status"] == "failed" and row["attempt"] == 1
    run_render(render_reg, times=2)
    reason, row = page_state("bad")
    assert reason.startswith("render_failed: ")
    assert row["attempt"] == 3
    assert not (env.images / "bad.png").exists()
    with db.conn() as c:
        q = db.fetch_one(c, "select reason from quarantine where source_path = %s", (str(bad),))
    assert q["reason"] == reason
    assert stage_counts(render_reg["render"]) == {
        "done": 1,
        "failed": 0,
        "running": 0,
        "eligible": 0,
    }
    assert render_mod.stats() == {
        "rendered": 1,
        "multi_page": 0,
        "small_render": 1,
        "quarantined": 1,
    }
    # `stage reset render --page bad` lifts the render_failed quarantine.
    reset_stage(render_reg["render"], page_id="bad")
    reason, row = page_state("bad")
    assert reason is None and row is None
    with db.conn() as c:
        assert (
            db.fetch_one(c, "select 1 from quarantine where source_path = %s", (str(bad),)) is None
        )
    assert stage_counts(render_reg["render"])["eligible"] == 1


@pytest.mark.stores
def test_reset_keeps_other_quarantine_reasons(env, ledger, render_reg):
    from newsrag.ledger import db
    from newsrag.stages.runner import reset_stage

    add_page("x", env.tmp / "x.pdf")
    db.quarantine_page("x", "test_or_dummy")
    reset_stage(render_reg["render"], all_rows=True)
    assert page_state("x")[0] == "test_or_dummy"


@pytest.mark.stores
def test_timeout_counts_as_failure(env, ledger, render_reg):
    fake = fake_tool(env.tmp / "pdftoppm", "sleep 30")
    env.set(pdftoppm_bin=str(fake), render_timeout_s=1)
    add_page("slow", make_pdf(env.tmp / "slow.pdf"))
    run_render(render_reg)
    reason, row = page_state("slow")
    assert reason is None
    assert row["status"] == "failed" and row["attempt"] == 1
    assert "render_failed: timeout" in row["error"]
    run_render(render_reg, times=2)
    assert page_state("slow")[0] == "render_failed: timeout"
    assert list(env.images.iterdir()) == []


@pytest.mark.stores
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_environment_error_never_quarantines(env, ledger, render_reg):
    env.images.mkdir()
    env.images.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        add_page("p", make_pdf(env.tmp / "p.pdf"))
        run_render(render_reg, times=3)
        reason, row = page_state("p")
        assert reason is None
        assert row["status"] == "failed" and row["attempt"] == 3
        assert "not writable" in row["error"]
    finally:
        env.images.chmod(0o755)


@pytest.mark.stores
@needs_poppler
def test_render_stats_and_skip_done(env, ledger, render_reg):
    from newsrag.stages.runner import stage_counts

    add_page("one", make_pdf(env.tmp / "one.pdf", size=(100, 100)))
    add_page("multi", make_pdf(env.tmp / "multi.pdf", pages=2, size=(100, 100)))
    run_render(render_reg)
    assert render_mod.stats() == {
        "rendered": 2,
        "multi_page": 1,
        "small_render": 2,
        "quarantined": 0,
    }
    assert stage_counts(render_reg["render"])["eligible"] == 0
    mtime = (env.images / "one.png").stat().st_mtime_ns
    run_render(render_reg)
    assert (env.images / "one.png").stat().st_mtime_ns == mtime


def mark_rendered(pages: list[PageRow], version: str) -> None:
    from newsrag.ledger import db

    with db.conn() as c:
        for p in pages:
            c.execute(
                "insert into stage_runs (page_id, stage, version, input_versions, status, "
                "attempt, started_at, finished_at) values (%s, 'render', %s, '{}'::jsonb, "
                "'done', 1, now(), now())",
                (p.page_id, version),
            )


@pytest.mark.stores
def test_sample_build_show_and_refusals(env, ledger, render_reg):
    from newsrag.cli import app

    pages = synthetic_pages()
    for p in pages:
        add_page(
            p.page_id,
            Path(p.source_path),
            pub_date=p.pub_date,
            edition_code=p.edition_code,
            page_num=p.page_num,
            page_end=p.page_end,
        )
    runner = CliRunner()

    result = runner.invoke(app, ["sample", "build"])
    assert result.exit_code == 1 and "render not finished" in result.output
    assert not env.sample_file.exists()

    mark_rendered(pages, render_reg["render"].version)
    result = runner.invoke(app, ["sample", "build"])
    assert result.exit_code == 0, result.output
    lines = env.sample_file.read_text().splitlines()
    assert len(lines) == 20 and lines == sorted(lines)
    assert lines == sample_mod.choose(pages)[0]

    result = runner.invoke(app, ["sample", "build"])
    assert result.exit_code == 1
    assert "dev-sample.txt exists (use --force)" in result.output

    first = env.sample_file.read_text()
    assert runner.invoke(app, ["sample", "build", "--force"]).exit_code == 0
    assert env.sample_file.read_text() == first

    env.images.mkdir()
    (env.images / f"{lines[0]}.png").write_bytes(b"x")
    result = runner.invoke(app, ["sample", "show"])
    assert result.exit_code == 0
    shown = result.output.splitlines()
    assert len(shown) == 20
    assert shown[0] == f"{lines[0]}\t{env.images / (lines[0] + '.png')}\tok"
    assert all(line.endswith("\tmissing") for line in shown[1:])


@pytest.mark.stores
def test_sample_skips_quarantined_pages(env, ledger, render_reg):
    from newsrag.ledger import db

    pages = synthetic_pages()
    for p in pages:
        add_page(
            p.page_id,
            Path(p.source_path),
            pub_date=p.pub_date,
            edition_code=p.edition_code,
            page_num=p.page_num,
            page_end=p.page_end,
        )
    mark_rendered(pages, render_reg["render"].version)
    db.quarantine_page("2025-03-05_ALL_main_04", "render_failed: x")
    _, ids, short = sample_mod.build()
    assert "2025-03-05_ALL_main_04" not in ids
    assert short == ["# short: 2025 all"]
    assert env.sample_file.read_text().splitlines()[0] == "# short: 2025 all"
