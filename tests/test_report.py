"""Ticket 030: the quality report (bars, verdict, links, sections)."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from newsrag import config
from newsrag.report import bars as B
from newsrag.report import build as rb
from newsrag.report import collect

# --- inputs that pass every bar ---------------------------------------------------------------

PAGES_OK = {"registered": 1000, "quarantined_pages": 3, "rejects": 2, "reject_files": []}
INDEXED_OK = {
    "pages": 997,
    "indexed_pages": 997,
    "active_generation": "bge-m3__v1",
    "expected_generation": "bge-m3__v1",
    "validate_ok": True,
    "validate_error": None,
}
GROUPING_OK = {
    "pages": 100,
    "scored_pages": 100,
    "mean_score": 0.95,
    "judge_low": 2,
    "judge_low_pct": 2.0,
    "retried": 5,
    "retried_pct": 5.0,
    "retry_gain": 0.1,
    "page_flags": {"low_confidence:judge_low": 2},
}
SPOTCHECK_OK = {"invented_pct": 1.5, "n_lines": 1000, "n_items": 100}
VERDICTS_OK = {"pages": {}, "signoff": {"name": "reader", "signed_at": "2026-11-09T15:49:31Z"}}
COMPARE_OK = {
    "old": {"recall_at_10": 0.5, "mrr": 0.3, "n": 100},
    "new": {"recall_at_10": 0.7, "mrr": 0.5, "n": 100},
}
ANSWERS_OK = {"run_id": "r1", "supported_pct": 95.0, "n_claims": 100, "n_failed": 0}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Settings pointing every input into tmp_path, every file input written so that it passes,
    and the queried inputs (ledger, stores) replaced by passing numbers."""
    reports = tmp_path / "reports"
    s = config.settings.model_copy(
        update={
            "reports_dir": reports,
            "quality_report_dir": reports / "quality",
            "transcribe_spotcheck_file": reports / "transcription-spotcheck.json",
            "ocr_check_dir": reports / "ocr-check",
            "baseline_compare_file": reports / "baseline-compare.json",
            "customer_answers_file": reports / "customer-answers.json",
            "customer_signoff_file": reports / "customer-answers-signoff.json",
            "embed_bakeoff_report_file": reports / "embed-bakeoff.md",
            "runs_dir": reports / "runs",
            "alias_dir": tmp_path / "aliases",
        }
    )
    monkeypatch.setattr(config, "settings", s)

    def write(path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    write(s.transcribe_spotcheck_file, SPOTCHECK_OK)
    write(s.ocr_check_dir / "verdicts.json", VERDICTS_OK)
    write(s.baseline_compare_file, COMPARE_OK)
    answers = write(s.customer_answers_file, ANSWERS_OK)
    sha1 = hashlib.sha1(answers.read_bytes()).hexdigest()
    write(s.customer_signoff_file, {"name": "reader", "signed_at": "t", "answers_sha1": sha1})

    monkeypatch.setattr(collect, "pages_input", lambda: dict(PAGES_OK))
    monkeypatch.setattr(collect, "indexed_input", lambda: dict(INDEXED_OK))
    monkeypatch.setattr(collect, "grouping_input", lambda: dict(GROUPING_OK))
    for _title, fn_name in rb.SECTIONS:
        monkeypatch.setattr(collect, fn_name, lambda: {"n": 1})
    monkeypatch.setattr(collect, "bakeoffs", lambda: {"embedding": None, "grouping": None})

    class World:
        settings = s

        @staticmethod
        def write_file(path: Path, data: Any) -> Path:
            return write(path, data)

        @staticmethod
        def build():
            report, page = rb.build()
            return report, page.read_text(encoding="utf-8")

    return World


def header_of(page: str) -> str:
    m = re.search(r'<span class="verdict [A-Z]+">([^<]+)</span>', page)
    assert m
    return m.group(1)


# --- E-1 ----------------------------------------------------------------------------------------


def test_all_bars_pass_gives_phase_pass(world):
    report, page = world.build()
    assert [b.passed for b in report.bars] == [True] * 7
    assert report.phase1 == "PASS"
    assert header_of(page) == "PHASE 1: PASS"
    data = json.loads((world.settings.quality_report_dir / "report.json").read_text())
    assert data["phase1"] == "PASS" and len(data["bars"]) == 7
    assert [b["name"] for b in data["bars"]] == list(B.NAMES)


def test_one_bar_fail_gives_phase_fail_count(world, monkeypatch):
    world.write_file(world.settings.transcribe_spotcheck_file, {"invented_pct": 2.5})
    report, page = world.build()
    assert report.phase1 == "FAIL"
    assert header_of(page) == "PHASE 1: FAIL (1 bars)"
    assert [b.number for b in report.bars if not b.passed] == [4]

    monkeypatch.setattr(collect, "grouping_input", lambda: {**GROUPING_OK, "mean_score": 0.89})
    report, page = world.build()
    assert header_of(page) == "PHASE 1: FAIL (2 bars)"


def test_missing_input_is_fail(world, monkeypatch):
    s = world.settings
    for path in (
        s.transcribe_spotcheck_file,
        s.ocr_check_dir / "verdicts.json",
        s.baseline_compare_file,
        s.customer_answers_file,
    ):
        path.unlink()

    def down():
        raise ConnectionError("stores down")

    monkeypatch.setattr(collect, "pages_input", down)
    monkeypatch.setattr(collect, "indexed_input", lambda: None)
    monkeypatch.setattr(collect, "grouping_input", lambda: {**GROUPING_OK, "pages": 0})
    report, page = world.build()
    assert [b.passed for b in report.bars] == [False] * 7
    assert all(b.value == "missing" for b in report.bars)
    assert header_of(page) == "PHASE 1: FAIL (7 bars)"
    assert "ConnectionError" in report.bars[0].note
    # pure functions: None is a FAIL for every bar
    for fn in (B.pages_bar, B.indexed_bar, B.grouping_bar, B.transcription_bar):
        assert not fn(None).passed
    assert not B.ocr_signoff_bar(None).passed
    assert not B.retrieval_bar(None).passed
    assert not B.customer_bar(None, None, None).passed


def test_bar_links_to_source(world):
    report, page = world.build()
    out = world.settings.quality_report_dir
    expected = {
        1: "inputs/bar-1.json",
        2: "inputs/bar-2.json",
        3: "inputs/bar-3.json",
        4: "../transcription-spotcheck.json",
        5: "../ocr-check/verdicts.json",
        6: "../baseline-compare.json",
        7: "../customer-answers.json",
    }
    assert {b.number: b.source for b in report.bars} == expected
    for number, link in expected.items():
        assert f'href="{link}"' in page
        assert (out / link).resolve().is_file(), f"bar {number}: {link} does not exist"
    snap = json.loads((out / "inputs" / "bar-1.json").read_text())
    assert snap["bar"] == 1 and snap["data"]["registered"] == PAGES_OK["registered"]
    # FR-4 / FR-1: phone viewport, no external request
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page
    assert not re.search(r"https?://", page)
    assert not re.search(r"<(script|link|img|iframe)\b", page)


def test_quarantine_pct_formula():
    assert B.quarantine_pct(rejects=2, quarantined_pages=3, registered=998) == pytest.approx(0.5)
    assert B.quarantine_pct(0, 0, 0) is None
    ok = B.pages_bar({"rejects": 5, "quarantined_pages": 5, "registered": 995})  # exactly 1%
    assert ok.passed and ok.value == "1.00%"
    bad = B.pages_bar({"rejects": 5, "quarantined_pages": 6, "registered": 995})
    assert not bad.passed
    assert not B.pages_bar({"rejects": 0, "quarantined_pages": 0, "registered": 0}).passed


def test_signoff_must_match_answers():
    sha = "a" * 40
    signoff = {"name": "reader", "signed_at": "t", "answers_sha1": sha}
    assert B.customer_bar(ANSWERS_OK, signoff, sha).passed
    stale = B.customer_bar(ANSWERS_OK, signoff, "b" * 40)
    assert not stale.passed and "sha1" in stale.note
    assert not B.customer_bar(ANSWERS_OK, None, sha).passed
    assert not B.customer_bar({**ANSWERS_OK, "n_failed": 1}, signoff, sha).passed
    assert not B.customer_bar({**ANSWERS_OK, "supported_pct": 89.9}, signoff, sha).passed


def test_signoff_must_match_answers_file(world):
    # editing the answers after the sign-off retires it
    answers = world.settings.customer_answers_file
    world.write_file(answers, {**ANSWERS_OK, "run_id": "r2"})
    report, _ = world.build()
    assert not report.bars[6].passed


def test_eval_tester_signoff_rejected(world):
    s = world.settings
    signoff = json.loads(s.customer_signoff_file.read_text())
    world.write_file(s.customer_signoff_file, {**signoff, "name": B.TEST_SIGNER})
    report, _ = world.build()
    assert not report.bars[6].passed
    assert B.TEST_SIGNER in report.bars[6].note


def test_missing_stats_section_not_available(world, monkeypatch):
    monkeypatch.setattr(collect, "dedup", lambda: None)

    def broken():
        raise FileNotFoundError("no runs dir")

    monkeypatch.setattr(collect, "runs", broken)
    report, page = world.build()
    assert report.sections["Dedup"] is None and report.sections["Runs"] is None
    for title in ("Dedup", "Runs"):
        block = page.split(f"<h2>{title}</h2>", 1)[1].split("<h2>", 1)[0]
        assert rb.NOT_AVAILABLE in block
    assert "FileNotFoundError" in report.errors["Runs"]
    # every section title is on the page
    for title, _fn in rb.SECTIONS:
        assert f"<h2>{title}</h2>" in page


# --- E-5: second-review clarifications -----------------------------------------------------------


def test_ocr_signoff_rejects_eval_tester():
    signed = {"signoff": {"name": "reader", "signed_at": "2026-11-09T15:49:31Z"}}
    assert B.ocr_signoff_bar(signed).passed
    tester = {"signoff": {"name": B.TEST_SIGNER, "signed_at": "2026-11-09T15:49:31Z"}}
    assert not B.ocr_signoff_bar(tester).passed
    assert not B.ocr_signoff_bar({"signoff": {"name": "reader"}}).passed  # no date
    assert not B.ocr_signoff_bar({"pages": {}, "signoff": None}).passed


def test_reject_key_dedups_parseable_names():
    a = "/x/2024/31-02-2024/main/out/31B-MAIN_01.pdf"
    b = "/y/copy/31-02-2024/main/31b-main_01.pdf"
    other = "/x/2024/31-02-2024/main/out/31B-MAIN_02.pdf"
    unparseable = ["/x/a/weird.pdf", "/x/b/weird.pdf"]
    assert collect.count_rejects([a, b, other, *unparseable]) == 4


# --- ledger / warehouse (stores) ------------------------------------------------------------------


def _page(page_id: str, pub_date: date, source: str, **extra: Any) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "pub_date": pub_date,
        "edition_code": "B",
        "section": "main",
        "page_num": 1,
        "source_path": source,
        "source_archive": "a.zip",
        **extra,
    }


