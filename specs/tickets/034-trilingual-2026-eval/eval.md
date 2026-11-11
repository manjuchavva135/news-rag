# 034: Trilingual 2026 test (retrieval + answers) - Evaluation

## Setup

- Prerequisites: the 2026 full run (ticket 031 FR-2) reached `index` for every 2026 date and `uv run newsrag index activate multilingual-e5-large-instruct__v1` was run; `pipeline.yaml` has `corpus_from_date: '2026-01-01'`.
- Start: `uv sync && uv run newsrag stores up && uv run newsrag models status` (prints `GPU free`)
- Base URL: n/a
- Test account: n/a
- Seed / reset: none (`retrieval_seed` 7)

## Automated checks

- `uv run ruff check . && uv run ruff format --check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0

## Acceptance checks

### E-1: The 300-question set (covers FR-1, FR-6, NFR-1)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag eval build-retrieval --n 300 --langs te:100,en:100,te_latn:100 --window-n 0 --from-date 2026-01-01 --out data/eval/retrieval-2026.jsonl`
  2. `python3 -c "import json,collections as c;L=[json.loads(x) for x in open('data/eval/retrieval-2026.jsonl')];print(len(L),dict(c.Counter(x['lang'] for x in L)),min(x['pub_date'] for x in L),any(x['in_baseline_window'] for x in L),len({x['story_id'] for x in L}))"`
  3. `python3 -c "import json,re;m=set(open('data/eval/te-latn-markers.txt').read().split());L=[json.loads(x) for x in open('data/eval/retrieval-2026.jsonl') if '\"te_latn\"' in x];print(len(m)>=60, all(not re.search('[ఀ-౿]',x['question']) and sum(w in m for w in re.findall('[a-z]+',x['question'].lower()))>=2 for x in L))"`
- **Expected:** step 1 ends with `wrote 300 questions … te=100 en=100 te_latn=100` (or fewer with `short:` lines printed); step 2 prints `300 {'te': 100, 'en': 100, 'te_latn': 100} 2026-… False 300` (first date starts with `2026-`); step 3 prints `True True`.

### E-2: Retrieval per language, with and without transliteration (covers FR-2, FR-3)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag search "Tirumala lo darshanam tokens eppudu istharu" --translit --k 3`
  2. `time uv run newsrag eval retrieval --set data/eval/retrieval-2026.jsonl --translit`
  3. `python3 -c "import json;d=json.load(open('/home/admin1/raw/reports/retrieval-2026.json'));print(sorted(d['by_lang']));[print(k,v['n'],sorted(v)) for k,v in d['by_lang'].items()];print(len(d['per_question']), all('top1_headline' in q for q in d['per_question']), d['k'])"`
  4. `python3 -c "import json;print(sorted(json.load(open('data/eval/retrieval-2026.jsonl.meta.json'))))"`
- **Expected:** step 1 first line starts with `translit: ` followed by Telugu-script text, then 3 hit lines; step 2 prints `recall@10=… mrr=… median_search_ms=… n=300`; step 3 prints `['en', 'te', 'te_latn', 'te_latn_translit']`, each with n = 100 and keys including `mrr`, `recall_at_1`, `recall_at_5`, `recall_at_10`, then `300 True 10`; step 4 lists keys including `from_date`, `langs`, `stories_available`, `writer_model`.

### E-3: Answer check (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `time uv run newsrag eval answers`
  2. `python3 -c "import json;d=json.load(open('/home/admin1/raw/reports/answers-2026.json'));print(d['n'],d['n_failed'],sorted(d['by_lang']));o=d['overall'];print(round(o['correct_pct']+o['partial_pct']+o['wrong_pct']));print({i['grade'] for i in d['items']}<= {'correct','partial','wrong',None})"`
  3. `ls /home/admin1/raw/reports/answers-2026.md`
- **Expected:** step 2 prints `300 <n_failed> ['en', 'te', 'te_latn']`, then `100`, then `True`; step 3 lists the file.

### E-4: Report explains the numbers (covers FR-5)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag eval report-2026 && grep -cE "^## How it was measured|^## Examples" /home/admin1/raw/reports/eval-2026.md`
  2. `grep -E "5-gram|0\.3|recall@10|MRR|enrich_model|answer_judge_model|synthetic|$(sha1sum data/eval/retrieval-2026.jsonl | cut -c1-12)" -c /home/admin1/raw/reports/eval-2026.md`
  3. `grep -E "^\| *(te|en|te_latn|te_latn \(transliterated\)) *\|" -c /home/admin1/raw/reports/eval-2026.md`
- **Expected:** step 1 prints `2`; step 2 prints a number ≥ 8; step 3 prints `4`.

## Edge cases

### E-5: Romanized check, translit fallback and per-language metrics (covers FR-1, FR-2, FR-3, NFR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_eval_retrieval.py tests/test_translit.py tests/test_eval_answers.py -k "te_latn or translit or recall_at or langs or grade or from_date"`
- **Expected:** all selected tests pass, including at least: an English question rejected as `not in Romanized Telugu`; a question with Telugu letters rejected for `te_latn`; `--langs` gives exact counts; stories before `from_date` never chosen; a translit reply in Latin letters retried 3 times then `translit_failed: true` with the original query; `recall_at_1`/`recall_at_5` computed from fixed ranks; an unparseable grade becomes `grade: null` and counts in `n_failed`; `--langs` not summing to `--n` exits 2; a Romanized question in an `en` slot rejected as `not in English`; translit makes exactly 3 calls before falling back; no hits → `wrong` with no judge call; `eval answers` with a retrieval file lacking `te_latn_translit` exits 1; `report-2026` with a missing input exits 1 `missing <path>`; no test holds two models at once (fake `use` raises on a second profile).

### E-6: Nothing else changed (covers Don't touch)

- **Type:** CLI
- **Steps:**
  1. `B=$(git log --format=%H --grep '^034' --reverse | head -1)~1; git diff --stat $B HEAD -- data/eval/retrieval-v1.jsonl data/eval/retrieval-v2.jsonl data/eval/customer-questions.json src/newsrag/report/bars.py`
  2. `sha1sum /home/admin1/raw/reports/retrieval-new.json /home/admin1/raw/reports/customer-answers.json` before the first E-1 step and again after E-4
  3. `uv run newsrag config show | grep -E "^(retrieval_hit_overlap|retrieval_ngram|retrieval_k) "`
  4. `uv run pytest -q tests/test_search.py tests/test_eval_retrieval.py -k "not translit"`
- **Expected:** step 1 prints nothing; step 2 gives the same sums both times (or "No such file" both times); step 3 prints `retrieval_hit_overlap = 0.3`, `retrieval_ngram = 5`, `retrieval_k = 10`; step 4 passes (the 025 behaviour without `--langs`/`--translit` is unchanged).

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-1, E-5 |
| FR-2 | E-2, E-5 |
| FR-3 | E-2, E-5 |
| FR-4 | E-3, E-5 |
| FR-5 | E-4 |
| FR-6 | E-1 |
| FR-7 | E-2, E-3, E-5 |
| NFR-1 | E-1, E-2, E-3 (sum of the three `time` outputs, recorded in results.md) |
| NFR-2 | E-5 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-4 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (`retrieval-2026.json`, `answers-2026.json`, `eval-2026.md`, command outputs with times) saved in `evidence/` and listed in `results.md`
