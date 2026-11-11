"""Continuation stitching tests (ticket 016): synthetic articles, no GPU, no data."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.stitch import cli as stitch_cli
from newsrag.stitch import markers as mk
from newsrag.stitch import verify as vf
from newsrag.stitch.match import (
    AMBIGUOUS,
    UNMATCHED_FLAG,
    PageInfo,
    Params,
    collect_parts,
    editions_overlap,
    match_parts,
    stitch_date,
    tokens,
)
from newsrag.stitch.merge import link_pairs, merge_articles

PATTERNS = mk.load_patterns(Path(__file__).resolve().parents[1] / "continuations.yaml")
P = Params()
DAY = date(2025, 8, 2)


def tok(i: int) -> str:
    """A distinct two-letter Telugu word."""
    return chr(0x0C15 + i // 20) + chr(0x0C15 + i % 20)


def words(*ids: int) -> str:
    return " ".join(tok(i) for i in ids)


def page(num: int, edition: str = "B", end: int | None = None, section: str = "main") -> PageInfo:
    return PageInfo(f"{DAY}_{edition}_{section}_{num:02d}", edition, section, num, end)


def art(pg: PageInfo, n: int, text: str, head: str = "", kind: str = "news", **kw) -> dict:
    return {
        "article_id": f"{pg.page_id}#a{n:02d}",
        "kind": kind,
        "blocks": [{"block_id": f"b{n:03d}", "role": "body"}],
        "headline": head,
        "text": text,
        "bbox_union": kw.pop("bbox", [100, 100 * n, 900, 100 * n + 80]),
        "flags": kw.pop("flags", []),
        "judge_verdict": "correct",
    }


def group(pg: PageInfo, *articles: dict, unassigned: list[str] | None = None) -> dict:
    return {"page_id": pg.page_id, "articles": list(articles), "unassigned": unassigned or []}


def run(pages, groups, blocks=None, params: Params = P):
    return stitch_date(
        DAY, pages, {g["page_id"]: g for g in groups}, blocks or {}, PATTERNS, params
    )


def link_of(artifact, source_id):
    found = [x for x in artifact["links"] if x["source_article_id"] == source_id]
    return found[0] if found else None


def reasons(artifact):
    return {u["article_id"]: u["reason"] for u in artifact["unmatched"]}


SRC = words(*range(20))  # the story
CONT = words(*range(10, 30))  # its continuation: 10 shared words of 30 -> 0.33


def pair(src_marker="మిగతా 3వ పేజీలో", cont_marker="మొదటి పేజీ తరువాయి", **kw):
    """Source on page 1, continuation on page 3; returns (pages, groups, source, cont)."""
    p1, p3 = page(1, kw.get("e1", "B")), page(3, kw.get("e3", "B"))
    s = art(p1, 1, f"{SRC} {src_marker}", head=kw.get("head", ""))
    c = art(p3, 1, f"{cont_marker} {CONT}")
    return [p1, p3], [group(p1, s), group(p3, c)], s, c


# --- FR-1: the patterns ---------------------------------------------------------------------


def test_patterns_file_loads_both_kinds():
    kinds = {p.kind for p in PATTERNS}
    assert kinds == {mk.CONTINUES_ON, mk.CONTINUED_FROM}


@pytest.mark.parametrize(
    ("text", "kind", "page"),
    [
        ("... మిగతా 9వ పేజీలో", "a", 9),
        ("... మిగతా 9న పేజీలో ▶", "a", 9),
        ("... మిగతా ౭వ పేజీలో", "a", 7),  # Telugu digit
        ("మిగతా ఒవ పేజీలో", "a", None),  # OCR cannot read the number
        ("మొదటి పేజీ తరువాయి", "b", 1),  # front page wording means page 1
        ("▶ మొదటి పేజీ తరువాయి బిల్లులు ఇలా", "b", 1),
        ("(...6వ పేజీ తరువాయి)", "b", 6),
        ("6 పేజీ తరువాయి", "b", 6),
        ("పేజీ తరువాయి", "b", None),
        ("మొదటి ఏజీ తరువాయి నివేదికలో", "b", 1),
    ],
)
def test_find_markers(text, kind, page):
    found = mk.find_markers(text, PATTERNS)
    assert [(m.kind, m.page) for m in found] == [(kind, page)]


def test_find_markers_ignores_ordinary_text():
    assert mk.find_markers("దానిని మొదటి పేజీలో ప్రచురించాలి. మెడికల్ పేజీలో లోకల్ కోటా", PATTERNS) == []


def test_strip_markers_removes_phrase_and_glyphs():
    text = f"{words(1, 2)} ▶ మిగతా 9వ పేజీలో"
    assert mk.strip_markers(text, PATTERNS) == words(1, 2)
    text = f"(...6వ పేజీ తరువాయి) {words(3, 4)}"
    assert mk.strip_markers(text, PATTERNS) == words(3, 4)
    assert mk.strip_markers(f"{words(1)} మిగతా 9వ పేజీలో $\\blacktriangleright$", PATTERNS) == words(1)


# --- FR-2 / FR-3: matching ------------------------------------------------------------------


def test_front_page_marker_means_page_1():
    pages, groups, s, c = pair(cont_marker="మొదటి పేజీ తరువాయి")
    link = link_of(run(pages, groups), s["article_id"])
    assert link is not None and link["continuation_article_id"] == c["article_id"]
    assert link["method"] == "page_ref"  # the (b) marker names page 1 = the source's page


def test_page_ref_bonus():
    pages, groups, s, _ = pair(cont_marker="మొదటి పేజీ తరువాయి")
    with_ref = link_of(run(pages, groups), s["article_id"])
    pages, groups, s, _ = pair(cont_marker="పేజీ తరువాయి")  # no page named
    without = link_of(run(pages, groups), s["article_id"])
    assert with_ref["method"] == "page_ref" and without["method"] == "similarity"
    assert with_ref["score"] == pytest.approx(without["score"] + P.page_ref_bonus, abs=1e-3)
    assert without["score"] == pytest.approx(10 / 30, abs=1e-3)


def test_edition_overlap_required():
    pages, groups, s, _c = pair(e1="B", e3="G")
    art_ = run(pages, groups)
    assert art_["links"] == []
    assert reasons(art_)[s["article_id"]] == "no_candidate"
    pages, groups, s, _c = pair(e1="BG", e3="GN")  # share the letter G
    assert len(run(pages, groups)["links"]) == 1


def test_all_fh_overlap_both_sides():
    assert editions_overlap("ALL", "B") and editions_overlap("B", "ALL")
    assert editions_overlap("FH", "G") and editions_overlap("G", "FH")
    assert editions_overlap("B-VK", "G") and editions_overlap("G", "B-VK")
    assert not editions_overlap("B", "G")
    assert editions_overlap("BG", "GN")
    pages, groups, _s, _ = pair(e1="ALL", e3="G")
    assert len(run(pages, groups)["links"]) == 1
    pages, groups, _s, _ = pair(e1="B", e3="FH")
    assert len(run(pages, groups)["links"]) == 1


def test_section_must_match():
    pages, groups, _s, _c = pair()
    pages[1] = PageInfo(pages[1].page_id, "B", "supplement", 3)
    assert run(pages, groups)["links"] == []


def test_ambiguous_flag():
    p1, p3 = page(1), page(3)
    s = art(p1, 1, f"{SRC} మిగతా 3వ పేజీలో")
    c1 = art(p3, 1, f"మొదటి పేజీ తరువాయి {CONT}")
    c2 = art(p3, 2, f"మొదటి పేజీ తరువాయి {CONT}")  # same words: same score
    out = run([p1, p3], [group(p1, s), group(p3, c1, c2)])
    (link,) = out["links"]
    assert link["flags"] == [AMBIGUOUS]
    assert link["continuation_article_id"] == c1["article_id"]  # ties: lowest id
    # a clearly worse second candidate does not flag
    c2["text"] = f"మొదటి పేజీ తరువాయి {words(15, 16, 17, 100, 101, 102, 103, 104, 105, 106)}"
    (link,) = run([p1, p3], [group(p1, s), group(p3, c1, c2)])["links"]
    assert link["flags"] == []
    assert stitch_cli.totals([run([p1, p3], [group(p1, s), group(p3, c1, c2)])])["ambiguous"] == 0


def test_unmatched_reasons():
    p1, p2, p4, p5, p6 = (page(n) for n in (1, 2, 4, 5, 6))
    no_target = art(p1, 1, f"{words(40, 41)} మిగతా 9వ పేజీలో")  # page 9 does not exist
    no_cand = art(p1, 2, f"{words(42, 43)} మిగతా 2వ పేజీలో")  # page 2 has no continuation part
    below = art(p1, 3, f"{words(44, 45)} మిగతా 4వ పేజీలో")
    below_c = art(p4, 1, f"పేజీ తరువాయి {words(200, 201)}")  # no shared word, no page named
    taken_a = art(p1, 4, f"{SRC} మిగతా 5వ పేజీలో")
    taken_b = art(p1, 5, f"{words(*range(15))} మిగతా 5వ పేజీలో")
    only_c = art(p5, 1, f"మొదటి పేజీ తరువాయి {CONT}")
    orphan_c = art(p6, 1, f"{words(300, 301)} పేజీ తరువాయి {words(302)}")
    out = run(
        [p1, p2, p4, p5, p6],
        [
            group(p1, no_target, no_cand, below, taken_a, taken_b),
            group(p2),
            group(p6, orphan_c),
            group(p4, below_c),
            group(p5, only_c),
        ],
    )
    got = reasons(out)
    assert got[no_target["article_id"]] == "no_target_page"
    assert got[no_cand["article_id"]] == "no_candidate"
    assert got[below["article_id"]] == "below_score"
    assert got[orphan_c["article_id"]] == "no_source"
    assert got[below_c["article_id"]] == "no_source"
    # taken_a (the better source) won only_c; taken_b lost it
    assert link_of(out, taken_a["article_id"])["continuation_article_id"] == only_c["article_id"]
    assert got[taken_b["article_id"]] == "taken"
    assert all(u["marker"] for u in out["unmatched"])
    totals = stitch_cli.totals([out])
    assert totals["unmatched_taken"] == 1 and totals["unmatched_no_source"] == 2
    assert totals["linked"] == 1 and totals["source_markers"] == 5


def test_one_link_per_continuation_and_taken_reason():
    p1, p3 = page(1, "B"), page(1, "G")
    pc = page(3, "BG")
    s1 = art(p1, 1, f"{SRC} మిగతా 3వ పేజీలో")
    s2 = art(p3, 1, f"{words(*range(14))} మిగతా 3వ పేజీలో")
    c = art(pc, 1, f"మొదటి పేజీ తరువాయి {CONT}")
    out = run([p1, p3, pc], [group(p1, s1), group(p3, s2), group(pc, c)])
    assert len(out["links"]) == 1  # global: one link per continuation part per date
    assert out["links"][0]["source_article_id"] == s1["article_id"]
    assert reasons(out) == {s2["article_id"]: "taken"}


def test_greedy_once_per_source_and_continuation():
    p1, p3 = page(1), page(3)
    s1 = art(p1, 1, f"{words(*range(20))} మిగతా 3వ పేజీలో")
    s2 = art(p1, 2, f"{words(*range(50, 70))} మిగతా 3వ పేజీలో")
    c1 = art(p3, 1, f"మొదటి పేజీ తరువాయి {words(*range(55, 75))}")
    c2 = art(p3, 2, f"మొదటి పేజీ తరువాయి {words(*range(10, 30))}")
    out = run([p1, p3], [group(p1, s1, s2), group(p3, c1, c2)])
    got = {x["source_article_id"]: x["continuation_article_id"] for x in out["links"]}
    assert got == {s1["article_id"]: c2["article_id"], s2["article_id"]: c1["article_id"]}


def test_spread_target_page():
    p1, spread = page(1), page(4, end=5)
    s = art(p1, 1, f"{SRC} మిగతా 5వ పేజీలో")
    c = art(spread, 1, f"మొదటి పేజీ తరువాయి {CONT}")
    out = run([p1, spread], [group(p1, s), group(spread, c)])
    assert link_of(out, s["article_id"])["continuation_article_id"] == c["article_id"]
    # the (b) marker may name any page of a source spread
    sp1, p6 = page(2, end=3), page(6)
    s2 = art(sp1, 1, f"{SRC} మిగతా 6వ పేజీలో")
    c2 = art(p6, 1, f"3వ పేజీ తరువాయి {CONT}")
    out = run([sp1, p6], [group(sp1, s2), group(p6, c2)])
    assert link_of(out, s2["article_id"])["method"] == "page_ref"


def test_source_without_page_number_searches_later_pages():
    p2, p5, p1 = page(2), page(5), page(1)
    s = art(p2, 1, f"{SRC} మిగతా ఒవ పేజీలో")
    later = art(p5, 1, f"పేజీ తరువాయి {CONT}")
    earlier = art(p1, 1, f"పేజీ తరువాయి {CONT}")
    out = run([p1, p2, p5], [group(p1, earlier), group(p2, s), group(p5, later)])
    assert link_of(out, s["article_id"])["continuation_article_id"] == later["article_id"]


def test_only_eligible_kinds_take_part():
    pages, groups, s, c = pair()
    c["kind"] = "ad_commercial"
    assert run(pages, groups)["links"] == []
    s["kind"] = "brief_item"
    c["kind"] = "editorial"
    assert len(run(pages, groups)["links"]) == 1


def test_chain_three_parts():
    p1, p3, p6 = page(1), page(3), page(6)
    a = art(p1, 1, f"{words(*range(20))} మిగతా 3వ పేజీలో", head=words(500))
    b = art(p3, 1, f"మొదటి పేజీ తరువాయి {words(*range(10, 30))} మిగతా 6వ పేజీలో", head=words(501))
    c = art(p6, 1, f"3వ పేజీ తరువాయి {words(*range(20, 40))}", head=words(502))
    groups = [group(p1, a), group(p3, b), group(p6, c)]
    out = run([p1, p3, p6], groups)
    assert {(x["source_article_id"], x["continuation_article_id"]) for x in out["links"]} == {
        (a["article_id"], b["article_id"]),
        (b["article_id"], c["article_id"]),
    }
    assert out["unmatched"] == []
    (merged,) = merge_articles([p1, p3, p6], {g["page_id"]: g for g in groups}, out, PATTERNS)
    assert merged.article_id == a["article_id"]
    assert [x["article_id"] for x in merged.parts] == [
        a["article_id"],
        b["article_id"],
        c["article_id"],
    ]


def test_no_link_cycles():
    p1, p3 = page(1), page(3)
    a = art(p1, 1, f"పేజీ తరువాయి {SRC} మిగతా 3వ పేజీలో")
    b = art(p3, 1, f"మొదటి పేజీ తరువాయి {SRC} మిగతా 1వ పేజీలో")
    out = run([p1, p3], [group(p1, a), group(p3, b)])
    assert len(out["links"]) == 1  # a->b and b->a would be a loop


# --- markers in blocks ------------------------------------------------------------------------


def with_marker_block(text_of_marker, bbox, role="continuation_marker", unassigned=False):
    p1, p3 = page(1), page(3)
    s = art(p1, 1, SRC, bbox=[100, 100, 900, 600])
    s["blocks"].append({"block_id": "bm", "role": role})
    c = art(p3, 1, f"మొదటి పేజీ తరువాయి {CONT}")
    g1 = group(p1, s, unassigned=["bm"] if unassigned else None)
    if unassigned:
        s["blocks"].pop()
    blocks = {p1.page_id: {"bm": {"block_id": "bm", "text": text_of_marker, "bbox": bbox}}}
    return [p1, p3], [g1, group(p3, c)], blocks, s, c


def test_marker_block_of_the_article_is_searched():
    pages, groups, blocks, s, _c = with_marker_block("▶ మిగతా 3వ పేజీలో", [100, 610, 400, 650])
    out = run(pages, groups, blocks)
    assert link_of(out, s["article_id"]) is not None


def test_unassigned_marker_attached():
    # an unassigned block 40 px under the article's box joins it
    pages, groups, blocks, s, _c = with_marker_block(
        "మిగతా 3వ పేజీలో", [100, 640, 400, 680], unassigned=True
    )
    out = run(pages, groups, blocks)
    assert link_of(out, s["article_id"]) is not None
    assert out["stats"]["orphan_markers"] == 0


def test_orphan_markers():
    # 300 px away from every article: ignored and counted
    pages, groups, blocks, _s, _c = with_marker_block(
        "మిగతా 3వ పేజీలో", [100, 900, 400, 940], unassigned=True
    )
    out = run(pages, groups, blocks)
    assert out["stats"]["orphan_markers"] == 1
    # the marker is not used as a source marker: only the reverse match can link the parts
    assert [x["method"] for x in out["links"]] == ["reverse"]


def test_marker_block_in_non_eligible_article_is_attached():
    p1, p3 = page(1), page(3)
    s = art(p1, 1, SRC, bbox=[100, 100, 900, 600])
    pic = art(p1, 2, "", kind="other", bbox=[100, 610, 400, 650])
    pic["blocks"] = [{"block_id": "bm", "role": "continuation_marker"}]
    c = art(p3, 1, f"మొదటి పేజీ తరువాయి {CONT}")
    blocks = {
        p1.page_id: {"bm": {"block_id": "bm", "text": "మిగతా 3వ పేజీలో", "bbox": [100, 610, 400, 650]}}
    }
    out = run([p1, p3], [group(p1, s, pic), group(p3, c)], blocks)
    assert link_of(out, s["article_id"]) is not None


# --- reverse match (FR-3, FR-7) -----------------------------------------------------------


def test_reverse_match():
    p1, p3 = page(1), page(3)
    s = art(p1, 1, SRC)  # no marker recognised on the source
    other = art(p1, 2, words(*range(300, 320)))
    c = art(p3, 1, f"మొదటి పేజీ తరువాయి {CONT}")
    out = run([p1, p3], [group(p1, s, other), group(p3, c)])
    (link,) = out["links"]
    assert link["method"] == "reverse"
    assert (link["source_article_id"], link["continuation_article_id"]) == (
        s["article_id"],
        c["article_id"],
    )
    assert out["unmatched"] == []


def test_reverse_after_greedy():
    # the forward pass links s1 -> c1; the reverse pass only handles c2 and must not reuse s1
    p1, p3 = page(1), page(3)
    s1 = art(p1, 1, f"{SRC} మిగతా 3వ పేజీలో")
    s2 = art(p1, 2, f"{words(*range(60, 80))}")
    c1 = art(p3, 1, f"మొదటి పేజీ తరువాయి {CONT}")
    c2 = art(p3, 2, f"మొదటి పేజీ తరువాయి {words(*range(65, 85))}")
    out = run([p1, p3], [group(p1, s1, s2), group(p3, c1, c2)])
    methods = {x["source_article_id"]: x["method"] for x in out["links"]}
    assert methods == {s1["article_id"]: "page_ref", s2["article_id"]: "reverse"}


def test_reverse_skips_sources_pointing_elsewhere_and_below_score():
    p1, p3, p4 = page(1), page(3), page(4)
    elsewhere = art(p1, 1, f"{SRC} మిగతా 4వ పేజీలో")  # its own marker says page 4
    c = art(p3, 1, f"మొదటి పేజీ తరువాయి {CONT}")
    c4 = art(p4, 1, f"పేజీ తరువాయి {words(*range(400, 420))}")
    out = run([p1, p3, p4], [group(p1, elsewhere), group(p3, c), group(p4, c4)])
    assert all(x["method"] != "reverse" for x in out["links"])
    assert reasons(out)[c["article_id"]] == "no_source"


# --- FR-4 / FR-5: reading the artifact ------------------------------------------------------


def stitched_case():
    p1, p3 = page(1), page(3)
    s = art(
        p1,
        1,
        f"{SRC} ▶ మిగతా 3వ పేజీలో",
        head=words(500, 501),
        flags=["order_from_model"],
    )
    s["blocks"].append({"block_id": "bx", "role": "photo"})
    c = art(p3, 1, f"మొదటి పేజీ తరువాయి {CONT}", head=words(600), flags=["disconnected"])
    other = art(p3, 2, words(*range(300, 310)), kind="ad_commercial")
    groups = {p1.page_id: group(p1, s), p3.page_id: group(p3, c, other)}
    return [p1, p3], groups, s, c, other


def test_stitched_text_and_parts():
    pages, groups, s, c, other = stitched_case()
    artifact = stitch_date(DAY, pages, groups, {}, PATTERNS, P)
    artifact["links"][0]["flags"] = [AMBIGUOUS]
    merged, untouched = merge_articles(pages, groups, artifact, PATTERNS)
    assert merged.article_id == s["article_id"] and merged.kind == "news"
    assert merged.headline == words(500, 501)  # the continuation's headline is dropped
    assert merged.text == f"{SRC}\n\n{CONT}"  # marker phrases removed, joined by a blank line
    assert merged.parts == [
        {"article_id": s["article_id"], "page_id": pages[0].page_id, "bbox_union": s["bbox_union"]},
        {"article_id": c["article_id"], "page_id": pages[1].page_id, "bbox_union": c["bbox_union"]},
    ]
    assert merged.flags == ["order_from_model", AMBIGUOUS, "disconnected"]
    assert {b["page_id"] for b in merged.blocks} == {pages[0].page_id, pages[1].page_id}
    assert untouched.article_id == other["article_id"] and len(untouched.parts) == 1
    assert untouched.text == other["text"]


def test_merged_fields_from_first_part():
    pages, groups, s, _c, _ = stitched_case()
    artifact = stitch_date(DAY, pages, groups, {}, PATTERNS, P)
    merged = merge_articles(pages, groups, artifact, PATTERNS)[0]
    first = pages[0]
    assert (merged.page_id, merged.edition_code, merged.section, merged.page_num) == (
        first.page_id,
        first.edition_code,
        first.section,
        first.page_num,
    )
    assert merged.bbox_union == s["bbox_union"] and merged.judge_verdict == "correct"


def test_unmatched_flag_added_at_read_time():
    p1, p3 = page(1), page(3)
    lonely = art(p1, 1, f"{SRC} మిగతా 8వ పేజీలో")
    fine = art(p1, 2, words(*range(300, 320)))
    groups = {p1.page_id: group(p1, lonely, fine)}
    before = copy.deepcopy(groups)
    artifact = stitch_date(DAY, [p1, p3], groups, {}, PATTERNS, P)
    out = {a.article_id: a for a in merge_articles([p1, p3], groups, artifact, PATTERNS)}
    assert UNMATCHED_FLAG in out[lonely["article_id"]].flags
    assert UNMATCHED_FLAG not in out[fine["article_id"]].flags
    assert groups == before  # the group_final data is not changed
    assert out[lonely["article_id"]].text == lonely["text"]  # an unmatched part stays as it is


def test_collect_parts_reports_markers_per_article():
    pages, groups, s, c = pair()
    parts, orphans = collect_parts(pages, {g["page_id"]: g for g in groups}, {}, PATTERNS, P)
    by_id = {x.article_id: x for x in parts}
    assert [m.page for m in by_id[s["article_id"]].a] == [3]
    assert [m.page for m in by_id[c["article_id"]].b] == [1]
    assert orphans == 0
    assert by_id[s["article_id"]].text == SRC  # marker phrase removed


def test_match_parts_without_artifacts():
    pages, groups, _, _ = pair()
    parts, _ = collect_parts(pages, {g["page_id"]: g for g in groups}, {}, PATTERNS, P)
    links, unmatched = match_parts(parts, pages, P)
    assert len(links) == 1 and unmatched == []


def test_tokens_are_telugu_words_of_two_characters():
    assert tokens("అ ab రాజధాని 123 నిర్మాణ") == {"రాజధాని", "నిర్మాణ"}


# --- FR-6 / NFR-1 ---------------------------------------------------------------------------


def test_stats_command(monkeypatch, tmp_path):
    pages, groups, _s, _c = pair()
    out = run(pages, groups)
    from newsrag.stitch import load

    file = tmp_path / "2025-08-02.json"
    file.write_text(json.dumps(out), encoding="utf-8")
    monkeypatch.setattr(load, "done_stitch", lambda d=None: {"2025-08-02": file})
    result = CliRunner().invoke(stitch_cli.app, ["stats", "--date", "2025-08-02"])
    assert result.exit_code == 0, result.output
    lines = result.output.split()
    for line in (
        "source_markers=1",
        "continuation_markers=1",
        "linked=1",
        "unmatched_no_target_page=0",
        "unmatched_no_candidate=0",
        "unmatched_below_score=0",
        "unmatched_taken=0",
        "unmatched_no_source=0",
        "ambiguous=0",
        "orphan_markers=0",
    ):
        assert line in lines


def link_dicts(n):
    return [
        {
            "pub_date": "2025-08-02",
            "source_article_id": f"s{i:02d}",
            "continuation_article_id": f"c{i:02d}",
            "method": "similarity",
            "score": 0.3,
            "source_headline": "h",
            "source_text": f"S{i}",
            "continuation_text": f"C{i}",
        }
        for i in range(n)
    ]


def test_parse_answer():
    assert vf.parse_answer("Yes") == "yes"
    assert vf.parse_answer("no.") == "no"
    assert vf.parse_answer("  YES, they are") == "yes"
    assert vf.parse_answer("maybe") == "unclear" and vf.parse_answer("") == "unclear"


def test_verify_samples_with_seed_and_counts_yes():
    links = link_dicts(30)
    assert vf.sample(links, 20, 3) == vf.sample(links, 20, 3)
    assert len(vf.sample(links, 20, 3)) == 20
    assert len(vf.sample(link_dicts(5), 20, 3)) == 5
    seen = []

    def ask(prompt):
        seen.append(prompt)
        return "yes" if len(seen) % 5 else "no"

    report = vf.run_verify(links, 20, 3, ask)
    assert report["n"] == 20 and report["yes"] + report["no"] == 20
    assert all("Part 1" in p and "Part 2" in p for p in seen)


def test_verify_command_exit_code(monkeypatch, tmp_path):
    settings = config.settings.model_copy(update={"stitch_verify_file": tmp_path / "v.json"})
    monkeypatch.setattr(config, "settings", settings)
    monkeypatch.setattr(vf, "collect_links", lambda day=None: link_dicts(10))
    monkeypatch.setattr(
        vf,
        "run_verify",
        lambda links, n, seed, ask=None: {
            "model": "m",
            "n_links_available": 10,
            "n": 10,
            "yes": 9,
            "items": [],
        },
    )
    ok = CliRunner().invoke(stitch_cli.app, ["verify", "--n", "20", "--seed", "3"])
    assert ok.exit_code == 0 and "yes: 9/10" in ok.output
    monkeypatch.setattr(
        vf,
        "run_verify",
        lambda links, n, seed, ask=None: {
            "model": "m",
            "n_links_available": 10,
            "n": 10,
            "yes": 8,
            "items": [],
        },
    )
    bad = CliRunner().invoke(stitch_cli.app, ["verify"])
    assert bad.exit_code == 1 and "yes: 8/10" in bad.output


def test_link_pairs_carry_clean_texts():
    pages, groups, _s, _c, _ = stitched_case()
    artifact = stitch_date(DAY, pages, groups, {}, PATTERNS, P)
    (pairs,) = link_pairs(pages, groups, artifact, PATTERNS)
    assert pairs["source_text"] == SRC and pairs["continuation_text"] == CONT
    assert pairs["source_headline"] == words(500, 501)


# --- the stage ------------------------------------------------------------------------------


def test_stage_version_follows_file_and_thresholds(tmp_path, monkeypatch):
    from newsrag.stages.stitch import StitchStage

    file = tmp_path / "continuations.yaml"
    file.write_text("continues_on: []\ncontinued_from: []\n", encoding="utf-8")
    import newsrag.stages.base as base_mod

    def use(current):
        for mod in (config, base_mod):
            monkeypatch.setattr(mod, "settings", current)

    base = config.settings.model_copy(update={"continuations_file": file})
    use(base)
    v1 = StitchStage().version
    assert v1.startswith("stitch-v1-") and len(v1.split("-")[-1]) == 8
    file.write_text("continues_on: []\ncontinued_from: ['x']\n", encoding="utf-8")
    assert StitchStage().version != v1
    use(base.model_copy(update={"stitch_min_score": 0.5}))
    file.write_text("continues_on: []\ncontinued_from: []\n", encoding="utf-8")
    assert StitchStage().version != v1


def test_stage_is_registered_as_date_stage():
    from newsrag.stages.base import DateStage
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["stitch"]
    assert isinstance(stage, DateStage) and stage.depends_on == ("group_final",)


# --- the stage through the runner (needs the stores) -------------------------------------------


@pytest.mark.stores
def test_date_waits_for_all_pages_then_stitches(ledger, registry, tmp_path, monkeypatch):
    import io

    from _dummy_stages import add_pages, rows

    import newsrag.stages.base as base_mod
    from newsrag.stages.base import PageRow, PageStage, StageResult
    from newsrag.stages.runner import RunFilters, run_stage
    from newsrag.stages.stitch import StitchStage
    from newsrag.stitch import stitched_articles

    current = config.settings.model_copy(update={"work_dir": tmp_path / "work"})
    for mod in (config, base_mod):
        monkeypatch.setattr(mod, "settings", current)

    day = date(2025, 8, 2)
    ids = add_pages(3, day, prefix=f"{day}_FH_main")
    p = {n: PageInfo(ids[n - 1], "FH", "main", n) for n in (1, 2, 3)}
    s = art(p[1], 1, f"{SRC} మిగతా 3వ పేజీలో", head=words(500))
    c = art(p[3], 1, f"మొదటి పేజీ తరువాయి {CONT}")
    contents = {
        p[1].page_id: group(p[1], s),
        p[2].page_id: group(p[2]),
        p[3].page_id: group(p[3], c),
    }

    class FakeFinal(PageStage):
        name = "group_final"
        version = "v1"

        def handle(self, page_row: PageRow) -> StageResult:
            path = tmp_path / f"{page_row.page_id}.json"
            path.write_text(json.dumps(contents[page_row.page_id]), encoding="utf-8")
            return StageResult(str(path))

    registry["group_final"] = final = FakeFinal()
    registry["stitch"] = stitch = StitchStage()

    def go(stage, **filters):
        return run_stage(stage, RunFilters(**filters), out=io.StringIO())

    go(final, page_ids=ids[:2])
    assert go(stitch).processed == 0  # page 3 has no group_final yet
    assert rows("stitch", "date_runs") == {}
    go(final)
    assert go(stitch).processed == 1
    row = rows("stitch", "date_runs")[day]
    assert row["status"] == "done" and row["detail"]["links"] == 1
    (merged,) = [a for a in stitched_articles(day) if a.article_id == s["article_id"]]
    assert merged.text == f"{SRC}\n\n{CONT}" and len(merged.parts) == 2
    assert go(stitch).processed == 0  # nothing to do until a dependency or the file changes
