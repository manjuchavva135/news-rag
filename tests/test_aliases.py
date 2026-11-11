"""Ticket 022: the name alias table (a fake model; DB tests use the throw-away ledger schema)."""

from __future__ import annotations

import contextlib
import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsrag import aliases, config
from newsrag.aliases import build as builder
from newsrag.aliases import cli as aliases_cli
from newsrag.aliases import group, judge, names, rules
from newsrag.aliases import verify as vf
from newsrag.aliases.judge import Verdict

DAY = "2025-06-10"
T0 = datetime(2025, 1, 1, tzinfo=UTC)


def set_settings(monkeypatch, **update):
    import newsrag.stages.base as base_mod

    new = config.settings.model_copy(update=update)
    monkeypatch.setattr(config, "settings", new)
    monkeypatch.setattr(base_mod, "settings", new)
    return new


def story(people=(), orgs=()):
    return {
        "category": "politics",
        "people": [{"name": n, "role": None} for n in people],
        "organisations": [{"name": n, "type": "party"} for n in orgs],
        "flags": [],
    }


def artifact(**stories):
    return {"pub_date": DAY, "stories": stories}


class Fake:
    """A `Decide` function: answers from a table keyed by the two normalised names (either order),
    `default` otherwise; records every question."""

    def __init__(self, answers=None, default=None):
        self.answers = {}
        for (a, b), v in (answers or {}).items():
            self.answers[frozenset((aliases.normalise(a), aliases.normalise(b)))] = v
        self.default = default or Verdict(False, 0.1)
        self.asked: list[tuple[str, str, str]] = []

    def __call__(self, kind, name_a, name_b, ex_a, ex_b):
        self.asked.append((kind, name_a, name_b))
        return self.answers.get(
            frozenset((aliases.normalise(name_a), aliases.normalise(name_b))), self.default
        )

    def factory(self):
        return contextlib.nullcontext(self)


def run(tmp_path, artifacts, fake, *, store=None, overrides=None, warn=None, texts=None, **kw):
    return builder.run_build(
        artifacts,
        texts or {},
        id_store=store if store is not None else group.MemoryIdStore(),
        overrides=overrides or group.Overrides(),
        out_dir=tmp_path / "out",
        review_path=tmp_path / "review.csv",
        decider=fake.factory,
        warn=warn or (lambda m: None),
        log=lambda m: None,
        **kw,
    )


def entity_of(result, kind, name):
    return result.entities["name_to_entity"][aliases.key(kind, name)]


def review(result):
    return [(r["name_a"], r["name_b"], r["rule"], r["confidence"]) for r in result.review_rows]


TE_CB = "చంద్రబాబు"


# --- helpers: normalisation, folding, key, lookup ---


def test_key_helper(tmp_path, monkeypatch):
    assert aliases.normalise("  Chandra  Babu ") == "chandra babu"
    assert aliases.key("person", "Y S  Jagan") == "person|y s jagan"
    assert aliases.key("org", TE_CB) == f"org|{TE_CB}"
    # NFC: a decomposed spelling gets the same key
    assert aliases.key("person", "é") == aliases.key("person", "é")
    set_settings(monkeypatch, alias_dir=tmp_path, enrich_model="m")
    path = tmp_path / "m-alias-p1" / "entities.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"name_to_entity": {"person|chandrababu": "e_1"}}))
    assert aliases.lookup("person", "Chandra Babu") is None
    assert aliases.lookup("person", "  CHANDRABABU ") == "e_1"
    assert aliases.lookup("org", "Chandrababu") is None


def test_folding_te_en():
    f = aliases.fold
    assert f(TE_CB) == f("Chandrababu") == "candrababu"
    assert f("లోకేశ్") == f("Lokesh") == f("Lokes")
    assert f("సాక్షి") == f("Sakshi")
    assert f("చదలవాడ") == f("Chadalavada") == "cadalavada"
    assert f("Y.S. Jagan") == f("YS  Jagan") == "ysjagan"  # spaces and dots removed
    assert f("Raamoo") == f("Ramu") and f("Sweeti") == f("Sviti")  # aa, oo, w
    assert f("అంబటి రాంబాబు") == f("Ambati Rambabu")  # the anusvara before a consonant is n/m
    assert aliases.fold_tokens("YS Jagan Mohan") == ("ys", "jagan", "mohan")