@pytest.mark.stores
def test_quarantine_excludes_test_files(ledger):
    from newsrag.ledger import db

    d = date(2024, 12, 1)
    db.upsert_page(**_page("p1", d, "/s/01-12-2024/01B-MAIN_01.pdf"))
    db.upsert_page(**_page("p2", d, "/s/01-12-2024/01B-MAIN_02.pdf"))
    db.quarantine_page("p2", "render:corrupt")  # a page: counted through quarantine_reason
    with db.conn() as c:
        c.cursor().executemany(
            "insert into quarantine (source_path, source_archive, reason) values (%s, %s, %s)",
            [
                ("/s/01-12-2024/15dummy-MAIN_10.pdf", "a.zip", "test_or_dummy"),
                ("/s/31-02-2024/31B-MAIN_01.pdf", "a.zip", "bad_date"),
                ("/t/31-02-2024/31B-MAIN_01.pdf", "b.zip", "bad_date"),  # same page again
                ("/s/01-12-2024/18V_10.pdf", "a.zip", "bad_filename"),
            ],
        )
    data = collect.pages_input()
    assert data["registered"] == 2
    assert data["quarantined_pages"] == 1
    assert data["rejects"] == 2  # test file left out, the two bad_date copies counted once
    assert B.quarantine_pct(data["rejects"], data["quarantined_pages"], data["registered"]) == (
        pytest.approx(3 / 4 * 100)
    )


