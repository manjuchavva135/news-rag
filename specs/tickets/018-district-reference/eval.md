# 018: District and mandal reference list - Evaluation

## Setup

- Start: `uv sync`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Files valid and counted (covers FR-1, FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag geo check; echo "exit=$?"`
  2. `uv run python -c "import yaml,glob;[print(f, yaml.safe_load(open(f))['as_of'], sum(u.startswith('https://') for u in yaml.safe_load(open(f))['source_urls'])) for f in sorted(glob.glob('data/geo/*_districts_*.yaml'))]"`
  3. `uv run python -c "import yaml;p=yaml.safe_load(open('data/geo/periods.yaml'));print([(x['file'],str(x['valid_from']),str(x['valid_to'])) for x in p['andhra_pradesh']])"`
- **Expected:** step 1 prints `ap_current=<≥ 26> ap_2014_2022=13 ts=<≥ 33> ap_mandals=<≥ 600> ts_mandals=<≥ 500>` and `exit=0`; step 2 prints each district file with an `as_of` date and a URL count ≥ 1; step 3 lists `ap_districts_2014_2022.yaml` ending `2022-04-03` followed by `ap_districts_2022.yaml` starting `2022-04-04`.

### E-2: Resolver (covers FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_geo.py -v`
- **Expected:** all pass, including these cases (matching on the `town` match where a name is both a town and a district):
  - `resolve("విజయవాడ", date(2020,5,1))` town → `districts_current == ["ntr"]`, `districts_at_date == ["krishna"]`
  - `resolve("Vizag")` and `resolve("విశాఖ")` → town `visakhapatnam`
  - `resolve("తిరుపతి", date(2020,5,1))` town → at_date `["chittoor"]`, current `["tirupati"]`; district match → at_date `["chittoor", "sri_potti_sriramulu_nellore"]`
  - `resolve("హైదరాబాద్")` → state `telangana`
  - `resolve("కృష్ణా జిల్లా")` → district `krishna`
  - `resolve("xyzabc")` → `[]`

### E-3: Speed (covers NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run python -c "import time;from newsrag.geo import resolve;resolve('విజయవాడ');t=time.time();[resolve('విజయవాడ') for _ in range(10000)];print(time.time()-t<1)"`
- **Expected:** `True`.

## Edge cases

### E-4: Broken data fails the check (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_geo.py -k "check_fails_on_missing_telugu_name or check_fails_on_mandal_without_period or check_fails_on_bad_alias_target"`
- **Expected:** 3 passed.

### E-5: Second-review clarifications (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_geo.py -k "old_krishna_maps_to_krishna_and_ntr or one_place_per_id or mandal_split_from or hq_mandal_link or check_entry_point"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-2 |
| FR-3 | E-2 |
| FR-4 | E-1, E-4 |
| FR-5 | E-1 |
| NFR-1 | E-3 |
| FR-6 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (check output, source list) saved in `evidence/` and listed in `results.md`