def test_transliteration_match_te_en(tmp_path):
    arts = [artifact(s1=story(people=[TE_CB]), s2=story(people=["Chandrababu"]))]
    pairs = rules.candidate_pairs(names.collect(arts), 0.9, 2)
    assert pairs == {("person|chandrababu", f"person|{TE_CB}"): "a"}
    fake = Fake({(TE_CB, "Chandrababu"): Verdict(True, 0.95)})
    res = run(tmp_path, arts, fake)
    assert entity_of(res, "person", TE_CB) == entity_of(res, "person", "Chandrababu")
    entity = res.entities["entities"][0]
    assert entity["canonical_te"] == TE_CB and entity["canonical_en"] == "Chandrababu"
    assert entity["aliases"] == sorted([TE_CB, "Chandrababu"])
    assert res.stats["auto_pairs"] == 1 and res.stats["entities"] == 1


def test_rule_b_jaro_winkler_in_blocks():
    arts = [artifact(s1=story(people=["Chadalwada Ravi"]), s2=story(people=["Chadalavada Ravi"]))]
    assert list(rules.candidate_pairs(names.collect(arts), 0.9, 2).values()) == ["b"]
    far = [artifact(s1=story(people=["Chadalwada Ravi"]), s2=story(people=["Pawan Kalyan"]))]
    assert rules.candidate_pairs(names.collect(far), 0.9, 2) == {}


def test_rule_d_needs_shared_surname_and_two_common_stories():
    two = [
        artifact(
            s1=story(people=["Jagan", "Y S Jagan"]),
            s2=story(people=["Jagan", "Y S Jagan"]),
        )
    ]
    assert list(rules.candidate_pairs(names.collect(two), 0.99, 2).values()) == ["d"]
    one = [artifact(s1=story(people=["Jagan", "Y S Jagan"]), s2=story(people=["Jagan"]))]
    assert rules.candidate_pairs(names.collect(one), 0.99, 2) == {}


def test_person_and_org_never_merge(tmp_path):
    arts = [artifact(s1=story(people=["Sakshi"], orgs=["Sakshi", "సాక్షి"]))]
    fake = Fake(default=Verdict(True, 0.99))
    res = run(tmp_path, arts, fake)
    assert entity_of(res, "person", "Sakshi") != entity_of(res, "org", "Sakshi")
    assert entity_of(res, "org", "సాక్షి") == entity_of(res, "org", "Sakshi")
    assert {k for k, *_ in fake.asked} == {"org"}  # only the org pair was ever asked about
    assert res.stats["entities"] == 2


# --- the decision rules (FR-4) ---


def test_low_confidence_goes_to_review(tmp_path):
    arts = [artifact(s1=story(people=["Chadalwada Ravi", "Chadalavada Ravi"]))]
    res = run(tmp_path, arts, Fake(default=Verdict(True, 0.8)))
    assert res.stats["entities"] == 2 and res.stats["auto_pairs"] == 0
    assert review(res) == [("Chadalavada Ravi", "Chadalwada Ravi", "b", "0.8")]
    # "same: false" is dropped, not reviewed
    res = run(tmp_path / "x", arts, Fake(default=Verdict(False, 0.99)))
    assert res.review_rows == [] and res.stats["entities"] == 2


def test_rule_d_never_auto_applied(tmp_path, monkeypatch):
    set_settings(monkeypatch, alias_jw_threshold=0.99)
    arts = [
        artifact(s1=story(people=["Jagan", "Y S Jagan"]), s2=story(people=["Jagan", "Y S Jagan"]))
    ]
    res = run(tmp_path, arts, Fake(default=Verdict(True, 1.0)))
    assert res.stats["entities"] == 2 and res.stats["auto_pairs"] == 0
    assert review(res) == [("Jagan", "Y S Jagan", "d", "1")]
    # accepting the row in overrides.yaml (merge) joins them
    ov = group.Overrides(
        merge=[(aliases.key("person", "Jagan"), aliases.key("person", "Y S Jagan"))]
    )
    res = run(tmp_path / "y", arts, Fake(default=Verdict(True, 1.0)), overrides=ov)
    assert res.stats["entities"] == 1 and res.review_rows == []


