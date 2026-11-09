# 004: Unpack, normalize and register pages

**Status:** done
**Mode:** solo
**Runs:** waits for 003: registers pages in the ledger

## Summary

Turns the delivered archives (ZIP now, RAR possible later) into one clean ledger row per unique newspaper page. It extracts each
archive (the originals stay untouched), parses the messy filenames (day prefix, edition letter codes,
sections, spreads, revisions, e-paper and web copies, odd tags), keeps the newest revision of each
page, quarantines anything it can't parse, and writes a report. Re-running is safe, and a new year's
archive dropped into the archive folder later is picked up by the same commands (grill A-16).

## Functional requirements

- **FR-1** - `uv run newsrag ingest unpack [--archive NAME.zip|NAME.rar]` extracts every `*.zip` and `*.rar` in `settings.archive_dir` (or just the named one) into `<source_dir>/<archive_stem>/`: a ZIP with Python's `zipfile` (overwriting existing files; member paths that would land outside the folder are refused), a RAR with `unrar x -o+ -idq <rar> <source_dir>/<archive_stem>/`. Other files (e.g. `vinayaka.jpg`) are ignored. Two archives with the same stem (e.g. `2025.zip` and `2025.rar`) are both reported as failed (`two archives share stem <stem>`). An old marker is removed before re-extracting. After success it writes `<source_dir>/<archive_stem>/.unpacked` containing the archive's size and mtime; an archive whose marker matches is skipped and reported `skipped (already unpacked)`. The archives in `archive_dir` are never modified, moved or deleted.
- **FR-2** - If at least one **RAR** archive needs extracting and `unrar` is not on PATH (ZIPs need no external tool), `ingest unpack` exits 2 with `unrar not found: run  sudo apt install unrar`.
- **FR-3** - `uv run newsrag ingest register` walks `<source_dir>/<stem>/**/*.pdf` for **every** stem that has a valid `.unpacked` marker (stems without one are skipped with `skipping <stem>: not fully unpacked`; case-insensitive extension; other files ignored) and parses each path with `src/newsrag/ingest/filenames.py:parse(path) -> ParsedPage | Rejected` using these rules:
  - **Date:** if any path segment matches `DD-MM-YYYY`, that is the date. Otherwise take month+year from the segment matching `<MonthName>-YYYY` or `<MonthName>YYYY` (e.g. `April-2025`, `September2026`) and the day from the filename's two leading digits; if a week-folder segment `DD-DD` exists and the file's day is lower than the folder's start day, the month rolls to the next month (grill A-46). When a date folder exists and the filename's day differs, the folder wins and the case is counted as `day_mismatch_warnings` in the report.
  - **Filename:** after removing control/format characters (e.g. U+2008, U+00AD) and surrounding spaces: `^(?P<day>\d{2})\s*(?P<edition>[A-Za-z]+)\s*[-_]\s*(?P<section>MAIN|MAI|FEA)\s*_?\s*(?P<page>\d{1,2})(?:-(?P<page_end>\d{2}))?(?P<rest>.*)\.pdf$` (case-insensitive). `MAI` is read as `MAIN`.
  - **Edition code:** the letter run, upper-cased (e.g. `FH`, `ALL`, `BGNOPRSVACLT`). If `rest` contains `(VK)` the code becomes `<CODE>-VK` (own edition variant, grill A-37).
  - **Section:** `main` or `fea`; if `rest` contains `SPLUS` the section is `splus` (own supplement section, A-37).
  - **Revision:** length of the **leading** run of upper-case `R` in `rest` after removing `VK`, `SPLUS`, separators `._-()` and spaces (so `R`, `RR`, `(RR)`, `RR-SPLUS`, `_EPAPER` → 1, 2, 2, 2, 0; lower-case `r` does not count).
  - **Copy kind:** `epaper` if `rest` matches `e[\s_-]*p[ae]p[ae]r` (any case; covers `EPAPER`, `E-PAPER`, `E PAPER`, `E_PAPER`, `Epapar`, `E-PEPAR` seen in the ZIPs); `web` if it contains `for net` (case-insensitive); else `print`.
  - **Rejected** (quarantined) with the first reason that applies, checked in this order: no date folder and no month folder (`no_date`); the file name (not the folders) contains `DUMMY` or `TEST` as a case-insensitive substring (`test_or_dummy`); the filename regex doesn't match (`bad_filename`); the computed date doesn't exist, e.g. day 31 in April (`bad_date`). A month rollover from December goes to January of the next year.
