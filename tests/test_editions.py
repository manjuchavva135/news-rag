"""Ticket 011 FR-3..FR-6: the per-year code map, page editions, report; and the folio stage."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from newsrag.editions import mapping, store
from newsrag.editions.folio import choose_place
from newsrag.geo import Place

VJA = ("విజయవాడ", ["ntr"], ["ntr"], "andhra_pradesh")
GNT = ("గుంటూరు", ["guntur"], ["guntur"], "andhra_pradesh")
TPT = ("తిరుపతి", ["tirupati"], ["tirupati"], "andhra_pradesh")
HYD = ("హైదరాబాద్", ["hyderabad"], ["hyderabad"], "telangana")

_n = 0


def rec(code, place=None, *, pub=date(2025, 3, 1), state=None, printed=None):
    global _n
    _n += 1
    name, cur, at, st = place if place else (None, None, None, state)
    return {
        "page_id": f"p{_n:04d}",
        "pub_date": pub,
        "edition_code": code,
        "place_te": name,
        "place_districts_current": cur,
        "place_districts_at_print": at,
        "place_state": st,
        "printed_date": printed,
    }


def build(records):
    return mapping.build_map(records, 0.6, 3)


# --- FR-3 --------------------------------------------------------------------------------------


def test_ok_entry():
    m = build([rec("B", VJA), rec("B", VJA), rec("B", VJA), rec("B", GNT), rec("B")])
    e = m[2025]["B"]
    assert e["name_te"] == "విజయవాడ"
    assert e["n_pages"] == 4  # pages with a place only
    assert e["agreement"] == 0.75
    assert e["status"] == "ok"
    assert (e["districts_current"], e["state"]) == (["ntr"], "andhra_pradesh")


def test_low_agreement_is_ambiguous():
    m = build([rec("V", VJA), rec("V", VJA), rec("V", GNT), rec("V", GNT), rec("V", TPT)])
    assert m[2025]["V"]["agreement"] == 0.4
    assert m[2025]["V"]["status"] == "ambiguous"


def test_fewer_than_three_pages_is_ambiguous():
    m = build([rec("G", GNT), rec("G", GNT), rec("G"), rec("G")])
    assert m[2025]["G"]["n_pages"] == 2
    assert m[2025]["G"]["agreement"] == 1.0
    assert m[2025]["G"]["status"] == "ambiguous"


def test_map_is_per_year():
    m = build([rec("B", VJA, pub=date(2024, 12, 1)), rec("B", GNT, pub=date(2025, 1, 1))])
    assert m[2024]["B"]["name_te"] == "విజయవాడ"
    assert m[2025]["B"]["name_te"] == "గుంటూరు"


def test_multi_letter_gets_names_from_letters():
    recs = [rec("B", VJA) for _ in range(3)] + [rec("G", GNT) for _ in range(3)]
    recs += [rec("BG", VJA)]
    e = build(recs)[2025]["BG"]
    assert e["names_from_letters"] == ["విజయవాడ", "గుంటూరు"]
    assert e["letters_missing"] == []


def test_partial_letters_flag():
    recs = [rec("B", VJA) for _ in range(3)] + [rec("BGX", VJA)]
    m = build(recs)
    assert m[2025]["BGX"]["names_from_letters"] == ["విజయవాడ"]
    assert m[2025]["BGX"]["letters_missing"] == ["G", "X"]
    pe = mapping.page_edition(rec("BGX", VJA), m[2025])
    assert pe.source == "letters"
    assert mapping.LETTERS_PARTIAL in pe.flags


def test_all_and_fh_not_split_into_letters():
    recs = [rec(c, VJA) for c in "ALFH" for _ in range(3)]
    recs += [rec("ALL", HYD) for _ in range(3)] + [rec("FH", HYD) for _ in range(3)]
    recs += [rec("BV-VK", VJA) for _ in range(3)]
    m = build(recs)[2025]
    for code in ("ALL", "FH", "BV-VK"):
        assert "names_from_letters" not in m[code]
        assert "letters_missing" not in m[code]
    assert m["ALL"]["name_te"] == "హైదరాబాద్"
    pe = mapping.page_edition(rec("ALL", HYD), m)
    assert (pe.source, pe.edition_names) == ("code_map", ["హైదరాబాద్"])


def test_hand_added_keys_kept(tmp_path):
    path = tmp_path / "editions.yaml"
    first = build([rec("B", VJA) for _ in range(3)])
    store.write_editions(first, path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert list(data) == [2025]  # years are int keys
    text = path.read_text(encoding="utf-8").replace(
        "    status: ok\n", "    status: ok\n    name_en: Vijayawada\n"
    )
    path.write_text(text, encoding="utf-8")
    second = build([rec("B", GNT) for _ in range(4)])
    store.write_editions(second, path)
    e = yaml.safe_load(path.read_text(encoding="utf-8"))[2025]["B"]
    assert e["name_en"] == "Vijayawada"
    assert (e["name_te"], e["n_pages"]) == ("గుంటూరు", 4)


def test_rebuild_drops_stale_generated_keys(tmp_path):
    path = tmp_path / "editions.yaml"
    entry = {"name_te": "x", "names_from_letters": ["x"], "name_en": "X"}
    merged = mapping.merge_entry(entry, {"name_te": "y", "status": "ok"})
    assert merged == {"name_te": "y", "status": "ok", "name_en": "X"}
    store.write_editions(build([rec("B", VJA)]), path)
    assert yaml.safe_load(path.read_text(encoding="utf-8"))[2025]["B"]["status"] == "ambiguous"


# --- folio place choice (FR-2) -------------------------------------------------------------------


def test_ambiguous_place_prefers_town():
    town = Place("town", "kurnool", "Kurnool", "కర్నూలు", "andhra_pradesh", ["kurnool"], ["kurnool"])
    district = Place(
        "district",
        "kurnool",
        "Kurnool",
        "కర్నూలు",
        "andhra_pradesh",
        ["kurnool", "nandyal"],
        ["kurnool"],
    )
    mandal = Place("mandal", "k", "K", "కర్నూలు", "telangana", ["x"], ["x"])
    assert choose_place([district, mandal, town]) is town
    assert choose_place([district, mandal]) is district
    two_towns = [town, Place("town", "t2", "T", "కర్నూలు", "telangana", ["y"], ["y"])]
    assert choose_place(two_towns) is None
    # all agree on state and districts: one of them, the town
    agree = Place("district", "d", "D", "D", "andhra_pradesh", ["kurnool"], ["kurnool"])
    assert choose_place([agree, town]) is town


def test_state_only_folio():
    from newsrag.editions.folio import parse_folio

    r = parse_folio("తెలంగాణ | 3-3-2025", date(2025, 3, 3))
    assert (r["place_te"], r["place_state"]) == (None, "telangana")


# --- FR-4 / FR-6 ---------------------------------------------------------------------------------


def test_letters_before_code_map():
    recs = [rec("B", VJA) for _ in range(3)] + [rec("G", GNT) for _ in range(3)]
    recs += [rec("BG", VJA) for _ in range(3)]  # BG's own folios are ok too
    m = build(recs)[2025]
    assert m["BG"]["status"] == "ok"
    pe = mapping.page_edition(rec("BG", VJA), m)
    assert pe.source == "letters"
    assert pe.edition_names == ["విజయవాడ", "గుంటూరు"]
    assert pe.flags == []


def test_copies_entry_districts():
    year = {
        "B": {
            "name_te": "విజయవాడ",
            "status": "ok",
            "districts_current": ["ntr"],
            "districts_at_print": ["krishna"],
            "state": "andhra_pradesh",
        },
        "T": {
            "name_te": "హైదరాబాద్",
            "status": "ok",
            "districts_current": ["hyderabad"],
            "districts_at_print": ["hyderabad"],
            "state": "telangana",
        },
        "BT": {
            "name_te": None,
            "status": "ambiguous",
            "names_from_letters": ["విజయవాడ", "హైదరాబాద్"],
            "letters_missing": [],
        },
    }
    pe = mapping.page_edition(rec("B", GNT), year)  # the page's own folio is not used
    assert (pe.edition_names, pe.districts_current, pe.districts_at_print, pe.states) == (
        ["విజయవాడ"],
        ["ntr"],
        ["krishna"],
        ["andhra_pradesh"],
    )
    assert pe.source == "code_map"
    pe = mapping.page_edition(rec("BT"), year)
    assert pe.districts_current == ["ntr", "hyderabad"]
    assert pe.districts_at_print == ["krishna", "hyderabad"]
    assert pe.states == ["andhra_pradesh", "telangana"]


def test_ambiguous_code_falls_back_to_page_folio():
    m = build([rec("V", VJA), rec("V", GNT)])[2025]
    pe = mapping.page_edition(rec("V", GNT), m)
    assert (pe.source, pe.edition_names, pe.flags) == ("folio", ["గుంటూరు"], ["edition_from_folio"])


def test_state_only_page():
    m = build([rec("V"), rec("V")])[2025]
    pe = mapping.page_edition(rec("V", state="telangana"), m)
    assert (pe.edition_names, pe.states, pe.source) == ([], ["telangana"], "folio_state")
    assert pe.flags == [mapping.UNKNOWN]


def test_unknown_edition():
    pe = mapping.page_edition(rec("Z"), {})
    assert (pe.edition_names, pe.states, pe.source, pe.flags) == (
        [],
        [],
        "unknown",
        [mapping.UNKNOWN],
    )


def test_date_mismatch_flag():
    m = build([rec("B", VJA) for _ in range(3)])[2025]
    same = mapping.page_edition(rec("B", VJA, printed="2025-03-01"), m)
    assert mapping.DATE_MISMATCH not in same.flags
    other = mapping.page_edition(rec("B", VJA, printed="2025-03-02"), m)
    assert mapping.DATE_MISMATCH in other.flags
    # informational, never low_confidence
    assert not any(f.startswith("low_confidence:") for f in other.flags)


def test_report_lists_ambiguous():
    recs = [rec("B", VJA) for _ in range(3)] + [rec("V", VJA), rec("V", GNT)]
    editions = build(recs)
    pages = [
        (date(2025, 3, 1), "V", [mapping.UNKNOWN]),
        (date(2025, 3, 1), "B", [mapping.DATE_MISMATCH]),
    ]
    lines = mapping.report_lines(editions, pages, recs)
    assert lines[0] == "2025 codes=2 ok=1 ambiguous=1 unknown_edition=1 date_mismatch=1"
    assert "  2025 V 2 0.50 గుంటూరు:1, విజయవాడ:1" in lines
    assert "  2025 V 1" in lines


# --- the folio stage (no GPU: a fake text reader) ---------------------------------------------------


class FakeText:
    def __init__(self, texts):
        self.texts = list(texts)
        self.requests = []

    def read(self, image, blocks):
        self.requests.append(blocks)
        return [self.texts.pop(0) if self.texts else "" for _ in blocks]


def _page(page_id="2025-05-04_B_main_02", pub=date(2025, 5, 4)):
    from newsrag.stages.base import PageRow

    return PageRow(page_id, pub, "B", "main", 2, None, "x.pdf", "x.zip", 1)


def _final(tmp_path, blocks):
    from PIL import Image

    image = tmp_path / "page.png"
    Image.new("L", (1000, 2000), 255).save(image)
    return {
        "page_id": "2025-05-04_B_main_02",
        "image": {"path": str(image), "width": 1000, "height": 2000},
        "blocks": blocks,
    }


@pytest.fixture
def work(tmp_path, monkeypatch):
    from newsrag import config

    monkeypatch.setattr(config.settings, "__dict__", {**config.settings.__dict__})
    object.__setattr__(config.settings, "work_dir", tmp_path / "work")
    return tmp_path


def test_folio_stage_uses_text_and_rereads_empty(work):
    from newsrag.stages.folio import folio_page

    blocks = [
        {
            "block_id": "b1",
            "order": 1,
            "label": "PageHeader",
            "bbox": [600, 50, 990, 110],
            "text": "ఆదివారం | 4 | మే | 2025",
        },
        {"block_id": "b2", "order": 2, "label": "Picture", "bbox": [10, 40, 500, 150], "text": ""},
        {"block_id": "b3", "order": 3, "label": "Text", "bbox": [10, 200, 500, 900], "text": "కథ"},
        {
            "block_id": "b4",
            "order": 4,
            "label": "PageFooter",
            "bbox": [10, 1900, 990, 1990],
            "text": r"$$ \text{---} $$",
        },
    ]
    fake = FakeText(["విజయవాడ | www.sakshi.com"])
    result = folio_page(_final(work, blocks), _page(), lambda: fake, "folio-v1-test")
    art = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert [b["block_id"] for b in art["folio_blocks"]] == ["b1", "b2", "b4"]
    assert fake.requests == [[{"bbox": [10, 40, 500, 150], "type": "text"}]]
    assert art["folio_text"] == "ఆదివారం | 4 | మే | 2025 | విజయవాడ | www.sakshi.com"
    assert (art["place_te"], art["place_state"], art["printed_date"]) == (
        "విజయవాడ",
        "andhra_pradesh",
        "2025-05-04",
    )
    assert set(art) >= {
        "page_id",
        "folio_blocks",
        "folio_text",
        "place_te",
        "place_kind",
        "place_districts_current",
        "place_districts_at_print",
        "place_state",
        "printed_date",
        "printed_page_no",
    }


def test_folio_stage_reads_the_top_band_when_no_place(work):
    from newsrag.stages.folio import folio_page

    blocks = [
        {"block_id": "b1", "order": 1, "label": "Text", "bbox": [10, 400, 500, 900], "text": "కథ"}
    ]
    fake = FakeText(["2 విజయవాడ । ఆదివారం । మే । 4 । 2025", "సాక్షి"])
    result = folio_page(_final(work, blocks), _page(), lambda: fake, "folio-v1-test")
    art = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert fake.requests == [
        [
            {"bbox": [0, 0, 500, 160], "type": "text"},
            {"bbox": [500, 0, 1000, 160], "type": "text"},
        ]
    ]
    assert art["place_te"] == "విజయవాడ"
    assert art["printed_page_no"] == 2
    assert result.detail["n_band_pieces"] == 2


def test_folio_stage_no_gpu_when_not_needed(work):
    from newsrag.stages.folio import folio_page

    blocks = [
        {
            "block_id": "b1",
            "order": 1,
            "label": "PageHeader",
            "bbox": [0, 10, 900, 100],
            "text": "విజయవాడ | 4-5-2025",
        }
    ]

    def no_gpu():
        raise AssertionError("text model started")

    result = folio_page(_final(work, blocks), _page(), no_gpu, "folio-v1-test")
    assert result.detail["place_te"] == "విజయవాడ"


def test_folio_stage_registered():
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["folio"]
    assert stage.depends_on == ("ocr_final",)
    assert stage.concurrency == 1
    assert stage.version.startswith("folio-v1-")


# --- page_editions in Postgres -------------------------------------------------------------------


@pytest.mark.stores
def test_apply_replaces_page_editions(ledger, tmp_path, monkeypatch):
    from newsrag.ledger import db

    recs = [rec("B", VJA) for _ in range(3)] + [rec("V")]
    for r in recs:
        db.upsert_page(
            page_id=r["page_id"],
            pub_date=r["pub_date"],
            edition_code=r["edition_code"],
            section="main",
            page_num=1,
            source_path=f"/x/{r['page_id']}.pdf",
            source_archive="x",
        )
    monkeypatch.setattr(store, "folio_records", lambda: recs)
    path = tmp_path / "editions.yaml"
    store.write_editions(build(recs), path)
    assert store.apply(path) == 4
    assert store.apply(path) == 4  # replaced, not appended
    with db.conn() as c:
        rows = db.fetch_all(c, "select * from page_editions order by page_id")
    assert len(rows) == 4
    assert rows[0]["edition_names"] == ["విజయవాడ"] and rows[0]["source"] == "code_map"
    assert rows[3]["flags"] == [mapping.UNKNOWN]
    lines = store.report(path)
    assert lines[0] == "2025 codes=2 ok=1 ambiguous=1 unknown_edition=1 date_mismatch=0"
