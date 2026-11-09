"""Ticket 018: district/mandal reference list and resolver."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from newsrag.config import settings
from newsrag.geo import check, load_index, normalise, resolve

REAL = settings.geo_dir


def _one(places, kind):
    hits = [p for p in places if p.kind == kind]
    assert len(hits) == 1, places
    return hits[0]


# --- the committed data -------------------------------------------------------------------------


def test_committed_data_is_valid():
    assert check(REAL, None) == []


def test_periods_cut_over_on_2022_04_04():
    p = yaml.safe_load((REAL / "periods.yaml").read_text(encoding="utf-8"))
    ap = [(x["file"], str(x["valid_from"]), str(x["valid_to"])) for x in p["andhra_pradesh"]]
    assert ap[0] == ("ap_districts_2014_2022.yaml", "2014-06-02", "2022-04-03")
    assert ap[1][:2] == ("ap_districts_2022.yaml", "2022-04-04")
    assert p["telangana"][0]["file"] == "ts_districts_2019.yaml"


def test_vijayawada_town_2020():
    town = _one(resolve("విజయవాడ", date(2020, 5, 1)), "town")
    assert town.id == "vijayawada"
    assert town.districts_current == ["ntr"]
    assert town.districts_at_date == ["krishna"]


@pytest.mark.parametrize("name", ["Vizag", "విశాఖ"])
def test_vizag_aliases(name):
    assert _one(resolve(name), "town").id == "visakhapatnam"


def test_tirupati_town_and_district_2020():
    places = resolve("తిరుపతి", date(2020, 5, 1))
    town = _one(places, "town")
    assert town.districts_at_date == ["chittoor"]
    assert town.districts_current == ["tirupati"]
    district = _one(places, "district")
    assert district.districts_at_date == ["chittoor", "sri_potti_sriramulu_nellore"]
    assert district.districts_current == ["tirupati"]


def test_hyderabad_is_telangana():
    places = resolve("హైదరాబాద్")
    assert places and all(p.state == "telangana" for p in places)


def test_krishna_district_with_suffix():
    assert _one(resolve("కృష్ణా జిల్లా"), "district").id == "krishna"


def test_no_match():
    assert resolve("xyzabc") == []


def test_old_krishna_maps_to_krishna_and_ntr():
    d = _one(resolve("Krishna district", date(2020, 5, 1)), "district")
    assert d.districts_at_date == ["krishna"]
    assert {"krishna", "ntr"} <= set(d.districts_current)
    # Today it is only itself.
    assert _one(resolve("Krishna district"), "district").districts_current == ["krishna"]


def test_one_place_per_id():
    # Guntur is a district in all three AP periods and an HQ town: one Place each.
    places = resolve("Guntur", date(2023, 1, 1))
    keys = [(p.kind, p.id) for p in places]
    assert len(keys) == len(set(keys))
    assert ("district", "guntur") in keys and ("town", "guntur") in keys


def test_hq_mandal_link():
    index = load_index(REAL)
    for did, by_file in index.districts.items():
        for f, entry in by_file.items():
            mid = entry["hq"]["mandal_id"]
            assert index.mandals[mid]["districts"][f] == did, (f, did, mid)


def test_date_before_first_period_uses_first():
    town = _one(resolve("విజయవాడ", date(2001, 1, 1)), "town")
    assert town.districts_at_date == ["krishna"]


def test_normalise():
    assert normalise("  Krishna  District ") == "krishna"
    assert normalise("కృష్ణా జిల్లా") == "కృష్ణా"
    assert normalise("మహబూబ్ నగర్") == normalise("మహబూబ్‌నగర్")
    assert normalise("G. Madugula mandal") == "gmadugula"


def test_speed():
    resolve("విజయవాడ")
    t = time.time()
    for _ in range(10000):
        resolve("విజయవాడ")
    assert time.time() - t < 1


def test_returned_lists_do_not_change_the_cache():
    a = _one(resolve("విజయవాడ"), "town")
    a.districts_current.append("x")
    assert _one(resolve("విజయవాడ"), "town").districts_current == ["ntr"]


def test_cli_check():
    from newsrag.cli import app

    result = CliRunner().invoke(app, ["geo", "check"])
    assert result.exit_code == 0, result.output
    first = result.output.splitlines()[0]
    assert first.startswith("ap_current=28 ap_2014_2022=13 ts=33 ")


# --- small fixture data ---------------------------------------------------------------------------


def _write(path: Path, doc) -> None:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _hq(town, mandal):
    return {"id": town, "name_en": town.title(), "name_te": "ఊరు", "mandal_id": mandal}


@pytest.fixture
def tiny(tmp_path):
    """Two AP periods: old `big` splits into `big` + `new`; mandal m3 split from m2 later."""
    geo = tmp_path / "geo"
    geo.mkdir()
    _write(
        geo / "periods.yaml",
        {
            "andhra_pradesh": [
                {"file": "old.yaml", "valid_from": "2014-06-02", "valid_to": "2022-04-03"},
                {"file": "new.yaml", "valid_from": "2022-04-04", "valid_to": None},
            ]
        },
    )
    common = {"as_of": "2026-01-01", "source_urls": ["https://example.org"]}
    _write(
        geo / "old.yaml",
        {
            **common,
            "districts": [
                {"id": "big", "name_en": "Big", "name_te": "పెద్ద", "hq": _hq("bigtown", "m1")}
            ],
        },
    )
    _write(
        geo / "new.yaml",
        {
            **common,
            "districts": [
                {"id": "big", "name_en": "Big", "name_te": "పెద్ద", "hq": _hq("bigtown", "m1")},
                {"id": "new", "name_en": "New", "name_te": "కొత్త", "hq": _hq("newtown", "m2")},
            ],
        },
    )
    mandals = [
        {
            "id": "m1",
            "name_en": "One",
            "name_te": "ఒకటి",
            "state": "andhra_pradesh",
            "districts": {"old.yaml": "big", "new.yaml": "big"},
        },
        {
            "id": "m2",
            "name_en": "Two",
            "name_te": "రెండు",
            "state": "andhra_pradesh",
            "districts": {"old.yaml": "big", "new.yaml": "new"},
        },
        {
            "id": "m3",
            "name_en": "Three",
            "name_te": "మూడు",
            "state": "andhra_pradesh",
            "districts": {"old.yaml": None, "new.yaml": "new"},
            "split_from": "m2",
        },
    ]
    _write(geo / "mandals.yaml", {"as_of": "2026-01-01", "source_urls": [], "mandals": mandals})
    aliases = tmp_path / "aliases.yaml"
    _write(aliases, [{"alias": "Bigga", "kind": "town", "id": "bigtown"}])
    return geo, aliases


def _edit(path: Path, fn) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(doc)
    _write(path, doc)


def test_check_entry_point(tiny):
    geo, aliases = tiny
    assert check(geo, aliases) == []
    index = load_index(geo, aliases)
    assert [p.id for p in index.resolve("Bigga")] == ["bigtown"]


def test_check_fails_on_missing_telugu_name(tiny):
    geo, aliases = tiny
    _edit(geo / "mandals.yaml", lambda d: d["mandals"][0].update(name_te=""))
    _edit(geo / "new.yaml", lambda d: d["districts"][1].update(name_te=None))
    problems = check(geo, aliases)
    assert any("m1 has no name_te" in p for p in problems)
    assert any("district new has no name_te" in p for p in problems)


def test_check_fails_on_mandal_without_period(tiny):
    geo, aliases = tiny
    _edit(geo / "mandals.yaml", lambda d: d["mandals"][1]["districts"].pop("old.yaml"))
    problems = check(geo, aliases)
    assert any("m2 has no district for old.yaml" in p for p in problems)
    # a district id that is not in that period's file is a problem too
    _edit(geo / "mandals.yaml", lambda d: d["mandals"][1]["districts"].update({"old.yaml": "new"}))
    assert any("district new is not in old.yaml" in p for p in check(geo, aliases))


def test_check_fails_on_bad_alias_target(tiny):
    geo, aliases = tiny
    _write(aliases, [{"alias": "Nowhere", "kind": "town", "id": "nope"}])
    assert any("missing town 'nope'" in p for p in check(geo, aliases))


def test_check_fails_on_district_without_mandal_and_bad_hq(tiny):
    geo, aliases = tiny
    _edit(geo / "new.yaml", lambda d: d["districts"][0]["hq"].update(mandal_id="zz"))
    _edit(
        geo / "mandals.yaml",
        lambda d: [m["districts"].update({"new.yaml": "big"}) for m in d["mandals"][1:]],
    )
    problems = check(geo, aliases)
    assert any("hq.mandal_id zz not found" in p for p in problems)
    assert any("district new has no mandal" in p for p in problems)


def test_check_current_period_may_not_be_null(tiny):
    geo, aliases = tiny
    _edit(geo / "mandals.yaml", lambda d: d["mandals"][0]["districts"].update({"new.yaml": None}))
    assert any("m1 has no district for new.yaml" in p for p in check(geo, aliases))


def test_mandal_split_from(tiny):
    geo, aliases = tiny
    index = load_index(geo, aliases)
    m3 = index.resolve("మూడు", date(2020, 1, 1))[0]
    assert (m3.kind, m3.districts_at_date, m3.districts_current) == ("mandal", ["big"], ["new"])
    # without split_from: no district before it existed
    _edit(geo / "mandals.yaml", lambda d: d["mandals"][2].pop("split_from"))
    m3 = load_index(geo, aliases).resolve("Three", date(2020, 1, 1))[0]
    assert m3.districts_at_date == []


def test_district_by_period(tiny):
    geo, aliases = tiny
    index = load_index(geo, aliases)
    old_big = index.resolve("Big", date(2020, 1, 1))[0]
    assert old_big.districts_at_date == ["big"]
    assert old_big.districts_current == ["big", "new"]
    assert index.resolve("Big", date(2024, 1, 1))[0].districts_current == ["big"]
    new_2020 = index.resolve("కొత్త జిల్లా", date(2020, 1, 1))[0]
    assert (new_2020.districts_at_date, new_2020.districts_current) == (["big"], ["new"])


def test_town_districts_follow_its_mandal(tiny):
    geo, aliases = tiny
    index = load_index(geo, aliases)
    town = next(p for p in index.resolve("Newtown", date(2020, 1, 1)) if p.kind == "town")
    assert (town.districts_at_date, town.districts_current) == (["big"], ["new"])
