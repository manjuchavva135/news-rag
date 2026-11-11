"""Ticket 023: chunking (a whitespace tokenizer stands in for the embed model's)."""

from __future__ import annotations

import itertools
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from newsrag import chunking, config
from newsrag.chunking import build, load
from newsrag.chunking import cli as chunk_cli
from newsrag.chunking.build import DateInputs
from newsrag.chunking.split import NO_HEADLINE, Chunker, split_sentences
from newsrag.stages.base import StageUnavailable
from newsrag.stages.chunk import ChunkStage

DAY = date(2026, 4, 3)
PAGE = "2026-04-03_HYD_main_1"
SPECIALS = 2  # like <s> and </s>


def count(text: str) -> int:
    """Whitespace tokens plus two special tokens: a tiny stand-in for the real tokenizer."""
    return len(text.split()) + SPECIALS


class WhitespaceTokenizer:
    def __call__(self, text: str, add_special_tokens: bool = True):
        ids = list(range(len(text.split())))
        if add_special_tokens:
            ids += [0] * SPECIALS
        return SimpleNamespace(input_ids=ids)


def chunker(max_tokens: int = 40, doc_prefix: str = "") -> Chunker:
    return Chunker(count, doc_prefix, max_tokens)


def words(n: int, tag: str = "w") -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


def story(story_id="s_1", text="hello world", **kw) -> dict:
    record = {
        "story_id": story_id,
        "kind": "news",
        "canonical_unit_id": f"{PAGE}#a01",
        "headline": "Big news",
        "text": text,
        "department": None,
        "notice_number": None,
        "deadline": None,
        "printings": [
            {
                "unit_id": f"{PAGE}#a01",
                "page_id": PAGE,
                "edition_code": "HYD",
                "section": "main",
                "page_num": 1,
                "parts": [{"article_id": f"{PAGE}#a01", "page_id": PAGE, "bbox_union": []}],
                "flags": [],
            }
        ],
        "flags": [],
    }
    record.update(kw)
    return record


def table_item(markdown, article_id=f"{PAGE}#a01", **kw) -> dict:
    item = {
        "item_id": "x#t01",
        "article_id": article_id,
        "type": "table",
        "title": "Table title",
        "markdown": markdown,
        "flags": [],
        "inlined": False,
    }
    item.update(kw)
    return item


def inputs(stories, items=None, enriched=None, **kw) -> DateInputs:
    base = {
        "pub_date": DAY,
        "stories": stories,
        "enriched": enriched or {},
        "items": items or {PAGE: []},
        "page_editions": {
            PAGE: {
                "page_id": PAGE,
                "edition_names": ["హైదరాబాద్"],
                "districts_current": ["hyderabad"],
                "districts_at_print": ["hyderabad"],
                "states": ["telangana"],
                "flags": [],
            }
        },
        "page_flags": {PAGE: []},
        "name_to_entity": {},
        "editions": {"HYD": {"name_te": "హైదరాబాద్"}},
    }
    base.update(kw)
    return DateInputs(**base)


def run(inp: DateInputs, max_tokens: int = 40):
    return build.build_date(inp, chunker(max_tokens))


# --- E-1: split rules -----------------------------------------------------------------------


def test_short_story_one_chunk():
    result = run(inputs([story(text=words(10))]))
    assert [c["chunk_id"] for c in result.chunks] == ["s_1#c01"]
    chunk = result.chunks[0]
    assert chunk["text"] == words(10)
    assert chunk["seq"] == 1
    assert chunk["kind"] == "news"
    assert chunk["editions"] == ["HYD"]
    assert chunk["edition_names"] == ["హైదరాబాద్"]
    assert chunk["edition_districts"] == ["hyderabad"]
    assert chunk["edition_states"] == ["telangana"]
    assert chunk["page_ids"] == [PAGE]
    assert chunk["canonical_page_id"] == PAGE
    assert chunk["pub_date"] == "2026-04-03"
    assert chunk["event_date"] == "2026-04-03"  # no enrichment: the print date
    assert chunk["low_confidence"] is False
    assert chunk["low_confidence_reasons"] == []


