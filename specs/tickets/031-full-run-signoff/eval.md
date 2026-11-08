# 031: Full-corpus run and phase-1 sign-off - Evaluation

## Setup

- Prerequisites: tickets 001–030 done; user steps done: OCR check signed (ticket 009 tool), old bundle copied (027), `HF_TOKEN` set, `SARVAM_API_KEY` set if fallback rules are non-empty.
- Start: `uv sync && uv run newsrag stores up && uv run newsrag baseline up`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Preflight (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag preflight; echo "exit=$?"`
- **Expected:** every line `ok` and `exit=0`.

### E-2: Everything processed (covers FR-2, FR-3, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag stage status`
  2. `uv run newsrag index validate; echo "exit=$?"`
  3. `uv run newsrag index collections`
  4. `python3 -c "import json,glob,datetime as d;rs=[json.load(open(f)) for f in glob.glob('/home/admin1/raw/reports/runs/*.json')];rs=[r for r in rs if r.get('tag')=='full'];print(len(rs), sum((d.datetime.fromisoformat(r['end'])-d.datetime.fromisoformat(r['start'])).total_seconds() for r in rs)/86400)"`
- **Expected:** every stage `eligible=0`, `running=0` and `failed=0` (failing pages were quarantined); `exit=0`; the active generation is `<embed_model from pipeline.yaml>__v1`; step 4 prints ≥ 1 run and a day total (≤ 10 is the NFR; above it, `results.md` explains).

### E-3: Full-corpus evaluations (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `ls -l /home/admin1/raw/reports/transcription-spotcheck.json /home/admin1/raw/reports/retrieval-new.json /home/admin1/raw/reports/baseline-compare.json /home/admin1/raw/reports/customer-answers.json /home/admin1/raw/reports/quality/index.html data/eval/retrieval-v2.jsonl`
- **Expected:** all six exist with modification times after the end of the last `full` run; `retrieval-new.json` has `"set"` ending in `retrieval-v2.jsonl`.

### E-4: Phase-1 pass (covers FR-5, FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag report build && python3 -c "import json;d=json.load(open('/home/admin1/raw/reports/quality/report.json'));print(d['phase1']);[print(b['name'],b['value'],b['status']) for b in d['bars']]"`
- **Expected:** first line `PASS`; all seven bars `PASS` (including the customer-answers sign-off by the user, not by a tester).

## Edge cases

### E-5: No bar was changed (covers Don't touch)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag config show | grep -E "^(judge_threshold|dedup_jaccard|dedup_cosine|stitch_min_score|hit_overlap|chunk_max_tokens) "`
  2. `uv run pytest -q tests/test_report.py -k bars_constants` (asserts the seven bar thresholds of ticket 030 FR-2 are unchanged)
- **Expected:** step 1 prints `judge_threshold = 0.8`, `dedup_jaccard = 0.85`, `dedup_cosine = 0.9`, `stitch_min_score = 0.15`, `hit_overlap = 0.3`, `chunk_max_tokens = 512`; step 2 passes.

### E-6: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_report.py tests/test_orchestrator.py -k "bars_constants or quarantine_failed_date_stage or run_file_keys"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-2 |
| FR-3 | E-2 |
| FR-4 | E-3 |
| FR-5 | E-4 |
| FR-6 | E-4 |
| NFR-1 | E-2 |
| FR-7 | E-6 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (final report.json, run files list, preflight output) saved in `evidence/` and listed in `results.md`
