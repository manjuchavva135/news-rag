# 017: Cross-edition story dedup (per date) - Results

Branch: ticket/017-cross-edition-dedup
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-aa843952b7b6a3b87
Port: 8701 (unused, no web app)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| lint | PASS | `uv run ruff check .` exit 0 (evidence/tester-ruff.log) |
| unit tests | PASS | 567 passed, 67 deselected (evidence/tester-pytest.log) |
| E-1 | PASS | 25/25 in tests/test_dedup.py, incl. all 11 named tests (evidence/tester-E-1.log) |
| E-2 | PASS (dates substituted, see notes) | evidence/E-2.log, evidence/E-2-2024-12-26.log, evidence/tester-E-2.log |
| E-3 | PASS | `yes: 30/30`, 129 merged pairs available (evidence/E-3.log, evidence/tester-E-3.log) |
| E-4 | PASS | story_ids md5 `9f37d2eef30e6736e035e88e8381eba1` before and after reset + rerun (evidence/E-4.log) |
| E-5 | PASS | 5 selected tests pass (evidence/tester-E-5.log) |

E-2 numbers (dedup time per date, model already loaded; limit 120 s):

| Date | Units | Stories | Time |
|------|-------|---------|------|
| 2025-06-10 | 89 | 89 | 0.2 s |
| 2025-08-02 | 101 | 101 | 0.2 s |
| 2025-04-11 | 99 | 99 | 0.1-0.2 s |
| 2025-01-28 | 76 | 76 | 0.1 s |
| 2024-12-26 (11 district-edition pages) | 246 | 117 | 1.5 s (mean 2.1, max 10 printings, 13 stories with >= 5) |

## Attempts
- Attempt 1: everything passed at the first full run. The reviewer's minors were fixed before the tester ran (verify skips a story whose canonical unit vanished; `embed()` raises when the model is not open instead of leaving the GPU lock held).

## Changes
- `src/newsrag/stages/dedup.py` - DateStage `dedup` (depends on stitch + transcribe), model opened in `open()`, logs `dedup <date>: ...`
- `src/newsrag/dedup/{minhash,units,cluster,ids,load,verify,cli}.py` - normalisation + MinHash/LSH, unit building (FR-1, FR-7), pair/cluster/canonical/artifact, stable story ids, loaders, `dedup verify`, `dedup stats`
- `src/newsrag/ledger/sql/003_story_ids.sql` - `story_ids` table
- `src/newsrag/config.py` - `# --- dedup ---` block (dedup_jaccard, dedup_cosine, dedup_device, and the other knobs), `dedup_verify_file` report path
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` - registration
- `pyproject.toml`, `uv.lock` - `datasketch`
- `tests/test_dedup.py` - 25 tests

## Notes for the router
- The eval dates 2026-04-03 and 2020-04-02 do not exist in the ledger (it holds print dates 2024-12 to 2025-12). Substitutes: the four dates that already had `stitch` done (2025-06-10, 2025-08-02, 2025-04-11, 2025-01-28; one page per edition group, so no duplicates and stories == units is correct) and 2024-12-26, a date with 11 district-edition pages at page 5, for which I ran group, judge, group_retry, judge_retry, group_final, transcribe and stitch (logs in evidence/prep-*.log). "Stories < units" is shown on 2024-12-26 only. I also ran `transcribe` on the pages of the four dates that lacked it (evidence/prep-transcribe.log). This added real ledger rows and artifacts for those dates.
- The worktree has no `envs/vllm`; GPU runs from the worktree need `NEWSRAG_VLLM_ENV_DIR=/home/admin1/news-rag/envs/vllm`.
- `first_seen` in `story_ids` is the day the unit got its current story id (the run day), not the print date; the FR-3 tie-break "oldest first_seen" depends on it.
- Added settings beyond the three the ticket names (num_perm, shingle size, LSH threshold 0.5, batch, kinds, verify knobs); all are in the stage version hash. LSH uses a wider net (0.5) than `dedup_jaccard` for recall; the estimated Jaccard is then checked against 0.85.
- Migration 003 was applied to the shared ledger (`newsrag ledger init`).
- Nothing in `src/newsrag/stitch` was changed.