def test_paragraph_split_with_one_paragraph_overlap():
    paragraphs = [words(10, tag) for tag in "abcdef"]
    text = "\n\n".join(paragraphs)
    c = chunker(40)
    prefix = "H | 03-04-2026 | 1 printing"
    windows = c.split_text(prefix, text)
    assert len(windows) > 1
    for window in windows:
        assert c.n_tokens(prefix, window) <= 40
    for before, after in itertools.pairwise(windows):
        last = before.split("\n\n")[-1]
        assert after.split("\n\n")[0] == last  # one-paragraph overlap
    # every paragraph appears somewhere, in order
    seen = [p for w in windows for p in w.split("\n\n")]
    assert [p for p in paragraphs if p in seen] == paragraphs


def test_long_paragraph_sentence_split():
    sentences = [f"{words(8, tag)}." for tag in "abcdef"]
    text = " ".join(sentences)  # one paragraph, too long for the budget
    c = chunker(30)
    windows = c.split_text("H", text)
    assert len(windows) > 1
    for window in windows:
        assert c.n_tokens("H", window) <= 30
    joined = " ".join(windows)
    for sentence in sentences:
        assert sentence in joined  # cut only at sentence ends


def test_decimal_not_sentence_end():
    assert split_sentences("రేటు 3.5 శాతం. తరువాత 12.75 కోట్లు?") == [
        "రేటు 3.5 శాతం.",
        "తరువాత 12.75 కోట్లు?",
    ]
    assert split_sentences("ఏమిటి! సరే। అవును") == ["ఏమిటి!", "సరే।", "అవును"]


def test_hard_split_when_one_sentence_is_too_long():
    c = chunker(20)
    windows = c.split_text("H", words(100))
    assert len(windows) > 1
    for window in windows:
        assert c.n_tokens("H", window) <= 20
    assert " ".join(windows).split() == words(100).split()


def test_prefix_counted():
    # 36 words of text fit 40 tokens on their own; the prefix (5 words) pushes it over
    text = words(36)
    c = chunker(40)
    assert c.n_tokens("", text) <= 40
    prefix = "Headline words here | 03-04-2026 | 2 printings"
    assert c.n_tokens(prefix, text) > 40
    assert len(c.split_text(prefix, text)) == 2


def test_prefix_format():
    c = chunker()
    assert build.make_prefix(c, "Big news", DAY, 1) == "Big news | 03-04-2026 | 1 printing"
    assert build.make_prefix(c, "Big news", DAY, 3) == "Big news | 03-04-2026 | 3 printings"
    two = story()
    two["printings"].append({**two["printings"][0], "unit_id": "x"})
    assert run(inputs([two])).chunks[0]["prefix"] == "Big news | 03-04-2026 | 2 printings"
    empty = run(inputs([story(headline="")])).chunks[0]
    assert empty["prefix"].startswith(f"{NO_HEADLINE} | ")


def test_long_headline_is_cut():
    c = chunker(40)
    prefix = build.make_prefix(c, words(80), DAY, 1)
    assert c.n_tokens(prefix, "") <= 20


def test_n_tokens_matches_embedded_string():
    c = Chunker(count, "passage: ", 40)
    result = build.build_date(inputs([story(text=words(70))]), c)
    assert len(result.chunks) > 1
    for chunk in result.chunks:
        embedded = "passage: " + chunk["prefix"] + "\n" + chunk["text"]
        assert chunk["n_tokens"] == len(WhitespaceTokenizer()(embedded).input_ids)
        assert chunk["n_tokens"] <= 40


def test_overlap_shrinks_when_too_big():
    # paragraph B has two sentences; as an overlap it would leave no room for the big paragraph C
    a = words(6, "a") + "."
    b_first = words(5, "p") + "."
    b_last = words(4, "q") + "."
    b = f"{b_first} {b_last}"
    big = words(14, "c")
    c = chunker(24)
    prefix = ""
    windows = c.split_text(prefix, f"{a}\n\n{b}\n\n{big}")
    assert len(windows) == 2
    assert windows[0] == f"{a}\n\n{b}"
    # B (9 words) + C (14) = 23 + 2 specials + prefix > 24: the overlap is B's last sentence
    assert windows[1] == f"{b_last}\n\n{big}"


