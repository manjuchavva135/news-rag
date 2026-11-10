"""Ticket 012: geometry rules, reading order, duplicates, article assembly (CPU only)."""

from __future__ import annotations

import pytest

from newsrag.grouping import geometry as geo
from newsrag.grouping.geometry import Params, build_grouping, column_order

P = Params()


def blk(order: int, bbox, text: str = "", label: str = "Text") -> dict:
    return {
        "block_id": f"b{order:03d}",
        "order": order,
        "label": label,
        "type": "text",
        "bbox": list(bbox),
        "text": text,
    }


def art(kind: str, *entries) -> dict:
    return {"kind": kind, "blocks": [{"n": n, "role": role} for n, role in entries]}


def build(blocks, articles, unassigned=(), page_id="p"):
    return build_grouping(articles, list(unassigned), blocks, page_id, P)


def ids(article: dict) -> list[str]:
    return [b["block_id"] for b in article["blocks"]]


# --- FR-5: every block accounted for ----------------------------------------------------------


def test_omitted_blocks_go_unassigned():
    blocks = [
        blk(1, (0, 0, 100, 50), "a"),
        blk(2, (0, 60, 100, 110), "b"),
        blk(3, (0, 120, 100, 170)),
    ]
    got = build(blocks, [art("news", (1, "headline"))], unassigned=[2])
    assert got.unassigned == ["b002", "b003"]
    assert got.page_flags == ["blocks_omitted"]


def test_duplicate_block_flag():
    # block 2 is used by two articles: the first use is kept, the page is flagged
    blocks = [blk(1, (0, 0, 100, 50)), blk(2, (0, 60, 100, 110)), blk(3, (0, 120, 100, 170))]
    got = build(
        blocks,
        [art("news", (1, "headline"), (2, "body")), art("news", (2, "headline"), (3, "body"))],
    )
    assert got.page_flags == ["block_duplicated"]
    assert ids(got.articles[0]) == ["b001", "b002"]
    assert ids(got.articles[1]) == ["b003"]


def test_block_in_article_and_unassigned_keeps_the_article():
    blocks = [blk(1, (0, 0, 100, 50)), blk(2, (0, 60, 100, 110))]
    got = build(blocks, [art("news", (1, "headline"), (2, "body"))], unassigned=[2])
    assert got.page_flags == ["block_duplicated"]
    assert got.unassigned == []


def test_disconnected_flag():
    # two pairs of blocks, each pair close together, the pairs 1000 px apart
    blocks = [
        blk(1, (0, 0, 100, 50)),
        blk(2, (0, 60, 100, 110)),
        blk(3, (0, 1200, 100, 1250)),
        blk(4, (0, 1260, 100, 1310)),
    ]
    far = build(blocks, [art("news", (1, "headline"), (2, "body"), (3, "body"), (4, "body"))])
    assert "disconnected" in far.articles[0]["flags"]
    near = build(blocks[:2], [art("news", (1, "headline"), (2, "body"))])
    assert "disconnected" not in near.articles[0]["flags"]


def test_disconnected_gap_is_edge_to_edge():
    a, b = blk(1, (0, 0, 100, 50)), blk(2, (100 + 60, 0, 300, 50))
    assert geo.gap(a["bbox"], b["bbox"]) == 60
    ok = build([a, b], [art("news", (1, "headline"), (2, "body"))])
    assert "disconnected" not in ok.articles[0]["flags"]
    b2 = blk(2, (100 + 61, 0, 300, 50))
    bad = build([a, b2], [art("news", (1, "headline"), (2, "body"))])
    assert "disconnected" in bad.articles[0]["flags"]
    # touching / overlapping boxes have gap 0
    assert geo.gap((0, 0, 10, 10), (5, 5, 20, 20)) == 0
    # a diagonal gap is the straight distance between the corners
    assert geo.gap((0, 0, 10, 10), (13, 14, 20, 20)) == 5


def test_overlaps_other_flag():
    # article A's union box (blocks 1 and 3, far apart) covers B's block 2 completely
    blocks = [
        blk(1, (0, 0, 400, 50)),
        blk(2, (100, 100, 300, 200)),
        blk(3, (0, 300, 400, 350)),
    ]
    got = build(blocks, [art("news", (1, "headline"), (3, "body")), art("news", (2, "headline"))])
    first, second = got.articles
    assert "overlaps_other" in first["flags"]
    assert "overlaps_other" not in second["flags"]


