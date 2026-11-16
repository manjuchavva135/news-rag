# Milestone 1, part 1: trustworthy yardstick and answer-step fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the lenient answer grader with an AI-only yardstick we can trust, measure the two
processed months on one scoreboard, then fix the answer step (language, loops, give-ups) and prove
the gain on that scoreboard.

**Architecture:** New eval modules in `src/newsrag/evals/` (traps, grading, staff set, relevance,
product-path answers, scoreboard) and answer guards in `src/newsrag/answer/guards.py`. The eval
answers through the product's `answer.service.ask` instead of its own prompt. Two graders from
model families other than the answer model, chosen by a trap test, must agree for an answer to
count as correct.

**Tech Stack:** Python 3.12, uv, Typer, pydantic-settings, vLLM profiles via `newsrag.models.use`,
Postgres warehouse, pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-milestone-1-accuracy-design.md`

**Not in this plan (own plans later):** search tickets 035–041 (already specified in
`specs/tickets/`), the corrective retry (design §4), the grouping audit (design §5).

## Global Constraints

- Corpus under test: print dates 2026-10-01 to 2026-11-10; dates outside are excluded from every set.
- Every new setting goes in `src/newsrag/config.py` under its module's block (`# --- evals ---`,
  `# --- answer ---`); every prompt is a versioned file in `src/newsrag/prompts/`.
- No grader may share a model family with the answer model (`models.yaml` `family`); Sarvam-M
  (`sarvam-m-24b`) is a control candidate only, never a chosen grader.
- A grader is chosen only with ≥ 95% accuracy on the trap set (`eval_grader_min_accuracy = 0.95`).
- An answer counts as correct only when both graders say `correct`; differing labels are `uncertain`.
- A refusal to a question written from a story in the corpus is `wrong` (code rule, no model call).
- One GPU, one model loaded at a time: every pass opens one model, does all its calls, closes it.
- `tests/test_layers.py` must keep passing (evals and answer are layer 4; they must not import each
  other in a cycle).
- Lint: `uv run ruff check . && uv run ruff format --check .`; unit tests:
  `uv run pytest -q -m "not gpu and not data and not stores"`.

## Review Focus

1. A grader reply that is unparseable or missing (`ask_label` returns `None`) must make the answer
   `uncertain`, never `correct` — test in Task 4.
2. Refusals worded differently ("ఈ ప్రశ్నకు సంబంధించిన సమాచారం మూలాల్లో లేదు.") must be caught,
   while an answer that gives facts and adds one "not covered" sentence must not be called a
   refusal — tests in Task 2.
3. A Romanized-Telugu question answered in Telugu script or in English is in the right language;
   an English question answered in Telugu is not — test in Task 2.
4. A question whose search returns no sources is `wrong` without any grader call — test in Task 5.
5. A scoreboard run whose yardstick (set files, prompts, graders) differs from the previous run's
   must not print the two side by side as a before/after — test in Task 8.

---

### Task 1: Story metadata loaders move to the warehouse

`answer/service.py` imports `evals.customer` for `load_story_meta` and `load_covered_years`. Later
tasks make `evals` import `answer`, which would create a cycle that `tests/test_layers.py` rejects.

**Files:**
- Modify: `src/newsrag/warehouse/db.py` (add the two functions, bodies moved from `evals/customer.py:146-175`)
- Modify: `src/newsrag/evals/customer.py` (delete the two bodies; import from warehouse)
- Modify: `src/newsrag/answer/service.py:131-135`
- Test: `tests/test_layers.py` (add a check), existing `tests/test_eval_customer.py`, `tests/test_answer.py`

**Interfaces:**
- Produces: `newsrag.warehouse.db.story_meta(story_ids: Sequence[str]) -> dict[str, dict[str, Any]]`
  and `newsrag.warehouse.db.covered_years(generation: str | None = None) -> list[int]`.

- [ ] **Step 1: Write the failing test** in `tests/test_layers.py`:

```python
def test_answer_does_not_import_evals():
    assert ("answer", "evals") not in edges()
```

- [ ] **Step 2:** Run `uv run pytest -q tests/test_layers.py::test_answer_does_not_import_evals` — expected FAIL.
- [ ] **Step 3:** Move the bodies; `service.ask` defaults to `wdb.story_meta` / `wdb.covered_years()`; update every caller found by `grep -rn "load_story_meta\|load_covered_years" src tests`.
- [ ] **Step 4:** Run `uv run pytest -q tests/test_layers.py tests/test_eval_customer.py tests/test_answer.py` — expected PASS.
- [ ] **Step 5:** Commit: `git commit -m "Move story metadata loaders to the warehouse so answer does not import evals"`

