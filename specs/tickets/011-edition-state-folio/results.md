# 011: Edition and state from the folio - Results

Built on branch `ticket-011-folio`, which starts from `ticket-018-geo` (ticket 018 was built
first because 011 needs its resolver). `ocr_final` was run for all pages (no fallback rules, so
it only copies `ocr_split`). The `folio` stage then ran over all 3,497 pages: 0 failed, in
2,355 s. The ledger has been migrated with `002_page_editions.sql`. `editions build` and
`editions apply` have run on the real ledger.

## Main finding: on this corpus the folio names the printing centre, not the edition

All 3,497 registered pages are section `main` (state pages). Their folio line reads
`<weekday> | <day> | <month> | <year> / విజయవాడ | www.sakshi.com` whatever the edition code
(`B`, `G`, `BGNOPRSVACLT`, …).

- Of the 2,727 pages with a folio place, 2,685 say **విజయవాడ**.
- The other 42 are mostly headline words that sit in the top band (వైజాగ్, పోలవరం, అమరావతి…).
  `ALL` (family supplement) pages say హైదరాబాద్.

As a result:
- Every `ok` entry in `data/editions.yaml` is విజయవాడ: 29 of 146 codes in 2024 and 36 of 130
  in 2025.
- `page_editions` gives almost every page `edition_names = [విజయవాడ]` and
  `districts_current = [ntr]`. Sources: 1,793 `letters`, 1,699 `code_map`, 3 `folio`,
  2 `unknown`.

That is the place the state pages were printed, not what the letter codes mean. **These edition
names and districts should not be used by later tickets (020's district tagging, search
filters) until this is resolved.** The mechanism works as specified. A district-page corpus,
or a different source for the code meanings, would give real names.

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
  - `build_map` implements FR-3, including the letter rule. `ALL`, `FH` and `-` variants are
    never split into letters.
  - `merge_entry` keeps hand-added keys; the YAML is written with ruamel and int year keys.
  - `page_edition` implements FR-4 with FR-6's order: letters → the code's `ok` entry → the
    page's own folio → `folio_state` → unknown, and the `date_mismatch` flag.
  - `report_lines` implements FR-5 and FR-6.
- `src/newsrag/editions/cli.py`: `newsrag editions build | apply | report`, registered in
  `cli.py`.
- `src/newsrag/ledger/sql/002_page_editions.sql`: the `page_editions` table. `apply` deletes
  and re-inserts it in one transaction.
- Settings under `# --- editions ---`:
  - `folio_top_fraction` (0.08), `folio_band_pieces` (2)
  - `editions_file` (`<repo>/data/editions.yaml`, a `REPO_ROOT_DERIVED` rule)
  - `editions_min_agreement` (0.6), `editions_min_pages` (3)
- `data/editions.yaml`: generated and committed.
- Tests:
  - `tests/test_folio_parse.py` (26): real folio strings OCR'd from dev pages, plus the E-1
    string.
  - `tests/test_editions.py` (23): every name listed in E-5/E-6. The folio stage is tested
    with a fake text reader, and `apply`/`report` against a throw-away ledger schema
    (`stores`).

## Changes beyond the ticket text

- **Top-band fallback (`folio_band_pieces`).** When the folio blocks give no place, the top 8%
  band is read too, in two side-by-side pieces. Surya's layout missed the folio strip on
  2025-05-04 page 6, and OCR misread విజయవాడ on 2024-12-09 R page 3. This recovered both
  (dev sample 9 → 11 pages with a place). Setting it to `0` turns it off.
- **The place is looked for first in the blocks that carry the printed date**, then in all
  folio blocks. Otherwise a headline sitting in the top band (e.g. "నేడు ప్రకాశం జిల్లా…")
  would win over the folio line.
- **Blocks read as LaTeX are dropped from `folio_text`.** These are decorative footers that the
  OCR model turns into `\text{---}` markup. They are still listed in `folio_blocks`.

## Checks (`evidence/eval-run.txt`)

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` | PASS |
| `pytest -q -m "not gpu and not data and not stores"` | PASS (308 passed) |
| E-1 `tests/test_folio_parse.py` | PASS (26), E-1 string → విజయవాడ, andhra_pradesh, 2026-01-14 |
| E-2 (dev sample, FR-6 glob) | **FAIL on NFR-1**: `20 11` (≥ 15 required) |
| E-3 with 2026 as written | **cannot run**: the corpus has no 2026 pages (2024-12 and 2025 only, as found in 010) |
| E-3 with 2025 | `130 36` (≥ 20 codes, ≥ 10 ok); 2024 gives `146 29`. Report prints both years' lines |
| E-4 | PASS: `3497 2 3495`; folio done = 3497; 3497 = 2 + 3495 |
| E-5 `tests/test_editions.py -v` | PASS (23) |
| E-6 | PASS (4 selected) |

**Why NFR-1 fails:** 7 of the 20 dev-sample pages are front pages, whose masthead prints the
weekday and date but no place. The other 2 misses are the 2025-12-16 pages 6-7 spread (no folio
line) and the `ALL` family supplement (హైదరాబాద్ is printed below the top 8%). At most 11 of 20
can resolve. Over the whole corpus, 2,727 of 3,497 pages (78%) have a place.

## Not done / open

- E-3 and NFR-1 need a spec decision: run E-3 on 2025, and lower NFR-1 or change the sample.
- The edition names are not meaningful on this corpus (see the main finding).
- Evidence: `evidence/eval-run.txt`, `evidence/e2-folio-dev-sample.log`,
  `evidence/e3-folio-all.log`, `data/editions.yaml`.