def test_transitive_merge(tmp_path):
    arts = [artifact(s1=story(people=["Chadalwada Ravi", "Chadalavada Ravi", "చదలవాడ రవి"]))]
    res = run(tmp_path, arts, Fake(default=Verdict(True, 0.95)))
    assert res.stats["entities"] == 1 and res.stats["largest_group"] == 3
    assert res.stats["auto_groups"] == [
        ["person|chadalavada ravi", "person|chadalwada ravi", "person|చదలవాడ రవి"]
    ]


def test_invalid_llm_reply_to_review(tmp_path, monkeypatch):
    set_settings(monkeypatch, enrich_retry_backoff_s=(0.0,))

    class Garbage:
        calls = 0

        def chat(self, messages, **kw):
            Garbage.calls += 1
            return "this is not json"

    decide = judge.decide_with(Garbage())
    assert decide("person", "a", "b", [], []) == judge.INVALID
    assert Garbage.calls == 3  # the first try and the 2 retries
    # wrong shape is as bad as no JSON
    for bad in ('{"same": "yes", "confidence": 1}', '{"same": true, "confidence": 7}', "[1]"):
        with pytest.raises(ValueError):
            judge.parse_verdict(bad)

    arts = [artifact(s1=story(people=["Chadalwada Ravi", "Chadalavada Ravi"]))]
    res = run(tmp_path, arts, Fake(default=judge.INVALID))
    assert res.stats["entities"] == 2
    assert review(res) == [("Chadalavada Ravi", "Chadalwada Ravi", "b", "")]


def test_reply_is_parsed_and_asked_with_the_schema(monkeypatch):
    seen = {}

    class Client:
        def chat(self, messages, **kw):
            seen.update(messages=messages, **kw)
            return '```json\n{"same": true, "confidence": 0.93}\n```'

    verdict = judge.decide_with(Client())("org", "టీడీపీ", "TDP", ["టీడీపీ నేతలు"], [])
    assert verdict == Verdict(True, 0.93)
    system, user = (m["content"] for m in seen["messages"])
    assert "SAME person or organisation" in system
    assert "Type: organisation" in user and "- టీడీపీ నేతలు" in user and "(none)" in user
    assert seen["temperature"] == 0
    assert seen["response_format"]["json_schema"]["schema"] == judge.REPLY_SCHEMA


def test_loops_until_no_pending(tmp_path, monkeypatch):
    set_settings(monkeypatch, alias_max_llm_pairs=2)
    people = ["Chadalwada Ravi", "Chadalavada Ravi", "Chadalawada Ravi", "Chadalvada Ravi"]
    arts = [artifact(s1=story(people=people))]
    fake = Fake(default=Verdict(True, 0.95))
    res = run(tmp_path, arts, fake)
    pairs = len(fake.asked)
    assert pairs == 6 and res.asked == 6  # 6 pairs, batches of 2: three batches in one build
    assert res.stats["entities"] == 1
    again = Fake()
    res2 = run(tmp_path, arts, again)  # the cache answers everything
    assert again.asked == [] and res2.asked == 0
    assert res2.entities == res.entities
    lines = (tmp_path / "out" / "verdicts.jsonl").read_text().splitlines()
    assert len(lines) == 6


def test_cache_is_used_by_pair_and_new_pairs_only(tmp_path):
    arts = [artifact(s1=story(people=["Chadalwada Ravi", "Chadalavada Ravi"]))]
    run(tmp_path, arts, Fake(default=Verdict(False, 0.9)))
    arts = [artifact(s1=story(people=["Chadalwada Ravi", "Chadalavada Ravi", "Chadalavada Ravi."]))]
    fake = Fake(default=Verdict(False, 0.9))
    run(tmp_path, arts, fake)
    assert fake.asked  # pairs with the new spelling only
    assert all("Chadalavada Ravi." in (a, b) for _, a, b in fake.asked)


