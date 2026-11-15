# 036: Resolve names through the alias table before searching - Evaluation

## Setup

- Prerequisite: ticket 035 is merged; `uv run newsrag aliases stats` prints entity counts (the alias table exists).
- Start: `export PATH=$HOME/.local/bin:$PATH && uv sync && uv run newsrag stores up && uv run newsrag models status` (prints `GPU free`)
- Base URL: n/a
- Test account: n/a
- Seed / reset: none. `T=specs/tickets/036-name-alias-resolution/evidence`

## Automated checks

- `uv run ruff check . && uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

- Every `pytest -k` selection below must run at least one test (pytest exit code 5, "no tests ran", is a failure)
- `cat specs/tickets/036-name-alias-resolution/evidence/base-commit.txt` prints one commit hash (the base commit, see rd.md "Affected areas")

## Acceptance checks

### E-1: Resolution rules (covers FR-1, FR-2, FR-5, NFR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_search_names.py`
- **Expected:** all pass, on a fake `entities.json` in a temporary path, including at least: an entity with `canonical_te` null gives its `canonical`; `a b c` with names `a b` and `b c` resolves `a b` only (left position first); a fuzzy key mapping to 4 entities is not a candidate; a Latin spelling resolves to the entity whose Telugu alias folds to the same key (`exact=True`, score 100); a two-word span wins over its one-word parts and its words are not reused; a one-letter-off spelling of a 6+ character key resolves with `exact=False`; the same typo on a 5-character key does not resolve; a key mapping to 4 entities is skipped and one mapping to 3 is returned ordered by `story_count`; keys shorter than 3 characters or without a letter are not indexed; a missing `entities.json` gives `[]`; the index is rebuilt when the file's stamp changes.

### E-2: The real alias table (covers FR-1, FR-2, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m data tests/test_search_names.py`
- **Expected:** passes, not skipped: on the real `entities.json`, `resolve("chandra babu")` returns one match whose `canonical` is `చంద్రబాబు`; the index build took ≤ 10 s and a 12-word query ≤ 50 ms (the test asserts both).

### E-3: Search uses the names (covers FR-3, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_search.py -k "names or detailed or weights"`
  2. `uv run newsrag search "chandra babu" --k 10 | head -1`
  3. `uv run newsrag search "chandra babu" --k 10 | tail -10 | grep -c "చంద్రబాబు"`
  4. `uv run newsrag search "chandra babu" --k 10 --no-names | grep -c "^names:"`
- **Expected:** step 1 passes, including: `query_text` gets the canonical appended once and not when it is already in the query; the third ranking is searched with the `entity_ids` filter and fused with `search_name_rrf_weight`; `rrf_fuse` without `weights` returns the same order as before; no match means exactly two rankings; a caller's `entity_ids` filter gives two rankings with the name still appended; `store="baseline"` receives no `resolve_names`; `resolve_names=False` resolves nothing. Step 2 prints a line starting `names: chandra babu -> చంద్రబాబు (e_`. Step 3 prints a number ≥ 5 (headlines naming him). Step 4 prints `0`.

### E-4: Before/after numbers and the guard (covers FR-6, FR-7, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `ls $T/before-retrieval.json $T/before-short.json`
  2. `uv run newsrag eval retrieval --set data/eval/retrieval-2026.jsonl --out $T/after-retrieval.json && uv run newsrag eval short --out $T/after-short.json`
  3. `python3 -c "import json;b=json.load(open('$T/before-retrieval.json'));a=json.load(open('$T/after-retrieval.json'));x=b['by_lang']['te']['recall_at_10'];y=a['by_lang']['te']['recall_at_10'];print(f'before={x:.4f} after={y:.4f}','GUARD OK' if y>=x-0.02 else 'GUARD FAIL')"`
  4. `python3 -c "import json;b=json.load(open('$T/before-short.json'));a=json.load(open('$T/after-short.json'));[print(f,b['names'][f]['mention'],a['names'][f]['mention']) for f in ('te','latin','typo')]"`
  5. `uv run newsrag config show | grep search_name_rrf_weight; grep -c "search_name_rrf_weight" specs/tickets/036-name-alias-resolution/results.md`
- **Expected:** step 1 lists both files; step 3 prints `GUARD OK`; step 4 prints three lines of before and after values (any values); step 5 prints the weight (1.0, 0.5 or 0.25) and a number ≥ 1.

## Edge cases

### E-5: Queries that must not resolve (covers FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag search "reddy" --k 3 | grep -c "^names:"`
  2. `uv run newsrag search "ab" --k 3 | grep -c "^names:"`
- **Expected:** both print `0` (a surname shared by more than 3 entities; a 2-letter word).

### E-6: Nothing else changed (covers Don't touch)

- **Type:** CLI
- **Steps:**
  1. `git diff --stat $(cat specs/tickets/036-name-alias-resolution/evidence/base-commit.txt) -- src/newsrag/aliases src/newsrag/baseline src/newsrag/answer data/eval`
  2. `uv run newsrag search "టెండర్ నోటీసు" --k 10 | awk '{print $5}' | grep -c notice_tender`
- **Expected:** step 1 prints nothing; step 2 prints `0` (ticket 035's default kinds still apply).

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-2 |
| FR-2 | E-1, E-2, E-5 |
| FR-3 | E-3, E-5 |
| FR-4 | E-3 |
| FR-5 | E-1, E-4 |
| FR-6 | E-4 |
| FR-7 | E-4 |
| FR-8 | E-1, E-3, E-4 |
| NFR-1 | E-4 |
| NFR-2 | E-2 |
| NFR-3 | E-1 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (the `before-`/`after-` JSON files of every weight tried, command outputs) saved in `evidence/` and listed in `results.md`
