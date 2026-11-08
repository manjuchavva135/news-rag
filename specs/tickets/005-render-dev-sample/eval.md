# 005: Render pages and dev sample - Evaluation

## Setup

- Prerequisites: ticket 004 done on real data (pages registered); user ran `sudo apt install poppler-utils`.
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Render all pages (covers FR-1, NFR-1, NFR-2)

- **Type:** CLI
- **Steps:**
  1. on a ledger with no `render` rows: `time uv run newsrag stage run render --workers 32`
  2. `for i in 1 2 3; do uv run newsrag stage run render --workers 32; done` (retries until failing pages reach max attempts and are quarantined)
  3. `uv run newsrag stage status | grep '^render'`
  4. `uv run newsrag db sql "select count(*) from ledger.pages where quarantine_reason is null"`
  5. `uv run newsrag db sql "select percentile_cont(0.5) within group (order by (detail->>'width')::int) from ledger.stage_runs where stage='render' and status='done'"`
- **Expected:** step 1 real time ≤ (number of registered pages / 5) seconds; step 3's `done=` equals step 4's count and `eligible=0`; step 5 ≥ 3000.

### E-2: PNG exists for a known page (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `P=$(grep -v '^#' data/dev-sample.txt | grep '^2026' | grep '_main_01$' | head -1); file /home/admin1/raw/images/$P.png` (after E-3 built the sample)
- **Expected:** contains `PNG image data`.

### E-3: Dev sample (covers FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag sample build && grep -v '^#' data/dev-sample.txt | wc -l`
  2. `cp data/dev-sample.txt /tmp/s1 && uv run newsrag sample build --force && diff /tmp/s1 data/dev-sample.txt && echo same; uv run newsrag sample build; echo "exit=$?"`
  3. `grep -v '^#' data/dev-sample.txt | cut -c1-4 | sort | uniq -c`
  4. `uv run newsrag sample show | grep -c 'ok$'`
  5. `grep -v '^#' data/dev-sample.txt | grep -c '_main_01$'; grep -v '^#' data/dev-sample.txt | grep -cE '_(fea|splus)_'`
- **Expected:** `20`; `same` then `exit=1`; counts per year 2020 = 8, 2025 = 6, 2026 = 6 (unless `# short:` lines explain a shortfall); step 4 prints `20`; step 5 prints ≥ 8 and ≥ 3 (unless `# short:` lines explain a shortfall).

## Edge cases

### E-4: Corrupt PDF is quarantined (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_render.py -k corrupt`
- **Expected:** passes; a page whose source is a 10-byte junk file ends with `quarantine_reason` starting `render_failed:` after 3 runs, and a test `test_timeout_counts_as_failure` (fake pdftoppm sleeping past a 1 s test timeout) passes, and `test_partial_png_not_left` shows no `<page_id>.png` after a failed render.

### E-5: Missing poppler message (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_render.py -k missing_poppler`
- **Expected:** passes, asserting exit 2 and `poppler not found: run  sudo apt install poppler-utils`.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-2 |
| FR-2 | E-4 |
| FR-3 | E-5 |
| FR-4 | E-3 |
| FR-5 | E-3 |
| NFR-1 | E-1 |
| NFR-2 | E-1 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (command logs, `data/dev-sample.txt`) saved in `evidence/` and listed in `results.md`
