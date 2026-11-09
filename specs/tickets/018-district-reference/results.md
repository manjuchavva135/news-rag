# 018: District and mandal reference list - Results

Built on branch `ticket-018-geo` (from main at `ccf9335`).

**Three AP periods, not two.** AP formed Markapuram and Polavaram districts with effect from
31 Dec 2025 (26 → 28 districts; Annamayya's HQ moved from Rayachoti to Madanapalle, and a few
mandals changed district). As FR-1 asks, this is a third period file:

| state | file | valid_from | valid_to | districts |
|---|---|---|---|---|
| andhra_pradesh | `ap_districts_2014_2022.yaml` | 2014-06-02 | 2022-04-03 | 13 |
| andhra_pradesh | `ap_districts_2022.yaml` | 2022-04-04 | 2025-12-30 | 26 |
| andhra_pradesh | `ap_districts_2025.yaml` | 2025-12-31 | (current) | 28 |
| telangana | `ts_districts_2019.yaml` | 2019-02-17 | (current) | 33 |

Current AP district count: **28**, effective **2025-12-31** (AP cabinet decision reported in
Eenadu, 30 Dec 2025; English Wikipedia's *List of districts of Andhra Pradesh*, which cites the
Government of India's igod.gov.in directory). I could not reach an AP gazette copy of the
notification: igod.gov.in and the LGD web service did not answer from this machine.

## What was built

- `data/geo/periods.yaml`, the four district files and `mandals.yaml` (688 AP + 621 TS mandals,
  each with `name_en`, `name_te`, `state`, its district in every period file of its state, and
  the `wikidata` id it came from). District files carry `as_of`, `source_urls` and
  `{id, name_en, name_te, hq: {id, name_en, name_te, mandal_id}}` per district.
- `src/newsrag/geo/resolve.py` (re-exported from `newsrag.geo`): `Place`, `normalise`,
  `load_index(geo_dir, aliases_path)`, `resolve(name, on_date=None)`, `check(geo_dir,
  aliases_path) -> list[str]`, `counts`, `summary_line`. The index is built once (cached) and
  lookups are cached per (normalised name, date): 10,000 lookups take ~0.04 s.
- `src/newsrag/geo/aliases.yaml` (package data): 68 extra spellings (Vizag, విశాఖ, Bezawada,
  రాజమండ్రి, short district names such as నెల్లూరు / కడప / కోనసీమ, Telangana `-ాదు` spellings…).
- `src/newsrag/geo/cli.py`: `uv run newsrag geo check`, registered in `cli.py`'s `SUB_APPS`.
- Settings under `# --- geo ---`: `geo_dir` (default `<repo>/data/geo`, a `REPO_ROOT_DERIVED`
  rule) and `geo_aliases_file` (None = the packaged file).
- `tests/test_geo.py`: 26 tests (real data + a tiny two-period fixture for `check` and the
  period rules).

## How the data was made (FR-5)

Machine-readable official mandal lists with Telugu names were not reachable (the LGD web
service answered "URL can not be found"; igod.gov.in did not answer). So:

- **Mandals, their Telugu names and their dated districts: Wikidata** (classes *mandal of
  Andhra Pradesh* Q122987710 and *mandal of Telangana* Q122987738; P131 with start/end
  qualifiers). Wikidata's current AP list matches the per-district mandal counts of the
  Wikipedia district table exactly (688 mandals in 28 districts). Telangana's 621 = the 612 of
  the official *Telangana State Statistical Abstract 2022* (telangana.gov.in) plus the
  mandals formed in 2022-23.
- **Gaps in the older periods: dated English Wikipedia tables** — revision 1075240456
  (March 2022, 13 districts) and 1304848017 (August 2025, 26 districts), joined to Wikidata by
  the linked article's Wikidata id or a unique name match. Where both have a value, Wikidata
  wins: the August 2025 table puts five Chittoor mandals (Punganur…) in Annamayya, which gives
  Annamayya 36 mandals against the official 30.
- **25 Telangana mandals formed in 2022-23 have no Telugu label on Wikidata:** their Telugu
  names were checked against Telugu Wikipedia page titles (`te_lookup.py`); one
  (మహమ్మద్‌నగర్) has no tewiki page and is my own spelling.
- **District and HQ names in Telugu**: Wikidata labels, normalised to the usual spelling
  (e.g. హనుమకొండ, మహబూబ్‌నగర్).

The build scripts, the source snapshots and the build log are in `evidence/build/`. The build
log lists every decision it made:
- 9 conflicts where the table and Wikidata disagree; Wikidata was used each time.
- 5 earlier-period districts inferred from the later district's main parent: Nandyal Urban and
  Rural → kurnool, Eluru Urban and Rural → west_godavari (all four were split after 2022), and
  Gurtedu (new in Dec 2025) → alluri_sitharama_raju / visakhapatnam. No mandal needed
  `split_from`, so none has one. The loader and check support it, and the fixture tests cover
  it.

Because of those splits, the 2014-2022 file covers 688 mandals, not the 679 that existed then.

## Decisions made while building

- **`geo check` output.** E-1 wants `ap_current=… ap_2014_2022=13 ts=… ap_mandals=…
  ts_mandals=…`, and FR-6 wants one `<state>_<file stem>=<n>` per period. The command prints
  the E-1 line first, then the per-period lines and the mandal counts.
- **A district asked about on a date when it did not exist yet** (Tirupati in 2020) is described
  through its mandals in the nearest later period, i.e. as it was first formed. Taking its
  current mandals would add `ysr_kadapa`, because Koduru moved into Tirupati in Dec 2025, and
  the E-2 example wants `["chittoor", "sri_potti_sriramulu_nellore"]`.
- **District lists are sorted alphabetically.** Old Krishna in 2020 has
  `districts_current == ["eluru", "krishna", "ntr"]`: the Nuzvid mandals went to Eluru. The
  test checks that `krishna` and `ntr` are in it.
- **`null` in a mandal's districts** is allowed only before its first non-null period, and never
  for the current period.
- **Matching ignores** case, spaces, dots, zero-width joiners, dashes and apostrophes, plus a
  trailing జిల్లా/district/మండలం/mandal. So మహబూబ్ నగర్ = మహబూబ్‌నగర్.
- **District ids** are slugs of the official English name, e.g. `dr_b_r_ambedkar_konaseema`,
  `ysr_kadapa`, `kumuram_bheem_asifabad`. TS Warangal Urban/Rural are `hanumakonda` /
  `warangal`, their names since 2021.
- `check` also verifies that each HQ's mandal lies in that district in that period, that period
  dates do not overlap, and that `split_from` targets exist.

## Checks (`evidence/eval-run.txt`)

| Check | Result |
|---|---|
| `uv run ruff check .` / `ruff format --check .` | PASS |
| `uv run pytest -q -m "not gpu and not data and not stores"` | PASS (260 passed) |
| E-1 `geo check` | PASS: `ap_current=28 ap_2014_2022=13 ts=33 ap_mandals=688 ts_mandals=621`, exit 0 |
| E-1 step 2 | PASS: each district file has `as_of` 2026-11-09 and 4-5 https URLs |
| E-1 step 3 | PASS: 2014_2022 ends 2022-04-03, 2022 starts 2022-04-04 (then 2025 from 2025-12-31) |
| E-2 `tests/test_geo.py -v` | PASS (26 passed, all listed cases) |
| E-3 speed | PASS (`True`, ~0.04 s) |
| E-4 | PASS (3 passed) |
| E-5 | PASS (5 passed) |

## Not done / limits

- Mandal-level membership comes from Wikidata/Wikipedia, cross-checked against official
  counts, not taken from a gazette. It may be wrong for a handful of mandals (the build log
  lists the ones that needed a decision).
- Village names are not covered (out of scope), so a folio or story naming a village resolves
  to nothing.
