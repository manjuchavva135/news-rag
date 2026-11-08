# 019: Enrichment: category, people/orgs, event date

**Status:** draft
**Mode:** solo
**Runs:** waits for 017: enriches canonical stories

## Summary

Gives every canonical story the metadata that filtering and "what did X say / since June 2024"
questions need: a topic category, the people and organisations named in it, and the date the event
actually happened, which is often not the print date ("yesterday's press meet") (grill D-12, A-15,
A-33). Everything runs on a local text LLM.

## Functional requirements

- **FR-1** - DateStage `enrich_core` (`depends_on=("dedup",)`, `version="<enrich_model>-core-p1"`, `enrich_model` default `qwen3-30b-a3b`) processes every story in the date's `dedup` artifact; `models_needed=(enrich_model,)`, `concurrency = enrich_concurrency` (default 32 threads), model opened once in `open()`. One story per request: input = headline + "\n" + text (the dedup story text, which for notices/tables is their markdown), truncated to 6,000 characters. Requests use `temperature: 0`, `max_tokens: 1024`, timeout 120 s, up to 3 retries with backoff on transport errors (timeout, 5xx, connection).
- **FR-2** - Prompt `src/newsrag/prompts/enrich_core_v1.md` asks for strict JSON: `{"category": one of "sports","politics","health","cinema","family","education","other", "people":[{"name": verbatim, "role": str|null}], "organisations":[{"name": verbatim, "type": "party"|"government_department"|"company"|"court"|"other"}], "event_date_expression": verbatim phrase or null, "event_date": "YYYY-MM-DD"|null}`. The prompt gives the print date, explains Telugu relative expressions (నిన్న = yesterday, రేపు = tomorrow, మంగళవారం = the most recent Tuesday on or before print date unless the text says it is coming, etc.), and carries over the old category rules from `/home/admin1/RAG/pipeline/stage05_enrich/run.py` (politics includes government and policy; family includes devotional, food and festivals; crime, weather, business, agriculture, notices and obituaries go to `other`). The judge prompt in NFR-1 uses the same rules. There is no section-code shortcut.
- **FR-3** - Validation in code: a person or organisation name that is not a substring of the model input (headline + text, after NFC and whitespace normalisation on both sides) is dropped (counted as `dropped_names`); names are de-duplicated per story after normalisation; an organisation `type` outside the list becomes `other`. `event_date`: a valid date between 3 years before and 366 days after the print date is kept (`event_date_basis = "model"`, or `"model_future"` when after the print date); an invalid or out-of-range date gets flag `event_date_rejected`; whenever the final `event_date` would be null it becomes the print date with basis `"print_date"` (no expression found) or `"print_date_fallback"` (rejected or unresolved), so `event_date` is never null. Invalid JSON or schema errors (category outside the 7 values, `people`/`organisations` not lists) → 2 retries → category `other`, empty lists, flag `low_confidence:enrich_failed`. A story whose request still fails after the transport retries gets the same fallback; if more than 20% of a date's stories end that way, the date fails (so it is retried as a whole).
- **FR-4** - Artifact per date: `{pub_date, stories:{<story_id>:{category, people, organisations, event_date, event_date_basis, event_date_expression, flags}}}`.
- **FR-5** - `uv run newsrag enrich stats [--date D]` prints one line each: `category_<name>=<n>` for the 7 values, `people_per_story=<x>`, `orgs_per_story=<x>`, `dropped_names=<n>`, `event_date_differs=<n>`, `enrich_failed=<n>`, `stories_per_s=<x>` (from the stage detail `stories_per_s`, measured over requests only, model load excluded).
- **FR-6** - `newsrag.enrich.load(pub_date) -> dict[story_id, dict]` merges the date's `enrich_core`, `enrich_places` and `enrich_facts` artifacts (each stage writes its own artifact; later stages never rewrite earlier ones); tickets 020, 021 and 023 use it. This ticket implements it for `enrich_core` and makes it tolerate the other two being absent.
- **FR-7** - Clarifications (second review):
  - DateStage `concurrency` is 1; inside `handle` the stage sends `enrich_concurrency` (32) parallel requests over the date's stories, sharing the client from `open()`. Tickets 020 and 021 do the same.
  - `newsrag.enrich.load()`: `flags` = ordered, de-duplicated union over the three artifacts; every other key belongs to one stage (a clash raises).
  - The 20% date-fail rule counts all stories that end with `low_confidence:enrich_failed` (transport or invalid output).
  - `enrich stats --date` is repeatable; `stories_per_s` over several dates = total stories / total request seconds.

## Non-functional requirements

- **NFR-1** - Quality check: `uv run newsrag enrich verify-core --n 50 --seed 11` samples 50 stories from all enriched dates, gives the judge model (`gemma3-27b`, text only, same 6,000-char input and category rules) each story with the extracted fields, and asks whether the category is right and whether every listed name is a real person/organisation in the text (an empty list counts as ok); an unparseable judge reply counts as not ok. It prints `category_ok=<a>/50 names_ok=<b>/50`; a ≥ 45 and b ≥ 45.
- **NFR-2** - Throughput ≥ 5 stories/s on this GPU.

## Affected areas

- `src/newsrag/stages/enrich_core.py`, `src/newsrag/enrich/{__init__,core,validate}.py`, `src/newsrag/prompts/enrich_core_v1.md` (new)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_enrich_core.py` (new, fake model)

## Don't touch

- `dedup` artifacts and ids.

## Out of scope

- Places (020), amounts (021), merging spellings (022).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Model | local Qwen3 30B-class | grill A-15 |
| D-2 | Category list | the old 7 values | grill A-33 |
| D-3 | Event date | separate from print date, default = print date | grill D-12; fixes F-6 "wrong event dates" |
| D-4 | Name hallucination guard | names must be verbatim in the text | the old system's working rule (entity stage) |
| D-5 | Null event date | falls back to the print date with its basis recorded | filters and facts need a date (024, 021) |
| D-6 | Future events | allowed up to 366 days after print | notices, exams and announced meetings are real future events |
| D-7 | Enrich failure | `low_confidence:enrich_failed`; >20% of a date → date fails | doubtful items flagged (D-20); a broken model run is retried |

## Open questions

None.
