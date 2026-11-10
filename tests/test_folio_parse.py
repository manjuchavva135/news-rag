"""Ticket 011 FR-2: parsing the folio line. Fixtures are folio texts as OCR'd from real pages."""

from __future__ import annotations

from datetime import date

import pytest

from newsrag.editions.folio import (
    find_date,
    find_page_no,
    is_folio_block,
    parse_folio,
    parse_segments,
    strip_noise,
)

# (folio text, pub_date, place_te, state, printed_date, page_no)
REAL = [
    # the old-system folio string named in E-1
    (
        "బుధవారం । 14 । జనవరి । 2026 విజయవాడ । www.sakshi.com",
        date(2026, 1, 14),
        "విజయవాడ",
        "andhra_pradesh",
        "2026-01-14",
        None,
    ),
    (
        "14 శుక్రవారం | 6 | డిసెంబర్ | 2024\nవిజయవాడ | www.sakshi.com",
        date(2024, 12, 6),
        "విజయవాడ",
        "andhra_pradesh",
        "2024-12-06",
        14,
    ),
    (
        "12 / సోమవారం | 27 | జనవరి | 2025\nవిజయవాడ | www.sakshi.com",
        date(2025, 1, 27),
        "విజయవాడ",
        "andhra_pradesh",
        "2025-01-27",
        12,
    ),
    (
        "14. మంగళవారం । 25 । ఫిబ్రవరి । 2025\nవిజయవాడ । www.sakshi.com",
        date(2025, 2, 25),
        "విజయవాడ",
        "andhra_pradesh",
        "2025-02-25",
        14,
    ),
    (
        "ఆదివారం | 11 | మే | 2025\nవిజయవాడ | www.sakshi.com\n3",
        date(2025, 5, 11),
        "విజయవాడ",
        "andhra_pradesh",
        "2025-05-11",
        3,
    ),
    (
        "2 విజయవాడ । ఆదివారం । మే । 4 । 2025 । www.sakshi.com",
        date(2025, 5, 4),
        "విజయవాడ",
        "andhra_pradesh",
        "2025-05-04",
        2,
    ),
    # front-page masthead: date only, no place
    ("సోమవారం | 2-12-2024", date(2024, 12, 2), None, None, "2024-12-02", None),
    # a family supplement printed in Hyderabad
    ("హైదరాబాద్ | సోమవారం ॥ 20-10-2025", date(2025, 10, 20), "హైదరాబాద్", "telangana", "2025-10-20", None),
]


@pytest.mark.parametrize(("text", "pub", "place", "state", "printed", "page_no"), REAL)
def test_real_folios(text, pub, place, state, printed, page_no):
    r = parse_folio(text, pub)
    assert r["place_te"] == place
    assert r["place_state"] == state
    assert r["printed_date"] == printed
    assert r["printed_page_no"] == page_no


def test_e1_old_system_string():
    r = parse_folio("బుధవారం । 14 । జనవరి । 2026 విజయవాడ । www.sakshi.com", date(2026, 1, 14))
    assert (r["place_te"], r["place_state"], r["printed_date"]) == (
        "విజయవాడ",
        "andhra_pradesh",
        "2026-01-14",
    )
    assert r["place_kind"] == "town"
    assert r["place_districts_current"] == ["ntr"]
    assert r["place_districts_at_print"] == ["ntr"]


def test_districts_at_print_follow_the_date():
    r = parse_folio("విజయవాడ | 1-6-2020", date(2020, 6, 1))
    assert r["place_districts_at_print"] == ["krishna"]
    assert r["place_districts_current"] == ["ntr"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("27-12-2024", date(2024, 12, 27)),
        ("27.12.2024", date(2024, 12, 27)),
        ("27 । 12 । 2024", date(2024, 12, 27)),
        ("సోమవారం | 9 | డిసెంబర్ | 2024", date(2024, 12, 9)),
        ("ఆదివారం । మే । 4 । 2025", date(2025, 5, 4)),
        ("౨౭-౧౨-౨౦౨౪", date(2024, 12, 27)),  # Telugu digits
        ("31-02-2024", None),  # not a date
        ("www.sakshi.com", None),
    ],
)
def test_find_date(text, expected):
    assert find_date(text)[0] == expected


def test_page_number_ignores_volume_and_issue():
    text = "సంపుటి: 17 సంచిక: 276 పేజీలు: 14 27-12-2024"
    assert find_page_no(text, find_date(text)[1]) is None


def test_page_number_not_taken_from_words_or_headlines():
    segs = ["[Image of a 3D 3D 3D]", "17 మంది మృతి", "సోమవారం | 3-11-2025"]
    assert parse_segments(segs, date(2025, 11, 3))["printed_page_no"] is None
    segs = ["సాక్షి / రాష్ట్రీయం", "సోమవారం | 9 | డిసెంబర్ | 2024", "$$3$$"]
    assert parse_segments(segs, date(2024, 12, 9))["printed_page_no"] == 3


def test_latex_footers_are_noise_but_bare_numbers_stay():
    assert strip_noise(r"$$ \text{---} \text{---} $$") == ""
    assert strip_noise("$$3$$").strip() == "3"


def test_place_next_to_the_date_wins_over_a_headline():
    # a headline in the top band names a district; the folio line names the edition
    segments = ["నేడు ప్రకాశం జిల్లా నేతలతో జగన్ సమావేశం", "బుధవారం | 11 | డిసెంబర్ | 2024\nవిజయవాడ"]
    assert parse_segments(segments, date(2024, 12, 11))["place_te"] == "విజయవాడ"


def test_state_only():
    r = parse_folio("ఆంధ్రప్రదేశ్ | 5-1-2025", date(2025, 1, 5))
    assert r["place_te"] is None
    assert r["place_state"] == "andhra_pradesh"
    r = parse_folio("Telangana edition", date(2025, 1, 5))
    assert r["place_state"] == "telangana"


def test_nothing_found_is_null():
    r = parse_folio("రాష్ట్రీయం సాక్షి", date(2025, 1, 5))
    assert all(
        r[k] is None
        for k in ("place_te", "place_kind", "place_state", "printed_date", "printed_page_no")
    )


def test_ambiguous_run_keeps_candidates():
    from newsrag.geo import Place

    def resolver(run, on_date):
        if run == "ఊరు":
            return [
                Place("mandal", "a", "A", "ఊరు", "andhra_pradesh", ["x"], ["x"]),
                Place("mandal", "b", "B", "ఊరు", "telangana", ["y"], ["y"]),
            ]
        return []

    r = parse_folio("ఊరు | 1-1-2025", date(2025, 1, 1), resolver)
    assert r["place_te"] is None
    assert {c["id"] for c in r["place_candidates"]} == {"a", "b"}


def test_block_selection():
    labels = ("PageHeader", "PageFooter")
    assert is_folio_block({"label": "PageFooter", "bbox": [0, 900, 10, 990]}, 1000, labels, 0.08)
    assert is_folio_block({"label": "Text", "bbox": [0, 10, 10, 80]}, 1000, labels, 0.08)
    assert not is_folio_block({"label": "Text", "bbox": [0, 10, 10, 81]}, 1000, labels, 0.08)
