# 022: Name alias table - Results

Branch: ticket/022-name-aliases
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-aa03a84c27bdd8589
Port: 8701 (not used, no web app)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| ruff check + format --check | PASS | evidence/ruff.log |
| unit tests (`not gpu and not data and not stores`) | PASS, 784 passed | evidence/pytest-unit.log |
| E-1 rules | PASS, 41 passed (incl. the 3 `stores` tests) | evidence/E-1.log |
| E-2 build on real data | PASS: names=1395 entities=1322 auto_pairs=82 review_pairs=0 largest_group=5; entities.json exists; CSV has the FR-4 header | evidence/E-2.log |
| E-3 precision | PASS: `same: 40/40` (judge gemma3-27b, 64 auto-merged groups, seed 17) | evidence/E-3.log, `/home/admin1/raw/reports/alias-verify.json` |
| E-4 re-run | PASS: md5 `bbd9ee1d546156769f9d67b1aea455c0` before and after (0 pairs asked) | evidence/E-4.log |
| E-5 clarifications | PASS, 5 selected tests passed | evidence/E-5.log |

Real data: 5 dates (482 stories), 1395 names, 209 candidate pairs (rule a 35, b 171, d 3), all
answered by `qwen3-30b-a3b`; 82 pairs auto-applied.

## Attempts
- Attempt 1: first test run had 9 failing tests, all wrong expectations in my tests (display case,
  pair order, a Jaro-Winkler 0.905 for "Jagan"/"Y S Jagan" making it rule b); fixed in the tests.
- Attempt 2: first real-data build wrongly merged a woman's and a man's name (రమణమ్మ/రమణయ్య);
  prompt tightened (prompt hash is part of the verdict cache, so the pairs were asked again).
- Attempt 3: reviewer found (a) a contested entity id could go to the wrong group when a group
  falls back to its second id: `assign_ids` now visits all stored names oldest first; (b) rule (d)
  only used the last word as surname, Telugu house names come first: now first or last word;
  (c) a cut-off last line in verdicts.jsonl broke every later build: now skipped; (d) `mint_id`
  built a set per group. Tests added for (a)-(c). Re-ran everything: all checks pass, same hash.

## Changes
- `src/newsrag/aliases/` (new) - `__init__` (normalise, key, lookup, fold), `names`, `rules`,
  `judge`, `group` (overrides, union-find, entities, ids), `build`, `verify`, `cli`
- `src/newsrag/prompts/alias_v1.md` (new), `data/aliases/overrides.yaml` (new, empty)
- `src/newsrag/ledger/sql/004_entity_ids.sql` (new migration; applied to the real ledger by `aliases build` via `db.migrate()`)
- `src/newsrag/config.py` - `# --- aliases ---` block, three derived-path entries
- `src/newsrag/cli.py` - one SUB_APPS line; `pyproject.toml`/`uv.lock` - `indic-transliteration`, `rapidfuzz`
- `tests/test_aliases.py` (new, 41 tests)
- `tests/test_ledger.py` - exception to "Affected areas": the exact-table-list assertion was already
  stale (story_ids, ticket 017) and breaks with any new migration; now checks a subset and that
  every migration file is applied.

## Notes for the router
- All 164 `same: true` verdicts (82 pairs, twice) have confidence exactly 0.9 or higher; the model
  never answers lower, so `review_pairs=0` (the CSV is header only). The gate for rule (d) and
  invalid replies is what feeds the CSV; on this corpus only 3 rule (d) pairs exist and all were `same: false`.
- Judge 40/40 is lenient: a manual look at the 82 auto pairs finds a few doubtful merges
  (బీసీఈ + బీసీబీ, ఈ ప్రభుత్వం + ఏపీ ప్రభుత్వం + ప్రభుత్వం, చెవిరెడ్డి + చెవిరెడ్డి భాస్కర్,
  శ్రీసత్యసాయి జిల్లా + ... కలెక్టరేట్). Fix without code: put pairs into `data/aliases/overrides.yaml` `split`.
- Deviations / decisions: anusvara folded to n (m before p/b/m or at word end) before diacritic
  stripping so "చంద్రబాబు" folds like "Chandrababu"; invalid-reply verdicts are cached (otherwise
  the loop-until-none-pending never ends); `stats.json` (next to entities.json) holds the stats
  line numbers and the auto-merged groups for `verify`; verdict cache lines carry the prompt hash;
  `verify` reuses prompt alias_v1 with the judge model; `lookup(type, name)` returns the entity id
  or None.
- vLLM needs `NEWSRAG_VLLM_ENV_DIR=/home/admin1/news-rag/envs/vllm` from a worktree (no envs/ there).
- No vLLM server or background job of mine is left running.
