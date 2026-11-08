# 018: District and mandal reference list

**Status:** draft
**Mode:** solo
**Runs:** waits for 001: needs repo layout

## Summary

Customer questions are district-wise, and AP redrew its districts in April 2022 (13 → 26, possibly more
since), so 2020 pages use old districts and 2025/2026 pages the new ones. This ticket builds, once, an
official reference of AP and Telangana districts and mandals in Telugu and English, with dated periods
and mandal-level old→new mapping, plus a resolver that turns a place name as printed into its districts
(grill D-18, A-44). Used by folio parsing (011) and place enrichment (020).

## Functional requirements

- **FR-1** - `data/geo/` (committed; path from setting `geo_dir`, default `<repo>/data/geo`) holds:
  - `periods.yaml` — for each state (`andhra_pradesh`, `telangana`), an ordered list of district-list periods: `{file, valid_from, valid_to}` (`valid_to: null` = current). AP has at least `ap_districts_2014_2022.yaml` (valid_from 2014-06-02, valid_to 2022-04-03) and `ap_districts_2022.yaml` (valid_from 2022-04-04). If official sources show AP districts added after 2022-04-04, the worker adds another period file with its official effective date (so pages printed in between keep the list valid then). Telangana has one period file `ts_districts_2019.yaml` (33 districts, valid_from 2019-02-17) unless sources show a later change, handled the same way.
  - Each district file: header `as_of`, `source_urls` (inline YAML list), and per district `{id, name_en, name_te, hq: {id, name_en, name_te}}`. `id` = lower-case ASCII slug of the official English name (`ntr`, `krishna`, `tirupati`, `sri_potti_sriramulu_nellore`). No mandal lists in these files.
  - `mandals.yaml` — the single source of mandal data: for every AP and TS mandal `{id, name_en, name_te, state, districts: {<period file>: <district id>, ...}}` giving its district in every period file of its state. A district HQ town is linked to its mandal by `hq.mandal_id` in the district files.
- **FR-2** - `newsrag.geo.resolve(name: str, on_date: date | None = None) -> list[Place]` (defined in `src/newsrag/geo/resolve.py`, re-exported from `newsrag.geo`). It normalises the input (NFC, lower-case for Latin, drop spaces, dots and the suffixes `జిల్లా`, `district`, `మండలం`, `mandal`) and matches district names, HQ town names and mandal names from **all** periods plus `aliases.yaml`. Each match is `Place(kind: "district"|"mandal"|"town", id, name_en, name_te, state, districts_current: list[str], districts_at_date: list[str])` with district **ids**:
  - mandal / town: `districts_current` = its district in the current period; `districts_at_date` = its district in the period containing `on_date` (current if `on_date` is None);
  - district: `districts_current` = `[id]` if the district exists in the current period, else the current districts of its mandals; `districts_at_date` = the districts of its mandals in the period containing `on_date` (a new district made from several old ones gives several ids, e.g. `tirupati` before 2022-04-04 → `["chittoor", "sri_potti_sriramulu_nellore"]`);
  - a date before the first period uses the first period. Ambiguous names return all matches; no match returns `[]`.
- **FR-3** - `src/newsrag/geo/aliases.yaml` (shipped as package data) is a list of `{alias, kind, id}` entries pointing an extra spelling at one district, town or mandal id — e.g. `{alias: Vizag, kind: town, id: visakhapatnam}`, `{alias: విశాఖ, kind: town, id: visakhapatnam}`, `{alias: Bezawada, kind: town, id: vijayawada}`, `{alias: Rajahmundry, kind: town, id: rajamahendravaram}`. Ticket 020 appends entries in this format.
- **FR-4** - `uv run newsrag geo check` validates: every district and mandal has non-empty `name_en` and `name_te`; every mandal has a district for every period file of its state and that district id exists in that file; every district has ≥ 1 mandal; every `hq.mandal_id` and alias target exists. It prints `ap_current=<n> ap_2014_2022=13 ts=<n> ap_mandals=<n> ts_mandals=<n>` and exits 1 (listing the problems) on any problem.
- **FR-5** - Sources: official AP/TS government sources first (gazette notifications, `ap.gov.in`, `telangana.gov.in`, district portals); for Telugu spellings missing from official lists, Telugu Wikipedia may be used. Every file's `source_urls` lists what was used.
- **FR-6** - Clarifications (second review):
  - Districts by period: a district match is taken from the period containing `on_date`; its `districts_current` = the current districts of that period's mandals (old Krishna in 2020 → `["krishna", "ntr"]`); `districts_at_date` = `[id]`.
  - One Place per `(kind, id)`: entries of the same id in several periods are merged, using the period containing `on_date`.
  - A mandal may have `null` for periods before it existed; then `districts_at_date` = the district of the mandal it was split from (`split_from: <mandal id>` in `mandals.yaml`), else `[]`.
  - District files' `hq` has `mandal_id`; a town's districts come from that mandal in `mandals.yaml`.
  - Testable entry points: `newsrag.geo.check(geo_dir, aliases_path) -> list[str]` and `newsrag.geo.load_index(geo_dir, aliases_path)`; the CLI wraps them. `geo check` prints one `<state>_<file stem>=<n>` per period plus the mandal counts; `results.md` names the official source and effective date for the current AP district count.

## Non-functional requirements

- **NFR-1** - `resolve` handles 10,000 lookups in < 1 s (in-memory index built once).

## Affected areas

- `data/geo/periods.yaml`, `data/geo/ap_districts_2014_2022.yaml`, `data/geo/ap_districts_2022.yaml` (+ later AP period files if needed), `data/geo/ts_districts_2019.yaml`, `data/geo/mandals.yaml` (new)
- `src/newsrag/geo/__init__.py`, `resolve.py`, `aliases.yaml` (new)
- `src/newsrag/config.py` - `geo_dir` (changed); `pyproject.toml` - `pyyaml` dependency and package data for `aliases.yaml` (changed)
- `src/newsrag/cli.py` - `geo` group (changed)
- `tests/test_geo.py` (new)

## Don't touch

- Any other module.

## Out of scope

- Constituencies, villages, pre-2019 Telangana districts (no data before 2020).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Old vs new districts | both, through dated periods | grill D-18 |
| D-2 | Sources | official first; Telugu Wikipedia only for missing Telugu spellings; all URLs recorded | grill A-44; official lists may lack Telugu mandal names |
| D-3 | Cut-over dates | stored per period in `periods.yaml`, not hardcoded | AP may have changed districts again after 2022 (A-44) |
| D-4 | Value form | district ids (ASCII slugs); state `andhra_pradesh`/`telangana` | one stable form shared by 011, 020, 024 |
| D-5 | New district from several old ones | `districts_at_date` is a list | e.g. Tirupati came from Chittoor and Nellore |
| D-6 | Mandal data | one file (`mandals.yaml`), district files have none | avoids two copies drifting apart |

## Open questions

None.