@pytest.mark.stores
def test_done_means_current_version(ledger, registry, tmp_path):
    from psycopg.types.json import Jsonb

    from newsrag.ledger import db
    from newsrag.stages.base import PageStage

    class FinalStage(PageStage):
        name = "group_final"
        depends_on = ()
        version = "gf-v2"

    registry["group_final"] = FinalStage()
    d = date(2024, 12, 1)
    rows = []
    for page_id, version, score in (
        ("cur", "gf-v2", 1.0),
        ("old", "gf-v1", 0.0),
        ("q", "gf-v2", 0.0),
    ):
        db.upsert_page(**_page(page_id, d, f"/s/{page_id}.pdf"))
        art = tmp_path / f"{page_id}.json"
        art.write_text(json.dumps({"judge_score": score, "page_flags": [], "retried": False}))
        rows.append((page_id, version, str(art)))
    db.quarantine_page("q", "render:corrupt")
    with db.conn() as c:
        c.cursor().executemany(
            "insert into stage_runs (page_id, stage, version, input_versions, status, attempt, "
            "artifact_path, finished_at) values (%s, 'group_final', %s, %s, 'done', 1, %s, now())",
            [(p, v, Jsonb({}), a) for p, v, a in rows],
        )
    data = collect.grouping_input()
    assert data["pages"] == 1  # the stale version and the quarantined page are left out
    assert data["mean_score"] == pytest.approx(1.0)


