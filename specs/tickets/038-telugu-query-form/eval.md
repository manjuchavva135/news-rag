# 038: Search Latin-letter questions in Telugu too (transliterate or translate) - Evaluation

## Setup

- Prerequisite: tickets 035, 036, 037, 039 and 040 are merged; `uv run newsrag answer init` was run.
- Start: `export PATH=$HOME/.local/bin:$PATH && uv sync && uv run newsrag stores up && uv run newsrag models status` (prints `GPU free`)
- Base URL: n/a
- Test account: n/a
- Seed / reset: none. `T=specs/tickets/038-telugu-query-form/evidence`

## Automated checks

- `uv run ruff check . && uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

- Every `pytest -k` selection below must run at least one test (pytest exit code 5, "no tests ran", is a failure)
- `cat specs/tickets/038-telugu-query-form/evidence/base-commit.txt` prints one commit hash (the base commit, see rd.md "Affected areas")

## Acceptance checks

### E-1: Which form a query gets (covers FR-1, NFR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_telugu_form.py`
  2. `grep -c "{query}" src/newsrag/prompts/translate_query_v1.md; grep -c "ఆక్వా మేత ధరలు" src/newsrag/prompts/translate_query_v1.md`
- **Expected:** step 1 passes with a fake `ask`, including: a Telugu-script query gives `how="none"`, `text=None` and no call; `Tirumala lo darshanam tokens eppudu istharu` gives `how="translit"` and uses the `translit_v1.md` prompt; `Who won the Jubilee Hills by-election?` and `chandra babu` give `how="translate"` and use `translate_query_v1.md`; a reply in Latin letters is asked again and after exactly 3 calls gives `text=None`, `failed=True`; an unparseable reply counts as a try; an `ask` that raises counts as a try and gives `failed=True` after 3. Step 2 prints `1` and `1`.

### E-2: Several query texts in one search (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_search.py -k "also"`
- **Expected:** all pass, including: with one `also` text four text rankings (dense and BM25 for each text) plus the entity ranking are fused; both texts get the same filters; names are resolved on the typed query only; the fake reranker receives the `also` text; `also=()` sends the same requests as before.

### E-3: The answer path (covers FR-3, FR-4, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_answer.py -k "telugu_form or also"`
  2. `uv run newsrag answer ask "Who won the Jubilee Hills by-election?" | grep -E "^search \(te, translate\): "`
  3. `uv run newsrag answer ask "జూబ్లీహిల్స్ ఉప ఎన్నికలో ఎవరు గెలిచారు?" | grep -c "^search (te"`
  4. `uv run newsrag search "Tirumala lo darshanam tokens eppudu istharu" --telugu-form --k 3 | head -1`
- **Expected:** step 1 passes, including: a Latin-letter question makes one extra `complete` call with `thinking=False` and `max_tokens=256` and the search gets `also=[<form>]`; a Telugu question makes no extra call; a failed form searches as typed; `search_telugu_form=False` makes no call. Step 2 prints one line with Telugu-script text after the colon. Step 3 prints `0`. Step 4 prints a line starting `telugu (translit): ` followed by Telugu-script text.

### E-4: Before/after numbers and the guard (covers FR-5, FR-7, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `ls $T/before-retrieval.json`
  2. `uv run newsrag eval retrieval --set data/eval/retrieval-2026.jsonl --translit --telugu-form --out $T/after-retrieval.json`
  3. `python3 -c "import json;b=json.load(open('$T/before-retrieval.json'));a=json.load(open('$T/after-retrieval.json'));x=b['by_lang']['te']['recall_at_10'];y=a['by_lang']['te']['recall_at_10'];print(f'before={x:.4f} after={y:.4f}','GUARD OK' if y>=x-0.02 else 'GUARD FAIL');print(sorted(a['by_lang']));[print(l,a['by_lang'][l]['n'],a['by_lang'][l]['recall_at_1'],a['by_lang'][l]['recall_at_10']) for l in ('en','en_both','te_latn','te_latn_translit','te_latn_both')];q=a['per_question'];print(sum(1 for p in q if p.get('telugu_form_failed')), all('rank_both' in p for p in q if p['lang'] in ('en','te_latn')))"`
  4. `grep -cE "en_both|te_latn_both" specs/tickets/038-telugu-query-form/results.md`
- **Expected:** step 1 lists the file; step 3 prints `GUARD OK`, then `['en', 'en_both', 'te', 'te_latn', 'te_latn_both', 'te_latn_translit']`, five lines each with n = 100 and two values (any values), then `<failed count> True`; step 4 prints a number ≥ 2.

## Edge cases

### E-5: Models are never held together (covers FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_eval_retrieval.py -k "telugu_form"`
  2. `uv run newsrag models status`
- **Expected:** step 1 passes, including a test whose fake `use` raises on a second profile while the first is open, one where `--telugu-form` and `--translit` together fill both `te_latn_translit` and `te_latn_both` with one model call per Romanized question, one where a failed form gives `rank_both == rank`, and one where `--store baseline --telugu-form` exits 2 with `--telugu-form needs --store new`; step 2 prints `GPU free`.

### E-6: Nothing else changed (covers Don't touch)

- **Type:** CLI
- **Steps:**
  1. `git diff --stat $(cat specs/tickets/038-telugu-query-form/evidence/base-commit.txt) -- src/newsrag/search/translit.py src/newsrag/prompts/translit_v1.md src/newsrag/prompts/answer_rewrite_v1.md src/newsrag/answer/threads.py src/newsrag/answer/sql data/eval src/newsrag/baseline`
  2. `uv run newsrag search "Tirumala lo darshanam tokens eppudu istharu" --translit --k 3 | head -1`
- **Expected:** step 1 prints nothing; step 2 prints a line starting `translit: ` (the 034 option still works).

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1 |
| FR-2 | E-2 |
| FR-3 | E-3 |
| FR-4 | E-3, E-5 |
| FR-5 | E-4, E-5 |
| FR-6 | E-3 |
| FR-7 | E-4 |
| FR-8 | E-1, E-3, E-4, E-5 |
| NFR-1 | E-4 |
| NFR-2 | E-3 |
| NFR-3 | E-1, E-3 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (`before-retrieval.json`, `after-retrieval.json`, command outputs) saved in `evidence/` and listed in `results.md`
