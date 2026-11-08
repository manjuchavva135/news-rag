# 011: Edition and state from the folio

**Status:** draft
**Mode:** solo
**Runs:** waits for 010: reads `ocr_final`; waits for 018: place → district/state list

## Summary

File names only carry letter codes (`FH`, `ALL`, `B`, `BGNOPRSVACLT`…), and nobody has a list of what
they mean. This ticket reads the folio/header line printed on each page (date, place, page number),
learns a per-year code → edition name map from it, and gives every page its edition names and state
(grill D-8, D-19, A-10, A-23). Unknown or ambiguous codes are flagged, never guessed.

## Functional requirements

- **FR-1** - PageStage `folio` (`depends_on=("ocr_final",)`, `version="folio-v1"`, `concurrency = 1`) collects the page's blocks whose label is in `folio_labels` (the `header_footer` and `page_number` groups of ticket 007's `labels.yaml`) plus any block whose box lies in the top 8% of the page height. Blocks that already have text are used as they are; the others (indic-ocr detects but does not transcribe headers/footers, per its model card) are re-OCR'd in one `op: blocks` request (`type: "text"`) through the ticket-007 worker client, which `open()` starts under `gpu_lock("indic-ocr")`. `folio_text` = the folio blocks' texts in reading order joined by ` | `. Artifact: `{page_id, folio_blocks:[{block_id, bbox, text}], folio_text, place_te, place_kind, place_districts_current, place_districts_at_print, place_state, printed_date, printed_page_no}`.
- **FR-2** - Parsing: `folio_text` is split into tokens on whitespace and `। | , -`; every run of 1–3 consecutive tokens is tried, left to right, with `newsrag.geo.resolve(run, on_date=pub_date)` (ticket 018). The first run with matches wins: if all its matches agree on one state and one `districts_current` list it is used; otherwise the match of kind `town` is preferred, then `district`, then `mandal`; if still several, the place stays `null` and the artifact gets `place_candidates`. `place_te` = the run's text; `place_kind`, `place_districts_current`, `place_districts_at_print` (= resolver's `districts_at_date`), `place_state` come from the chosen match. If no place resolves but the folio names a state (`ఆంధ్రప్రదేశ్`/`Andhra Pradesh` → `andhra_pradesh`, `తెలంగాణ`/`Telangana` → `telangana`), `place_state` is set alone. `printed_date` = a date in `folio_text` in Telugu or digits (`DD-MM-YYYY`, `DD.MM.YYYY`, `DD । MM । YYYY`, Telugu month names) → ISO; `printed_page_no` = the page number. Any field not found is `null`.
- **FR-3** - `uv run newsrag editions build` aggregates `folio` artifacts per `(year, edition_code)` and writes `data/editions.yaml` (committed) with layout `{<year as int>: {<code>: {name_te, districts_current, districts_at_print, state, n_pages, agreement, status, names_from_letters?, letters_missing?}}}`. `n_pages` = that code's pages with a non-null `place_te`; `name_te` = the most common `place_te` among them; `agreement` = its share of `n_pages` (2 decimals); `status` = `ok` if agreement ≥ 0.6 and n_pages ≥ 3, else `ambiguous`. Letter rule (grill A-10): a code longer than 1 letter, made only of letters, not `ALL`/`FH`, and with no `-` variant suffix, gets `names_from_letters` = the names of those of its letters that have an `ok` single-letter entry in the same year, and `letters_missing` = the others. `ALL`, `FH` and `-VK` variants use their own folios only (D-19). Re-running keeps any key a person added to an entry (e.g. `name_en`) and only rewrites the generated keys.
- **FR-4** - `uv run newsrag editions apply` replaces (in one transaction) table `ledger.page_editions(page_id PK, edition_names text[], districts_current text[], districts_at_print text[], states text[], source text, flags text[])` with one row per page whose `folio` stage is `done`: if its year+code entry is `ok` → that `name_te` (`source = 'code_map'`); else if it has `names_from_letters` (non-empty) → those names (`source = 'letters'`, plus flag `edition_letters_partial` when `letters_missing` is non-empty); else if the page's own folio resolved → that place (`source = 'folio'`, flag `edition_from_folio`); else empty arrays with flag `low_confidence:unknown_edition`. Districts and states come from resolving each name (on the page's `pub_date`). A page whose `printed_date` disagrees with its `pub_date` gets the informational flag `date_mismatch`.
- **FR-5** - `uv run newsrag editions report` prints per year: number of codes, `ok`, `ambiguous`, pages with `unknown_edition`, pages with `date_mismatch`.
- **FR-6** - Clarifications (second review):
  - FR-4 order for letters-only multi-letter codes (letter rule of FR-3): `names_from_letters` is used before the code's own `ok` entry (grill A-10: a shared page gets all its names).
  - A page with no resolved place but a folio state gets `states=[place_state]`, empty names, `source='folio_state'` and flag `low_confidence:unknown_edition`.
  - FR-4 copies `districts_current`/`districts_at_print`/`state` from the `editions.yaml` entry (same year, so the same district period); for letters it unions the letters' entries; it does not re-resolve.
  - `editions report` also lists every `ambiguous` code (`year code n_pages agreement top-3 places`) and every code with `unknown_edition` pages.
  - E-2's glob is limited to the dev-sample page_ids under `folio/<current version>/`.

## Non-functional requirements

- **NFR-1** - On the dev sample, `place_te` is found for ≥ 15 of 20 pages.

## Affected areas

- `src/newsrag/stages/folio.py`, `src/newsrag/editions/` (new)
- `src/newsrag/ledger/sql/00N_page_editions.sql` (new migration)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `data/editions.yaml` (new, committed, generated)
- `tests/test_folio_parse.py`, `tests/test_editions.py` (new; real folio strings as fixtures)

## Don't touch

- `ocr_final` artifacts; the filename parser from ticket 004.

## Out of scope

- English names for editions (can be added by hand to `editions.yaml` later); place districts inside stories (020).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Source of edition names | the printed folio | grill D-8 |
| D-2 | Map scope | per year | grill A-23: codes may change |
| D-3 | FH/ALL | read from folio; disagreement → keep raw code, flagged | grill D-19 |
| D-4 | Unknown codes | indexed with flag `unknown_edition` | grill A-23, D-20 |
| D-5 | Ambiguity threshold | agreement ≥ 0.6 and ≥ 3 pages | majority with a margin; below it the code is listed, not named |
| D-6 | Edition district form | both current and at-print district id lists | ticket 018 returns both; 020 stores edition districts next to story districts |
| D-7 | Flags | only `low_confidence:unknown_edition` marks doubt; `edition_from_folio`, `edition_letters_partial`, `date_mismatch` are informational | grill D-20 and the `low_confidence:` convention |
| D-8 | Hand edits to `editions.yaml` | kept on rebuild | English names may be added by hand |

## Open questions

None.