def test_overlaps_other_needs_more_than_20_percent():
    blocks = [blk(1, (0, 0, 100, 100)), blk(2, (80, 0, 180, 100)), blk(3, (0, 150, 100, 200))]
    # block 2 is exactly 20% inside the union of article A (blocks 1 and 3): not more than 20%
    got = build(blocks, [art("news", (1, "headline"), (3, "body")), art("news", (2, "headline"))])
    assert "overlaps_other" not in got.articles[0]["flags"]
    blocks[1] = blk(2, (79, 0, 179, 100))
    got = build(blocks, [art("news", (1, "headline"), (3, "body")), art("news", (2, "headline"))])
    assert "overlaps_other" in got.articles[0]["flags"]


def test_no_headline_flag():
    blocks = [blk(1, (0, 0, 100, 50), "x"), blk(2, (0, 60, 100, 110), "y")]
    news = build(blocks, [art("news", (1, "body"), (2, "body"))])
    assert "no_headline" in news.articles[0]["flags"]
    editorial = build(blocks, [art("editorial", (1, "body"), (2, "body"))])
    assert "no_headline" in editorial.articles[0]["flags"]
    brief = build(blocks, [art("brief_item", (1, "body"), (2, "body"))])
    assert "no_headline" not in brief.articles[0]["flags"]
    ok = build(blocks, [art("news", (1, "headline"), (2, "body"))])
    assert "no_headline" not in ok.articles[0]["flags"]


def test_headline_below_body_flag():
    # headline y1 = 150; the top body y1 = 100: 150 > 100 + 50 is false, 151 is true
    body = blk(2, (0, 100, 100, 300), "b")
    at_limit = build(
        [blk(1, (0, 150, 100, 190), "h"), body], [art("news", (1, "headline"), (2, "body"))]
    )
    assert "headline_below_body" not in at_limit.articles[0]["flags"]
    below = build(
        [blk(1, (0, 151, 100, 190), "h"), body], [art("news", (1, "headline"), (2, "body"))]
    )
    assert "headline_below_body" in below.articles[0]["flags"]
    no_body = build([blk(1, (0, 500, 100, 540), "h")], [art("news", (1, "headline"))])
    assert "headline_below_body" not in no_body.articles[0]["flags"]


def test_empty_article_dropped():
    blocks = [blk(1, (0, 0, 100, 50)), blk(2, (0, 60, 100, 110))]
    got = build(blocks, [art("news"), art("news", (1, "headline"), (2, "body"))])
    assert len(got.articles) == 1
    assert got.articles[0]["article_id"] == "p#a01"
    # an article whose only blocks were already used by an earlier article is dropped too
    got = build(blocks, [art("news", (1, "headline"), (2, "body")), art("other", (1, "body"))])
    assert len(got.articles) == 1
    assert got.page_flags == ["block_duplicated"]


# --- FR-6: assembled article ------------------------------------------------------------------


def test_text_order_and_exclusions():
    blocks = [
        blk(1, (0, 0, 400, 40), "KICKER"),
        blk(2, (0, 50, 400, 100), "HEAD one"),
        blk(3, (0, 110, 400, 150), "HEAD two"),
        blk(4, (0, 160, 400, 200), "SUB"),
        blk(5, (0, 210, 400, 240), "BY"),
        blk(6, (0, 250, 400, 280), "DATE"),
        blk(7, (0, 290, 190, 600), "body left"),
        blk(8, (210, 290, 400, 600), "body right"),
        blk(9, (0, 610, 400, 800), "", label="Picture"),
        blk(10, (0, 810, 400, 850), "CAPTION"),
        blk(11, (0, 860, 400, 900), "PULLQUOTE"),
        blk(12, (0, 910, 400, 950), "CONT"),
        blk(13, (0, 960, 400, 1000), "TABLE"),
        blk(14, (0, 1010, 400, 1050), "INFO"),
    ]
    entries = [
        (14, "infographic"),
        (13, "table"),
        (8, "body"),
        (7, "body"),
        (12, "continuation_marker"),
        (11, "pull_quote"),
        (10, "caption"),
        (9, "photo"),
        (6, "dateline"),
        (5, "byline"),
        (4, "subheadline"),
        (3, "headline"),
        (2, "headline"),
        (1, "kicker"),
    ]
    got = build(blocks, [art("news", *entries)]).articles[0]
    assert got["headline"] == "HEAD one HEAD two"
    assert got["text"] == "KICKER\n\nSUB\n\nBY\n\nDATE\n\nbody left\n\nbody right"
    roles = [b["role"] for b in got["blocks"]]
    assert roles == [
        "headline",
        "headline",
        "kicker",
        "subheadline",
        "byline",
        "dateline",
        "body",
        "body",
        "caption",
        "photo",
        "table",
        "infographic",
        "pull_quote",
        "continuation_marker",
    ]
    assert ids(got)[:2] == ["b002", "b003"]
    assert got["bbox_union"] == [0, 0, 400, 1050]


