# Milestone 1: answer accuracy on the two processed months

**Status:** draft for approval (2026-11-16)
**Corpus:** print dates 2026-10-01 to 2026-11-10 (1,041 pages grouped, 39 dates indexed, 7,521
stories). The few 2024–25 dates in the index are left out of every test.

## Intent

Prove, with numbers, how accurate the answers are on the two months already processed, and raise
that accuracy before more years are processed (2 years next, then more). Staff on AWS see the
system only after this milestone; until then every judgement is made by AI models, and people
check the result only after deployment.

## What we found (the starting point)

From `reports/answers-2026.json` (300 model-written questions, 100 per script) and
`reports/short-2026.json` (short, staff-style queries):

1. **The grader is lenient.** Sarvam-M graded answers that only say "the sources do not cover
   this" as correct, against its own prompt: 11 of 20 such answers (reasoning off), 23 of 47
   (reasoning on). Counted as wrong, accuracy is 81.7% (Telugu 93, English 76, Romanized 76), not
   the reported 85.3%; with reasoning on it is 74.0%.
2. **The test questions are easier than real ones.** They are long and copy names and numbers from
   the story. Real staff questions are 1–6 words for 68% of queries and 47% are in Latin letters.
   On short queries, Latin-letter story search has recall@10 0.69 and a Latin-letter name finds
   stories that mention the person only 46% of the time (Telugu: 96%).
3. **Causes of the wrong and partial answers (reasoning off, 44 of 300):** the source story was
   not among the 12 sources in 25; the story was there but the answer gave up or missed facts in
   19. 11 answers loop (one sentence repeated), 7 of them graded correct. All 100 English
   questions were answered in Telugu (the eval prompt says "Write the answer in Telugu"). Ads and
   notices appear as sources (26% of top-10 hits for real staff questions are not news).
4. **The reasoning model is worse** (74.0% vs 81.7%, 47 refusals vs 20) and is the product
   default (`answer_thinking = True`).
5. **The eval does not test the product.** `evals/answers.py` uses `answer_check_v1.md`; the chat
   service (`answer/service.py`) uses `answer_chat_v1.md`, its own rewrite step and its own
   settings.
6. **Article grouping is unverified.** Mean judge score 0.72, 642 of 1,200 pages below 0.8, retry
   mean 0.66 with 495 of 635 still below; but the judge is the grouping model itself (gemma4-31b).

## Goals

- A trustworthy, AI-only yardstick for the two months, frozen and versioned, that every change is
  measured against.
- Raise answer accuracy to the exit bars below, for all three scripts, on staff-style questions.
- Know whether article grouping is a real loss, from judges that did not do the grouping.

## Non-goals

- Human labelling (comes after AWS deployment).
- Processing more dates or years; storage or hosting changes; AWS deployment.
- RAPTOR, a ColBERT index, HyDE (an experiment at most, later).

## Anti-goals (a failure even if the numbers rise)

- A score that rises because the grader or the test got easier, not because answers got better.
- Telugu accuracy falling while English and Romanized rise.
- Answers slower than the time budget below.
- Answers that cite sources for facts the sources do not state.

## Exit bars (measured by the scoreboard on the frozen yardstick)

| Measure | Now | Bar |
|---|---|---|
| Answer facts correct, refusal = wrong, per script (te / en / te-Latn) | 93 / 76 / 76 | ≥ 90 each |
| Answer facts correct on the short staff-style set, per script | not measured | ≥ 85 each |
| Right story in the top 10, long set, per script | 95 / 76 / 84 | ≥ 90 each |
| Right story in the top 10, short set, Latin letters | 69 | ≥ 85 |
| Latin-letter name: hits that mention the person | 46 | ≥ 85 |
| Top-10 hits that are notices / ads (real staff questions) | 26 | ≤ 5 |
| Claims supported by their cited sources | 93 | ≥ 95 |
| Answers that give the event date when the question is about an event | not measured | ≥ 95 |
| Answers in the language of the question | 0 (English) | ≥ 98 |
| Looping answers | 11 / 300 | 0 |
| Grouping: mean score of the two independent judges | unknown | ≥ 0.85 |

Time budget (my call, open to change): a fact question answers in ≤ 15 s; list and timeline
questions in ≤ 60 s.

## Approaches considered