def test_overlap_dropped_when_last_sentence_does_not_fit_either():
    a = words(3, "a") + "."
    b = words(5, "b") + "."  # a single sentence: nothing to shrink to
    big = words(18, "c")
    c = chunker(22)
    windows = c.split_text("", f"{a}\n\n{b}\n\n{big}")
    assert windows[-1] == big


def test_window_always_has_new_text():
    paragraphs = [words(n, tag) for n, tag in zip((12, 11, 13, 9, 12, 10), "abcdef", strict=True)]
    c = chunker(30)
    windows = c.split_text("H | d | 1 printing", "\n\n".join(paragraphs))
    seen: set[str] = set()
    previous: set[str] = set()
    for window in windows:
        current = set(window.split("\n\n"))
        assert current - previous, "a window repeats the previous one"
        seen |= current
        previous = current
    assert set(paragraphs) <= seen


def test_table_chunk_linked_to_story():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    result = run(inputs([story(text=words(10))], items={PAGE: [table_item(md)]}))
    story_chunk, table_chunk = result.chunks
    assert story_chunk["kind"] == "news"
    assert table_chunk["kind"] == "table"
    assert table_chunk["story_id"] == "s_1"
    assert table_chunk["chunk_id"] == "s_1#c02"
    assert table_chunk["seq"] == 2
    assert table_chunk["text"] == md
    assert table_chunk["headline"] == "Big news"  # the parent's headline
    assert table_chunk["prefix"] == "Big news | 03-04-2026 | 1 printing"
    assert result.table_chunks == 1


def test_table_headline_falls_back_to_title():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    inp = inputs([story(headline="")], items={PAGE: [table_item(md, title="Pass rates")]})
    story_chunk, table_chunk = run(inp).chunks
    assert story_chunk["headline"] == NO_HEADLINE
    assert table_chunk["headline"] == "Pass rates"
    assert table_chunk["prefix"].startswith("Pass rates | ")
    no_title = inputs([story(headline="")], items={PAGE: [table_item(md, title=None)]})
    assert run(no_title).chunks[1]["headline"] == NO_HEADLINE


def test_table_split_repeats_header():
    header = "| district | total |\n|---|---|"
    rows = [f"| {tag}{i} | {i} |" for i, tag in enumerate("abcdefghij")]
    c = chunker(22)
    windows = c.split_table("H", "\n".join([header, *rows]))
    assert len(windows) > 1
    for window in windows:
        assert window.startswith(header)
        assert c.n_tokens("H", window) <= 22
    body = [ln for w in windows for ln in w.split("\n")[2:]]
    assert body == rows


def test_table_story_with_several_tables_keeps_each_header():
    one = "| d | n |\n|---|---|\n" + "\n".join(f"| a{i} | {i} |" for i in range(8))
    two = "| school | pass | fail |\n|---|---|---|\n" + "\n".join(
        f"| s{i} | {i} | 1 |" for i in range(8)
    )
    c = chunker(24)
    windows = c.split_table("H", f"{one}\n\n{two}")
    assert len(windows) > 2
    for window in windows:
        assert c.n_tokens("H", window) <= 24
        first = window.split("\n")[0]
        rows = window.split("\n")[2:]
        if first == "| d | n |":
            assert all(r.startswith("| a") for r in rows)
        else:
            assert first == "| school | pass | fail |"
            assert all(r.startswith("| s") for r in rows)


def test_stale_date_with_reset_inputs(monkeypatch):
    def gone(day):
        raise FileNotFoundError("no done dedup artifact")

    monkeypatch.setattr(load, "load_lookup_inputs", gone)
    monkeypatch.setattr(load, "done_dates", lambda: {DAY: {"lookup_hash": "x"}})
    assert chunking.stale_dates() == [DAY]


def test_table_in_continuation_part_found():
    # the story's first printing starts on page 1 and continues on page 2; the table sits on
    # the continuation's article id, which is only a *part* of the story
    page2 = "2026-04-03_HYD_main_2"
    cont = f"{page2}#a05"
    s = story()
    s["printings"][0]["parts"].append({"article_id": cont, "page_id": page2, "bbox_union": []})
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    inp = inputs(
        [s],
        items={PAGE: [], page2: [table_item(md, article_id=cont)]},
        page_editions={},
    )
    chunks = run(inp).chunks
    assert [c["kind"] for c in chunks] == ["news", "table"]
    assert chunks[1]["page_ids"] == [PAGE, page2]


