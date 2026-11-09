# 010: Sarvam fallback for named page types

**Status:** done
**Mode:** solo
**Runs:** waits for 008: wraps the OCR block output

## Summary

Creates the final OCR stage `ocr_final` that every later stage reads. For most pages it simply passes
the `ocr_split` blocks through. For page types the Telugu reader named as failing in the sanity check,
it re-reads each text block crop with Sarvam, keeping our block numbering (grill D-17, A-38). This is
the only external API allowed in ingestion. With no rules configured, nothing is sent anywhere.

## Functional requirements

- **FR-1** - The file named by setting `ocr_fallback_file` (default `<repo>/ocr_fallback.yaml`, env `NEWSRAG_OCR_FALLBACK_FILE`) holds a list `rules:`; each rule may set `years`, `sections`, `page_nums`, `edition_codes` (lists; omitted = any; a spread matches `page_nums` if any number in `page_num..page_end` is listed) and `labels` (block labels to re-read; omitted = `text_labels` + `title_labels` from ticket 007). The committed default is `rules: []`.
- **FR-2** - PageStage `ocr_final` (`depends_on=("ocr_split",)`, `version="final-v1"`) uses `version_for(page)` (ticket 003 FR-3): a page that matches no rule gets `final-v1`; a matching page gets `final-v1-sarvam-<first 8 hex of sha1 of the canonical JSON of the rules it matches>`. So editing the rules reruns only pages whose matching rules changed. A page that matches no rule: artifact = copy of the `ocr_split` artifact with every block given `ocr_source: "indic"`; detail `{fallback: false}`. No network call.
- **FR-3** - A page that matches a rule: each block whose label matches and whose bbox is at least 20 × 20 px is cropped from the page PNG (2 px padding); smaller blocks are not sent (`indic_kept`). Crops are packed ≤ 10 per PDF (one crop per PDF page) and sent to Sarvam Document Intelligence (`sarvamai` SDK, `language="te-IN"`, `output_format="md"`, the pattern in `/home/admin1/RAG/pipeline/stage03_ocr/run_hybrid.py`; job timeout 600 s). Each crop's returned text = all Sarvam blocks on that PDF page, **excluding** `layout_tag == "image"`, joined with `"\n"` in reading order, NFC-normalised; it replaces the block's text and the block gets `ocr_source: "sarvam"`, keeping `block_id`, `order`, `bbox`, `label`, `type`, `conf`. Blocks Sarvam returns empty for keep the indic-ocr text with `ocr_source: "indic_kept"`. Results are cached by sha1 of the crop PNG bytes in `<work_dir>/sarvam-cache/` so a retry never pays twice. Failure handling: a job ending in any state other than `Completed`, a timeout, a missing `metadata/page_NNN.json`, or a job still failing after its retries → the affected blocks keep the indic-ocr text with `ocr_source: "indic_kept"`, the page gets detail `sarvam_failed_blocks: <n>` and page flag `low_confidence:sarvam_failed`, and the page is still `done`. Detail `{fallback: true, n_sent, n_replaced, sarvam_job_ids: [...], sarvam_failed_blocks}`.
- **FR-4** - If a page matches a rule and `SARVAM_API_KEY` is not set, the stage fails that page with `SARVAM_API_KEY not set but page matches an ocr_fallback rule`.
- **FR-5** - `uv run newsrag ocr fallback preview` prints, for the current rules, one line `<year>\t<section>\t<count>` per year and section with count > 0 (non-quarantined registered pages that match), without calling the API.
- **FR-6** - Clarifications (second review):
  - The `ocr_final` artifact has a top-level `page_flags: []` (holding `low_confidence:sarvam_failed` when set); ticket 012 copies `ocr_final.page_flags` into its own `page_flags`, so the flag reaches articles via ticket 013.
  - In a rule, a key that is omitted, `null` or `[]` means "any"; rules are normalised (empty keys dropped, lists sorted) before hashing and before ticket 031's comparison.
  - `labels` is not part of the sign-off comparison in ticket 031 (it is a technical refinement the user may add).

## Non-functional requirements

- **NFR-1** - At most 4 Sarvam jobs run at once per `stage run` process (a process-wide semaphore; the stage's `concurrency` is 1). Every SDK call (create, upload, start, poll, download) is retried up to 3 more times (4 attempts) on HTTP 429/5xx or connection errors, with backoff 2 s, 8 s, 30 s.

## Affected areas

- `ocr_fallback.yaml` (new); `src/newsrag/stages/ocr_final.py`, `src/newsrag/ocr/sarvam.py` (new)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `pyproject.toml` - add `sarvamai` (changed)
- `tests/test_ocr_final.py` (new; fake Sarvam client, no network)

## Don't touch

- `ocr` and `ocr_split` artifacts and code.

## Out of scope

- Deciding which page types fail (the reader does that in ticket 009); sending whole pages to Sarvam; using Sarvam anywhere else.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Is Sarvam allowed? | only for page types named as failing | grill D-17 (exception to D-2) |
| D-2 | Unit sent | indic-ocr block crops, so block ids stay | grill A-38 |
| D-3 | Sarvam image descriptions | discarded | grill A-38; old system leaked invented descriptions (F-6 item 4) |
| D-4 | Default | `rules: []`, no calls | nothing has failed until the reader says so |
| D-5 | What reruns when rules change | only pages whose matching rules changed (`version_for`) | a rule edit must not rerun the whole corpus |
| D-6 | Sarvam failure on a page | keep indic-ocr text, flag `low_confidence:sarvam_failed` | the page is still usable; doubtful items are flagged (grill D-20) |

## Open questions

None.
