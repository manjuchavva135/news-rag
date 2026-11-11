# 033: Grouping retry for Gemma 4 - Evaluation

## Setup

- Prerequisites: ticket 014 merged (`group_model: gemma4-31b`); `ocr_final` done for `data/dev-sample.txt`; GPU free.
- Start: `uv sync && uv run newsrag stores up`

## Automated checks

- `uv run ruff check . && uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Measurement (covers FR-2)

- **Type:** CLI
- **Steps:** `uv run python scripts/regroup_experiment.py <out> $(grep -v '^#' data/dev-sample.txt)`
- **Expected:** a table with one row per variant of FR-2 (retry gain, improved, still < 0.8, seconds);
  saved in `evidence/`; the chosen default named in `results.md` with the rule of FR-2 applied.

### E-2: Retry request (covers FR-1)

- **Type:** CLI
- **Steps:** `uv run pytest -q tests/test_judge.py -k "retry_temperature or retry_seed or retry_scale_one"`
- **Expected:** pass: the retry request carries `temperature`/`seed` from the settings and the base image size
  when `judge_retry_scale` is 1.0; changing either setting changes `group_retry`'s version.

### E-3: Passes still run (covers FR-1, FR-3, NFR-1)

- **Type:** CLI
- **Steps:** `uv run newsrag stage run judge group_retry judge_retry group_final --pages-file data/dev-sample.txt`, then `uv run newsrag judge stats --pages-file data/dev-sample.txt`
- **Expected:** 0 failures; stats print `retry_gain` and `retry_null`.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2, E-3 |
| FR-2 | E-1 |
| FR-3 | E-3 |
| NFR-1 | E-3 |
