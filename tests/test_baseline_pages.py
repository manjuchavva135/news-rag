"""Ticket 027 FR-3, FR-4, FR-5: page map, baseline search, comparison set, calibration, compare."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.baseline import compare, pages
from newsrag.baseline import search as bsearch
from newsrag.cli import app
from newsrag.search import hybrid

runner = CliRunner()
DAY = date(2026, 2, 19)


def _page(page_id: str, section: str, num: int) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "pub_date": DAY,
        "edition_code": "BGNOPRSVACLT",
        "section": section,
        "page_num": num,
    }


NEW_PAGES = [
    _page("2026-02-19_BGNOPRSVACLT_main_01", "main", 1),
    _page("2026-02-19_BGNOPRSVACLT_fea_02", "fea", 2),
    _page("2026-02-19_BGNOPRSVACLT_main_08-09", "main", 8),
    _page("2026-02-19_BGNOPRSVACLT_main_10", "main", 10),
    _page("2026-02-19_BGNOPRSVACLT_main_10-11", "main", 10),
]


@pytest.fixture
def reports(tmp_path, monkeypatch):
    r = tmp_path / "reports"
    current = config.settings.model_copy(
        update={
            "reports_dir": r,
            "baseline_page_map_file": r / "baseline-page-map.csv",
            "baseline_calibration_file": r / "baseline-calibration.json",
            "baseline_compare_file": r / "baseline-compare.json",
            "baseline_set_file": tmp_path / "retrieval-baseline-v1.jsonl",
        }
    )
    monkeypatch.setattr(config, "settings", current)
    return r


# --- page ids and the map ---------------------------------------------------------------------


def test_parse_old_page_ids():
    assert pages.parse_old_page_id("ap-bgnoprsvaclt-2026-02-19#p01") == pages.OldPage(
        DAY, "BGNOPRSVACLT", "main", 1
    )
    assert pages.parse_old_page_id("ap-b-2026-03-12#fea02") == pages.OldPage(
        date(2026, 3, 12), "B", "fea", 2
    )
    assert pages.parse_old_page_id("ap-b-2026-03-12") is None
    assert pages.parse_old_page_id("ap-b-2026-02-30#p01") is None


def test_map_main_fea_and_spread():
    mapping = pages.map_pages(
        [
            "ap-bgnoprsvaclt-2026-02-19#p01",
            "ap-bgnoprsvaclt-2026-02-19#fea02",
            "ap-bgnoprsvaclt-2026-02-19#p08",
            "ap-bgnoprsvaclt-2026-02-19#p05",
            "ap-other-2026-02-19#p01",
            "garbage",
        ],
        NEW_PAGES,
    )
    assert mapping["ap-bgnoprsvaclt-2026-02-19#p01"] == ["2026-02-19_BGNOPRSVACLT_main_01"]
    assert mapping["ap-bgnoprsvaclt-2026-02-19#fea02"] == ["2026-02-19_BGNOPRSVACLT_fea_02"]
    assert mapping["ap-bgnoprsvaclt-2026-02-19#p08"] == ["2026-02-19_BGNOPRSVACLT_main_08-09"]
    assert mapping["ap-bgnoprsvaclt-2026-02-19#p05"] == []
    assert mapping["ap-other-2026-02-19#p01"] == [] and mapping["garbage"] == []


def test_spread_maps_both(reports):
    mapping = pages.map_pages(["ap-bgnoprsvaclt-2026-02-19#p10"], NEW_PAGES)
    both = ["2026-02-19_BGNOPRSVACLT_main_10", "2026-02-19_BGNOPRSVACLT_main_10-11"]
    assert mapping["ap-bgnoprsvaclt-2026-02-19#p10"] == both
    path = pages.write_page_map(mapping)
    text = path.read_text()
    assert text.splitlines()[0] == "old_page_id,new_page_id,found_in_new,new_page_ids"
    assert "ap-bgnoprsvaclt-2026-02-19#p10,2026-02-19_BGNOPRSVACLT_main_10,True," in text
    assert pages.read_page_map(path) == mapping
    # a baseline hit on that old page gets both new page ids
    hit = _baseline_hits(["c1"], {"c1": "ap-bgnoprsvaclt-2026-02-19#p10"})[0]
    assert hit.page_ids == both


def test_unmapped_page_written_false(reports):
    path = pages.write_page_map({"ap-x-2026-01-01#p01": []})
    assert "ap-x-2026-01-01#p01,,False," in path.read_text()
    assert pages.read_page_map(path) == {"ap-x-2026-01-01#p01": []}


# --- the baseline search backend --------------------------------------------------------------


def _baseline_hits(chunk_ids: list[str], page_of: dict[str, str], **kwargs: Any) -> list[Any]:
    calls: dict[str, Any] = {}

    def embed(query: str, profile: str) -> list[float]:
        calls["profile"] = profile
        return [0.0]

    patches = {
        (hybrid, "embed_query"): embed,
        (bsearch, "dense_search"): lambda v, limit: [{"chunk_id": c} for c in chunk_ids],
        (bsearch, "bm25_search"): lambda q, limit: [{"chunk_id": c} for c in reversed(chunk_ids)],
        (bsearch, "chunk_rows"): lambda ids: {
            c: {
                "chunk_id": c,
                "story_id": f"s-{c}",
                "page_id": page_of.get(c),
                "text": f"text {c}",
                "headline": f"head {c}",
                "pub_date": DAY,
            }
            for c in ids
        },
    }
    with pytest.MonkeyPatch.context() as mp:
        for (mod, name), fn in patches.items():
            mp.setattr(mod, name, fn)
        hits = hybrid.search("q", store="baseline", **kwargs)
    assert calls["profile"] == config.settings.baseline_embed_profile
    return hits


def test_baseline_search_fields(reports):
    pages.write_page_map({"ap-b-2026-02-19#p01": ["2026-02-19_B_main_01"]})
    hits = _baseline_hits(["c1", "c2"], {"c1": "ap-b-2026-02-19#p01", "c2": "ap-b-2026-02-19#p02"})
    assert [h.chunk_id for h in hits] == ["c1", "c2"]
    assert hits[0].page_ids == ["2026-02-19_B_main_01"] and hits[1].page_ids == []
    assert hits[0].text == "text c1" and hits[0].headline == "head c1"
    assert hits[0].pub_date == "2026-02-19" and hits[0].story_id == "s-c1"


def test_baseline_search_refuses_page_filter_and_needs_map(reports):
    with pytest.raises(hybrid.StoreNotConfigured, match="map-pages"):
        hybrid.search("q", store="baseline")
    pages.write_page_map({})
    with pytest.raises(ValueError, match="store new only"):
        hybrid.search("q", store="baseline", filters={"page_ids": ["p"]})


# --- build-set --------------------------------------------------------------------------------


def _story(i: int, page: str) -> dict[str, Any]:
    return {
        "story_id": f"st{i:03d}",
        "pub_date": DAY,
        "kind": "news",
        "headline": f"గుంటూరు వార్త {i}",
        "text": "తెలుగు వార్త పాఠం " * 20 + str(i),
        "page_ids": [page],
    }


def _ask(prompt: str) -> str:
    return json.dumps({"question": "గుంటూరు జిల్లాలో ఏమి జరిగింది ఈ రోజు?"})


def _ask_any(prompt: str) -> str:
    if "in English" in prompt:
        return json.dumps({"question": "What happened in the district on that day?"})
    return _ask(prompt)


def test_build_set_only_mapped_stories(reports, tmp_path):
    mapping = {"ap-b-2026-02-19#p01": ["2026-02-19_B_main_01"], "ap-b-2026-02-19#p02": []}
    stories = [_story(i, "2026-02-19_B_main_01") for i in range(6)] + [
        _story(100 + i, "2026-02-19_B_main_02") for i in range(6)
    ]
    out = tmp_path / "set.jsonl"
    summary = compare.build_set(
        n=5, seed=7, out=out, mapping=mapping, ask=_ask_any, stories=stories, echo=lambda _: None
    )
    assert summary["n"] == 5 and summary["stories_available"] == 6
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert all(x["gold_page_ids"] == ["2026-02-19_B_main_01"] for x in lines)


def test_short_set_fails(reports, tmp_path, monkeypatch):
    mapping = {"ap-b-2026-02-19#p01": ["2026-02-19_B_main_01"]}
    stories = [_story(i, "2026-02-19_B_main_01") for i in range(3)]
    with pytest.raises(compare.CompareError, match="short set: 3 of 5"):
        compare.build_set(
            n=5,
            out=tmp_path / "s.jsonl",
            mapping=mapping,
            ask=_ask_any,
            stories=stories,
            echo=lambda _: None,
        )

    def short(**kwargs: Any) -> dict[str, Any]:
        raise compare.CompareError("short set: 42 of 100 questions (42 stories on mapped pages)")

    monkeypatch.setattr("newsrag.baseline.cli._write_page_map", lambda: None)
    monkeypatch.setattr(compare, "build_set", short)
    result = runner.invoke(app, ["baseline", "build-set"])
    assert result.exit_code == 1 and "short set: 42 of 100" in result.output


# --- calibrate --------------------------------------------------------------------------------


def _write_set(path: Path, n: int) -> list[dict[str, Any]]:
    lines = []
    for i in range(n):
        lines.append(
            {
                "qid": f"q{i + 1:04d}",
                "lang": "te",
                "question": f"ప్రశ్న {i}",
                "story_id": f"st{i:03d}",
                "gold_page_ids": [f"2026-02-19_B_main_{i + 1:02d}"],
                "gold_text": f"శీర్షిక {i}\n" + f"తెలుగు వార్త పాఠం సంఖ్య {i} " * 10,
                "pub_date": "2026-02-19",
                "in_baseline_window": True,
            }
        )
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
    return lines


def test_calibrate_share_and_file(reports):
    set_file = config.settings.baseline_set_file
    lines = _write_set(set_file, 10)
    mapping = {
        f"ap-b-2026-02-19#p{i + 1:02d}": [q["gold_page_ids"][0]] for i, q in enumerate(lines)
    }

    def old_chunks(old_ids: list[str]) -> list[dict[str, Any]]:
        out = []
        for old in old_ids:
            i = int(old[-2:]) - 1
            # stories 0..7: the old OCR read the same text; 8, 9: unrelated text
            text = lines[i]["gold_text"] if i < 8 else "పూర్తిగా వేరే వార్త " * 10
            out.append({"page_id": old, "text": text})
        return out

    report = compare.calibrate(n=10, seed=9, mapping=mapping, old_chunks=old_chunks)
    assert report["calibration"] == 80.0 and report["n"] == 10 and report["passed"] == 8
    data = json.loads(config.settings.baseline_calibration_file.read_text())
    assert set(data) >= {"calibration", "n", "sampled_from", "at"}
    assert data["sampled_from"] == str(set_file)


# --- compare ----------------------------------------------------------------------------------


def _fake_search(questions: list[dict[str, Any]], new_right: int, old_right: int):
    calls: list[dict[str, Any]] = []

    def search_fn(query: str, k: int, store: str, filters: Any, **kwargs: Any) -> list[Any]:
        calls.append({"store": store, "filters": filters})
        i = next(n for n, q in enumerate(questions) if q["question"] == query)
        q = questions[i]
        right = (new_right if store == "new" else old_right) > i
        text = q["gold_text"] if right else "పూర్తిగా వేరే వార్త " * 10
        return [
            hybrid.Hit(
                chunk_id="c", story_id="s", score=1.0, page_ids=q["gold_page_ids"], text=text
            )
        ]

    return search_fn, calls


def test_calibration_file_required(reports):
    result = runner.invoke(app, ["baseline", "compare"])
    assert result.exit_code == 1
    assert "run 'newsrag baseline calibrate' first" in result.output
    config.settings.baseline_calibration_file.parent.mkdir(parents=True, exist_ok=True)
    config.settings.baseline_calibration_file.write_text(json.dumps({"calibration": 72.0}))
    result = runner.invoke(app, ["baseline", "compare"])
    assert result.exit_code == 1 and "calibration below 80%: 72" in result.output


def test_compare_output_paths(reports):
    set_file = config.settings.baseline_set_file
    lines = _write_set(set_file, 10)
    mapping = {
        f"ap-b-2026-02-19#p{i + 1:02d}": [q["gold_page_ids"][0]] for i, q in enumerate(lines)
    }
    mapping["ap-b-2026-02-19#p99"] = []
    reports.mkdir(parents=True, exist_ok=True)
    config.settings.baseline_calibration_file.write_text(json.dumps({"calibration": 86.0}))
    search_fn, calls = _fake_search(lines, new_right=9, old_right=6)

    result = compare.compare(mapping=mapping, search_fn=search_fn)
    assert result == {
        "set": str(set_file),
        "calibration": 86.0,
        "old": {"recall_at_10": 0.6, "mrr": 0.6, "n": 10},
        "new": {"recall_at_10": 0.9, "mrr": 0.9, "n": 10},
        "new_beats_old": True,
    }
    assert compare.compare_line(result) == (
        "old recall@10=0.6000 new recall@10=0.9000 new_beats_old=true"
    )
    assert json.loads((reports / "baseline-compare.json").read_text()) == result
    assert json.loads((reports / "baseline-compare-old.json").read_text())["store"] == "baseline"
    assert json.loads((reports / "baseline-compare-new.json").read_text())["store"] == "new"
    assert not list(reports.glob("retrieval-*.json"))
    # baseline unfiltered; new restricted to every mapped new page
    assert {c["filters"] is None for c in calls if c["store"] == "baseline"} == {True}
    restricted = [c["filters"]["page_ids"] for c in calls if c["store"] == "new"]
    assert restricted and all(
        sorted(r) == sorted(pages.mapped_new_pages(mapping)) for r in restricted
    )


def test_compare_tie_is_not_a_win(reports):
    set_file = config.settings.baseline_set_file
    lines = _write_set(set_file, 4)
    mapping = {
        f"ap-b-2026-02-19#p{i + 1:02d}": [q["gold_page_ids"][0]] for i, q in enumerate(lines)
    }
    reports.mkdir(parents=True, exist_ok=True)
    config.settings.baseline_calibration_file.write_text(json.dumps({"calibration": 80.0}))
    search_fn, _ = _fake_search(lines, new_right=2, old_right=2)
    assert compare.compare(mapping=mapping, search_fn=search_fn)["new_beats_old"] is False