def test_table_duplicated_across_printings_kept_once():
    other = "2026-04-03_WGL_main_3"
    s = story()
    s["printings"].append(
        {
            "unit_id": f"{other}#a02",
            "page_id": other,
            "edition_code": "WGL",
            "section": "main",
            "page_num": 3,
            "parts": [{"article_id": f"{other}#a02", "page_id": other, "bbox_union": []}],
            "flags": [],
        }
    )
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    inp = inputs(
        [s],
        items={PAGE: [table_item(md)], other: [table_item(md, article_id=f"{other}#a02")]},
    )
    chunks = run(inp).chunks
    assert [c["kind"] for c in chunks] == ["news", "table"]
    assert chunks[0]["editions"] == ["HYD", "WGL"]


def test_table_of_another_story_not_attached():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    inp = inputs([story()], items={PAGE: [table_item(md, article_id=f"{PAGE}#a09")]})
    assert [c["kind"] for c in run(inp).chunks] == ["news"]


def test_ads_not_chunked():
    stories = [
        story("s_1", kind="news"),
        story("s_2", kind="ad_commercial"),
        story("s_3", kind="masthead"),
        story("s_4", kind="other"),
    ]
    result = run(inputs(stories))
    assert {c["story_id"] for c in result.chunks} == {"s_1"}
    assert result.empty_stories == 0


def test_notice_fields_and_kind():
    s = story(kind="notice_tender", department="Roads", notice_number="N-5", deadline="2026-05-01")
    chunk = run(inputs([s])).chunks[0]
    assert chunk["kind"] == "notice_tender"
    assert (chunk["department"], chunk["notice_number"], chunk["deadline"]) == (
        "Roads",
        "N-5",
        "2026-05-01",
    )


def test_enrichment_fields_and_entity_ids():
    enriched = {
        "s_1": {
            "category": "politics",
            "event_date": "2026-04-02",
            "people": [{"name": "Ravi  Kumar"}, {"name": "Unknown Man"}],
            "organisations": [{"name": "ACME"}],
            "states": ["telangana"],
            "districts_current": ["hyderabad"],
            "districts_at_print": ["hyderabad"],
            "facts": [{"fact_id": "s_1#f001"}],
            "flags": [],
        }
    }
    from newsrag.aliases import key

    n2e = {key("person", "Ravi Kumar"): "e_1", key("org", "ACME"): "e_2"}
    result = run(inputs([story()], enriched=enriched, name_to_entity=n2e))
    chunk = result.chunks[0]
    assert chunk["entity_ids"] == ["e_1", "e_2"]
    assert result.unmapped_names == 1
    assert chunk["facts_ids"] == ["s_1#f001"]
    assert chunk["category"] == "politics"
    assert chunk["event_date"] == "2026-04-02"
    assert chunk["states"] == ["telangana"]
    assert chunk["districts_current"] == chunk["districts_at_print"] == ["hyderabad"]


def test_low_confidence_propagates():
    s = story(flags=["low_confidence:judge_low", "informational"])
    chunk = run(inputs([s])).chunks[0]
    assert chunk["low_confidence"] is True
    assert chunk["low_confidence_reasons"] == ["judge_low"]


