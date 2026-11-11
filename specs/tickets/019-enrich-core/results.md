# 019: Enrichment: category, people/orgs, event date - Results

Branch: ticket/019-enrich-core
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-a27b028abc992c7f2
Port: 8701 (unused, no web app)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| lint | PASS | `uv run ruff check .` exit 0 (evidence/tester-ruff.log) |
| unit tests | PASS | 614 passed, 68 deselected (evidence/tester-pytest.log) |
| E-1 | PASS | 48 passed incl. all 11 named tests (evidence/tester-E-1.log; needs `NEWSRAG_VLLM_ENV_DIR` for the gpu test, see tester-E-1-noenv.log) |
| E-2 | FAIL (throughput only, see below) | evidence/tester-E-2.log |
| E-3 | PASS | `category_ok=46/50 names_ok=47/50` (evidence/tester-E-3.log, tester-E-3-report.json) |
| E-4 | PASS | `test_yesterday_live` 1 passed, event_date 2026-04-02 asserted (evidence/tester-E-4.log) |
| E-5 | PASS | 4 selected tests pass (evidence/tester-E-5.log) |

E-2 numbers (cold vLLM start per date, dates substituted, see notes):

| Date | Stories | enrich_failed | stories_per_s |
|------|---------|---------------|---------------|
| 2025-06-10 | 89 | 0 | 4.93 (FAIL, < 5) |
| 2024-12-26 | 117 | 0 | 5.29 |
| 2025-08-02 | 101 | 0 | 4.90 (FAIL, < 5) |
| 2025-01-28 / 2025-04-11 (E-3 prep) | 76 / 99 | 0 / 0 | 5.64 / 5.33 |

All dates `done`, only the 7 category values, `enrich_failed` 0%. Over all five dates: 5.3 stories/s. Two of three E-2 dates are 1-2% under the 5/s requirement; it was not reached reliably.

## Attempts
- Attempt 1 (own runs): bf16 qwen3-30b-a3b at 32 requests gave 1.7-3.3 stories/s (first run also had 2 truncated 1024-token replies -> enrich_failed). Fixes: JSON-schema constrained reply with `maxItems`, shorter-list retries, longest-first ordering, cache warm-up request, fp8 + n-gram speculative decoding on the profile, concurrency 64: 4.8-5.5 stories/s.
- Reviewer round: SDK-level retries (fixed: `raw.with_options(max_retries=0)`), silent list cap (fixed: flag `enrich_list_capped`, cap 8), role cut mid-word (fixed: maxLength 60).
- Tester round 1: everything passes except E-2 throughput (4.93 and 4.90 on two dates).
- Further tuning tried and dropped (no gain): concurrency 128, speculative tokens 8, `--max-num-batched-tokens 16384`. The GPU is at 100% utilisation and power capped (300 W), so the run is compute bound: about 130k uncached prefill tokens (Telugu is about 1.3 tokens per character) plus about 25k generated tokens per date.

## Changes
- `src/newsrag/stages/enrich_core.py` - DateStage `enrich_core` (depends on `dedup`), model opened in `open()`, parallel pool in `handle`, 20% date-fail rule
- `src/newsrag/enrich/{__init__,core,validate,verify,cli}.py` - `load()`/`merge()`, requests/retries/prompt building, validation, `verify-core`, `stats`
- `src/newsrag/prompts/enrich_core_v1.md`, `enrich_core_judge_v1.md` - extraction prompt (with the category rules block), judge prompt for NFR-1
- `src/newsrag/config.py` - `# --- enrich ---` block, `enrich_verify_file` report path
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` - registration
- `models.yaml` - qwen3-30b-a3b profile: `--quantization fp8` and n-gram `--speculative-config` (see notes)
- `tests/test_enrich_core.py` - 48 tests (47 CPU + 1 gpu)

## Notes for the router
- **Dates:** 2026-04-03 and 2020-04-02 do not exist in the ledger (it holds 2024-12-26, 2025-01-28, 2025-04-11, 2025-06-10, 2025-08-02, all with dedup done). E-2 and E-3 used 2025-06-10, 2024-12-26, 2025-08-02 (+ the other two for E-3). `test_yesterday_live` uses a synthetic story printed 2026-04-03, so that date is not needed in the ledger.
- **Deviation 1, models.yaml (outside "Affected areas"):** the shared `qwen3-30b-a3b` profile now runs fp8 weights + n-gram speculative decoding. On bf16 the stage ran 3.3 stories/s; with this about 5.2. Speculation is lossless; fp8 can change outputs slightly (dedup/stitch verify also use this profile; their recorded results came from bf16). NFR-1 was measured on the fp8 run. Revert is a 6-line deletion but the stage then fails NFR-2 clearly.
- **Deviation 2, `enrich_concurrency` default 64** instead of 32 (FR-1/FR-7): 32 measured about 4.7 stories/s. It is only a setting.
- **Deviation 3, additions beyond the rd:** every request carries a JSON schema (valid JSON, category enum, at most `enrich_max_listed`=8 people and 8 organisations, role maxLength 60) so one notice with 20+ officials cannot run to max_tokens; format retries ask for 4 then 2 entries; a list that reaches the cap gets the informational flag `enrich_list_capped` (names beyond the cap are not extracted); a 1-token warm-up request caches the system prompt before the pool starts (its time is inside `request_seconds`); stories with empty headline and text are not sent (flag `empty_story`).
- **Options for NFR-2 (decide):** A) accept about 5.3 stories/s overall (every date 4.9-5.6) and relax the E-2 wording to the total; B) drop `role` from the model output or lower the lists to 4 (about 10% fewer generated tokens, needs an rd change for FR-2); C) use a smaller or faster enrich model than the 30B (changes D-1); D) tune the MoE kernels for this GPU in vLLM (separate work). Recommendation: A.
- Dropped names are 10-15% of listed names (mostly inflected forms, ZWNJ differences, places listed as organisations): the verbatim guard works as specified.
- The worktree has no `envs/vllm`; GPU runs from it need `NEWSRAG_VLLM_ENV_DIR=/home/admin1/news-rag/envs/vllm`.
- The ledger now has `enrich_core` done rows and artifacts for the five dates under `/home/admin1/raw/work/enrich_core/`.
- **Acceptance (2026-11-11):** the user accepted option A (about 5.3 stories/s over five dates, 4.90-5.64 per date, in place of >= 5 on every date), the fp8 + n-gram speculation change to the shared `qwen3-30b-a3b` profile, and `enrich_concurrency` = 64.
