# 020: Enrichment: places and districts - Results

Branch: ticket/020-enrich-places
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-a68e5215fd60c4a9f
Port: 8701 (unused, no web app)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| lint | PASS | `uv run ruff check .` exit 0 (evidence/tester-ruff.log) |
| unit tests | PASS | 663 passed, 68 deselected (evidence/tester-pytest.log) |
| E-1 | PASS | tests/test_enrich_places.py 49 passed, incl. all 9 named tests (evidence/tester-E-1.log) |
| E-2 | PASS (dates substituted) | all 3 dates done, combined_resolved=80.9 (evidence/tester-E-2.log; own first run: E-2-run-1.log, E-2-stats-1.log, combined 80.0) |
| E-3 | PASS | `-k non_verbatim` 1 passed (evidence/tester-E-3.log) |
| artifact shape + `enrich.load` merge | PASS | evidence/tester-artifact.log |

E-2 (tester run, fresh after `stage reset enrich_places --all`):

| Date | Stories | Places | resolved / ambiguous / unresolved (district, mandal, city) | failed | stories/s |
|------|---------|--------|---------|--------|-----------|
| 2025-06-10 | 89 | 284 | 132 / 4 / 20 | 0 | 8.2 |
| 2024-12-26 | 117 | 421 | 123 / 1 / 42 | 0 | 7.5 |
| 2025-08-02 | 101 | 307 | 118 / 0 / 21 | 0 | 7.9 |

`enrich stats --places`: places_per_story=3.3; district resolved 90.2 / ambiguous 0.0 / unresolved 9.8; mandal 69.0 / 7.0 / 23.9; city 78.5 / 0.0 / 21.5; **combined_resolved=80.9** (NFR-1 >= 70). Top unresolved: తిరుమల (11), ఢిల్లీ, బెంగళూరు, అన్నవరం, న్యూఢిల్లీ, ... (places outside the AP/Telangana list or without a geo entry).

Dates: 2026-04-03 and 2020-04-02 are not in the ledger; 2025-06-10, 2024-12-26 and 2025-08-02 were used (as in 019). Old-vs-new district behaviour is therefore covered only by `test_2020_story_gets_old_and_new_district` (and `test_story_fields_are_sorted_unique_over_non_ambiguous_places`, printed 2020), not by real data.

## Attempts
- Attempt 1: all checks pass on the first full run (own run combined_resolved 80.0, tester run 80.9; the difference is model non-determinism at temperature 0 under batching).
- Reviewer: no blockers. Two minor findings fixed: `preflight` now loads the geo data (StageUnavailable before any request); a missing aliases file no longer breaks `version`. Test added.

## Changes
- `src/newsrag/enrich/places.py` - prompt/schema, reply validation (verbatim, dedup), suffix stripping, resolution, story fields, requests, `place_totals`
- `src/newsrag/stages/enrich_places.py` - DateStage `enrich_places` (subclass of the enrich_core stage for model use)
- `src/newsrag/prompts/enrich_places_v1.md` - prompt
- `src/newsrag/stages/registry.py` - registration
- `src/newsrag/enrich/cli.py` - `enrich stats --places`
- `src/newsrag/config.py` - `enrich_max_places` (16) in the `# --- enrich ---` block
- `src/newsrag/geo/aliases.yaml` - alias `అరకు` -> mandal `araku_valley` (data has only అరకులోయ)
- `tests/test_enrich_places.py` - 49 tests

## Notes for the router
- Outside the rd "Affected areas": `src/newsrag/config.py` (new setting, per convention), `src/newsrag/enrich/cli.py` (the `stats` command lives in 019's enrich sub-app; `src/newsrag/cli.py` needed no change), `src/newsrag/geo/aliases.yaml` (allowed by Don't touch).
- Place records carry an extra `id` key (resolved geo id; null otherwise); `kind` is null for state/village/constituency/other and unresolved/ambiguous places. Ambiguous places have `candidates`.
- Story-level `flags` also carry the places' informational flags (`place_ambiguous`, `place_unresolved`) plus `enrich_list_capped` / `empty_story` like 019.
- Cap of 16 places per story (schema maxItems; retries ask for 8 then 4).
- 10-14% of the model's places are dropped as non-verbatim (inflected forms etc.); the guard works as specified.
- Data gap seen: mandals are numbered in the geo data (e.g. `Adoni-1`), so a plain "ఆదోని" does not resolve; growing aliases.yaml from `enrich stats --places` top-unresolved is the intended next step (not done: NFR-1 already met).
- `test_yesterday_live` of ticket 019 fails if run by file without `NEWSRAG_VLLM_ENV_DIR` (it is gpu-marked; excluded from the unit run).
