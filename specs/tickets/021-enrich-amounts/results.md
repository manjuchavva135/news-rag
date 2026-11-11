# 021: Enrichment: amounts as structured facts - Results

Branch: ticket/021-enrich-amounts
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-a9424f9a6ce5fe905
Port: 8701 (unused, no web app)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| lint | PASS | `uv run ruff check .` exit 0 (evidence/lint.log) |
| unit tests | PASS | 746 passed, 68 deselected (evidence/unit-tests.log) |
| E-1 | PASS | tests/test_enrich_facts.py 83 passed, incl. all named tests (evidence/E-1.log) |
| E-2 | PASS (dates substituted) | 3 dates done, facts_per_story=1.36 (> 0.5), unit_ and both dropped_ lines printed (evidence/E-2-run.log, E-2-stats.log) |
| E-3 | PASS | `correct: 46/50` (k >= 45) (evidence/E-3.log, E-3-verify-report.json) |
| E-4 | PASS | `-k table_markdown` 1 passed (evidence/E-4.log) |
| E-5 | PASS | 5 selected tests passed (evidence/E-5.log) |

E-2 (tester run, fresh after `stage reset enrich_facts --all`):

| Date | Stories | Facts | dropped non-verbatim | dropped unparsed | failed |
|------|---------|-------|----|----|----|
| 2025-06-10 | 89 | 73 | 12 | 7 | 0 |
| 2024-12-26 | 117 | 198 | 18 | 22 | 0 |
| 2025-08-02 | 101 | 148 | 11 | 18 | 0 |

`enrich stats --facts`: facts_per_story=1.36, unit_INR=166, unit_percent=36, unit_count=172, unit_acres=13, unit_tonnes=3, unit_km=8, unit_other=21, dropped_non_verbatim=41, dropped_unparsed=47.

Dates: 2026-04-03 and 2020-04-02 are not in the ledger (no enrich_places); 2025-06-10, 2024-12-26 and 2025-08-02 were used (enrich_places done for them). Old-vs-new districts of a fact place are covered by unit test `test_districts_of_the_print_date` only.

## Attempts
- Attempt 1: first real run (own): facts_per_story 1.38; found the model sometimes puts dates ("మే 15 నుంచి") and sq-km areas in as facts/acres. Fixed: a value_text with a month name is dropped as unparsed; prompt tightened on units. Own verify-facts run: 46/50.
- Reviewer: 1 major, 3 minor. Major fixed: a scale word in running text ("కోట్ల రూపాయల విలువైన 50 ఎకరాల") was taken as a context scale; now only announcing phrases count in running text ("కోట్లలో", "in crore", "Rs crore", "(lakh)"); table titles/headers stay lenient. Minor fixed: full dates as period_text (ISO, d-m-y) give that day; misplaced `geo.resolve("")` moved before the pool; judge prompt now explains the lakh-crore value convention. Minor left as is: for "లక్ష కోట్ల" the stored `value` is the number in crore (7.5 lakh crore -> value 750000, scale crore) because the scale enum has no lakh-crore; normalized_value is right.
- Attempt 2 (tester, all checks): all pass.

## Changes
- `src/newsrag/enrich/facts.py` - prompt/schema, reply validation (verbatim, value/scale/unit from text, context scale, periods, places), requests, artifact records, `fact_totals`, verify-facts (sample, judge prompt, verdict)
- `src/newsrag/stages/enrich_facts.py` - DateStage `enrich_facts` (subclass of the `enrich_places` stage for model use and geo preflight); event dates read from the done `enrich_core` artifact
- `src/newsrag/prompts/enrich_facts_v1.md`, `src/newsrag/prompts/facts_judge_v1.md` - prompts
- `src/newsrag/stages/registry.py` - registration
- `src/newsrag/enrich/cli.py` - `enrich stats --facts`, `enrich verify-facts`; `_done_runs` takes a stage name
- `src/newsrag/config.py` - `enrich_max_facts` (16), `enrich_facts_scale_window` (40), `enrich_facts_verify_n/seed/file` in the `# --- enrich ---` block; report file default under `reports_dir`
- `tests/test_enrich_facts.py` - 83 tests

## Notes for the router
- Outside the rd "Affected areas": `src/newsrag/config.py` (new settings, per convention) and `src/newsrag/enrich/cli.py` (the enrich sub-app lives there; `src/newsrag/cli.py` needed no change). `src/newsrag/prompts/facts_judge_v1.md` is named in FR-5.
- Interpretations of the rd: (1) a non-verbatim `period_text` or `place` is nulled and the fact kept (counted `dropped_period` / `dropped_place` in the stage detail, not in stats); (2) flag `scale_from_model` only when the model's scale is not "none"; flags `scale_from_model` / `period_unparsed` are story-level (the fact has no flags field); (3) a model `value` is never used (the code parses the number); (4) facts with an empty `refers_to`, or not objects, are dropped (`dropped_invalid`); (5) value_text with several numbers (ranges) or a month name is dropped as unparsed; (6) context scale in running text accepts announcing phrases only (see Attempts); in tables the column header, then the title line above, then the label column header; (7) the fact place carries no type, so its kind is read from the name (జిల్లా -> district, మండలం -> mandal, else town when the geo list has one), then resolved like 020 (ambiguous -> no districts, state only if shared); state names give only a state; (8) a "లక్ష కోట్ల" compound is stored as value x 100000 in scale crore; (9) event date comes from the `enrich_core` artifact (print date if missing); facts are numbered in order of appearance in the story; identical (value_text, refers_to, period) facts are merged.
- Ticket 024's `facts` table needs a `state` column (FR-5); `state` is the last field of each fact in the artifact.
- Facts reply is capped at 16 per story (schema maxItems, retries ask 8 then 4); 19 stories on the 3 dates hit the cap (flag `enrich_list_capped`), mostly tables.
- About 14% of the model's value_texts are dropped as non-verbatim (it re-formats commas/spaces); the guard works as specified.
- Throughput 3.3-4.9 stories/s (no NFR for this stage).
- The 4 facts the judge marked incorrect in the final sample: two moisture percentages (judge disagrees with refers_to), `12 మీటర్లు` typed unit km by the model (should be other), and a `రూ.20 వేల కోట్ల` fact whose period `2014-19` the judge rejected. Model/judge disagreements, no code bug found.