def test_article_without_headline_has_empty_headline():
    got = build([blk(1, (0, 0, 100, 50), "t")], [art("other", (1, "body"))]).articles[0]
    assert got["headline"] == ""
    assert got["text"] == "t"


def test_article_numbering_by_lowest_order():
    blocks = [blk(i, (0, i * 100, 100, i * 100 + 50), f"t{i}") for i in range(1, 7)]
    # the model lists the article with the later blocks first
    got = build(
        blocks,
        [
            art("news", (5, "headline"), (6, "body")),
            art("news", (3, "headline"), (4, "body")),
            art("news", (1, "headline"), (2, "body")),
        ],
        page_id="2024-12-06_X_main_01",
    )
    assert [a["article_id"] for a in got.articles] == [
        "2024-12-06_X_main_01#a01",
        "2024-12-06_X_main_01#a02",
        "2024-12-06_X_main_01#a03",
    ]
    assert [ids(a)[0] for a in got.articles] == ["b001", "b003", "b005"]


def test_flags_are_in_fixed_order_and_unique():
    blocks = [blk(1, (0, 0, 100, 50), "x"), blk(2, (0, 2000, 100, 2050), "y")]
    got = build(blocks, [art("news", (1, "body"), (2, "body"))]).articles[0]
    assert got["flags"] == ["disconnected", "no_headline"]


# --- FR-9: reading order ----------------------------------------------------------------------


def order_ids(blocks):
    return [b["block_id"] for b in column_order(blocks, P)]


def test_column_order_two_columns():
    # Surya numbers run across the columns: 1 (left top), 2 (right top), 3 (left bottom)
    blocks = [
        blk(1, (0, 0, 100, 100)),
        blk(2, (150, 0, 250, 100)),
        blk(3, (0, 110, 100, 200)),
        blk(4, (150, 110, 250, 200)),
    ]
    assert order_ids(blocks) == ["b001", "b003", "b002", "b004"]


def test_column_order_ties_go_to_lower_order():
    blocks = [blk(2, (0, 0, 100, 50)), blk(1, (0, 0, 100, 50))]
    assert order_ids(blocks) == ["b001", "b002"]


def test_column_order_same_column_needs_half_of_narrower():
    narrow = blk(1, (0, 0, 100, 50))
    half = blk(2, (50, 60, 400, 110))  # overlaps exactly 50% of the narrower: same column
    assert geo._same_column(narrow["bbox"], half["bbox"], 0.5)
    less = blk(3, (51, 60, 400, 110))
    assert not geo._same_column(narrow["bbox"], less["bbox"], 0.5)


def test_column_order_wide_block_cuts_bands():
    # two columns, a wide block across both, two more columns under it
    blocks = [
        blk(1, (0, 0, 100, 100)),
        blk(2, (150, 0, 250, 100)),
        blk(3, (0, 150, 250, 200)),  # wide
        blk(4, (0, 250, 100, 350)),
        blk(5, (150, 250, 250, 350)),
        blk(6, (0, 360, 100, 450)),
    ]
    assert order_ids(blocks) == ["b001", "b002", "b003", "b004", "b006", "b005"]
    # the wide block is not pulled into a column even though it overlaps both
    reversed_numbers = [dict(b, order=10 - b["order"]) for b in blocks]
    assert [b["order"] for b in column_order(reversed_numbers, P)] == [9, 8, 7, 6, 4, 5]


def test_column_order_wide_block_on_top_goes_first():
    blocks = [
        blk(5, (0, 0, 250, 40)),  # headline over both columns, highest number
        blk(1, (0, 60, 100, 200)),
        blk(2, (150, 60, 250, 200)),
    ]
    assert order_ids(blocks) == ["b005", "b001", "b002"]


def test_column_order_single_and_empty():
    assert column_order([], P) == []
    one = [blk(1, (0, 0, 10, 10))]
    assert column_order(one, P) == one


