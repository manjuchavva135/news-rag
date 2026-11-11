# 016: Continuation stitching (per date)

**Status:** ready
**Mode:** solo
**Runs:** waits for 013: needs final article groups

## Summary

Lead stories often continue on another page ("మిగతా 9వ పేజీలో" / "మొదటి పేజీ తరువాయి"). The old system only
flagged these, so front-page stories were incomplete. This per-date stage finds each continuation and
joins the parts into one article; citations can point at both page locations (grill D-10, A-27, A-43).

## Functional requirements

- **FR-1** - `continuations.yaml` (repo root) lists regexes for (a) "continues on page N" markers and (b) "continued from page N / front page" markers, each with a named group `page` where a number is given. Initial patterns include the old ones (`మొదటి పేజీ తరువాయి`, `పేజీ తరువాయి`, from `/home/admin1/RAG/config/settings.py`) plus `మిగతా\s*(?P<page>\d+)\s*వ?\s*పేజీలో` and `(?P<page>\d+)\s*వ?\s*పేజీ\s*తరువాయి`. The worker extends the list after looking at the dev-sample text and records each added pattern in `results.md`.
- **FR-2** - DateStage `stitch` (`depends_on=("group_final",)`, `version="stitch-v1-<first 8 hex of sha1(continuations.yaml + stitch_min_score + stitch_ambiguity_margin)>"`). Only articles of kind `news`, `editorial`, `brief_item` take part. Markers are searched in each article's text and in its `continuation_marker` blocks (text loaded from `ocr_final` by `block_id`). A `continuation_marker` block that the grouping left `unassigned` or in a non-eligible article is attached to the eligible article whose union box is nearest (edge-to-edge ≤ 100 px). An article with an (a) marker is a *source part*; with a (b) marker a *continuation part*; one article can be both (a chain). Front-page wording without a number means page 1. An (a) marker without a page number searches every later page of the date and section.
- **FR-3** - Matching: a source part on page P with target N may link to continuation parts on pages with `page_num = N` or `page_num ≤ N ≤ page_end` (spreads), of the same date and section, with edition overlap: codes made only of letters (not `ALL`/`FH`) overlap if they share a letter; `ALL`, `FH` and `-VK` variant codes overlap with every code, on either side. Score = Jaccard overlap of word sets (Telugu tokens ≥ 2 chars) between the source's headline + last 300 chars and the candidate's headline + first 300 chars, +0.2 when the candidate's (b) marker names page P (`method: page_ref`; otherwise `similarity`). Assignment is greedy over all (source, candidate) pairs of the date in descending score: a pair is linked if score ≥ `stitch_min_score` (0.15), the source has no link yet, and the candidate is not yet linked to a source of the same edition code (full code string). A link whose source had a second candidate within `stitch_ambiguity_margin` (0.02) gets flag `continuation_ambiguous`. Reverse match: a continuation part whose (b) marker names page P but no source on P has an (a) marker is matched to the best eligible article on P by the same score (`method: reverse`). Chains (P → N → M) are followed.
- **FR-4** - Artifact per date: `{pub_date, links:[{source_article_id, continuation_article_id, score, method: "page_ref"|"similarity"|"reverse", flags}], unmatched:[{article_id, marker, reason: "no_target_page"|"no_candidate"|"below_score"|"no_source"}]}` (`no_target_page` = page N does not exist for that date; `no_source` = a continuation part nobody linked). Unmatched parts stay separate articles; `stitched_articles` (FR-5) adds flag `low_confidence:continuation_unmatched` to them at read time (the `group_final` artifacts are not changed) (grill A-27, D-20).
- **FR-5** - Helper `newsrag.stitch.stitched_articles(pub_date) -> list[Article]`, where `Article` (a dataclass in `src/newsrag/stitch/model.py`) has the ticket-012 article fields plus `page_id, edition_code, section, page_num, parts`. It returns all the date's articles (all kinds) with each linked chain merged into one article: id, `kind`, `headline` = the first part's; `text` = the parts' texts in chain order joined by "\n\n" with marker phrases removed (continuation parts' own headlines are dropped); `flags` = union of the parts' flags plus the link flags; `parts:[{article_id, page_id, bbox_union}]` in chain order. Other articles are returned with `parts` of length 1.
- **FR-6** - `uv run newsrag stitch stats [--date D]` prints one line each: `source_markers=<n>`, `continuation_markers=<n>`, `linked=<n>`, `unmatched_<reason>=<n>` for each reason, `ambiguous=<n>`.
- **FR-7** - Clarifications (second review):
  - One link per continuation part **per date** (global, not per edition): the highest-scoring source wins; other sources pointing at it get unmatched reason `taken`. Ticket 017 therefore never sees the same continuation inside two units.
  - Reverse match runs after the greedy pass, only for continuation parts still unlinked whose (b) marker names page P, against eligible articles on P that are not yet linked as sources and whose own (a) markers (if any) don't point elsewhere; the same score threshold and edition-overlap rule apply.
  - The merged article's `page_id`, `edition_code`, `section`, `page_num` are the first part's.
  - An unassigned `continuation_marker` block with no eligible article within 100 px is ignored and counted as `orphan_markers` in `stitch stats`.

## Non-functional requirements

- **NFR-1** - Link quality: `uv run newsrag stitch verify --n 20 --seed 3` gives the local text LLM (`qwen3-30b-a3b`) 20 random links (source end + continuation start) and asks "Are these two parts of the same news story? yes/no"; ≥ 18 of 20 must be `yes`.

## Affected areas

- `continuations.yaml` (new); `src/newsrag/stages/stitch.py`, `src/newsrag/stitch/` (new)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_stitch.py` (new; synthetic articles)

## Don't touch

- `group_final` artifacts.

## Out of scope

- Cross-date continuations; stories that continue in another edition only.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Stitch or link | stitch into one article; parts keep page locations | grill D-10 |
| D-2 | Matching | marker + page number on the same date, then headline/keyword similarity | grill A-27 |
| D-3 | No match | separate part, flagged | grill A-27, D-20 |
| D-4 | When it runs | per date, after all its pages are grouped | grill A-43 |

## Open questions

None.