### Task 2: Answer guards

**Files:**
- Create: `src/newsrag/answer/guards.py`
- Test: `tests/test_answer_guards.py`

**Interfaces:**
- Produces:
  - `is_refusal(answer: str) -> bool` — the whole answer says the sources do not cover the
    question: it has no citation mark `[n]` and matches a refusal pattern
    (`మూలాల్లో|మూలాలలో|sources` within 40 characters of `లేదు|లేవు|not`, or `సమాచారం` within 30 of `లేదు`).
  - `has_loop(answer: str, min_repeats: int = 3) -> bool` — some sentence of ≥ 15 characters
    (split on `.`, `।`, newline, citation marks removed) occurs `min_repeats` times or more.
  - `script_of(text: str) -> str` — `"te"` when ≥ 50% of letters are Telugu script, else `"latin"`.
  - `language_ok(question_lang: str, answer: str) -> bool` — `te` → answer `te`; `en` → `latin`;
    `te_latn` → either.
  - `key_term_overlap(query: str, text: str) -> float` — share of the query's words of ≥ 3
    letters (casefolded, NFC) found in `text`; 0.0 for a query with no such word.

- [ ] **Step 1: Write the failing tests**:

```python
def test_refusals():
    assert is_refusal("ఈ అంశంపై మూలాల్లో సమాచారం లేదు.")
    assert is_refusal("ఈ ప్రశ్నకు సంబంధించిన సమాచారం మూలాల్లో లేదు.")
    assert not is_refusal("సతీష్‌బాబు [1]. ఆయన వయసు గురించి మూలాల్లో సమాచారం లేదు.")

def test_loop():
    s = "ఈ పార్టీ పేరును ఎన్నికల కమిషన్ వద్ద నమోదు చేయించినట్లు సమాచారం ఉంది [1]. "
    assert has_loop(s * 3)
    assert not has_loop(s * 2)

def test_language():
    assert language_ok("te_latn", "విజయవాడలో [1].")
    assert language_ok("te_latn", "In Vijayawada [1].")
    assert not language_ok("en", "విజయవాడలో [1].")
    assert language_ok("en", "In Vijayawada [1].")

def test_overlap():
    assert key_term_overlap("polavaram budget", "polavaram project budget raised") == 1.0
    assert key_term_overlap("a of", "anything") == 0.0
```

- [ ] **Step 2:** Run `uv run pytest -q tests/test_answer_guards.py` — expected FAIL (module missing).
- [ ] **Step 3:** Implement the five functions in `src/newsrag/answer/guards.py` (pure, stdlib only).
- [ ] **Step 4:** Run the tests — expected PASS.
- [ ] **Step 5:** Commit: `git commit -m "Answer guards: refusal, loop, language and key-term checks"`

### Task 3: Trap set and grader calibration

**Files:**
- Create: `src/newsrag/evals/traps.py`, `src/newsrag/prompts/trap_pair_v1.md`
- Modify: `src/newsrag/config.py` (`# --- evals ---` block), `models.yaml`, `src/newsrag/evals/cli.py`
- Test: `tests/test_traps.py`

**Interfaces:**
- Consumes: `customer.ask_label(client, prompt, labels)`, `answers.grade_prompt(question, answer, gold_text)`, `rt.load_stories()`.
- Produces:
  - `@dataclass Pair(story_id: str, question: str, answer: str, story: str)` — `answer` is a
    verbatim span of `story` (checked in code; pairs failing the check are dropped).
  - `extract_pairs(stories: Sequence[Mapping], ask: Ask, n: int, seed: int) -> list[Pair]`
  - `@dataclass Trap(tid: str, kind: str, question: str, answer: str, story: str, label: str)`;
    kinds and labels: `correct`→correct, `cited`(answer + " [1]")→correct, `refusal`→wrong,
    `number_changed`(one digit changed in code; only pairs with a digit)→wrong,
    `other_story`(answer of another pair)→wrong, `partial`(first sentence of a ≥2-sentence
    answer)→partial.
  - `make_traps(pairs: Sequence[Pair], seed: int) -> list[Trap]`
  - `calibrate(traps: Sequence[Trap], candidates: Sequence[str], opener) -> dict[str, dict]` —
    per profile `{"accuracy": float, "by_kind": {kind: float}, "family": str}`.
  - `pick_graders(scores: Mapping[str, Mapping], answer_family: str, min_accuracy: float) -> tuple[str, str]`
    — the two most accurate candidates with accuracy ≥ `min_accuracy`, from two different
    families, neither equal to `answer_family`, never `sarvam-m-24b`; raises `GraderError` when
    no such pair exists.
