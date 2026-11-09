# 010: Sarvam fallback for named page types - Results

Built on branch `010-sarvam-fallback` (from main at `d2f1966`). The E-1 run used the real ledger:
`ocr_split` was done for all 3497 registered pages, and `ocr_final` now has the 20 dev-sample
pages done at `final-v1`. No Sarvam call was made: there is no `SARVAM_API_KEY` in `.env`, and
the committed rules are `rules: []`.

**One spec change, made with the user after building:** E-3 was written for the old
2020/2025/2026 corpus. The ledger holds 2024 and 2025 pages only (as in 009 D-5), so a 2020 rule
matches nothing. E-3 now uses `years: [2025]` and checks for no `2024` line (eval.md updated).

## What was built

- `ocr_fallback.yaml` (committed): a commented header and `rules: []`.
- `src/newsrag/ocr/fallback.py`: loads, validates and normalises the rules. Unknown keys and
  non-integer years / page numbers are rejected. Omitted, `null` and `[]` all mean "any". Lists
  are sorted and de-duplicated and the keys sorted. Also matching (a spread matches if any of
  `page_num..page_end` is listed), the per-page labels (a rule without `labels` gets
  `text_labels + title_labels`), the rules hash and the preview counts. The rules are cached per
  (path, mtime, size), because `version_for` is called for every page during eligibility.
- `src/newsrag/ocr/sarvam.py`: the `SarvamApi` protocol (create / upload / start / status /
  download). `SdkApi` implements it on the `sarvamai` SDK with the SDK's own retries off
  (`max_retries: 0`), so every call gets exactly 4 attempts with waits of 2 s, 8 s and 30 s on
  429/5xx or a connection error. Around that: `run_job` (one PDF of ≤ 10 crops, the 600 s job
  timeout, `metadata/page_NNN.json` read straight from the ZIP with nothing extracted), the
  process-wide `BoundedSemaphore(4)`, `page_text` (drops `layout_tag == "image"`, keeps reading
  order, joins with `\n`, NFC) and the crop cache in `<work_dir>/sarvam-cache/<aa>/<sha1>.json`.
- `src/newsrag/stages/ocr_final.py`: `OcrFinalStage` (`depends_on=("ocr_split",)`,
  `concurrency=1`, `version_for`), plus the pure function `final_page`, which the tests call.
- CLI `newsrag ocr fallback preview`, registered in `src/newsrag/ocr/cli.py` like 009's `check`.
- Settings under `# --- ocr_fallback ---`: `ocr_fallback_file`, `sarvam_cache_dir` (a new
  `WORK_DIR_DERIVED` rule), `sarvam_language`, `sarvam_output_format`, `sarvam_min_block_px`
  (20), `sarvam_crop_pad_px` (2), `sarvam_crops_per_job` (10), `sarvam_max_jobs` (4),
  `sarvam_job_timeout_s` (600), `sarvam_poll_interval_s` (3), `sarvam_http_timeout_s` (300),
  `sarvam_retry_backoff_s` (2, 8, 30).
- `pyproject.toml` / `uv.lock`: `sarvamai>=0.1.45`.
- `tests/test_ocr_final.py`: 35 tests. A fake `SarvamApi` reads each crop's size from the
  uploaded PDF and answers per PDF page, so no network is used.

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| ruff check / format | PASS | `evidence/auto-ruff.log` |
| unit tests | PASS, 234 passed | `evidence/auto-pytest-unit.log` |
| E-1 pass-through | PASS: the grep prints `rules: []`; `ocr_final done=20 failed=0`; `1097 {'indic'}` | `evidence/E-1.log` |
| E-2 fallback with fake Sarvam | PASS: 35 passed, including all 10 named tests | `evidence/E-2.log` |
| E-3 preview | PASS: `2025\tmain\t361` (matches a SQL count), no `2024` line; `-k preview_no_network` passes | `evidence/E-3.log` |
| E-4 missing key | PASS (2): the unit test checks the exact text; the `stores` test runs the real runner, and the page ends `failed` with that text while the unmatched page is `done` | `evidence/E-4-5-6.log` |
| E-5 versions | PASS (2) | `evidence/E-4-5-6.log` |
| E-6 clarifications | PASS (3) | `evidence/E-4-5-6.log` |

## Where I read the spec a particular way

- **Version string.** A page that matches no rule gets exactly `final-v1` (FR-2). No setting
  changes its output, so there is nothing for a config hash to cover. For a matching page,
  CLAUDE.md requires the trailing hash to cover every setting the stage reads. So the 8 hex
  after `final-v1-sarvam-` are the sha1 of `{"rules": <matched rules>, "settings": {language,
  output_format, min_block_px, crop_pad_px, text_labels, title_labels}}`. Editing the rules
  still reruns only the pages whose matching rules changed. The matched rules are de-duplicated
  and sorted, so reordering the file changes nothing.
- **Rejected API key.** HTTP 401/403 is not treated as "a job still failing": the page **fails**
  (`SarvamAuthError`). Otherwise a bad key would mark every matching page `done` with
  `low_confidence:sarvam_failed` and never retry it. Other errors that are not retried (such as
  400) count as a failed job (indic text kept, page flagged).
- **`PartiallyCompleted`** counts as failed for all of that job's crops ("any state other than
  `Completed`").
- **`n_sent`** counts blocks whose crop went to Sarvam *or* came from the cache. The cached part
  is also in `n_cached`. `sarvam_job_ids` includes the job ids stored with cached results, so
  every replaced text can be traced to a job.
- **The cache** stores empty results too, so a retry never pays twice for them. A crop whose job
  failed, or whose `metadata/page_NNN.json` was missing, is not cached and is sent again next
  time. Identical crops on one page are sent once.
- **Failure detail** `sarvam_errors` (≤ 20 entries) holds only the exception type and HTTP
  status. SDK and httpx messages can contain presigned URLs, so they are left out.
- **Artifact** = `ocr_split`'s schema plus `page_flags`, `fallback_rules` (the normalised rules
  the page matched) and `ocr_source` on every block. `markdown` is rebuilt from the final texts.
- **The Sarvam client** is only built when a job actually runs. A matching page whose crops are
  all cached needs no client, but it still needs the key (FR-4 is checked first).
- **`ocr fallback preview`** with no rules prints `no ocr_fallback rules: nothing is sent to
  Sarvam` on stderr and nothing on stdout.