- **FR-4** - **Revision dedup** runs over all unpacked stems together: pages are grouped by `(date, edition_code, section, page, page_end)` (so a spread and a single page are different pages); the winner has the highest revision, then copy kind `print` over `epaper`/`web`, then a path containing `/out/`, then the lexicographically smallest full path relative to `source_dir`. Losers are listed in the report under `superseded` with the winner's path (grill A-3, A-37).
- **FR-5** - **page_id** = `{YYYY-MM-DD}_{EDITION_CODE}_{section}_{NN}` with `NN` zero-padded to 2 digits and `-{MM}` appended for a spread (`page_end`), e.g. `2026-04-01_BGNOPRSVACLT_main_01`, `2020-04-02_FH-VK_main_01`, `2026-04-01_FH_splus_17`, `2026-01-12_B_main_08-09` (grill A-22: a spread is one page record).
- **FR-6** - Winners are upserted into `ledger.pages` (`source_path` = absolute path, `source_archive` = stem, `revision`, `page_end`). Rejected files go to `ledger.quarantine`. Re-running with the same files changes nothing (same rows, no duplicate). If a re-run picks a different winner for an existing page_id (a newer revision arrived), the row's `source_path`/`revision` are updated, its `quarantine_reason` and quarantine row are cleared, and all its `stage_runs` rows are deleted so it is reprocessed from the start. When the winner is unchanged, `quarantine_reason` is kept. Quarantine rows of reason `no_date`/`bad_filename`/`test_or_dummy`/`bad_date` for paths that now parse are deleted. Pages of stems that were skipped (no marker) are left untouched and are not used in that run's dedup. A page whose source file no longer exists in a scanned stem gets `quarantine_reason = 'source_missing'` (it is not deleted). `ParsedPage` fields: `date, edition_code, section, page, page_end, revision, copy_kind, path`; `Rejected` fields: `reason, path`. All paths in reports are absolute. SQL uses the configured ledger schema (ticket 003 FR-1).
- **FR-7** - Each register run writes one `<reports_dir>/register-<stem>.json` per stem with: `source_pdfs`, `registered` (unique pages), `superseded` (list of `{path, winner}`), `quarantined` (list of `{path, reason}`), `day_mismatch_warnings`, `quarantine_pct` = quarantined / (registered + quarantined) × 100, rounded to 2 decimals. It prints the same counts as one line: `<stem>: pdfs=<n> registered=<n> superseded=<n> quarantined=<n> (<pct>%)`.
- **FR-8** - Unpack failures: if `unrar` exits non-zero for an archive, the command prints `unrar failed for <rar>: <last stderr line>`; if a ZIP can't be read or a member fails its CRC, it prints `unzip failed for <zip>: <error>`. Either way it writes no marker for it (the partial folder is left for inspection and re-extracted next time with `-o+`), continues with the remaining archives, and exits 1 at the end.

## Non-functional requirements