@pytest.fixture
def warehouse(monkeypatch):
    from psycopg import sql

    from newsrag.stores import pg
    from newsrag.warehouse import db as wdb

    schema = f"test_wh_{secrets.token_hex(4)}"
    monkeypatch.setattr(
        config, "settings", config.settings.model_copy(update={"warehouse_schema": schema})
    )
    wdb.migrate()
    try:
        yield wdb
    finally:
        with pg().connection() as c:
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.stores
def test_low_confidence_counts_stories(warehouse):
    d = date(2024, 12, 1)
    stories = [
        ("s1", True, ["low_confidence:judge_low", "low_confidence:geometry"]),
        ("s2", True, ["low_confidence:judge_low"]),
        ("s3", False, []),
        ("gone", True, ["low_confidence:judge_low"]),  # no chunk in the active generation
    ]
    chunks = [("g1", "s1"), ("g1", "s1"), ("g1", "s2"), ("g1", "s3"), ("g0", "gone")]
    with warehouse.conn() as c:
        c.cursor().executemany(
            "insert into stories (story_id, pub_date, kind, headline, text, n_printings, "
            "low_confidence, low_confidence_reasons) values (%s, %s, 'news', 'h', 't', 1, %s, %s)",
            [(sid, d, low, reasons) for sid, low, reasons in stories],
        )
        c.cursor().executemany(
            "insert into chunks (generation, chunk_id, story_id, pub_date, seq, kind, headline, "
            "prefix, text, n_tokens) values (%s, %s, %s, %s, %s, 'news', 'h', 'p', 't', 1)",
            [(g, f"c{i}", sid, d, i) for i, (g, sid) in enumerate(chunks)],
        )
    warehouse.set_active_generation("g1")
    data = collect.low_confidence()
    assert data["stories"] == 3
    assert data["low_confidence_stories"] == 2
    assert data["reasons"] == {"low_confidence:judge_low": 2, "low_confidence:geometry": 1}


