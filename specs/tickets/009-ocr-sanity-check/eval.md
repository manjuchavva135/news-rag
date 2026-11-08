# 009: OCR sanity-check page and sign-off - Evaluation

## Setup

- Prerequisites: ticket 008 done; `ocr_split` done for `data/dev-sample.txt`.
- Start: `uv sync && uv run newsrag ocr check build && (uv run newsrag ocr check serve --port 8700 &)`
- Base URL: `http://localhost:8700`
- Test account: none
- Seed / reset: `mv /home/admin1/raw/reports/ocr-check/verdicts.json /tmp/verdicts.bak 2>/dev/null; true` before the checks, restore afterwards.

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: Page selection (covers FR-1)

- **Type:** CLI
- **Steps:**
  1. `grep -v '^#' data/ocr-check.txt | wc -l; grep -v '^#' data/ocr-check.txt | cut -c1-4 | sort | uniq -c; grep -v '^#' data/ocr-check.txt | grep -c '_main_01$'; grep -v '^#' data/ocr-check.txt | grep -cE '_(fea|splus)_'`
  2. `cp data/ocr-check.txt /tmp/oc && uv run newsrag ocr check build --force && diff /tmp/oc data/ocr-check.txt && echo same`
- **Expected:** `10`; 2020 → 4, 2025 → 3, 2026 → 3; `_main_01` count ≥ 4; fea/splus ≥ 1 (unless a `# short:` line explains it); `same`.

### E-2: Review page shows scan and text (covers FR-2, FR-5, NFR-1)

- **Type:** Browser
- **Steps:**
  1. `agent-browser open http://localhost:8700/`
  2. `agent-browser click` the first page link
  3. `agent-browser screenshot evidence/E-2.png`
  4. `agent-browser read text`
- **Expected:** the screenshot shows the page image with numbered boxes on the left and a block list with Telugu text on the right; the text contains `good`, `acceptable`, `bad`, `headlines`, `numbers`. Then `agent-browser click` block 3 in the right-hand list and `agent-browser screenshot evidence/E-2-highlight.png`: box 3 on the image is highlighted.

### E-3: Verdicts save and sign-off (covers FR-2, FR-3, FR-4)

- **Type:** Browser
- **Steps:**
  1. For each of the 10 pages: open `/page/<id>`, select `acceptable`, tick `numbers`, submit.
  2. `agent-browser open http://localhost:8700/signoff`, fill name `eval-tester`, tick `No page types need Sarvam`, click `Sign off`.
  3. `uv run newsrag ocr check summary`
- **Expected:** step 3 prints 10 lines containing `acceptable` and `numbers`, then `SIGNED OFF by eval-tester at ...`, then `none`. Then change one verdict to `good` and run `summary` again: it prints `NOT SIGNED OFF`.

## Edge cases

### E-4: Sign-off blocked until all pages have a verdict (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_ocr_check.py -k signoff_requires_all`
- **Expected:** passes (POST to sign-off with 9 verdicts returns an error and `signoff` stays `null`), and `uv run pytest -q tests/test_ocr_check.py -k "signoff_validation or row_to_json"` passes (the four rejections of FR-2; a row `years: 2020, page_nums: 1` stores `{"years": [2020], "page_nums": [1]}`).

### E-5: Survives restart (covers FR-3)

- **Type:** CLI
- **Steps:**
  1. `pkill -f 'newsrag ocr check serve'; (uv run newsrag ocr check serve --port 8700 &); sleep 5; uv run newsrag ocr check summary`
- **Expected:** same output as the last `summary` in E-3. Afterwards restore `/tmp/verdicts.bak` if it existed.

### E-6: Load time and image size (covers NFR-1)

- **Type:** API
- **Steps:**
  1. `P=$(grep -v '^#' data/ocr-check.txt | head -1); curl -s -o /dev/null -w '%{time_total}\n' http://localhost:8700/page/$P; curl -s -o /tmp/p.jpg -w '%{size_download} %{time_total}\n' http://localhost:8700/page/$P/image.jpg` (second call after the cache is warm)
  2. `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8700/page/not-a-page`
- **Expected:** page time < 2 s; image size ≤ 2000000 bytes and time < 2 s; `404`.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-2, E-3, E-4 |
| FR-3 | E-3, E-5 |
| FR-4 | E-3 |
| FR-5 | E-2 |
| NFR-1 | E-6 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (screenshots, logs) saved in `evidence/` and listed in `results.md`
- [ ] The tester's verdicts file was removed/restored so the real reader starts clean
