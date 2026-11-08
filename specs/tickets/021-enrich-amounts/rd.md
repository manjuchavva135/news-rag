# 021: Enrichment: amounts as structured facts

**Status:** draft
**Mode:** solo
**Runs:** waits for 020: same enrich module

## Summary

Many customer questions are about numbers: state debt before and after June 2024, budgets, tender
values, pass percentages. This stage pulls every money amount, count and percentage out of each story
as a structured fact (what the number is, its unit, what it refers to, when and where), so later
search can answer "how much" questions and compare them over time (grill D-12, A-33).

## Functional requirements

- **FR-1** - DateStage `enrich_facts` (`depends_on=("enrich_places",)`, `version="<enrich_model>-facts-p1"`), with the same input (headline + text, 6,000 chars; tables/notices as markdown), model use, concurrency, request settings, retries and failure rules as ticket 019 (FR-1, FR-3; story flag `low_confidence:enrich_failed` on total failure; a single bad fact is dropped on its own). It runs on every story in the date's `dedup` artifact (ads, mastheads and `other` are already excluded there). The prompt excludes: years and dates, times, phone/ID/notice/case numbers, page or edition numbers, sports scores, ranks and ages. The local LLM (prompt `src/newsrag/prompts/enrich_facts_v1.md`) returns `{"facts":[{"value_text": verbatim number as printed, "value": number, "unit": "INR"|"percent"|"count"|"acres"|"tonnes"|"km"|"other", "scale": "none"|"thousand"|"lakh"|"crore"|"million"|"billion", "refers_to": short phrase in the story's language, "period_text": verbatim|null (e.g. `2023-24 బడ్జెట్`, `జూన్ 2024`), "place": verbatim|null}]}`.
- **FR-2** - Validation in code: `value_text` must be a substring of the input (after NFC/whitespace normalisation and Telugu digits ౦-౯ → 0-9); otherwise the fact is dropped. The code parses `value` and `scale` **from `value_text`** itself (Indian and Western digit grouping, decimals, scale words కోటి/కోట్లు/crore → crore, లక్ష/లక్షలు/lakh → lakh, వేలు/వేల/thousand → thousand, million, billion, `%`/శాతం → percent); the model's `value`/`scale` are used only to pick the unit when the text has none, and a fact whose `value_text` cannot be parsed is dropped. `normalized_value` = value × scale factor (lakh 1e5, crore 1e7, thousand 1e3, million 1e6, billion 1e9). Period: from `period_text`, the code derives `period_start`/`period_end` (a financial year `YYYY-YY` → `YYYY-04-01`..`YYYY+1-03-31`; a month → that month; a year → that year); if none, both = the story's `event_date`. `place`: kept only if verbatim; resolved like ticket 020 FR-2 on the print date to `districts_current`/`districts_at_print` (lists; empty when unresolved or ambiguous).
- **FR-3** - Its own artifact per date (merged by `newsrag.enrich.load()`, ticket 019 FR-6): `{pub_date, stories:{<story_id>:{facts:[{fact_id: "<story_id>#fNNN", value_text, value, scale, normalized_value, unit, refers_to, period_text, period_start, period_end, place, districts_current, districts_at_print}], flags}}}` (`fNNN` = 3 digits in order of appearance). Ticket 024 writes these to the Postgres `facts` table; the facts' sentences stay in the chunk text (grill A-33).
- **FR-4** - `uv run newsrag enrich stats --facts [--date D ...]` prints `facts_per_story=<x>` (mean over all stories of those dates, zero-fact stories included), `unit_<unit>=<n>` lines, `dropped_non_verbatim=<n>`, `dropped_unparsed=<n>`.
- **FR-5** - Clarifications (second review):
  - Scale from context: when `value_text` has no scale word, the code looks for a scale phrase in the table's header row/title (e.g. `(రూ. కోట్లలో)`) or within 40 characters before the number; else it takes the model's `scale` and adds flag `scale_from_model`.
  - Unit: the model's `unit` is used when the text has no unit marker; INR markers are `₹`, `రూ.`, `రూపాయలు`, `Rs`, `Rs.`, `INR`. `value_text` is the full span as printed, including any currency sign and scale word.
  - Periods: Telugu and English month names; `period_text` must be verbatim (else dropped); a relative or unparseable period keeps `period_text` and uses the event date for `period_start`/`period_end` with flag `period_unparsed`.
  - Facts get a `state` (resolved place state, or ticket 020's state mapping for a state name); ticket 024's `facts` table gains a `state` column.
  - NFR-1's judge sees the story input (6,000 chars) and the fact's value_text, value, scale, unit, refers_to, period and place (prompt `src/newsrag/prompts/facts_judge_v1.md`); all of those must be right.
  - `depends_on=("enrich_places",)` is enough: ticket 003's `[version, finished_at]` rule carries upstream reruns down.

## Non-functional requirements

- **NFR-1** - `uv run newsrag enrich verify-facts --n 50 --seed 13` samples 50 facts from all enriched dates and asks the judge model (`gemma3-27b`, text only) whether each is correct — value, unit/scale and refers_to must all be right; an unparseable judge reply counts as incorrect. It prints `correct: k/50`; k ≥ 45.

## Affected areas

- `src/newsrag/stages/enrich_facts.py`, `src/newsrag/enrich/facts.py`, `src/newsrag/prompts/enrich_facts_v1.md` (new)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_enrich_facts.py` (new)

## Don't touch

- Earlier enrich artifacts' fields.

## Out of scope

- Aggregating facts into answers (phase 2).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Fact shape | value, unit, scale, refers_to, date, place | grill A-33 |
| D-2 | Indian scales | lakh/crore normalised to absolute numbers | needed to compare amounts |
| D-3 | Guard | value must appear verbatim | prevents invented numbers (old system garbled numbers, F-6) |
| D-4 | Who computes the number | code parses `value_text`; the model only finds the span | prevents garbled numbers (grill F-6) |
| D-5 | Periods | `period_start`/`period_end` from the period text, default the event date | before/after-June-2024 and FY comparisons need periods, not days |

## Open questions

None.
