# 004: Unpack, normalize and register pages - Evaluation

## Setup

- Prerequisite (user, one-time): `sudo apt install unrar`.
- Start: `uv sync && uv run newsrag stores up && uv run newsrag ledger init`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none. Unpacking all three archives takes ~1-2 h and ~90 GB; it is done once into `/home/admin1/raw/source` and reused by later tickets.

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0
- `uv run pytest -q -m stores tests/test_register.py` exits 0

## Acceptance checks

### E-1: Filename rules (covers FR-3, FR-5, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_filenames.py -v`
- **Expected:** all pass, and the output lists at least 11 parametrized cases including ids containing `EPAPER`, `SPLUS`, `VK`, `for Net`, `(RR)` and `September2026`.

### E-2: Unpack all archives, originals untouched (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `ls -l --time-style=full-iso /home/admin1/data > /tmp/e2-before.txt`
  2. `uv run newsrag ingest unpack`
  3. `ls -l --time-style=full-iso /home/admin1/data > /tmp/e2-after.txt && diff /tmp/e2-before.txt /tmp/e2-after.txt && echo same`
  4. `for s in 2020 2025 2026; do test -f /home/admin1/raw/source/$s/.unpacked && echo "$s ok"; done`
  5. `find /home/admin1/raw/source -iname '*.pdf' | wc -l`
- **Expected:** step 3 prints `same`; step 4 prints `2020 ok`, `2025 ok`, `2026 ok`; step 5 prints `21063` (11,859 + 4,683 + 4,521) or a number within ±5 of it.

### E-3: Unpack is skipped the second time (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag ingest unpack --archive 2025.rar`
- **Expected:** output contains `skipped (already unpacked)` and the command finishes in under 10 s.

### E-4: Register all and report (covers FR-4, FR-6, FR-7, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag ingest register`
  2. `for s in 2020 2025 2026; do python3 -c "import json;r=json.load(open('/home/admin1/raw/reports/register-$s.json'));print('$s',r['registered'],len(r['superseded']),len(r['quarantined']),r['quarantine_pct'])"; done`
  3. `uv run newsrag db sql "select count(*), count(distinct page_id) from ledger.pages"`
- **Expected:** step 1 real time < 10 min; for each archive `registered + superseded + quarantined` = `find /home/admin1/raw/source/<stem> -iname '*.pdf' | wc -l` (a page superseded by a file in another archive counts as superseded in its own archive's report); step 3's two numbers are equal and equal the sum of `registered`; each `quarantine_pct` is printed (its value is checked against the ≤ 1% bar in ticket 031, not here).

### E-5: Revision winner is correct on real data (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run python - <<'PY'
import json,glob
from newsrag.ingest.filenames import parse
bad=0;n=0
for f in glob.glob('/home/admin1/raw/reports/register-*.json'):
    for s in json.load(open(f))['superseded']:
        n+=1;w,l=parse(s['winner']),parse(s['path'])
        if (l.revision, l.copy_kind=='print') > (w.revision, w.copy_kind=='print'): bad+=1
print(n, bad)
PY`
- **Expected:** prints a superseded count > 0 and `0` (no loser has a higher revision, or the same revision as a print copy while the winner is not print).

### E-6: Re-run is idempotent (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag db sql "select md5(string_agg(page_id||source_path, ',' order by page_id)) from ledger.pages"`
  2. `uv run newsrag ingest register`
  3. repeat step 1
- **Expected:** steps 1 and 3 print the same hash.

## Edge cases

### E-7: Newer revision arriving later resets the page (covers FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_register.py -k newer_revision`
- **Expected:** passes; the test registers a temp tree with `01FH-MAIN_02.pdf`, adds a fake `stage_runs` row, adds `01FH-MAIN_02R.pdf`, re-registers, and asserts the page now points at the R file and has zero `stage_runs` rows.

### E-8: Quarantine reasons (covers FR-3, FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_register.py -k quarantine`
- **Expected:** passes; a temp tree with `xyz.pdf` (no date folder), `2020/April-2020/01-04-2020/01FH-DUMMY_01.pdf`, `.../01FH-MAIN_01TEST.pdf` and `2025/April-2025/26-30/31FH-MAIN_01.pdf` (April 31) produces quarantine rows with reasons `no_date`, `test_or_dummy`, `test_or_dummy`, `bad_date` and no pages rows for them; no pages rows for them. Then `uv run pytest -q tests/test_register.py -k unrar_failure` passes (fake unrar failing on one of two archives: exit 1, the FR-8 message, a marker only for the other archive).

### E-9: Missing unrar (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_register.py -k missing_unrar` (monkeypatches `shutil.which("unrar")` to `None`)
- **Expected:** passes, asserting exit code 2 and message `unrar not found: run  sudo apt install unrar`.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2, E-3 |
| FR-2 | E-9 |
| FR-3 | E-1, E-8 |
| FR-4 | E-4, E-5 |
| FR-5 | E-1 |
| FR-6 | E-4, E-6, E-7, E-8 |
| FR-7 | E-4 |
| FR-8 | E-8 |
| NFR-1 | E-1 |
| NFR-2 | E-4 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-6 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (command logs, the three register reports) saved in `evidence/` and listed in `results.md`