def test_flag_sources():
    base = story()
    assert run(inputs([base])).chunks[0]["low_confidence"] is False
    cases = {
        "printing": story(),
        "part": story(),
    }
    cases["printing"]["printings"][0]["flags"] = ["low_confidence:printing_flag"]
    cases["part"]["printings"][0]["parts"][0]["flags"] = ["low_confidence:part_flag"]
    for reason, s in {"printing_flag": cases["printing"], "part_flag": cases["part"]}.items():
        assert run(inputs([s])).chunks[0]["low_confidence_reasons"] == [reason]
    # canonical page: group_final and transcribe page flags
    paged = inputs([base], page_flags={PAGE: ["low_confidence:geometry", "other"]})
    assert run(paged).chunks[0]["low_confidence_reasons"] == ["geometry"]
    # page editions of the printings' pages
    editions = inputs([base])
    editions.page_editions[PAGE]["flags"] = ["low_confidence:unknown_edition"]
    assert run(editions).chunks[0]["low_confidence_reasons"] == ["unknown_edition"]
    # enrichment flags
    failed = inputs([base], enriched={"s_1": {"flags": ["low_confidence:enrich_failed"]}})
    assert run(failed).chunks[0]["low_confidence_reasons"] == ["enrich_failed"]
    # a page flag of a non-canonical page is not a source
    other = story()
    other["printings"].append({**other["printings"][0], "unit_id": "u2", "page_id": "p2"})
    quiet = inputs([other], page_flags={PAGE: [], "p2": ["low_confidence:geometry"]})
    assert run(quiet).chunks[0]["low_confidence"] is False
    # reasons are listed once and without the prefix
    both = story(flags=["low_confidence:geometry"])
    twice = inputs([both], page_flags={PAGE: ["low_confidence:geometry"]})
    assert run(twice).chunks[0]["low_confidence_reasons"] == ["geometry"]


def test_table_item_flags_only_on_its_chunk():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    item = table_item(md, flags=["low_confidence:geometry"])
    story_chunk, table_chunk = run(inputs([story()], items={PAGE: [item]})).chunks
    assert story_chunk["low_confidence"] is False
    assert table_chunk["low_confidence_reasons"] == ["geometry"]


# --- E-5: second-review clarifications ------------------------------------------------------


def test_inlined_items_skipped():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    inp = inputs([story()], items={PAGE: [table_item(md, inlined=True)]})
    assert [c["kind"] for c in run(inp).chunks] == ["news"]


def test_table_story_row_split():
    header = "| district | total |\n|---|---|"
    rows = [f"| {tag}{i} | {i} |" for i, tag in enumerate("abcdefghij")]
    text = "\n".join([header, *rows])
    for kind in ("table", "infographic", "table_only"):
        result = run(inputs([story(kind=kind, text=text)]), max_tokens=22)
        assert len(result.chunks) > 1
        for chunk in result.chunks:
            assert chunk["kind"] == kind
            assert chunk["text"].startswith(header)
            assert chunk["n_tokens"] <= 22


def test_empty_story_skipped():
    stories = [story("s_1", text="  \n "), story("s_2", text=""), story("s_3")]
    result = run(inputs(stories))
    assert {c["story_id"] for c in result.chunks} == {"s_3"}
    assert result.empty_stories == 2
    assert result.stories == 1


def test_stale_dates_only_changed(monkeypatch):
    from newsrag.aliases import key

    other = date(2026, 4, 4)
    s_a = story("s_a")
    s_b = story("s_b")
    enriched = {"s_a": {"people": [{"name": "Ravi"}]}, "s_b": {"people": [{"name": "Sita"}]}}
    n2e = {key("person", "Ravi"): "e_1", key("person", "Sita"): "e_2"}
    state = {"n2e": dict(n2e)}

    def inp(day, st, rec):
        return inputs(
            [st], enriched={st["story_id"]: rec}, name_to_entity=state["n2e"], pub_date=day
        )

    def load_inputs(day):
        return inp(day, *((s_a, enriched["s_a"]) if day == DAY else (s_b, enriched["s_b"])))

    def hash_of(day):
        return build.compute_lookup_hash(load_inputs(day))

    monkeypatch.setattr(load, "load_lookup_inputs", load_inputs)
    stored = {DAY: {"lookup_hash": hash_of(DAY)}, other: {"lookup_hash": hash_of(other)}}
    monkeypatch.setattr(load, "done_dates", lambda: stored)
    assert chunking.stale_dates() == []
    state["n2e"][key("person", "Sita")] = "e_3"  # an alias change that only touches `other`
    assert chunking.stale_dates() == [other]
    state["n2e"].pop(key("person", "Ravi"))  # a name that used to map is gone
    assert chunking.stale_dates() == [DAY, other]