- Settings: `eval_trap_file: Path` (default `{repo_root}/data/eval/traps-v1.jsonl`),
  `eval_trap_pairs: int = 120`, `eval_trap_seed: int = 19`,
  `eval_grader_candidates: tuple[str, ...] = ("gemma4-31b", "gemma3-27b", "llama-3.3-70b", "sarvam-m-24b")`,
  `eval_graders: tuple[str, ...] = ()`, `eval_grader_min_accuracy: float = 0.95`,
  `eval_calibration_file: Path` (default `{reports_dir}/grader-calibration.json`).
- `models.yaml`: profile `llama-3.3-70b`, `hf_id: meta-llama/Llama-3.3-70B-Instruct`,
  `family: llama`, `kind: llm`, FP8 quantization in `extra_args`, sized with `newsrag models fit`.
- CLI: `newsrag eval build-traps` (writes `eval_trap_file`), `newsrag eval calibrate-graders [--apply]`
  (`--apply` writes `eval_graders` with `config.set_pipeline_key`).

- [ ] **Step 1: Write the failing tests** (fake `ask` / fake opener, no GPU):

```python
def test_pairs_keep_only_verbatim_answers(): ...  # a reply whose answer is not in the story is dropped
def test_trap_labels():
    traps = make_traps([PAIR_WITH_DIGIT_AND_TWO_SENTENCES, OTHER_PAIR], seed=1)
    assert {t.kind: t.label for t in traps} == {
        "correct": "correct", "cited": "correct", "refusal": "wrong",
        "number_changed": "wrong", "other_story": "wrong", "partial": "partial"}
def test_pick_graders_rules():
    scores = {"gemma4-31b": {"accuracy": .97, "family": "gemma"},
              "gemma3-27b": {"accuracy": .98, "family": "gemma"},
              "llama-3.3-70b": {"accuracy": .96, "family": "llama"},
              "sarvam-m-24b": {"accuracy": .99, "family": "mistral"}}
    assert pick_graders(scores, "qwen", .95) == ("gemma3-27b", "llama-3.3-70b")
    with pytest.raises(GraderError):
        pick_graders({"gemma3-27b": {"accuracy": .98, "family": "gemma"}}, "qwen", .95)
```

- [ ] **Step 2:** Run `uv run pytest -q tests/test_traps.py` — expected FAIL.
- [ ] **Step 3:** Implement `traps.py`, the prompt (asks the answer-family model for one question
  and a verbatim answer span as JSON), settings, profile and the two commands.
- [ ] **Step 4:** Run the tests — expected PASS.
- [ ] **Step 5:** Commit: `git commit -m "Trap set and grader calibration"`

### Task 4: Two-grader grading

**Files:**
- Create: `src/newsrag/evals/grading.py`
- Test: `tests/test_grading.py`

**Interfaces:**
- Consumes: `guards.is_refusal`, `guards.has_loop`, `guards.language_ok` (Task 2);
  `answers.grade_prompt`; `customer.ask_label`; `config.settings.eval_graders` (Task 3).
- Produces:
  - `@dataclass GradeJob(key: str, question: str, lang: str, answer: str, gold_text: str)`
  - `@dataclass Grade(label: str, by_grader: dict[str, str | None], rule: str | None, flags: list[str], repeat: dict[str, str | None])`
    — `label` ∈ `correct | partial | wrong | uncertain`; `rule` is `"refusal"` when the code rule
    decided; `flags` ⊆ `{"loop", "wrong_language"}`.
  - `grade_all(jobs: Sequence[GradeJob], opener, graders: Sequence[str] | None = None, repeat_share: float = 0.1, seed: int = 23) -> dict[str, Grade]`
    — rules first; then one pass per grader (one model open at a time); `repeat_share` of the jobs
    are graded a second time by each grader into `repeat`.
  - `consistency(grades: Mapping[str, Grade]) -> dict[str, float]` — per grader, share of
    repeated jobs with the same label both times.
