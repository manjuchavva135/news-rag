# 030: Quality report - Evaluation

## Setup

- Prerequisites: tickets 026, 027, 028, 029 done (their output files exist for whatever data is processed so far).
- Start: `uv sync && uv run newsrag stores up`
- Base URL: n/a (static file)
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Bars and verdict logic (covers FR-2, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_report.py -v`
- **Expected:** all pass, including `test_all_bars_pass_gives_phase_pass`, `test_one_bar_fail_gives_phase_fail_count`, `test_missing_input_is_fail`, `test_bar_links_to_source`, `test_quarantine_pct_formula`, `test_signoff_must_match_answers`, `test_eval_tester_signoff_rejected`, `test_missing_stats_section_not_available`.

### E-2: Build on current data (covers FR-1, FR-3, NFR-1)

- **Type:** Browser
- **Steps:**
  1. `time uv run newsrag report build`
  2. `agent-browser open file:///home/admin1/raw/reports/quality/index.html`
  3. `agent-browser screenshot evidence/E-2.png`
  4. `agent-browser read text`
- **Expected:** step 1 < 2 min; the text contains `PHASE 1:`, the seven bar names from FR-2 each with `PASS` or `FAIL`, and the nine section titles from FR-3 (each with numbers or `not available`). Then `agent-browser set viewport 390 844`, `agent-browser screenshot evidence/E-2-phone.png`: no horizontal scrollbar, text readable.

### E-3: JSON matches HTML (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `python3 -c "import json;d=json.load(open('/home/admin1/raw/reports/quality/report.json'));print(len(d['bars']), d['phase1'])"`
- **Expected:** `7` and `PASS` or `FAIL` matching the HTML header.

## Edge cases

### E-4: No external requests (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `sed '/<pre class="bakeoff">/,/<\/pre>/d' /home/admin1/raw/reports/quality/index.html | grep -Eo 'https?://' | wc -l`
- **Expected:** `0`.

### E-5: Second-review clarifications (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_report.py -k "ocr_signoff_rejects_eval_tester or done_means_current_version or quarantine_excludes_test_files or low_confidence_counts_stories or section_values_match_stats"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2, E-3, E-4 |
| FR-2 | E-1, E-2 |
| FR-3 | E-2 |
| FR-4 | E-1 |
| NFR-1 | E-2 |
| FR-5 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (screenshot, report.json) saved in `evidence/` and listed in `results.md`