def test_lookup_hash_follows_alias_and_edition_entries():
    from newsrag.aliases import key

    enriched = {"s_1": {"people": [{"name": "Ravi"}]}}
    base = inputs([story()], enriched=enriched, name_to_entity={})
    h0 = build.compute_lookup_hash(base)
    assert h0 == build.build_date(base, chunker()).lookup_hash
    mapped = inputs([story()], enriched=enriched, name_to_entity={key("person", "Ravi"): "e_1"})
    assert build.compute_lookup_hash(mapped) != h0  # a name that was unmapped now maps
    unrelated = inputs(
        [story()],
        enriched=enriched,
        name_to_entity={key("person", "Someone else"): "e_9"},
    )
    assert build.compute_lookup_hash(unrelated) == h0
    renamed = inputs([story()], enriched=enriched, editions={"HYD": {"name_te": "భాగ్యనగరం"}})
    assert build.compute_lookup_hash(renamed) != h0
    other_code = inputs(
        [story()], enriched=enriched, editions={"HYD": {"name_te": "హైదరాబాద్"}, "X": {}}
    )
    assert build.compute_lookup_hash(other_code) == h0


# --- stage: version, preflight, handle --------------------------------------------------------


def set_settings(monkeypatch, **update):
    import newsrag.stages.base as base_mod

    new = config.settings.model_copy(update=update)
    monkeypatch.setattr(config, "settings", new)
    monkeypatch.setattr(base_mod, "settings", new)
    return new


@pytest.fixture
def test_profile(monkeypatch, tmp_path):
    """The repo's models.yaml plus a temporary embed profile `test-embed`."""
    text = config.settings.models_file.read_text(encoding="utf-8")
    text += (
        "\n  - name: test-embed\n    hf_id: test/embed\n    family: test\n    kind: embed\n"
        "    dtype: float16\n    dim: 8\n    query_prefix: 'query: '\n    doc_prefix: 'passage: '\n"
    )
    path = tmp_path / "models.yaml"
    path.write_text(text, encoding="utf-8")
    set_settings(monkeypatch, models_file=path)
    return "test-embed"


def aliases_present(monkeypatch, tmp_path):
    set_settings(monkeypatch, alias_dir=tmp_path / "aliases")
    from newsrag import aliases

    path = aliases.entities_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"entities": {}, "name_to_entity": {}}), encoding="utf-8")


def test_version_follows_embed_model(monkeypatch, test_profile):
    set_settings(monkeypatch, embed_model="bge-m3")
    v1 = ChunkStage().version
    assert v1.startswith("chunk-v1-bge-m3-")
    set_settings(monkeypatch, embed_model=test_profile)
    v2 = ChunkStage().version
    assert v2.startswith("chunk-v1-test-embed-")
    assert v1 != v2
    set_settings(monkeypatch, embed_model="bge-m3", chunk_max_tokens=256)
    assert ChunkStage().version != v1


def test_alias_change_changes_version(monkeypatch):
    """FR-7: alias / edition lookups are *not* part of the version (the version stays) but of
    the date's `lookup_hash`, which `stale_dates` compares."""
    from newsrag.aliases import key

    v = ChunkStage().version
    enriched = {"s_1": {"people": [{"name": "Ravi"}]}}
    a = inputs([story()], enriched=enriched, name_to_entity={})
    b = inputs([story()], enriched=enriched, name_to_entity={key("person", "Ravi"): "e_1"})
    assert build.compute_lookup_hash(a) != build.compute_lookup_hash(b)
    assert ChunkStage().version == v


def test_registered():
    from newsrag.stages.registry import REGISTRY

    stage = REGISTRY["chunk"]
    assert stage.name == "chunk"
    assert stage.depends_on == ("enrich_facts",)


def test_unknown_embed_profile(monkeypatch, tmp_path):
    aliases_present(monkeypatch, tmp_path)
    set_settings(monkeypatch, embed_model="no-such-model")
    with pytest.raises(StageUnavailable, match="embed_model no-such-model not in models.yaml"):
        ChunkStage().preflight()
    set_settings(monkeypatch, embed_model="qwen3-30b-a3b")  # a text model is no embed profile
    with pytest.raises(StageUnavailable, match="not an embed profile"):
        ChunkStage().preflight()


