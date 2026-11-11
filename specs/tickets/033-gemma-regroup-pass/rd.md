# 033: Grouping retry for Gemma 4 - a second sample, not more resolution

**Status:** draft
**Mode:** solo
**Runs:** waits for 014: `group_model` is `gemma4-31b`

## Summary

Ticket 013 regroups a page that scores below `judge_threshold` by sending the image at 1.5x resolution
(3584 -> 5376 px). Since ticket 014, `group_model` is `gemma4-31b`, which caps every image at 1120 soft
tokens (its largest setting), so the bigger image gives it no extra detail. An experiment on the 20
dev-sample pages (2026-11-11, `evidence/`) found that no way of giving Gemma 4 more detail beats today's
retry, and neither does telling it what the judge found. The retry's gain comes from getting a second,
different answer and keeping the better-judged one. Today that only happens by accident (a different
resize changes the temperature-0 output). This ticket makes the second answer deliberate and checks
that it is worth what it costs.

## Evidence (evidence/, script `regroup_exp.py`, judge gemma4-31b, one shared prompt)

All variants grouped all 20 dev-sample pages; 8 pages scored < 0.8 with the plain grouping (`base`).
Retry gain = mean over the low pages of max(base, retry) - base; a null retry score never wins (013 FR-5),
so the all-ads page `2025-05-04_BGNOPRSVACLT_main_06` (a null score under halves) is left out: 7 pages.

| variant | what the model gets | mean alone (20 pages) | retry gain (7) | improved | still < 0.8 | s (20 pages, 16 in flight) |
|---|---|---|---|---|---|---|
| base | whole page, 3584 px (today's first pass) | 0.828 | - | - | 7 | 132 |
| retry5376 | same, 5376 px (today's retry) | 0.737 | +0.079 | 3 | 6 | 98 |
| halves | left/right half, each cropped at full resolution, two requests | 0.799 | +0.073 | 4 | 5 | 116 |
| multi2 | whole page + 2 half crops in one request (profile `image: 5`) | 0.744 | +0.069* | 5* | 5* | 144 |
| multi4 | whole page + 4 quadrant crops in one request | 0.787 | +0.092* | 4* | 6* | 190 |
| feedback | whole page + the judge's verdicts on the base grouping as a fix list | - | +0.063 | 3 | 5 | 56 (8 pages) |

`*` over all 8 low pages (before the null rule); see `evidence/summary-*.json` for every page.

Findings:
- Gemma 4's image budget: `max_soft_tokens` allows 70, 140, 280, 560 or 1120 (transformers
  `image_processing_gemma4.py`), so 1120 is the ceiling. retry5376's prompt tokens equal base's (max 6092).
- Sharper crops don't help: judge verdicts on the low pages are mostly `merged`, which is layout
  reasoning, not reading. Halves also cut stories at the seam (mean alone 0.799).
- All retries gain about +0.07 on low pages. Judge noise is about ±0.04 per page (ticket 014), so on 7 pages
  the variants can't be told apart.
- Throughput note for 031: `base` grouped 20 pages in 132 s with 16 requests in flight (6.6 s/page,
  0.15 pages/s), much better than 014's 5-page figure (31 s/page with 5 in flight).

## Functional requirements

- **FR-1** - `group_retry` sends the same request as `group` (same image size), with `temperature` =
  `group_retry_temperature` (new setting, default 0.5) and a fixed `seed` = `group_retry_seed` (default 1),
  so the second answer is deliberately different and reproducible. `judge_retry_scale` stays and applies
  only when it is not 1.0 (default changes to 1.0); both settings go into `group_retry`'s version hash.
- **FR-2** - Before switching, the experiment script (moved to `scripts/regroup_experiment.py`) measures on
  the dev sample's low pages: (a) today's retry, (b) FR-1 with temperature 0.3, 0.5 and 0.8, (c) best of
  2 samples at 0.5. Report: retry gain, pages improved, still < 0.8, extra seconds. FR-1's default
  temperature is the best of (b); if none beats (a) by more than 0.03, keep today's behaviour and only
  document it (no code change).
- **FR-3** - `judge stats` gains `retry_null` (count of retries whose score was null) so the null rule's
  effect is visible.

## Non-functional requirements

- **NFR-1** - No change to ticket 012's prompt or parsing; `group_final` artifacts keep their schema.

## Affected areas

- `src/newsrag/stages/group_retry.py`, `src/newsrag/stages/group.py` (`make_create` options),
  `src/newsrag/config.py` (`# --- judge ---`), `src/newsrag/grouping/judge_cli.py`,
  `scripts/regroup_experiment.py` (new), `tests/test_judge.py`

## Don't touch

- `group` stage version and outputs; the bake-off (`src/newsrag/grouping/bakeoff.py`).

## Out of scope

- Prompt tuning; tiled or multi-image grouping (measured above: no gain); a different judge.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | More detail for Gemma 4 | not pursued | no variant beat today's retry (evidence above) |
| D-2 | What the retry should be | a deliberate second sample (temperature + seed), measured first | the gain comes from a second answer, not from resolution |

## Open questions

- Is a +0.07 gain on about 40% of pages worth one extra grouping request per low page at full-corpus scale?
  FR-2's numbers answer it; the user decides.