# --- sections come from the modules' stats() --------------------------------------------------------


def test_section_values_match_stats(world, monkeypatch):
    from newsrag.aliases import stats as alias_stats
    from newsrag.dedup import load as dedup_load
    from newsrag.dedup import stats as dedup_stats
    from newsrag.grouping import judge_cli
    from newsrag.grouping import stats as judge_stats
    from newsrag.stitch import load as stitch_load
    from newsrag.stitch import stats as stitch_stats

    # undo the world's section stubs: the real collect functions over fake module inputs
    monkeypatch.undo()
    monkeypatch.setattr(config, "settings", world.settings)
    dedup_arts = {
        "a": {"stories": [{"printings": [1]}, {"printings": [1, 2]}, {"printings": [1, 2, 3]}]},
        "b": {"stories": [{"printings": list(range(6))}]},
    }
    monkeypatch.setattr(dedup_load, "done_dedup", lambda d=None: {k: k for k in dedup_arts})
    monkeypatch.setattr(dedup_load, "load_dedup", lambda p: dedup_arts[p])
    stitch_arts = {
        "a": {
            "stats": {"source_markers": 2, "continuation_markers": 3},
            "links": [{"flags": []}],
            "unmatched": [{"reason": "no_source"}],
        }
    }
    monkeypatch.setattr(stitch_load, "done_stitch", lambda d=None: {"a": "a"})
    monkeypatch.setattr(stitch_load, "load_stitch", lambda p: stitch_arts[p])
    alias_numbers = {
        "names": 9,
        "entities": 7,
        "auto_pairs": 2,
        "review_pairs": 1,
        "largest_group": 3,
    }
    path = alias_stats.stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**alias_numbers, "auto_groups": [["a", "b"]]}))

    sections, _errors = rb.collect_sections()
    assert sections["Dedup"] == dedup_stats.stats()
    assert sections["Dedup"]["printings_buckets"] == {"1": 1, "2": 1, "3-4": 1, ">=5": 1}
    assert sections["Dedup"]["stories_with_5plus_printings"] == 1
    assert sections["Continuations"] == stitch_stats.stats()
    assert sections["Continuations"]["linked"] == 1
    assert sections["Continuations"]["unmatched_no_source"] == 1
    assert sections["Aliases"] == alias_numbers
    assert {k: v for k, v in alias_stats.stats().items() if k != "auto_groups"} == alias_numbers

    # `judge stats` prints the lines of the same summarize() the grouping bar uses
    pages = [
        {"page_id": "a", "judge_score": 0.9, "page_flags": [], "retried": True},
        {
            "page_id": "b",
            "judge_score": 0.5,
            "page_flags": [judge_stats.JUDGE_LOW],
            "retried": False,
        },
    ]
    summary = judge_stats.summarize(pages, {"a": 0.6})
    assert judge_cli.stats_lines(pages, {"a": 0.6}) == judge_cli.lines_of(summary)
    assert summary["judge_low_pct"] == 50.0 and summary["retry_gain"] == pytest.approx(0.3)
    assert B.grouping_bar(summary).value == "mean 0.700, judge_low 50.00%"