def test_column_order_009_page():
    boxes = {
        4: (75, 537, 459, 614),
        5: (81, 405, 1652, 521),
        7: (2504, 960, 3226, 1171),
        8: (885, 528, 1689, 1170),
        9: (1763, 426, 2464, 583),
        10: (2505, 424, 3186, 576),
        11: (3248, 436, 3974, 1168),
        12: (72, 632, 861, 1178),
        13: (1755, 760, 2476, 1174),
        14: (1760, 602, 2470, 761),
        15: (2504, 584, 3224, 993),
        32: (884, 408, 1662, 535),
    }
    texts = {5: "ఒక పెద్ద శీర్షిక ఇక్కడ ఉంది", 32: "ఒక పెద్ద శీర్షిక ఇక్కడ ఉంది"}
    blocks = [blk(n, box, texts.get(n, f"text {n}")) for n, box in boxes.items()]
    story1 = [(5, "headline"), (4, "subheadline"), (8, "body"), (12, "body"), (32, "headline")]
    story2 = [
        (9, "headline"),
        (10, "headline"),
        (14, "body"),
        (13, "body"),
        (15, "body"),
        (7, "body"),
        (11, "body"),
    ]
    got = build(blocks, [art("news", *story1), art("news", *story2)]).articles
    one, two = got
    assert ids(one) == ["b005", "b032", "b004", "b012", "b008"]
    assert one["blocks"][1]["duplicate_of"] == "b005"
    assert one["headline"] == "ఒక పెద్ద శీర్షిక ఇక్కడ ఉంది"  # not repeated
    assert one["text"].split("\n\n")[1:] == ["text 12", "text 8"]
    assert "duplicate_block" in one["flags"]
    assert ids(two) == ["b009", "b010", "b014", "b013", "b015", "b007", "b011"]
    assert "order_from_model" not in two["flags"]
    # the same text order when b014 is a subheadline instead of body
    story2[2] = (14, "subheadline")
    two = build(blocks, [art("news", *story1), art("news", *story2)]).articles[1]
    assert two["text"] == "\n\n".join(f"text {n}" for n in (14, 13, 15, 7, 11))


def test_order_from_model_when_unsure():
    # two body blocks overlapping 40% of the narrower (40 px of 100): the columns are unclear
    blocks = [blk(1, (0, 0, 100, 50), "first"), blk(2, (60, 100, 400, 150), "second")]
    got = build(
        blocks,
        [art("brief_item", (2, "body"), (1, "body"))],
    ).articles[0]
    assert ids(got) == ["b002", "b001"]  # the model's order
    assert "order_from_model" in got["flags"]
    # at 50% the rule is sure and orders by position
    blocks[1] = blk(2, (50, 100, 400, 150), "second")
    got = build(blocks, [art("brief_item", (2, "body"), (1, "body"))]).articles[0]
    assert ids(got) == ["b001", "b002"]
    assert "order_from_model" not in got["flags"]
    # 30 px or less is not "unsure": plainly different columns
    blocks[1] = blk(2, (70, 100, 400, 150), "second")  # overlap exactly 30
    assert not geo.order_is_unsure(blocks, P)
    blocks[1] = blk(2, (80, 100, 400, 150), "second")
    assert not geo.order_is_unsure(blocks, P)


def test_order_changed_flag():
    two_cols = [
        blk(1, (0, 0, 100, 100), "a"),
        blk(2, (150, 0, 250, 100), "b"),
        blk(3, (0, 110, 100, 200), "c"),
    ]
    got = build(two_cols, [art("brief_item", (1, "body"), (2, "body"), (3, "body"))]).articles[0]
    assert ids(got) == ["b001", "b003", "b002"]
    assert "order_changed" in got["flags"]
    straight = [blk(1, (0, 0, 100, 100), "a"), blk(2, (0, 110, 100, 200), "b")]
    got = build(straight, [art("brief_item", (1, "body"), (2, "body"))]).articles[0]
    assert "order_changed" not in got["flags"]


# --- FR-10: duplicates ------------------------------------------------------------------------


def test_duplicate_block_detected_inside_bigger_box():
    big = blk(1, (0, 0, 1000, 100), "Chief minister announces new scheme")
    small = blk(2, (100, 10, 900, 110), "Chief minister announces new scheme")
    # 800 x 90 of 800 x 100 = 90% inside
    dups = geo.find_duplicates([big, small], P)
    assert dups == {"b002": "b001"}


def test_duplicate_needs_text_match():
    # a box 90% inside another, but with different text, is kept
    big = blk(1, (0, 0, 1000, 100), "Chief minister announces new scheme")
    small = blk(2, (100, 10, 900, 110), "completely different words here, ok?")
    assert geo.find_duplicates([big, small], P) == {}
    got = build([big, small], [art("news", (1, "headline"), (2, "body"))]).articles[0]
    assert "duplicate_block" not in got["flags"]
    assert "duplicate_of" not in got["blocks"][1]


