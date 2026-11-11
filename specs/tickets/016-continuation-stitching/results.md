# 016: Continuation stitching (per date) - Results

Branch: ticket/016-continuation-stitching
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-abe4341b4d16a053e
Port: 8701 (not used: no web page)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| ruff check / format | PASS | evidence/lint.log |
| unit tests (`-m "not gpu and not data and not stores"`) | PASS, 547 passed | evidence/unit-tests.log |
| E-1 matching logic | PASS, 48 passed incl. all 13 named tests and the stores test | evidence/E-1.log |
| E-2 run on dates | PASS: all `stitch` rows done; linked=8 of source_markers=8 (**100%**), linked >= 3 | evidence/E-2.log |
| E-3 link quality | PASS: `yes: 8/8` (only 8 links exist, so all were used; bar 90%) | evidence/E-3.log, evidence/E-3-report.json |
| E-4 date waits for pages | PASS: `done=0 skipped=1`, `date_runs` count 0 (3 of 6 pages had group_final) | evidence/E-4.log, evidence/chain-2025-09-01-limit3.log |
| E-5 FR-7 clarifications | PASS, 4 selected tests passed | evidence/E-5.log |

Numbers over the stitched dates (`stitch stats`, all done dates): source_markers=8, continuation_markers=10,
linked=8, unmatched_no_source=2, all other unmatched reasons 0, ambiguous=1, orphan_markers=0.
Per date: 2025-08-02 3/3 linked; 2025-04-11 3/3 (+1 continuation whose source page is not in the date);
2025-01-28 2/2 (+1 no_source, 1 ambiguous link); 2025-06-10 has no markers.
Verify score: yes 8/8 (qwen3-30b-a3b, all 8 links were `page_ref`).

## Deviation from eval.md: dates
The dates named in eval.md do not exist in the ledger: only 2024-12-01..2025-12-31 pages are
registered (3497 pages; the 2026 archives are not unpacked and there is no 2020 data). Only
2025-06-10 exists (6 pages), and it contains no continuation marker. I therefore ran the whole
`render ocr ocr_split ocr_final group judge group_retry judge_retry group_final` chain on three
full dates picked for having markers, plus 2025-06-10, and used:
- E-2 dates: 2025-08-02 (7 pages), 2025-04-11 (6), 2025-01-28 (5), 2025-06-10 (6)
- E-4 date: 2025-09-01 with `--limit 3` (6 pages; 3 got group_final)
The router/user should confirm that this substitution is acceptable. The chain needed
`NEWSRAG_SURYA_ENV_DIR` / `NEWSRAG_VLLM_ENV_DIR` pointing at the main checkout's `envs/`
(the worktree has none); the chain logs are in evidence/chain-*.log.

## Attempts
- Attempt 1: tests: `test_unmatched_reasons` failed (my test fixture put a continuation on the
  "no candidate" page; and a `below_score` source got linked because the +0.2 `page_ref` bonus alone
  clears `stitch_min_score` 0.15 - that is what the spec says, so the test now uses a bare
  "పేజీ తరువాయి" marker); `test_orphan_markers` expected no link but the reverse match correctly
  recovers it; `test_stage_version...` patched only one of the two `settings` references. All fixed in the tests.
- Attempt 2: all checks pass. Reviewer found no blockers; its nits (test name `test_greedy_once_per_edition`,
  yaml comment, hardcoded worker count -> `stitch_verify_workers`) were fixed.

## Continuation patterns (continuations.yaml)
From the old system (`/home/admin1/RAG/config/settings.py`): `మొదటి\s*పేజీ\s*తరువాయి` (page 1), `పేజీ\s*తరువాయి`.
From the ticket: `మిగతా\s*(?P<page>\d+)\s*వ?\s*పేజీలో`, `(?P<page>\d+)\s*వ?\s*పేజీ\s*తరువాయి`.
Added after reading the dev sample text (581 marker blocks in the 3497 dev-sample pages):
1. `(?:మిగతా|బుగతా)\s*(?P<page>\d+)\s*[వన]\s*పేజీలో` - OCR reads the ordinal "వ" as "న" ("మిగతా 9న పేజీలో") and once "మిగతా" as "బుగతా".
2. `మిగతా\s*[^\s\d]{1,2}\s*వ\s*పేజీలో` - number unreadable ("మిగతా ఒవ పేజీలో", "మిగతా ఓవ పేజీలో"): source with no page, searches later pages.
3. `మొదటి\s*[ఏపె]?జీ\s*తరువాయి` (page 1) - OCR drops letters ("మొదటి ఏజీ తరువాయి").
4. `(?P<page>\d+)\s*న\s*పేజీ\s*తరువాయి` - the same "న" misreading in the (b) form.
Not added on purpose: the old bare `పేజీలో` (matches "మొదటి పేజీలో ప్రచురించాలి", "మెడికల్ పేజీలో ...") and
"లోపలి పేజీలో.." teasers (they do not continue a story).

## Changes
- `continuations.yaml` - marker regexes (new)
- `src/newsrag/stitch/` - `markers.py` (patterns, find/strip), `match.py` (parts, matching, artifact), `merge.py` (merged articles, link pairs), `load.py` (ledger/artifact reading), `model.py` (`Article`), `verify.py`, `cli.py` (`stats`, `verify`), `__init__.py` (`stitched_articles`)
- `src/newsrag/stages/stitch.py` - DateStage `stitch`; `registry.py` registers it
- `src/newsrag/config.py` - `# --- stitch ---` block (`continuations_file`, `stitch_*` knobs, `stitch_verify_file`), `stitch_kinds` list validator
- `src/newsrag/cli.py` - registers the `stitch` sub-app
- `tests/test_stitch.py` - 48 tests (synthetic articles; one `stores` test through the runner)

## Notes for the router
- `stitch` version = `versioned("stitch-v1", [stitch_min_score, stitch_ambiguity_margin, stitch_page_ref_bonus, stitch_kinds, stitch_tail_chars, stitch_token_min_chars, stitch_attach_px], [continuations.yaml])` - more settings than the rd lists, per the CLAUDE.md "list every setting the stage reads" rule; format is `stitch-v1-<8 hex>`.
- The artifact has an extra `stats` key ({source_markers, continuation_markers, orphan_markers}, counted in parts) for `stitch stats`.
- Spec quirk: score = Jaccard + 0.2 means a `page_ref` pair clears `stitch_min_score` (0.15) with zero word overlap; `below_score` can only happen for pairs without a named page. Followed as written.
- Reverse match uses the plain Jaccard (the +0.2 would always apply, making the threshold meaningless).
- A `continuation_marker` block that also holds body text (e.g. "మొదటి పేజీ తరువాయి విజయవాడలోని ...") stays out of the merged text, as the marker blocks are excluded from `group_final` `text`; the spec joins only the parts' `text`.
- `stitched_articles` raises `FileNotFoundError` when the date has no done `stitch` row.
- `pipeline.yaml` group_model is `gemma4-31b`; the new date chains ran with it (the 100 pre-existing sample group_final pages used earlier models).
- Chain-run side effect: `group`..`group_final` now done for 2025-08-02, 2025-04-11, 2025-01-28, 2025-06-10 and 3 pages of 2025-09-01 (ledger rows, artifacts under /home/admin1/raw/work).
