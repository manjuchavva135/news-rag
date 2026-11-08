# 007: indic-ocr stage - Evaluation

## Setup

- Prerequisites: tickets 005 and 006 done; user accepted the indic-ocr licence and set `HF_TOKEN` in `.env`; `data/dev-sample.txt` exists; GPU free.
- Start: `uv sync && uv run newsrag stores up && uv run newsrag ocr install`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Install (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag ocr install && grep indic_ocr_revision pipeline.yaml && uv run newsrag ocr install`
  2. `envs/indic-ocr/bin/python -c "import torch;print(torch.cuda.is_available(), torch.version.cuda)"`
- **Expected:** step 1 prints a line with a 40-character hex hash and the second install finishes in < 60 s without downloading; step 2 prints `True` and a CUDA version ≥ `12.8`.

### E-2: OCR the dev sample (covers FR-2, FR-3, FR-4, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag stage run ocr --pages-file data/dev-sample.txt`
  2. `uv run newsrag stage status | grep '^ocr '`
  3. `uv run python -c "import json,glob;fs=glob.glob('/home/admin1/raw/work/ocr/*/*.json');d=json.load(open(fs[0]));b=d['blocks'][0];print(sorted(d), sorted(b))"`
- **Expected:** `uv run newsrag stage failures ocr` lists none of the `data/dev-sample.txt` page_ids and step 2 shows `done=` ≥ 20; step 3 (use a file with ≥ 1 block) prints `['blocks', 'image', 'markdown', 'model', 'page_id']` and `['bbox', 'block_id', 'conf', 'label', 'order', 'text', 'type']`; the line `ocr: ... pages/s` printed by step 1 shows ≥ 0.5.

### E-3: Telugu text present and clean (covers NFR-2)

- **Type:** CLI
- **Steps:**
  1. `P=$(grep -v '^#' data/dev-sample.txt | grep '^2026' | grep '_main_01$' | head -1); uv run newsrag ocr show $P | head -20`
  2. `uv run python -c "import json,glob,unicodedata as u;t=''.join(b['text'] for f in glob.glob('/home/admin1/raw/work/ocr/*/*.json') for b in json.load(open(f))['blocks']);print(len(t), t.count('�')/max(1,len(t)), t==u.normalize('NFC',t))"`
- **Expected:** step 1 shows Telugu-script text lines; step 2 prints a char count > 100000, a ratio ≤ 0.001 and `True`.

## Edge cases

### E-4: Missing token message (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_ocr_stage.py -k missing_token`
- **Expected:** passes, asserting exit 2 and the exact message in FR-1.

### E-5: Zero-block page (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_ocr_stage.py -k zero_blocks` (fake worker returns no blocks)
- **Expected:** passes; the stage row is `done` with `detail.zero_blocks = true`.

### E-6: Main venv does not import indic-ocr (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run python -c "import importlib.util;print(importlib.util.find_spec('indic_ocr'))"`
- **Expected:** prints `None`.

### E-7: Worker protocol and labels (covers FR-2, FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_ocr_stage.py -k "blocks_op or error_reply_keeps_worker or worker_restart_on_crash or timeout_kills_worker or not_installed_preflight"`
  2. `uv run python -c "import yaml;d=yaml.safe_load(open('labels.yaml'));print(len(d['labels']), sorted(set(d['labels'].values())))"`
- **Expected:** 5 passed; step 2 prints `37` and a subset of the ten group names from FR-6 that includes `text`, `title`, `table`.

### E-8: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_ocr_stage.py -k "label_settings or process_group_killed or unknown_label_other or labels_in_version or nfc_in_client"`
- **Expected:** all selected tests pass.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-4 |
| FR-2 | E-2, E-6, E-7 |
| FR-3 | E-2 |
| FR-4 | E-2, E-5 |
| FR-5 | E-3 |
| FR-6 | E-7 |
| NFR-1 | E-2 |
| NFR-2 | E-3 |
| FR-7 | E-8 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (command logs, one sample artifact JSON) saved in `evidence/` and listed in `results.md`
