# 039: Answer from whole stories, one source per story - Evaluation

## Setup

- Prerequisite: everything in the main checkout is committed: `git -C /home/admin1/news-rag status --short -- src tests data/eval models.yaml` prints nothing (the answer package, its settings block in `config.py`, its `SUB_APPS` line, the prompts, `evals/short.py` and the eval sets must be in the commit the worktree starts from). Also `uv run newsrag answer init` was run.
- Start: `export PATH=$HOME/.local/bin:$PATH && uv sync && uv run newsrag stores up && uv run newsrag models status` (prints `GPU free`)
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check . && uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

- Every `pytest -k` selection below must run at least one test (pytest exit code 5, "no tests ran", is a failure)
- `cat specs/tickets/039-story-sources/evidence/base-commit.txt` prints one commit hash (the base commit, see rd.md "Affected areas")

## Acceptance checks

### E-1: Collapse, text, budget and numbers (covers FR-2, FR-3, FR-4, FR-5, FR-6, FR-8, NFR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_answer.py -k "story or stories or budget or number or carried or source"`
- **Expected:** all pass, with dict stories and fake hits, including at least: two hits of a story that was not loaded give one source cut to `answer_source_chars`; after a source that does not fit, a later smaller one is not added and no number is skipped; a chunk absent from `chunk_kinds` is not appended; two hit chunks of one story give one source at the rank of the better hit; 12 stories are kept from 30 hits; a story of 6,000 characters is sent whole and one of 6,001 gives its hit chunks in `seq` order joined by `\n[…]\n`; a hit chunk of kind `table` is appended once and not when its text is already in the story; sources stop at `answer_sources_tokens` and a first source over the budget is cut to it; a hit without `story_id` and a hit whose story is missing stay chunk sources cut to `answer_source_chars`; a story cited as `[3]` in an earlier turn (stored as a chunk record) is `[3]` again and is not carried twice; two earlier records of one story give the lower number; `record()` contains `kind`.

### E-2: Story loader on real tables (covers FR-1, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_answer_stories.py`
- **Expected:** all pass, none skipped: in a throw-away warehouse schema a story with two printings on pages of two editions returns both edition names sorted, its `canonical_page_id`, ISO `pub_date` and `event_date`; an unknown id is absent; the test counts ≤ 2 SQL statements for 50 ids.

### E-3: The sources of a real question (covers FR-2, FR-3, FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag answer sources "చంద్రబాబు" > /tmp/e3-039.txt; tail -1 /tmp/e3-039.txt`
  2. `grep -cE "^\[[0-9]+\]" /tmp/e3-039.txt`
  3. `grep -E "^\[[0-9]+\]" /tmp/e3-039.txt | sed -E 's/^\[[0-9]+\] //; s/  \([0-9]+ chars.*$//' | sort | uniq -d | wc -l`
  4. `grep -oE "\(([0-9]+) chars" /tmp/e3-039.txt | grep -oE "[0-9]+" | sort -n | tail -1`
  5. `uv run newsrag models status`
- **Expected:** step 1 prints `sources: <n>  est_tokens: <t>` with n ≤ 12 and t ≤ 16000; step 2 prints the same n (≥ 1); step 3 prints `0` (no two sources with the same header, i.e. no story twice); step 4 prints a number greater than 1500 (a whole story, not a 1,500-character cut) unless every story found is shorter, in which case `results.md` shows one source's length next to `select length(text) from stories` for that story, equal; step 5 prints `GPU free`.

### E-4: An answer still works end to end (covers FR-2, FR-6)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag answer ask "జూబ్లీహిల్స్ ఉప ఎన్నికలో ఎవరు గెలిచారు?" | tee /tmp/e4-039.txt | tail -1`
  2. `ID=$(tail -1 /tmp/e4-039.txt | awk '{print $2}'); uv run newsrag answer ask "ఎన్ని ఓట్ల తేడాతో?" -t $ID | tail -1`
- **Expected:** step 1 ends with `thread <id> turn 1` and the output above it has an answer with at least one `[n]` citation or one sentence saying the sources do not cover it; step 2 ends with `thread <same id> turn 2`. (Uses `answer_provider`; with `local` the answer model is loaded and released by the command.)

## Edge cases

### E-5: Unknown thread, no hits (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag answer sources "x" --thread nope; echo exit=$?`
  2. `uv run pytest -q tests/test_answer.py -k "no_hits"`
- **Expected:** step 1 prints `no thread nope` and `exit=1`; step 2 passes (no hits still answers, with `(no sources were found for this question)` sent to the model).

### E-6: Nothing else changed (covers NFR-1, Don't touch)

- **Type:** CLI
- **Steps:**
  1. `git diff --stat $(cat specs/tickets/039-story-sources/evidence/base-commit.txt) -- src/newsrag/search src/newsrag/evals src/newsrag/answer/threads.py src/newsrag/answer/sql src/newsrag/prompts/answer_chat_v1.md`
  2. `uv run pytest -q tests/test_eval_answers.py tests/test_answer.py -k "pg_store or customer or grade"`
- **Expected:** step 1 prints nothing; step 2 passes.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2 |
| FR-2 | E-1, E-3, E-4 |
| FR-3 | E-1, E-3 |
| FR-4 | E-1, E-3 |
| FR-5 | E-1 |
| FR-6 | E-1, E-4 |
| FR-7 | E-3, E-5 |
| FR-8 | E-1 |
| FR-9 | E-1, E-3 |
| NFR-1 | E-6 |
| NFR-2 | E-2 |
| NFR-3 | E-1 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (`e3-039.txt`, `e4-039.txt`, command outputs) saved in `evidence/` and listed in `results.md`
