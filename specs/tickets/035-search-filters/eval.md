# 035: Search filters on the indexed fields, notices off by default - Evaluation

## Setup

- Prerequisite: everything in the main checkout is committed: `git -C /home/admin1/news-rag status --short -- src tests data/eval models.yaml` prints nothing (the answer package, its settings block in `config.py`, its `SUB_APPS` line, the prompts, `evals/short.py` and the eval sets must be in the commit the worktree starts from).
- Start: `export PATH=$HOME/.local/bin:$PATH && uv sync && uv run newsrag stores up && uv run newsrag models status` (prints `GPU free`)
- Base URL: n/a
- Test account: n/a
- Seed / reset: none. `T=specs/tickets/035-search-filters/evidence`

## Automated checks

- `uv run ruff check . && uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

- Every `pytest -k` selection below must run at least one test (pytest exit code 5, "no tests ran", is a failure)
- `cat specs/tickets/035-search-filters/evidence/base-commit.txt` prints one commit hash (the base commit, see rd.md "Affected areas")

## Acceptance checks

### E-1: Filters are validated and sent to both stores (covers FR-1, FR-2, FR-3, FR-4, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_search.py -k "filter or kinds or default"`
- **Expected:** all selected tests pass, including at least: each key of FR-1 produces the Qdrant condition and the OpenSearch clause of its row; two keys are ANDed; a string for a list key, an empty list, an unknown kind, an unknown category, a bad date and `date_from > date_to` raise the exact messages of FR-1/FR-2; no `kinds` gives the `search_default_kinds` filter; `kinds=["all"]` with nothing else gives a `None` Qdrant filter and the pre-ticket `bm25_body` query part; an empty `search_default_kinds` gives no kind filter; `kinds=["all","news"]` raises `filter kinds: all cannot be combined with other kinds`; `{"kinds": None}` behaves as no `kinds`; `search(store="baseline", filters={"kinds": ["news"]})` raises `baseline store takes only the page_ids filter` and `{"kinds": ["all"]}` does not; `run_retrieval` without `kinds` searches with `["all"]`; `hit_line` prints `-` for an empty kind.

### E-2: Filters against real stores (covers FR-1, FR-4, FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m stores tests/test_search.py`
- **Expected:** all pass, none skipped, including tests that index chunks of two kinds, two dates, two states and two entities into a throw-away generation and get only the matching chunks for `kinds`, `date_from`/`date_to`, `states` and `entity_ids`, with `Hit.kind` filled.

### E-3: The command line (covers FR-3, FR-6, FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag index ensure-payload-indexes`
  2. `uv run newsrag search "టెండర్ నోటీసు" --k 10 | awk '{print $5}' | grep -c notice_tender`
  3. `uv run newsrag search "టెండర్ నోటీసు" --k 10 --kind notice_tender | awk '{print $5}' | sort -u`
  4. `uv run newsrag search "ముఖ్యమంత్రి" --k 10 --from 2026-11-01 --to 2026-11-05 | awk '{print $3}' | sort | sed -n '1p;$p'`
  5. `uv run newsrag search "ముఖ్యమంత్రి" --k 10 --kind all | wc -l`
- **Expected:** step 1 prints a line ending `: payload indexes ok`; step 2 prints `0`; step 3 prints `notice_tender` only; step 4 prints two dates, both between `2026-11-01` and `2026-11-05`; step 5 prints `10`.

### E-4: Before/after numbers and the guard (covers FR-8, FR-9, NFR-1, NFR-3)

- **Type:** CLI
- **Steps:**
  1. `ls $T/before-retrieval.json $T/before-short.json`
  2. `uv run newsrag eval retrieval --set data/eval/retrieval-2026.jsonl --out $T/after-retrieval.json && uv run newsrag eval short --out $T/after-short.json`
  3. `python3 -c "import json;b=json.load(open('$T/before-retrieval.json'));a=json.load(open('$T/after-retrieval.json'));x=b['by_lang']['te']['recall_at_10'];y=a['by_lang']['te']['recall_at_10'];print(f'before={x:.4f} after={y:.4f}','GUARD OK' if y>=x-0.02 else 'GUARD FAIL');print('TIME OK' if a['median_search_ms']<=2*b['median_search_ms'] else 'TIME FAIL')"`
  4. `python3 -c "import json;b=json.load(open('$T/before-short.json'));a=json.load(open('$T/after-short.json'));[print(k,b['real'][k]['other_kind'],a['real'][k]['other_kind']) for k in ('all','person')]"`
  5. `grep -c "other_kind" specs/tickets/035-search-filters/results.md`
- **Expected:** step 1 lists both files; step 3 prints `GUARD OK` and `TIME OK`; step 4 prints two lines of before and after values (any values); step 5 prints a number ≥ 1.

## Edge cases

### E-5: Bad option values (covers FR-2, FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag search "x" --kind foo; echo exit=$?`
  2. `uv run newsrag search "x" --from 2026-13-01; echo exit=$?`
  3. `uv run newsrag search "x" --from 2026-11-05 --to 2026-11-01; echo exit=$?`
- **Expected:** step 1 prints `unknown kind foo (known: news, editorial, brief_item, notice_tender, table_only, table, infographic, all)` and `exit=2`; step 2 prints `--from must be YYYY-MM-DD` and `exit=2`; step 3 prints `--from is after --to` and `exit=2`.

### E-6: Nothing else changed (covers Don't touch)

- **Type:** CLI
- **Steps:**
  1. `git diff --stat $(cat specs/tickets/035-search-filters/evidence/base-commit.txt) -- src/newsrag/baseline src/newsrag/stages/index.py src/newsrag/answer data/eval`
  2. `uv run newsrag stage status | grep -E "^index"` before E-1 and again after E-4
- **Expected:** step 1 prints nothing; step 2 prints the same line both times.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-2 |
| FR-2 | E-1, E-5 |
| FR-3 | E-1, E-3 |
| FR-4 | E-1, E-2 |
| FR-5 | E-2, E-3 |
| FR-6 | E-3 |
| FR-7 | E-3, E-5 |
| FR-8 | E-4 |
| FR-9 | E-4 |
| FR-10 | E-1, E-3, E-5 |
| NFR-1 | E-4 |
| NFR-2 | E-1 |
| NFR-3 | E-4 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (the four `before-`/`after-` JSON files, command outputs) saved in `evidence/` and listed in `results.md`