- **A. Measure first, then fix (chosen, with an AI-only yardstick).** Fix the grader and build
  the scoreboard first, then the answer-step and search fixes, judged on the scoreboard; regroup
  pages only if independent judges show grouping is the big loss. Slower start; every later change
  is proven.
- **B. Search and answer fixes first, measured by the current grader.** Faster visible gains, but
  the current grader passes refusals, so gains and noise cannot be told apart.
- **C. Regroup the pages first.** Spends GPU days before knowing whether grouping is the problem.

## Design

### 1. The yardstick (first)

- **Graders.** Sarvam-M is dropped. Candidates: gemma4-31b, gemma3-27b, a new non-Qwen,
  non-Gemma text model (my call: Llama-3.3-70B-Instruct in FP8), and Sarvam-M as a control.
  Each candidate grades a **trap set** built automatically from the corpus, where the right label is
  known by construction: the story's own key sentence (correct), a refusal (wrong), the same
  sentence with a number or name changed (wrong), half the asked facts dropped (partial), a fluent
  answer from a different story (wrong). The two best candidates from different model families that
  score ≥ 95% on the traps become graders; neither may share a family with the answer model.
- **Rules before the graders.** A refusal when the question was written from a story in the corpus
  is wrong; a looping answer is flagged; an answer in the wrong language is flagged. These are
  code, not model calls.
- **Agreement.** An answer is correct only when both graders say so; disagreements are reported as
  "uncertain" with their count. A 10% sample is graded twice to measure each grader's
  self-consistency.
- **Test sets (frozen, versioned in `data/eval/`):**
  - the existing 300 long questions;
  - a new short staff-style set: 300 questions of 1–6 words, 100 per script, written from stories
    in the two months, styled on the 165 real staff questions, with name-only and "latest" queries
    included;
  - the 165 real staff questions, with relevance of the pooled top-10 hits (from every search
    variant under test) judged by the two graders;
  - the 30 customer questions (claim support).
- **The eval runs the product.** Answers are produced by `answer.service.ask` with the product
  settings; the separate eval prompt is retired.
- **Scoreboard.** One command, `newsrag eval scoreboard`, runs every test above and writes one
  report (Markdown + JSON) with every exit-bar measure, per script, next to the previous run, so a
  change shows its gain or regression at a glance.

### 2. Answer-step fixes

- Answer in the language of the question (the chat service already decides it; the eval used a
  fixed Telugu prompt).
- Reasoning off by default (`answer_thinking = False`), unless the scoreboard shows otherwise.
- Loop guard: stop and retry an answer whose sentences repeat (detected in code), with a stronger
  repetition setting on the retry.
- No give-up when the answer is in the sources: if the model refuses but a source shares the
  question's key terms, ask once more with the top 3 sources only.
- Optional comparison on the scoreboard: Claude on Bedrock as the answer model (allowed by D-2),
  to show how much of the gap is the answer model.

### 3. Search fixes (existing tickets, in dependency order)

035 filters with notices off by default → 039 one source per story → 036 names through the alias
table → 040 event dates in sources → 037 cross-encoder reranker → 038 Telugu form of Latin-letter
questions searched with the typed form (multi-query with fusion) → 041 list / timeline / latest
routing. Each ticket's result is a scoreboard run.

### 4. Corrective retry

When the graded-in-code check finds the sources do not cover the question (no source shares the
question's key terms or names), search once more with wider dates, the other script and a larger
`k` before answering. Measured by the scoreboard; kept only if it raises accuracy within the time
budget.

### 5. Grouping audit (runs on the GPU while sections 2–3 are coded)

The two independent judges (vision models from families other than Gemma, e.g. Qwen3-VL-32B and
InternVL3.5-38B; the trap idea applies here as well, with deliberately broken groupings) score the
1,041 pages. If both agree that more than 15% of pages are badly grouped, and the scoreboard's
misses trace to those pages, a regroup design follows as its own decision; otherwise grouping is
left as is for milestone 1.

## Constraints

- One GPU, one model loaded at a time: grader, judge and answer passes are scheduled one after
  another; the scoreboard groups calls by model.
- Every new setting goes into `config.py`; every prompt and test set is versioned so the
  yardstick can be frozen.
- Package layering (`tests/test_layers.py`) holds.

## Left to the builder

Exact trap counts per type, prompt wording, the scoreboard's report layout, and the order of
calls inside a scoreboard run.
