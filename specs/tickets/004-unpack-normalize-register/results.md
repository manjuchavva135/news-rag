# 004: Unpack, normalize and register pages - Results

Built in the main checkout on `main` (after 001-003); committed as `9e289b8`. The input changed before
the build: on 2026-11-09 `/home/admin1/data` held `2024.zip` (956 PDFs, December 2024 only) and
`2025.zip` (2,751 PDFs, January-December 2025) instead of the three RARs the ticket was written
for. The user chose to build for the ZIPs and keep RAR support, so rd.md and eval.md were updated
first (D-11 ... D-13, new FR-1/FR-2/FR-8 wording, counts 3,707 and stems 2024/2025).

Both ZIPs are now extracted into `/home/admin1/raw/source/{2024,2025}` and registered in the real
`ledger` schema: 3,497 pages and 2 quarantine rows.

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| `uv run ruff check .` / `ruff format --check .` | PASS | `evidence/auto-ruff.log` |
| unit tests | PASS, 81 passed | `evidence/auto-pytest-unit.log` |
| `-m stores tests/test_register.py` | PASS, 8 passed | `evidence/auto-pytest-stores.log` |
| E-1 filename rules | PASS, 42 cases; ids include EPAPER, SPLUS, VK, for Net, (RR), September2026 | `evidence/E-1.log` |
| E-2 unpack, originals untouched | PASS: `same`, `2024 ok`, `2025 ok`, 3,707 PDFs, 24.8 s | `evidence/E-2.log` |
| E-3 second unpack skipped | PASS: `skipped (already unpacked)`, 1.4 s | `evidence/E-3.log` |
| E-4 register + reports | PASS: 1.7 s; 2024: 746 + 208 + 2 = 956; 2025: 2,751 + 0 + 0 = 2,751; 3,497 = 3,497 = 746 + 2,751 | `evidence/E-4.log`, `evidence/register-*.json` |
| E-5 revision winners on real data | PASS: `208 0` | `evidence/E-5.log` |
| E-6 re-run idempotent | PASS: same md5 before and after | `evidence/E-6.log` |
| E-7 newer revision resets the page | PASS | `evidence/E-7.log` |
| E-8 quarantine reasons, unrar failure, ZIP cases | PASS (3 + 1 + 5) | `evidence/E-8.log` |
| E-9 missing unrar | PASS (exit 2 + message; also: ZIPs alone don't need unrar) | `evidence/E-9.log` |

Real-data notes:
- Quarantine: `15dummy-MAIN_10.pdf` (`test_or_dummy`) and `18V_10.pdf` (`bad_filename`, no
  MAIN/FEA), both in 2024: 0.27% for 2024, 0% for 2025.
- 2024 has 2 `day_mismatch_warnings`: `15GNORSVACLT-MAIN_10.pdf` and `15P-MAIN_10.pdf` sit in
  folder `17-12-2024` (next to `15dummy-MAIN_10.pdf`); the folder wins, so they are registered as
  2024-12-17. Worth a look when the page images exist (ticket 005): they may be pages of the 15th.
- 2025 has **no** superseded files: the 2025 delivery already holds only one file per page,
  although 1,500+ of its names carry `R` revisions. 2024 has 208 superseded (older revisions and
  e-paper copies).

## Where I read the spec a particular way

- **ZIP extraction** runs `unpack_workers` (default 8) threads, each with its own `ZipFile`
  handle. All folders are created first, so the threads never race on `mkdir`. Any member with an
  absolute path, a drive letter or `..` makes the whole archive fail before anything is written.
- **Marker:** JSON with `size` and `mtime_ns` (plus the archive name and times, for people
  reading it). It is deleted before extraction starts and written last. For `register`, a marker
  is "valid" if it exists and parses; it is not compared with the archive again (the archive may
  have been removed since).
- **Same stem, two formats** (`2025.zip` + `2025.rar`): both archives fail (exit 1) rather than
  one silently winning.
- **Unknown `--archive` name:** exit 2 with `no archive named <name> in <archive_dir>`.
- **Missing unrar** is checked before anything is extracted. It only counts when a RAR still
  needs extracting.
- **`ParsedPage`** has one extra field, `day_mismatch`, plus `page_id` / `group_key` properties.
- **Month folders** also accept 3-letter abbreviations (`Sep-2025`) and `Sept`. A month folder
  is only used when there is no `DD-MM-YYYY` folder, and the deepest matching folder wins.
- **Whitespace:** besides Cc/Cf characters, every non-ASCII space (Unicode `Z*`, e.g. U+2008)
  is removed; ASCII spaces inside the name are kept for the regex.
- **`source_missing` coming back:** when the file of a `source_missing` page exists again with the
  same winner, the flag and its quarantine row are cleared (D-13). Other quarantine reasons are
  kept while the winner is unchanged (D-10).
- **Quarantine cleanup:** rows with a parse reason (`no_date`, `test_or_dummy`, `bad_filename`,
  `bad_date`) in a scanned stem are deleted when the path now parses **or no longer exists**.
- **Idempotence:** an unchanged page is not written at all, so `updated_at` stays the same too
  (the test compares whole rows).
- One register run is a single transaction under an advisory lock
  (`newsrag-register:<schema>`), so two runs at once can't interleave.
- `ingest register` on an uninitialised ledger exits 2: `ledger not initialised ...: run  uv run
  newsrag ledger init`.
- **E-1 command:** `pytest -q ... -v` lists no case ids (`-q` and `-v` cancel out), so eval.md
  now says `pytest tests/test_filenames.py -v`.

## Not done / follow-ups

- The grill record (`specs/grill/newspaper-rag-v2.md`, F-1 and summary item 2) still describes
  the 3 RARs and ~21k pages. Later tickets that quote those numbers (e.g. 005's dev sample, 031's
  full run) should be checked against the new 3,497-page corpus.
- No reviewer pass was run (built inline, not by a worker agent).

## Files

- `src/newsrag/ingest/__init__.py`, `filenames.py`, `unpack.py`, `register.py`, `cli.py` (new)
- `src/newsrag/cli.py` (adds the `ingest` group), `src/newsrag/config.py` (`# --- ingest ---`:
  `unrar_bin`, `unpack_workers`)
- `tests/test_filenames.py`, `tests/test_register.py` (new)
- `specs/tickets/004-unpack-normalize-register/rd.md`, `eval.md` (updated for the ZIPs)