def test_most_frequent_names_are_asked_first(tmp_path, monkeypatch):
    set_settings(monkeypatch, alias_max_llm_pairs=1, alias_concurrency=1)
    arts = [
        artifact(
            s1=story(people=["Chadalwada Ravi", "Chadalavada Ravi", "Kodali Nani", "Kodali Naani"]),
            s2=story(people=["Kodali Nani", "Kodali Naani"]),
            s3=story(people=["Kodali Nani"]),
        )
    ]
    fake = Fake()
    run(tmp_path, arts, fake)
    assert all("Kodali" in x for x in fake.asked[0][1:])  # count 3 + 2 beats count 1 + 1


def test_review_csv_header_even_without_rows(tmp_path):
    res = run(tmp_path, [artifact(s1=story(people=["Pawan Kalyan"]))], Fake())
    text = (tmp_path / "review.csv").read_text(encoding="utf-8")
    assert text.splitlines() == ["name_a,name_b,type,confidence,rule,example_a,example_b"]
    assert res.stats["review_pairs"] == 0 and res.stats["names"] == 1


def test_review_csv_has_examples(tmp_path):
    arts = [artifact(s1=story(people=["Chadalwada Ravi", "Chadalavada Ravi"]))]
    texts = {"s1": "Intro. Chadalwada Ravi spoke today. Then Chadalavada Ravi left. End."}
    res = run(tmp_path, arts, Fake(default=Verdict(True, 0.5)), texts=texts)
    with (tmp_path / "review.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["example_a"] == "Then Chadalavada Ravi left."
    assert rows[0]["example_b"] == "Chadalwada Ravi spoke today."
    assert res.stats["review_pairs"] == 1


# --- overrides (FR-6) ---


def test_overrides_win(tmp_path):
    ravi, ravi2 = "Chadalwada Ravi", "Chadalavada Ravi"
    arts = [artifact(s1=story(people=[ravi, ravi2, "Ravi Teja"]))]
    both = Fake({(ravi, ravi2): Verdict(True, 0.99)})
    split = group.Overrides(split=[(aliases.key("person", ravi), aliases.key("person", ravi2))])
    res = run(tmp_path, arts, both, overrides=split)
    assert entity_of(res, "person", ravi) != entity_of(res, "person", ravi2)
    assert res.review_rows == []  # a rejected pair is not listed again
    merge = group.Overrides(
        merge=[(aliases.key("person", ravi), aliases.key("person", "Ravi Teja"))]
    )
    res = run(tmp_path / "m", arts, Fake(default=Verdict(False, 0.9)), overrides=merge)
    assert entity_of(res, "person", ravi) == entity_of(res, "person", "Ravi Teja")


def test_split_is_cannot_link(tmp_path):
    a, b, c = "Chadalwada Ravi", "Chadalavada Ravi", "Chadalawada Ravi"
    arts = [artifact(s1=story(people=[a, b, c]))]
    fake = Fake(
        {(a, b): Verdict(True, 0.99), (b, c): Verdict(True, 0.95), (a, c): Verdict(False, 0.5)}
    )
    ov = group.Overrides(split=[(aliases.key("person", a), aliases.key("person", c))])
    res = run(tmp_path, arts, fake, overrides=ov)
    assert entity_of(res, "person", a) == entity_of(res, "person", b)  # the higher confidence first
    assert entity_of(res, "person", c) != entity_of(res, "person", b)
    assert res.stats["entities"] == 2


def test_bad_merges_are_ignored_with_a_warning(tmp_path):
    a, b = "Chadalwada Ravi", "Chadalavada Ravi"
    arts = [artifact(s1=story(people=[a, b, "Pawan Kalyan"]))]
    k = aliases.key
    ov = group.Overrides(
        merge=[(k("person", a), k("person", "Nobody At All")), (k("person", a), k("person", b))],
        split=[(k("person", a), k("person", b))],
    )
    warnings: list[str] = []
    res = run(tmp_path, arts, Fake(), overrides=ov, warn=warnings.append)
    assert len(warnings) == 2
    assert "unknown name" in warnings[0] and "conflicts with a split" in warnings[1]
    assert res.stats["entities"] == 3


def test_load_overrides_file(tmp_path):
    path = tmp_path / "overrides.yaml"
    path.write_text(
        "merge:\n  - [person, 'చంద్రబాబు', 'Chandrababu  Naidu']\n  - [thing, a, b]\nsplit:\n"
        "  - [org, TDP, YSRCP]\n",
        encoding="utf-8",
    )
    warnings: list[str] = []
    ov = group.load_overrides(path, warnings.append)
    assert ov.merge == [(f"person|{TE_CB}", "person|chandrababu naidu")]
    assert ov.split == [("org|tdp", "org|ysrcp")]
    assert len(warnings) == 1
    assert group.load_overrides(tmp_path / "missing.yaml").merge == []
    committed = Path(__file__).resolve().parents[1] / "data" / "aliases" / "overrides.yaml"
    empty = group.load_overrides(committed)
    assert empty.merge == [] and empty.split == []


# --- entities and ids (FR-5) ---


def test_entities_json_fields_and_order(tmp_path):
    arts = [
        artifact(
            s1=story(people=[TE_CB, "Chandrababu"], orgs=["టీడీపీ", "Tidipi"]),
            s2=story(people=[TE_CB], orgs=["టీడీపీ"]),
            s3=story(people=["Nara Lokesh"]),
        )
    ]
    fake = Fake(default=Verdict(True, 0.95))
    res = run(tmp_path, arts, fake)
    by_canonical = {e["canonical"]: e for e in res.entities["entities"]}
    cb = by_canonical[TE_CB]
    assert list(cb) == [
        "entity_id",
        "type",
        "org_type",
        "canonical",
        "canonical_te",
        "canonical_en",
        "aliases",
        "story_count",
    ]
    assert cb["type"] == "person" and cb["org_type"] is None and cb["story_count"] == 2
    assert cb["canonical_en"] == "Chandrababu"
    tdp = by_canonical["టీడీపీ"]
    assert tdp["type"] == "org" and tdp["org_type"] == "party" and tdp["canonical_te"] == "టీడీపీ"
    assert tdp["canonical_en"] == "Tidipi"
    lokesh = by_canonical["Nara Lokesh"]
    assert lokesh["canonical_te"] is None and lokesh["canonical_en"] == "Nara Lokesh"
    ids = [e["entity_id"] for e in res.entities["entities"]]
    assert ids == sorted(ids) and all(i.startswith("e_") and len(i) == 14 for i in ids)
    assert list(res.entities["name_to_entity"]) == sorted(res.entities["name_to_entity"])
    path = tmp_path / "out" / "entities.json"
    assert json.loads(path.read_text(encoding="utf-8")) == res.entities


def test_canonical_en_is_the_transliteration_when_there_is_no_latin_alias(tmp_path):
    res = run(tmp_path, [artifact(s1=story(people=["లోకేశ్"]))], Fake())
    entity = res.entities["entities"][0]
    assert entity["canonical_te"] == "లోకేశ్" and entity["canonical_en"] == "lōkēś"


def test_canonical_is_the_most_frequent_spelling(tmp_path):
    arts = [
        artifact(
            s1=story(people=["Chadalwada Ravi", "Chadalavada Ravi"]),
            s2=story(people=["Chadalawada Ravi"]),
            s3=story(people=["Chadalawada Ravi"]),
        )
    ]
    res = run(tmp_path, arts, Fake(default=Verdict(True, 0.95)))
    assert res.entities["entities"][0]["canonical"] == "Chadalawada Ravi"
    tie = [artifact(s1=story(people=["Ravi B", "Ravi A"]))]
    assert names.collect(tie)["person|ravi a"].display == "Ravi A"
    assert group.canonical_of(list(names.collect(tie).values())) == "Ravi A"


def test_ids_persist_when_canonical_changes(tmp_path):
    a, b = "Chadalwada Ravi", "Chadalavada Ravi"
    store = group.MemoryIdStore()
    fake = Fake(default=Verdict(True, 0.95))
    first = run(tmp_path / "1", [artifact(s1=story(people=[a, b, b]))], fake, store=store)
    entity = first.entities["entities"][0]
    assert entity["canonical"] == b
    # more stories later: the other spelling becomes the most frequent, the id stays
    later = [artifact(s1=story(people=[a, b, b]), s2=story(people=[a]), s3=story(people=[a]))]
    second = run(tmp_path / "2", later, fake, store=store)
    assert second.entities["entities"][0]["canonical"] == a
    assert second.entities["entities"][0]["entity_id"] == entity["entity_id"]


def test_id_split_two_groups():
    k = aliases.key
    a, b, c = (k("person", x) for x in ("aa", "bb", "cc"))
    rows = {
        a: ("e_old", T0),
        b: ("e_old", T0 + timedelta(days=1)),
        c: ("e_old", T0 + timedelta(days=2)),
    }
    # an override split: {a} and {b, c} are separate groups now; the group with the oldest name keeps
    ids = group.assign_ids([[b, c], [a]], ["bb", "aa"], rows)
    assert ids[1] == "e_old"
    assert ids[0] == group.mint_id("person", "bb", {"e_old"})
    # the fresh id is taken already: "#k" is added to the hash input until it is unused
    first = group.mint_id("person", "aa", set())
    assert group.mint_id("person", "aa", {first}) != first
    assert group.mint_id("person", "aa", {first}).startswith("e_")


def test_merge_keeps_the_oldest_id():
    k = aliases.key
    a, b = k("person", "aa"), k("person", "bb")
    rows = {a: ("e_young", T0 + timedelta(days=5)), b: ("e_old", T0)}
    assert group.assign_ids([[a, b]], ["aa"], rows) == ["e_old"]
    rows = {a: ("e_b", T0), b: ("e_a", T0)}  # same age: the smallest id
    assert group.assign_ids([[a, b]], ["aa"], rows) == ["e_a"]


def test_a_merge_points_the_loser_names_to_the_winner(tmp_path):
    a, b = "Chadalwada Ravi", "Chadalavada Ravi"
    store = group.MemoryIdStore()
    arts = [artifact(s1=story(people=[a, b]))]
    first = run(tmp_path / "1", arts, Fake(default=Verdict(False, 0.9)), store=store)
    ida, idb = entity_of(first, "person", a), entity_of(first, "person", b)
    assert ida != idb
    winner = min((ida, idb), key=lambda i: (min(t for e, t in store.rows.values() if e == i), i))
    second = run(tmp_path / "2", arts, Fake(default=Verdict(True, 0.95)), store=store)
    assert entity_of(second, "person", a) == entity_of(second, "person", b) == winner
    assert {e for e, _ in store.rows.values()} == {winner}


def test_entities_file_is_identical_on_rerun(tmp_path):
    arts = [artifact(s1=story(people=[TE_CB, "Chandrababu", "Pawan"], orgs=["TDP"]))]
    fake = Fake(default=Verdict(True, 0.95))
    store = group.MemoryIdStore()
    run(tmp_path, arts, fake, store=store)
    path = tmp_path / "out" / "entities.json"
    first = path.read_bytes()
    run(tmp_path, arts, Fake(), store=store)
    assert path.read_bytes() == first
    assert b"created_at" not in first  # no timestamps are written


# --- the cache (FR-3) ---


def test_a_verdict_of_another_prompt_is_asked_again(tmp_path):
    path = tmp_path / "v.jsonl"
    v = judge.Verdicts(path, prompt="aaaa")
    v.add("person", "a", "b", Verdict(True, 0.9))
    assert judge.Verdicts(path, prompt="aaaa").get("person", "a", "b") == Verdict(True, 0.9)
    assert judge.Verdicts(path, prompt="bbbb").get("person", "a", "b") is None
    assert judge.Verdicts(path, prompt="aaaa").get("org", "a", "b") is None


def test_examples_are_sentences_with_the_name():
    text = "Meeting held.\nChandrababu said so. He left! Chandrababu came back."
    assert names.example_for(text, ["Chandrababu"], 200) == "Chandrababu said so."
    assert names.example_for(text, ["Nobody"], 200) is None
    long = "x " * 200 + "Ravi spoke. " + "y " * 200
    cut = names.example_for(long, ["Ravi"], 40)
    assert cut is not None and "Ravi" in cut and len(cut) <= 40
    info = names.collect([artifact(s1=story(people=["Ravi"]), s2=story(people=["Ravi"]))])
    texts = {"s1": "Ravi spoke. Ravi left.", "s2": "Ravi spoke."}
    assert names.examples(info["person|ravi"], texts, 3, 100) == ["Ravi spoke."]


# --- stats / verify / cli ---


def test_sample_pairs():
    groups = [["a", "b", "c"], ["x", "y"], ["solo"]]
    assert sorted(vf.sample_pairs(groups, 10, 1)) == [
        ("a", "b"),
        ("a", "c"),
        ("b", "c"),
        ("x", "y"),
    ]
    big = [[f"n{i:02}" for i in range(30)]]
    picked = vf.sample_pairs(big, 40, 17)
    assert len(picked) == len(set(picked)) == 40
    assert all(a < b for a, b in picked)
    assert picked == vf.sample_pairs(big, 40, 17) and picked != vf.sample_pairs(big, 40, 18)
    assert [vf.unrank(4, i) for i in range(6)] == [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]


def test_verify_counts_the_judge_agreeing(tmp_path, monkeypatch):
    set_settings(monkeypatch, alias_verify_model="judge")
    arts = [artifact(s1=story(people=["Chadalwada Ravi", "Chadalavada Ravi", "చదలవాడ రవి"]))]
    res = run(tmp_path, arts, Fake(default=Verdict(True, 0.95)))
    infos = names.collect(arts)
    judge_says = Fake(default=Verdict(True, 0.5))
    report = vf.run_verify(res.stats["auto_groups"], infos, {}, 40, 17, judge_says)
    assert report["n"] == 3 and report["same"] == 3 and len(judge_says.asked) == 3
    no = Fake(default=Verdict(False, 0.9))
    assert vf.run_verify(res.stats["auto_groups"], infos, {}, 2, 17, no)["same"] == 0


def test_stats_command(tmp_path, monkeypatch):
    set_settings(monkeypatch, alias_dir=tmp_path, enrich_model="m")
    runner = CliRunner()
    missing = runner.invoke(aliases_cli.app, ["stats"])
    assert missing.exit_code == 1
    out = tmp_path / "m-alias-p1"
    out.mkdir()
    stats = {"names": 9, "entities": 6, "auto_pairs": 3, "review_pairs": 2, "largest_group": 3}
    (out / "stats.json").write_text(json.dumps(stats))
    result = runner.invoke(aliases_cli.app, ["stats"])
    assert result.exit_code == 0
    assert result.output.strip() == (
        "names=9 entities=6 auto_pairs=3 review_pairs=2 largest_group=3"
    )


def test_cli_group_is_registered():
    from newsrag.cli import SUB_APPS

    assert (aliases_cli.app, "aliases") in SUB_APPS


def test_settings_and_prompt_exist():
    s = config.settings
    assert s.alias_max_llm_pairs == 50_000 and s.alias_auto_confidence == 0.9
    assert s.alias_review_file == s.reports_dir / "alias-review.csv"
    assert s.alias_dir == s.work_dir / "aliases"
    assert s.alias_overrides_file == s.repo_root / "data" / "aliases" / "overrides.yaml"
    assert s.alias_verify_model == "gemma3-27b" and (s.alias_verify_n, s.alias_verify_seed) == (
        40,
        17,
    )
    assert judge.PROMPT_FILE.name == "alias_v1.md" and judge.PROMPT_FILE.is_file()
    assert aliases.version() == f"{s.enrich_model}-alias-p1"


# --- ledger table (needs the stores) ---


@pytest.mark.stores
def test_entity_ids_created_at(ledger):
    store = group.LedgerIdStore()
    k = aliases.key
    a, b = k("person", "aa"), k("person", "bb")
    store.save({a: "e_young", b: "e_old"})
    from newsrag.ledger import db

    with db.conn() as c:
        c.execute("update entity_ids set created_at = %s where name_key = %s", (T0, b))
        c.execute(
            "update entity_ids set created_at = %s where name_key = %s", (T0 + timedelta(days=9), a)
        )
    rows = store.load()
    assert rows[b] == ("e_old", T0)
    assert group.assign_ids([[a, b]], ["aa"], rows) == ["e_old"]
    # a save keeps created_at and only changes the id
    store.save({a: "e_old", b: "e_old"})
    after = store.load()
    assert after[a] == ("e_old", T0 + timedelta(days=9)) and after[b] == ("e_old", T0)
    with db.conn() as c:
        columns = {
            r[0]
            for r in c.execute(
                "select column_name from information_schema.columns "
                "where table_schema = %s and table_name = 'entity_ids'",
                (ledger,),
            )
        }
    assert columns == {"name_key", "entity_id", "created_at"}


@pytest.mark.stores
def test_ids_stable_across_runs(ledger, tmp_path):
    arts = [artifact(s1=story(people=[TE_CB, "Chandrababu", "Pawan Kalyan"], orgs=["TDP", "టీడీపీ"]))]
    fake = Fake(default=Verdict(True, 0.95))
    first = run(tmp_path, arts, fake, store=group.LedgerIdStore())
    path = tmp_path / "out" / "entities.json"
    before = path.read_bytes()
    second = run(tmp_path, arts, Fake(), store=group.LedgerIdStore())
    assert second.entities == first.entities and path.read_bytes() == before
    # new dates bring a new name and more mentions: the ids that exist do not change
    more = [
        artifact(
            s1=story(people=[TE_CB, "Chandrababu", "Pawan Kalyan"], orgs=["TDP", "టీడీపీ"]),
            s2=story(people=["Chandrababu"] * 1 + ["Nara Lokesh"]),
        )
    ]
    third = run(tmp_path, more, Fake(default=Verdict(True, 0.95)), store=group.LedgerIdStore())
    old = {e["entity_id"] for e in first.entities["entities"]}
    assert old <= {e["entity_id"] for e in third.entities["entities"]}
    assert {
        k: v
        for k, v in third.entities["name_to_entity"].items()
        if k in first.entities["name_to_entity"]
    } == first.entities["name_to_entity"]
    assert len(third.entities["entities"]) == len(first.entities["entities"]) + 1


@pytest.mark.stores
def test_migration_creates_entity_ids(ledger):
    from newsrag.ledger import db

    with db.conn() as c:
        tables = {
            r[0]
            for r in c.execute(
                "select table_name from information_schema.tables where table_schema = %s",
                (ledger,),
            )
        }
        applied = [r[0] for r in c.execute("select filename from schema_migrations order by 1")]
    assert "entity_ids" in tables and "004_entity_ids.sql" in applied


def test_a_contested_id_stays_with_the_group_of_its_oldest_name():
    k = aliases.key
    x1, y1, y2, z1 = (k("person", n) for n in ("x1", "y1", "y2", "z1"))
    rows = {
        x1: ("e_A", T0),
        y1: ("e_A", T0 + timedelta(days=1)),
        y2: ("e_B", T0 + timedelta(days=5)),
        z1: ("e_B", T0 + timedelta(days=2)),
    }
    ids = group.assign_ids([[x1], [y1, y2], [z1]], ["x", "y", "z"], rows)
    assert ids[0] == "e_A" and ids[2] == "e_B"
    assert ids[1] not in {"e_A", "e_B"} and ids[1].startswith("e_")


def test_rule_d_surname_may_come_first():
    arts = [
        artifact(
            s1=story(people=["Kakani Govardhan", "Kakani Sridhar"]),
            s2=story(people=["Kakani Govardhan", "Kakani Sridhar"]),
        )
    ]
    found = rules.candidate_pairs(names.collect(arts), 0.99, 2)
    assert list(found.values()) == ["d"]


def test_a_cut_off_cache_line_is_skipped(tmp_path):
    path = tmp_path / "v.jsonl"
    v = judge.Verdicts(path, prompt="aaaa")
    v.add("person", "a", "b", Verdict(True, 0.9))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"type": "person", "a": "c", "b"')
    again = judge.Verdicts(path, prompt="aaaa")
    assert again.get("person", "a", "b") == Verdict(True, 0.9) and len(again) == 1
