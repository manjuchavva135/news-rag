# 011: Edition and state from the folio - Results

Built on branch `ticket-011-folio`, which starts from `ticket-018-geo` (ticket 018 was built
first because 011 needs its resolver). `ocr_final` was run for all pages (no fallback rules, so
it only copies `ocr_split`). The `folio` stage then ran over all 3,497 pages: 0 failed, in
2,355 s. The ledger has been migrated with `002_page_editions.sql`. `editions build`,
`editions apply` and `editions report` have run on the real ledger.

## Spec change made with the user (2026-11-10): `-MAIN` pages are state pages

All 3,497 registered pages are section `main`. Their folio prints the printing centre whatever
the edition code: `… / విజయవాడ | www.sakshi.com` on 2,685 of the 2,727 pages with a place. The
other 42 are mostly headline words sitting in the top band, and `ALL` (family supplement) pages
print హైదరాబాద్.

As first built, every `ok` code mapped to విజయవాడ (29 codes in 2024, 36 in 2025), and
`page_editions` put almost every page in the edition విజయవాడ, district `ntr`. The user chose to
treat these as state pages (new rd.md FR-7, D-9; eval.md E-2, E-3, E-4 changed, E-7 added):

- Pages whose `section` is in `editions_state_sections` (default `["main"]`) never feed the
  code → name map.
- `editions apply` gives them no edition names or districts, and `states` = the page's own
  folio state. Failing that, it takes the most common folio state of that year's state pages
  with the same code, because front pages print no place but share their code with inner
  pages that do.
- They get `source = 'main'` and the informational flag `state_page`.
  `low_confidence:unknown_edition` is added only when no state is found at all.
- `editions report` adds `state_pages=<n>` to each year line and lists the state pages' top
  folio places, for information only.
- A story's district must come from its text (ticket 020).

**Two further changes, also in the spec:**
- E-3 uses 2025: the archives hold 2024-12 and 2025 only, as found in 010.
- NFR-1 is now "≥ 80% of the non-front dev-sample pages". 7 of the 20 sample pages are front
  pages, whose masthead prints no place.

So on today's corpus `data/editions.yaml` is empty (`{}`). The map, the letter rule and the
FR-4 order are built and unit-tested, and will name editions once non-main (district) pages are
registered.

## What was built

