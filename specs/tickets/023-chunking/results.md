# 023: Chunking - Results

Branch: ticket/023-chunking
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-a3cff284c73df078b
Port: 8701 (not used: no web app)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| lint (`ruff check .`, `ruff format --check .`) | PASS | evidence/lint.log |
| unit tests (`-m "not gpu and not data and not stores"`) | PASS (853 passed) | evidence/unit.log (first run, 851), re-run after fixes: 853 passed |
| E-1 split rules | PASS | evidence/E-1.log, E-1-names.log, E-1-final.log (40 passed) |
| E-2 real dates | PASS (substitute dates, see notes) | evidence/E-2.log, E-2-status.log, E-2-final.log |
| E-3 missing aliases | PASS | evidence/E-3.log, E-3-cli.log (exit 2, `run 'newsrag aliases build' first`) |
| E-4 embed model change re-chunks | PASS | evidence/E-4-qwen.log, E-4-other.log, E-4-unit.log (eligible=3 = done=3) |
| E-5 second-review clarifications | PASS | evidence/E-5.log |
| data test, real bge-m3 tokenizer (`-m data`) | PASS | run in worktree |
| `stale_dates()` on the real dates | PASS (`[]`) | evidence/stale_dates.log |

E-2 token max per date: 2024-12-26 512, 2025-06-10 510, 2025-08-02 512 (NFR-1: none above 512). The
reviewer also re-counted all 432 stored chunks with the real tokenizer: every `n_tokens` is exact.

## Attempts
- Attempt 1: all eval checks passed (tester). Reviewer found 2 MAJOR issues, fixed:
  - `split_table` put the rows of a second table of a `table_only` story under the first table's header. Fixed: blocks separated by blank lines are split by rows each with its own header (`test_table_story_with_several_tables_keeps_each_header`).
  - `stale_dates()` loaded every page artifact and crashed when an input of a done date had been reset. Fixed: a lighter `load.load_lookup_inputs`; a date whose inputs are missing counts as stale (`test_stale_date_with_reset_inputs`).
  - MINOR: `fit_headline` no longer cuts before a vowel sign.
- Attempt 2: all checks pass again (evidence/*-final.log).

## Changes
- `src/newsrag/chunking/{__init__,split,build,load,cli}.py` - new: splitting rules, chunk records, input loading, `stale_dates()`, `newsrag chunk stats`
- `src/newsrag/stages/chunk.py` - new DateStage `chunk`; registered in `stages/registry.py`
- `src/newsrag/cli.py` - one `SUB_APPS` line (`chunk`)
- `src/newsrag/config.py` - `# --- chunking ---` block: `embed_model`, `chunk_max_tokens`
- `pipeline.yaml` - `embed_model: bge-m3`
- `tests/test_chunking.py` - new (40 unit tests + 1 `data` test)

## Notes for the router
- E-2 dates: `2026-04-03` and `2020-04-02` have no `enrich_facts` output on this machine, so E-2 and E-4 ran on the three dates that do: 2024-12-26, 2025-06-10, 2025-08-02.
- E-4: `qwen3-embedding-8b` is not in models.yaml; `stage status` does not preflight, so it still shows the version change (eligible=3).
- `test_alias_change_changes_version` keeps the name from eval.md E-1 but, following FR-7, asserts that the version stays and the date's `lookup_hash` changes.
- Choices beyond the letter of FR-7: enrich-record flags and a table item's own flags (that table's chunks only) also feed `low_confidence`; `seq` is 1-based and equals the number in `chunk_id`; an over-long headline is cut so the prefix takes at most half of the token budget.
- `low_confidence_share` is 0.90 to 1.0 on the dev dates: nearly all pages carry the upstream `geometry` / `judge_low` flags.
- Known limitation (reviewer MINOR, left as is): the version holds the embed model name, not the profile's `hf_id` / `doc_prefix`; editing those in models.yaml would not re-chunk by itself.
- Nothing under "Don't touch" changed; no embedding or store writes.