- **NFR-1** - `tests/test_filenames.py` covers every rule above with real file names from the archives, at least these (path → expected): `2025/April-2025/01-04/01-04-2025/01FH-MAIN_03_EPAPER.pdf` → date 2025-04-01, FH, main, 3, rev 0, epaper; `2025/April-2025/26-30/02FH-MAIN_05R.pdf` → date 2025-05-02 (rollover), rev 1; `2026/April-2026/03-04-2026/03BGNOPRSVACLT-MAIN_17R-SPLUS.pdf` → section splus, rev 1; `2020/April-2020/02-04-2020/main/out/02FH-MAIN01(VK)R.pdf` → edition `FH-VK`, page 1, rev 1; `2020/April-2020/06-04-2020/main/out/06E-MAIN_10(for Net).pdf` → copy kind web; `2025/.../21FH-MAIN_01(RR).pdf` → rev 2; `25FH-MAIN_02R .pdf` → rev 1; `19FH-MAIN_08R­.pdf` → rev 1; `13FH-MAIN_14-EPAPER.pdf` → epaper; `01FH_MAIN_11R.pdf` → FH main 11 rev 1; `2026/September2026/02-09-2026/02FH-MAIN_05.pdf` → 2026-09-02; from the ZIPs: `2025/October-2025/10-10-2025/main/out/10B-MAIN_11RRRR.ps(EPAPER).pdf` → B main 11 rev 4 epaper; `...MAIN_03R(E-PAPER).pdf`, `...MAIN_03R(E PAPER).pdf`, `...MAIN_03R(Epapar).pdf`, `...MAIN_03R(E-PEPAR).pdf`, `...MAIN_03R.(EPAPER)ps.pdf` → rev 1 epaper; `2024/December-2024/18-12-2024/main/out/18V_10.pdf` → rejected `bad_filename`.
- **NFR-2** - Register over all three archives finishes in under 10 minutes (CPU only).

## Affected areas

- `src/newsrag/ingest/__init__.py`, `unpack.py`, `filenames.py`, `register.py` (new)
- `src/newsrag/cli.py` - add `ingest` group (changed)
- `tests/test_filenames.py`, `tests/test_register.py` (new; register test marked `stores`)

## Don't touch

- `/home/admin1/data/*.rar` - never modified, moved or deleted (grill A-4).
- `/home/admin1/RAG/**` (the old `ingest/normalize_ap2026.py` is reference only).

## Out of scope

- Rendering (005), edition names (011), anything reading PDF contents.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Revision tie-break | highest R, then print over epaper/web, then file name | grill A-3, A-37 |
| D-2 | Odd tags | SPLUS = section `splus`; (VK) = edition variant `-VK`; (for Net) = web copy; (RR) = revision 2 | grill A-37 |
| D-3 | 2025 week folders | month from `Month-YYYY` folder, day from filename, roll over when day < folder start | grill A-24, A-46 |
| D-4 | Spreads | one page record, id carries `NN-MM` | grill A-22 |
| D-5 | Date folder vs filename day disagree | folder wins, counted as a warning | the old normalizer used the folder date; quarantining would risk the ≤ 1% bar (A-34) |
| D-6 | Unparseable files | quarantined with a reason, never guessed | grill A-13 |
| D-7 | Extraction target | `/home/admin1/raw/source/<stem>/` | grill A-4 |
| D-8 | Register scope | always all unpacked stems, dedup across them | a page may appear in two archives (year-end rollover); per-archive dedup would flip-flop |
| D-9 | Spreads vs single pages | `page_end` is part of the group key | a spread and page 08 are different printed pages |
| D-10 | Quarantine on re-register | kept if the winner is unchanged, cleared if it changed | a newer revision may fix a corrupt PDF |
| D-11 | Archive format | the delivery changed on 2026-11-09 from 3 RARs (2020/2025/2026, ~21k PDFs) to 2 ZIPs: `2024.zip` (956 PDFs, Dec 2024) and `2025.zip` (2,751 PDFs, Jan-Dec 2025). Support both; ZIP via Python `zipfile` | user, 2026-11-09 |
| D-12 | New e-paper spellings | widen the e-paper rule to `e[\s_-]*p[ae]p[ae]r`; the `.ps` tag is ignored | seen in the ZIPs; a plain `epaper` substring missed about 50 of their names |
| D-13 | `source_missing` page whose file is back | the flag is cleared (it was set by register, not by a stage) | otherwise the page would stay quarantined forever |

## Open questions

None.