- Raises `GraderError` when fewer than two graders are configured.

- [ ] **Step 1: Write the failing tests** (fake opener whose client returns scripted labels):

```python
def test_refusal_is_wrong_without_model_call(): ...      # rule == "refusal", no client created
def test_both_correct_is_correct(): ...
def test_disagreement_is_uncertain(): ...                 # correct vs partial -> "uncertain"
def test_unparseable_reply_is_uncertain(): ...            # ask_label -> None for one grader
def test_flags():                                         # loop and wrong_language flagged, label still graded
    ...
def test_consistency_counts_repeats(): ...
```

- [ ] **Step 2:** Run `uv run pytest -q tests/test_grading.py` — expected FAIL.
- [ ] **Step 3:** Implement `grading.py`.
- [ ] **Step 4:** Run the tests — expected PASS.
- [ ] **Step 5:** Commit: `git commit -m "Two-grader grading with code rules and self-consistency"`

### Task 5: Answer eval through the product path

**Files:**
- Create: `src/newsrag/evals/product.py`
- Modify: `src/newsrag/answer/threads.py` (add `NullStore`), `src/newsrag/evals/cli.py`
- Test: `tests/test_eval_product.py`

**Interfaces:**
- Consumes: `answer.service.session(provider)`, `answer.service.ask(question, llm=..., store=..., search_fn=..., story_meta=..., covered_years=...)`, `grading.grade_all` (Task 4), `rt.read_set`, `rt.first_correct_rank`.
- Produces:
  - `answer.threads.NullStore` — `create_thread` returns `"eval"`, `load_turns` returns `[]`,
    `add_turn` returns `1`; stores nothing.
  - `run_product_answers(set_file: Path, out: Path, provider: str | None = None, search_fn=None, story_meta=None, covered_years=None, session_fn=None, opener=None, echo=print) -> dict`
    — per item `{qid, lang, question, search_query, answer, sources, retrieved_gold, seconds, grade, by_grader, rule, flags}`;
    summary `{"overall": {...}, "by_lang": {lang: {"correct","partial","wrong","uncertain","refusal","loop","wrong_language","n"}}, "p50_s", "p95_s"}`
    (shares in %). An item with no sources is `wrong` with `rule = "no_sources"`.
- CLI: `newsrag eval product --set <file> [--provider local|claude]`.

- [ ] **Step 1: Write the failing tests** (fake session, search and grader):

```python
def test_no_sources_is_wrong_without_grading(): ...
def test_uses_service_ask_and_records_search_query(): ...
def test_summary_by_lang_counts_flags(): ...
def test_null_store_stores_nothing(): ...
```

- [ ] **Step 2:** Run `uv run pytest -q tests/test_eval_product.py` — expected FAIL.
- [ ] **Step 3:** Implement; answers run inside one `session()`, grading after the session closes
  (the GPU holds one model).
- [ ] **Step 4:** Run the tests — expected PASS.
- [ ] **Step 5:** Commit: `git commit -m "Answer eval through the product's answer service"`

### Task 6: Staff-style question set

**Files:**
- Modify: `src/newsrag/evals/retrieval.py` (`load_style_questions`, `check_question`, `build_retrieval_set`), `src/newsrag/evals/cli.py` (`build-retrieval` options)
- Test: `tests/test_eval_retrieval.py`

**Interfaces:**
- Produces: `build_retrieval_set(..., max_words: int | None = None)` — a question with more words
  than `max_words` is rejected by `check_question` and asked again (existing retry loop);
  `load_style_questions(path)` also reads JSON lines with a `question` field
  (`data/eval/real-questions-v1.jsonl`). CLI: `newsrag eval build-retrieval --max-words 8 --style-file data/eval/real-questions-v1.jsonl --langs te=100,en=100,te_latn=100 --from 2026-10-01 --out data/eval/staff-2026.jsonl`.

- [ ] **Step 1: Write the failing tests**:

```python
def test_check_question_rejects_long_question():
    assert check_question("one two three four five six seven eight nine", "en", "h", max_words=8)
def test_style_from_real_questions_jsonl(tmp_path): ...   # returns the `question` texts
```