- `src/newsrag/stages/folio.py`: PageStage `folio` (`depends_on=("ocr_final",)`,
  `concurrency=1`). Its version is `versioned("folio-v1", [folio_labels, folio_top_fraction,
  folio_band_pieces], <data/geo/*.yaml + aliases>)` as a cached property, so changing the geo
  data reruns it (CLAUDE.md's version rule; FR-1 said plain `folio-v1`).
  - It collects `folio_labels` blocks plus blocks lying wholly in the top 8% of the page.
  - Blocks without text are re-read in one indic-ocr request, under
    `gpu_lock("indic-ocr")`. The model starts only when the first page needs it, as in
    `ocr_split`.
  - Artifact as in FR-1, plus `place_candidates` when the place stays ambiguous.
- `src/newsrag/editions/folio.py`: the pure parser.
  - Places: tokens split on whitespace and `। | , -` (plus `/` and `॥`), runs of 1–3 tokens
    tried left to right with `newsrag.geo.resolve`, and FR-2's choice rule. When the place
    stays ambiguous, the artifact gets `place_candidates`.
  - States: the AP/TS state name alone, when no place resolves.
  - Dates: `DD-MM-YYYY`, `DD.MM.YYYY`, `DD । MM । YYYY`, Telugu month names (both day-first
    and month-first) and Telugu digits.
  - Page numbers: taken from the dated folio line, or from a block that is only a number.
- `src/newsrag/editions/mapping.py` (pure) and `store.py` (ledger, YAML, DB):
  - `build_map` implements FR-3 with the letter rule; `ALL`, `FH` and `-` variants are never
    split, and state pages are skipped.
  - `merge_entry` keeps hand-added keys. A rebuild drops the generated keys of codes no longer
    built, and an entry left empty is removed.
  - `page_edition` implements FR-4, FR-6 and FR-7; `state_page_states` gives the per-code
    state fallback.
  - `report_lines` implements FR-5 and FR-6, plus the state pages.
- `src/newsrag/editions/cli.py`: `newsrag editions build | apply | report`, registered in
  `cli.py`.
- `src/newsrag/ledger/sql/002_page_editions.sql`: the `page_editions` table. `apply` deletes
  and re-inserts it in one transaction.
- Settings under `# --- editions ---`:
  - `folio_top_fraction` (0.08), `folio_band_pieces` (2)
  - `editions_file` (`<repo>/data/editions.yaml`)
  - `editions_min_agreement` (0.6), `editions_min_pages` (3)
  - `editions_state_sections` (`["main"]`)
- `data/editions.yaml`: generated and committed (`{}` on this corpus).
- Tests:
  - `tests/test_folio_parse.py` (26): real folio strings OCR'd from dev pages, plus the E-1
    string.
  - `tests/test_editions.py` (27): every name listed in E-5/E-6/E-7. The folio stage is tested
    with a fake text reader, and `apply`/`report` against a throw-away ledger schema
    (`stores`).

## Changes beyond the ticket text

- **Top-band fallback (`folio_band_pieces`).** When the folio blocks give no place, the top 8%
  band is read too, in two side-by-side pieces. Surya's layout missed the folio strip on
  2025-05-04 page 6, and OCR misread విజయవాడ on 2024-12-09 R page 3. This recovered both
  (dev sample 9 → 11 pages with a place). Setting it to `0` turns it off.
- **The place is looked for first in the blocks that carry the printed date**, then in all
  folio blocks, so a headline in the top band does not win over the folio line.
- **Blocks read as LaTeX are dropped from `folio_text`.** These are decorative footers that the
  OCR model turns into `\text{---}` markup. They are still listed in `folio_blocks`.

## Checks (`evidence/eval-run.txt`, script `evidence/eval011.sh`; eval.md as changed)

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` | PASS |
| `pytest -q -m "not gpu and not data and not stores"` | PASS (312 passed) |
| `stores` test of 011 (`apply`/`report` in a throw-away schema) | PASS |
| E-1 `tests/test_folio_parse.py` | PASS (26); E-1 string → విజయవాడ, andhra_pradesh, 2026-01-14 |
| E-2 / NFR-1 | PASS: `13 11` (85% of non-front pages; 11 of all 20) |
| E-3 | PASS: map `{}`; report lines `2024 … state_pages=746`, `2025 … state_pages=2751` = main pages with folio done |
| E-4 | PASS: `3497 36 0 3461`; folio done = 3497; 3497 = 36 + 0 + 3461; step 4 `0` |
| E-5 | PASS (9 selected) |
| E-6 | PASS (4 selected) |
| E-7 | PASS (3 selected) |

`page_editions` now holds 3,449 pages `andhra_pradesh`, 12 `telangana` and 36 with no state
(flagged `low_confidence:unknown_edition`).
- **The 12 `telangana` pages:** 6 are the `ALL` family supplement pages printed in Hyderabad.
  The other 6 had a Telangana place word in a headline in the top band.
- **The 36 with no state:** every page of their code that year lacked a readable state.
- **`date_mismatch`:** 11 pages in 2024 and 56 in 2025 (informational).

## Not done / open

- Edition names stay empty until non-main pages (the district editions F-2 expects in the
  2020/2026 archives) are registered. Then `editions build` / `apply` need re-running.
- Evidence: `evidence/eval-run.txt`, `evidence/eval011.sh`,
  `evidence/e2-folio-dev-sample.log`, `evidence/e3-folio-all.log`, `data/editions.yaml`.