def test_duplicate_needs_box_inside():
    big = blk(1, (0, 0, 1000, 100), "same words same words")
    side = blk(2, (900, 0, 1900, 100), "same words same words")  # only 10% inside
    assert geo.find_duplicates([big, side], P) == {}


def test_duplicate_empty_text_never_duplicate():
    big = blk(1, (0, 0, 1000, 100), "")
    small = blk(2, (100, 10, 900, 90), "")
    assert geo.find_duplicates([big, small], P) == {}
    assert (
        geo.find_duplicates([blk(1, (0, 0, 1000, 100), "x y"), blk(2, (0, 0, 10, 10), " ")], P)
        == {}
    )


def test_duplicate_text_match_is_partial():
    big = blk(1, (0, 0, 1000, 100), "alpha beta gamma delta epsilon")
    # one wrong character in 22: 95% matched is enough
    small = blk(2, (0, 0, 1000, 90), "alpha beta gamma deltX")
    assert geo.find_duplicates([big, small], P) == {"b002": "b001"}
    assert geo.text_match_frac("abcdefghij", "abcdefgxyz") == pytest.approx(0.7)


def test_duplicate_points_at_original():
    a = blk(1, (0, 0, 1000, 100), "one two three four")
    b = blk(2, (0, 0, 900, 100), "one two three four")
    c = blk(3, (0, 0, 800, 100), "one two three four")
    assert geo.find_duplicates([a, b, c], P) == {"b002": "b001", "b003": "b001"}


def test_duplicate_left_out_of_text_but_kept_in_blocks():
    big = blk(1, (0, 0, 1000, 100), "HEAD")
    small = blk(2, (0, 0, 900, 100), "HEAD")
    body = blk(3, (0, 150, 1000, 400), "BODY")
    got = build(
        [big, small, body], [art("news", (1, "headline"), (2, "body"), (3, "body"))]
    ).articles[0]
    assert got["headline"] == "HEAD"
    assert got["text"] == "BODY"
    assert ids(got) == ["b001", "b003", "b002"]
    assert got["blocks"][2] == {"block_id": "b002", "role": "body", "duplicate_of": "b001"}


def test_009_page_with_the_ids_of_the_ocr_final_artifact():
    """The same page as in the artifact: block ids, `order` and boxes as `ocr_final` has them
    (the ticket's example names the boxes differently: b012 there is b007 here, b032 is b012)."""
    rows = [
        (4, "b004", (75, 537, 459, 614), "మాజీ ఎంపీ హర్షకుమార్"),
        (5, "b005", (81, 405, 1652, 521), "హామీలను నెరవేర్చలేకే డైవర్సన్ పాలిటిక్స్"),
        (7, "b007", (72, 632, 861, 1178), "body one"),
        (8, "b008", (885, 528, 1689, 1170), "body two"),
        (9, "b009", (1763, 426, 2464, 583), "మాయ 'లేడి'.."),
        (10, "b010", (2505, 424, 3186, 576), "జగత్ కిలాడి!"),
        (11, "b011", (3248, 436, 3974, 1168), "col three"),
        (12, "b012", (884, 408, 1662, 535), "నేకే డైవర్సన్ పాలిటిక్స్"),
        (13, "b013", (1755, 760, 2476, 1174), "col one low"),
        (14, "b014", (1760, 602, 2470, 761), "col one top"),
        (15, "b015", (2504, 584, 3224, 993), "col two top"),
        (16, "b016", (2504, 960, 3226, 1171), "col two low"),
    ]
    blocks = [{**blk(order, box, text), "block_id": bid} for order, bid, box, text in rows]
    story1 = [(4, "kicker"), (5, "headline"), (8, "body"), (7, "body"), (12, "headline")]
    story2 = [
        (9, "headline"),
        (10, "headline"),
        (14, "body"),
        (13, "body"),
        (16, "body"),
        (15, "body"),
        (11, "body"),
    ]
    one, two = build(blocks, [art("news", *story1), art("news", *story2)]).articles
    assert ids(one) == ["b005", "b012", "b004", "b007", "b008"]
    assert one["blocks"][1]["duplicate_of"] == "b005"
    assert one["headline"] == "హామీలను నెరవేర్చలేకే డైవర్సన్ పాలిటిక్స్"
    assert ids(two) == ["b009", "b010", "b014", "b013", "b015", "b016", "b011"]
    assert two["headline"] == "మాయ 'లేడి'.. జగత్ కిలాడి!"