- [ ] **Step 2:** Run the two tests — expected FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `uv run pytest -q tests/test_eval_retrieval.py` — expected PASS.
- [ ] **Step 5:** Build the set (GPU): run the CLI line above; check
  `wc -l data/eval/staff-2026.jsonl` prints 300; commit the set and its `.meta.json`:
  `git commit -m "Staff-style question set: 300 questions of at most 8 words"`

### Task 7: Relevance of the real staff questions

**Files:**
- Create: `src/newsrag/evals/relevance.py`, `src/newsrag/prompts/relevance_v1.md`
- Modify: `src/newsrag/config.py`, `src/newsrag/evals/cli.py`
- Test: `tests/test_relevance.py`

**Interfaces:**
- Consumes: `short.read_real()`, `search.search`, `customer.ask_label`, `eval_graders`.
- Produces:
  - `pool(questions: Sequence[Mapping], search_fn, k: int = 10) -> dict[str, list[Hit]]`
  - `judge(pool: Mapping[str, Sequence[Hit]], cache: Path, opener, graders=None) -> dict[str, dict[str, str]]`
    — `qid -> story_id -> "relevant" | "not_relevant" | "uncertain"` (both graders must agree);
    labels are cached in `cache` by `(qid, story_id)` and only new pairs are judged.
  - `score(pool, labels) -> dict` — `{"any_relevant_at_10", "relevant_share_at_10", "other_kind_share", "uncertain_share"}` overall and by the real set's `kind`.
- Settings: `eval_relevance_file: Path` (default `{repo_root}/data/eval/real-relevance-v1.json`, committed).
- CLI: `newsrag eval relevance`.

- [ ] **Step 1: Write the failing tests**: cached pairs are not re-judged; disagreement →
  `uncertain`; `score` on a hand-made pool gives the exact shares.
- [ ] **Step 2:** Run `uv run pytest -q tests/test_relevance.py` — expected FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests — expected PASS.
- [ ] **Step 5:** Commit: `git commit -m "Grader-judged relevance for the real staff questions"`

### Task 8: Scoreboard

**Files:**
- Create: `src/newsrag/evals/scoreboard.py`
- Modify: `src/newsrag/answer/guards.py` (add date check), `src/newsrag/config.py`, `src/newsrag/evals/cli.py`
- Test: `tests/test_scoreboard.py`, `tests/test_answer_guards.py`

**Interfaces:**
- Consumes: `rt.run_retrieval` (long set and staff set), `short.run_short`, `product.run_product_answers` (Task 5, both sets), `relevance` (Task 7), `customer.run_customer`, `grading.consistency`.
- Produces:
  - `guards.event_date_ok(answer: str, pub_date: date, event_date: date | None) -> bool | None`
    — `None` when the answer states no date or the dates are equal; `False` when it states the
    print date and not the event date; else `True`.
  - `BARS: dict[str, tuple[str, float]]` in `scoreboard.py`, exactly:
    `answers_long_correct_{te,en,te_latn}: (">=", 90)`, `answers_staff_correct_{te,en,te_latn}: (">=", 85)`,
    `retrieval_long_r10_{te,en,te_latn}: (">=", 90)`, `retrieval_staff_r10_latin: (">=", 85)`,
    `latin_name_mention: (">=", 85)`, `real_other_kind_share: ("<=", 5)`,
    `claims_supported: (">=", 95)`, `event_date_ok: (">=", 95)`, `language_ok: (">=", 98)`,
    `loops: ("<=", 0)`, `answer_p95_s_staff: ("<=", 15)` (fact questions; the 60 s list/timeline budget is checked in the search plan with ticket 041).
  - `yardstick_hash() -> str` — sha1 over the set files, prompt files, `eval_graders` and the
    grading settings.
  - `run_scoreboard(out_dir: Path | None = None, provider: str | None = None, echo=print) -> dict`
    — writes `scoreboard-<UTC timestamp>.json` and `.md` in `eval_scoreboard_dir`
    (default `{reports_dir}/scoreboard`); each measure: `value`, `bar`, `passed`, `previous`.
  - `compare(current, previous) -> list[str]` — `previous` values are shown only when both runs
    have the same `yardstick_hash`; otherwise the report says "yardstick changed: no comparison".
- CLI: `newsrag eval scoreboard [--provider local|claude]`.

- [ ] **Step 1: Write the failing tests**:

