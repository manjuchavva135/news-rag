"""Ledger tests (ticket 003). DB tests use a throw-away schema; helper tests need no DB."""

import io
import json
import subprocess
import sys
from datetime import date

import pytest
from _dummy_stages import DummyDate, DummyPage, add_pages, rows
from pydantic import SecretStr

from newsrag.config import settings
from newsrag.ledger import db
from newsrag.stages.base import artifact_path, config_hash, write_json_atomic
from newsrag.stages.runner import RunFilters, list_eligible, parse_duration, run_stage
from newsrag.stores import pg

# --- no DB needed ---


def test_artifact_path_slug():
    path = artifact_path("ocr", "indic-ocr@v1/p2", "2026-04-01_FH_main_01")
    assert path == settings.work_dir / "ocr" / "indic-ocr_v1_p2" / "2026-04-01_FH_main_01.json"


def test_write_json_atomic(tmp_path):
    target = tmp_path / "a" / "b.json"
    write_json_atomic(target, {"x": "తెలుగు", "d": date(2026, 4, 1)})
    assert json.loads(target.read_text()) == {"x": "తెలుగు", "d": "2026-04-01"}
    assert [p.name for p in target.parent.iterdir()] == ["b.json"]  # no temp file left


def test_config_hash_follows_settings_and_files(tmp_path, monkeypatch):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("v1")
    first = config_hash(["render_dpi"], [prompt])
    assert len(first) == 8 and int(first, 16) >= 0
    assert config_hash(["render_dpi"], [prompt]) == first
    prompt.write_text("v2")
    assert config_hash(["render_dpi"], [prompt]) != first
    from newsrag.stages import base

    before = base.config_hash(["render_dpi"])
    monkeypatch.setattr(base, "settings", settings.model_copy(update={"render_dpi": 200}))
    assert base.config_hash(["render_dpi"]) != before
    secret = settings.model_copy(update={"hf_token": SecretStr("hf_x")})
    monkeypatch.setattr(base, "settings", secret)
    with pytest.raises(ValueError, match="secrets"):
        base.config_hash(["hf_token"])


def test_parse_duration():
    assert parse_duration("90s").total_seconds() == 90
    assert parse_duration("30m").total_seconds() == 1800
    assert parse_duration("2h").total_seconds() == 7200
    assert parse_duration("1d").total_seconds() == 86400
    with pytest.raises(ValueError):
        parse_duration("2 hours")


def test_registry_import_has_no_side_effects(tmp_path):
    code = (
        "import sys; import newsrag.stages.registry as r; "
        "assert 'torch' not in sys.modules and 'vllm' not in sys.modules; print(len(r.REGISTRY))"
    )
    env = {"NEWSRAG_RAW_ROOT": str(tmp_path / "nope"), "PATH": "/usr/bin:/bin"}
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert not (tmp_path / "nope").exists()


# --- with a throw-away ledger schema ---


@pytest.mark.stores
def test_migrations_idempotent_and_tables(ledger):
    assert db.migrate() == []  # already applied by the fixture
    with db.conn() as c:
        tables = [
            r[0]
            for r in c.execute(
                "select table_name from information_schema.tables where table_schema = %s "
                "order by 1",
                (ledger,),
            )
        ]
        applied = [r[0] for r in c.execute("select filename from schema_migrations")]
    assert {"date_runs", "pages", "quarantine", "schema_migrations", "stage_runs"} <= set(tables)
    assert "entity_ids" in tables  # ticket 022
    assert sorted(applied) == [p.name for p in db.migration_files()]


@pytest.mark.stores
def test_conn_search_path_and_no_leak(ledger):
    with db.conn() as c:
        assert c.execute("show search_path").fetchone()[0].startswith(ledger)
    for _ in range(3):  # pooled connections come back with the default search_path
        with pg().connection() as c:
            assert ledger not in c.execute("show search_path").fetchone()[0]


@pytest.mark.stores
def test_upsert_page_updates(ledger):
    (page_id,) = add_pages(1)
    db.upsert_page(
        page_id=page_id,
        pub_date=date(2026, 4, 1),
        edition_code="FH",
        section="main",
        page_num=1,
        source_path="/src/new.pdf",
        source_archive="test.rar",
        revision=2,
    )
    with db.conn() as c:
        row = db.fetch_one(c, "select * from pages where page_id = %s", (page_id,))
    assert row["revision"] == 2 and row["source_path"] == "/src/new.pdf"
    assert row["updated_at"] >= row["registered_at"]
    with pytest.raises(ValueError, match="missing"):
        db.upsert_page(page_id="x")


@pytest.mark.stores
def test_quarantine_page_never_eligible(ledger, registry):
    ids = add_pages(3)
    a = DummyPage("a")
    b = DummyPage("b", depends_on=("a",))
    d = DummyDate("d", depends_on=("a",))
    registry.update({"a": a, "b": b, "d": d})
    run_stage(a, RunFilters(), out=io.StringIO())
    db.quarantine_page(ids[1], "pdf will not open")
    with db.conn() as c:
        q = db.fetch_all(c, "select * from quarantine")
        page = db.fetch_one(c, "select quarantine_reason from pages where page_id = %s", (ids[1],))
    assert [(r["source_path"], r["reason"]) for r in q] == [
        (f"/src/{ids[1]}.pdf", "pdf will not open")
    ]
    assert page["quarantine_reason"] == "pdf will not open"

    a.version = "v2"  # every stage would rerun - except on the quarantined page
    for stage in (a, b):
        assert ids[1] not in list_eligible(stage, RunFilters())[0]
    assert ids[1] not in list_eligible(a, RunFilters(force=True))[0]
    run_stage(a, RunFilters(), out=io.StringIO())
    run_stage(b, RunFilters(), out=io.StringIO())
    assert ids[1] not in rows("b")
    run_stage(d, RunFilters(), out=io.StringIO())
    assert d.calls == [(date(2026, 4, 1), [ids[0], ids[2]])]

    db.quarantine_page(ids[1], "second reason")  # upsert keyed by source_path
    with db.conn() as c:
        assert c.execute("select count(*), max(reason) from quarantine").fetchone() == (
            1,
            "second reason",
        )
    with pytest.raises(KeyError):
        db.quarantine_page("no-such-page", "x")