def test_known_embed_profile_passes_preflight(monkeypatch, tmp_path, test_profile):
    aliases_present(monkeypatch, tmp_path)
    set_settings(monkeypatch, embed_model=test_profile)
    ChunkStage().preflight()


def test_missing_aliases(monkeypatch, tmp_path):
    set_settings(monkeypatch, alias_dir=tmp_path / "nothing")
    with pytest.raises(StageUnavailable, match=r"run 'newsrag aliases build' first"):
        ChunkStage().preflight()
    from newsrag.cli import app

    result = CliRunner().invoke(app, ["stage", "run", "chunk"])
    assert result.exit_code == 2
    assert "run 'newsrag aliases build' first" in result.output


def test_handle_writes_artifact_and_detail(monkeypatch, tmp_path):
    set_settings(monkeypatch, work_dir=tmp_path / "work")
    inp = inputs(
        [story("s_1", text=words(70)), story("s_2", text=""), story("s_3", kind="masthead")]
    )
    monkeypatch.setattr(load, "load_inputs", lambda day: inp)
    stage = ChunkStage()
    stage.tokenizer = WhitespaceTokenizer()
    set_settings(monkeypatch, chunk_max_tokens=40, work_dir=tmp_path / "work")
    result = stage.handle(DAY, [])
    artifact = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert artifact["pub_date"] == "2026-04-03"
    assert len(artifact["chunks"]) == result.detail["chunks"] > 1
    assert result.detail["stories"] == 1
    assert result.detail["empty_stories"] == 1
    assert result.detail["max_tokens"] <= 40
    assert result.detail["lookup_hash"] == build.compute_lookup_hash(inp)
    assert {c["story_id"] for c in artifact["chunks"]} == {"s_1"}


# --- stats ------------------------------------------------------------------------------------


def test_stats_of():
    chunks = [
        {"story_id": "a", "n_tokens": 10, "low_confidence": True},
        {"story_id": "a", "n_tokens": 20, "low_confidence": False},
        {"story_id": "b", "n_tokens": 30, "low_confidence": False},
        {"story_id": "c", "n_tokens": 40, "low_confidence": False},
    ]
    stats = chunk_cli.stats_of(chunks)
    assert stats["chunks"] == 4
    assert stats["stories"] == 3
    assert stats["chunks_per_story_mean"] == 1.33
    assert stats["chunks_per_story_max"] == 2
    assert (stats["tokens_p50"], stats["tokens_p95"], stats["tokens_max"]) == (20, 40, 40)
    assert stats["low_confidence_share"] == 0.25
    assert "tokens_max=40" in chunk_cli.stats_line(stats)
    assert chunk_cli.stats_of([])["chunks"] == 0


def test_stats_command(monkeypatch):
    chunks = [{"story_id": "a", "n_tokens": 10, "low_confidence": False}]
    monkeypatch.setattr(chunk_cli, "done_chunks", lambda day: chunks)
    from newsrag.cli import app

    result = CliRunner().invoke(app, ["chunk", "stats", "--date", "2026-04-03"])
    assert result.exit_code == 0
    assert "chunks=1 stories=1" in result.output
    monkeypatch.setattr(chunk_cli, "done_chunks", lambda day: [])
    assert CliRunner().invoke(app, ["chunk", "stats"]).exit_code == 1


# --- real tokenizer (data) --------------------------------------------------------------------


@pytest.mark.data
def test_real_tokenizer_respects_512():
    from transformers import AutoTokenizer

    from newsrag.models import get_profile

    profile = get_profile("bge-m3")
    tok = AutoTokenizer.from_pretrained(profile.hf_id, cache_dir=str(config.settings.hf_home))

    def real_count(text: str) -> int:
        return len(tok(text, add_special_tokens=True).input_ids)

    c = Chunker(real_count, profile.doc_prefix, 512)
    paragraph = "ప్రభుత్వం కొత్త పథకాన్ని ప్రారంభించింది. ముఖ్యమంత్రి మాట్లాడారు. " * 6
    text = "\n\n".join([paragraph] * 30)
    prefix = "శీర్షిక | 03-04-2026 | 2 printings"
    windows = c.split_text(prefix, text)
    assert len(windows) > 1
    assert all(c.n_tokens(prefix, w) <= 512 for w in windows)