```python
def test_event_date_ok():
    assert event_date_ok("2026-10-14న జరిగింది [1].", date(2026,10,15), date(2026,10,14)) is True
    assert event_date_ok("2026-10-15న జరిగింది [1].", date(2026,10,15), date(2026,10,14)) is False
    assert event_date_ok("జరిగింది [1].", date(2026,10,15), date(2026,10,14)) is None
def test_bars_pass_and_fail(): ...           # a value at the bar passes, one below fails
def test_no_comparison_when_yardstick_changed(): ...
def test_scoreboard_writes_json_and_markdown(tmp_path): ...  # all parts faked
```

- [ ] **Step 2:** Run `uv run pytest -q tests/test_scoreboard.py tests/test_answer_guards.py` — expected FAIL.
- [ ] **Step 3:** Implement; dates in Telugu answers are matched as `YYYY-MM-DD`, `DD-MM-YYYY`
  and `D <Telugu month name>` (month names from a constant in `guards.py`).
- [ ] **Step 4:** Run the tests — expected PASS.
- [ ] **Step 5:** Commit: `git commit -m "Scoreboard with exit bars and a frozen yardstick hash"`

### Task 9: Calibrate graders and record the baseline (GPU, no code)

- [ ] **Step 1:** `uv run newsrag eval build-traps` — expected: `data/eval/traps-v1.jsonl` with all six kinds.
- [ ] **Step 2:** `uv run newsrag eval calibrate-graders --apply` — expected: two graders written
  to `pipeline.yaml`, both ≥ 95%; if `GraderError`, stop and report the calibration table to the user.
- [ ] **Step 3:** `uv run newsrag eval relevance` — expected: `data/eval/real-relevance-v1.json` written.
- [ ] **Step 4:** `uv run newsrag eval scoreboard` — expected: report in `reports/scoreboard/`;
  this is the baseline.
- [ ] **Step 5:** Commit the trap set, relevance file and `pipeline.yaml`:
  `git commit -m "Yardstick v1: traps, graders, relevance; baseline scoreboard recorded"`
  and send the baseline report to the user.

### Task 10: Answer-step fixes

**Files:**
- Modify: `src/newsrag/answer/service.py` (`ask`), `src/newsrag/config.py` (`# --- answer ---`)
- Test: `tests/test_answer.py`

**Interfaces:**
- Consumes: `guards.has_loop`, `guards.is_refusal`, `guards.key_term_overlap` (Task 2).
- Produces (behaviour of `service.ask`):
  - `answer_thinking` default becomes `False`.
  - Loop guard: when `has_loop(reply.text)`, ask once more with
    `answer_loop_retry_repetition_penalty = 1.15`; if it loops again, keep the reply with the
    repeated sentences removed (each sentence kept once, order kept).
  - Give-up guard: when `is_refusal(reply.text)` and a source has
    `key_term_overlap(search_query, source.text) >= answer_refusal_min_overlap` (`0.5`), ask once
    more with only the `answer_refusal_retry_sources = 3` sources of highest overlap.
  - `Answer` gets `retries: list[str]` (`"loop"`, `"refusal"`) and the eval records it.
- `LocalLLM.complete` gains an optional `repetition_penalty: float | None = None`.

- [ ] **Step 1: Write the failing tests** (fake LLM with scripted replies):

```python
def test_loop_is_retried_with_higher_penalty(): ...
def test_second_loop_is_deduplicated(): ...
def test_refusal_with_matching_source_is_retried_with_three_sources(): ...
def test_refusal_without_matching_source_is_kept(): ...
def test_thinking_off_by_default(): ...
```

- [ ] **Step 2:** Run `uv run pytest -q tests/test_answer.py` — expected FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full unit suite — expected PASS.
- [ ] **Step 5:** Commit: `git commit -m "Answer step: retry loops and give-ups, reasoning off by default"`

### Task 11: Measure the fixes (GPU, no code)

- [ ] **Step 1:** `uv run newsrag eval scoreboard` — expected: the report shows the baseline as
  `previous` (same yardstick hash) and the gains or regressions per script.
- [ ] **Step 2:** `uv run newsrag eval scoreboard --provider claude` (only with the user's go-ahead:
  it sends questions and sources to Bedrock) — shows how much of the remaining gap is the answer
  model.
- [ ] **Step 3:** Send both reports to the user with the exit bars still missed; those set the
  order of the search plan (tickets 035–041).