def test_editions_year_counts_match_report_lines():
    from newsrag.editions import mapping

    editions = {2024: {"B": {"status": "ok"}, "C": {"status": "ambiguous"}}}
    pages = [
        (date(2024, 1, 1), "X", [mapping.UNKNOWN]),
        (date(2024, 1, 2), "B", [mapping.STATE_PAGE, mapping.DATE_MISMATCH]),
    ]
    counts = mapping.year_counts(editions, pages)
    assert counts == {
        2024: {
            "codes": 2,
            "ok": 1,
            "ambiguous": 1,
            "unknown_edition": 1,
            "date_mismatch": 1,
            "state_pages": 1,
        }
    }
    first = mapping.report_lines(editions, pages, [])[0]
    assert first == (
        "2024 codes=2 ok=1 ambiguous=1 unknown_edition=1 date_mismatch=1 state_pages=1"
    )


def test_bakeoff_pre_blocks_survive_e4_filter(world, monkeypatch):
    text = "# report\nsee https://example.org\n\nwinner: bge-m3\n"
    monkeypatch.setattr(
        collect,
        "bakeoffs",
        lambda: {"embedding": {"file": "f", "winner": ["bge-m3"], "text": text}, "grouping": None},
    )
    _, page = world.build()
    assert "winner: <strong>bge-m3</strong>" in page
    # E-4 deletes from the line with <pre class="bakeoff"> to the next line with </pre>
    kept, skipping = [], False
    for line in page.splitlines():
        if '<pre class="bakeoff">' in line:
            skipping = True
        if not skipping:
            kept.append(line)
        if skipping and "</pre>" in line:
            skipping = False
    rest = "\n".join(kept)
    assert not re.search(r"https?://", rest)
    assert "<h2>Runs</h2>" in rest  # the filter did not eat the rest of the page


def test_bars_constants():
    """Ticket 031: the seven bar thresholds of ticket 030 FR-2 are unchanged (never lowered)."""
    assert B.MAX_QUARANTINE_PCT == 1.0
    assert B.INDEXED_PCT == 100.0
    assert B.MIN_JUDGE_MEAN == 0.90
    assert B.MAX_JUDGE_LOW_PCT == 5.0
    assert B.MAX_INVENTED_PCT == 2.0
    assert B.MIN_SUPPORTED_PCT == 90.0
    assert B.TEST_SIGNER == "eval-tester"
    assert len(B.NAMES) == 7


def test_reject_in_corpus(monkeypatch):
    """Ticket 031: with `corpus_from_date`, rejects from folders of earlier years are left out."""
    monkeypatch.setattr(
        config,
        "settings",
        config.settings.model_copy(update={"corpus_from_date": date(2026, 1, 1)}),
    )
    assert collect.reject_in_corpus("/s/2026/October-2026/08-10-2026/x.pdf")
    assert not collect.reject_in_corpus("/s/2024/December-2024/18-12-2024/main/out/18V_10.pdf")
    assert collect.reject_in_corpus("/s/unknown/x.pdf")  # no year: counted


@pytest.mark.stores
def test_pages_input_scoped_to_corpus(ledger, monkeypatch):
    from newsrag.ledger import db

    db.upsert_page(**_page("old", date(2024, 12, 1), "/s/2024/01-12-2024/01B-MAIN_01.pdf"))
    db.upsert_page(**_page("new", date(2026, 10, 8), "/s/2026/08-10-2026/08B-MAIN_01.pdf"))
    db.quarantine_page("old", "render:corrupt")
    with db.conn() as c:
        c.execute(
            "insert into quarantine (source_path, source_archive, reason) values (%s, %s, %s)",
            ("/s/2024/December-2024/18-12-2024/18V_10.pdf", "a.zip", "bad_filename"),
        )
    monkeypatch.setattr(
        config,
        "settings",
        config.settings.model_copy(update={"corpus_from_date": date(2026, 1, 1)}),
    )
    data = collect.pages_input()
    assert (data["registered"], data["quarantined_pages"], data["rejects"]) == (1, 0, 0)
