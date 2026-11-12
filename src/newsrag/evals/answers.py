"""The graded answer check of the trilingual 2026 test (ticket 034 FR-4).

Three passes, one model at a time (as in ticket 028):

1. `search(k=customer_k)` for every question (a `te_latn` question with its transliterated text
   from the retrieval report) inside one embed scope;
2. `use(answer_model)`: an answer from the numbered sources (the 028 prompt);
3. `use(answer_judge_model)`: `correct` | `partial` | `wrong` against the story the question was
   written from.

Accuracy = share of the graded answers that are `correct`.

Optional passes (off by default, so the ticket's numbers do not change):

- `thinking`: after pass 2 every question is answered again by the reasoning profile
  `eval_2026_thinking_model` from the same prompt and sources; its reasoning text is kept and its
  answer is graded the same way (`grade_thinking`).
- `explain`: the judge also rates how well each answer explains itself, `clear` | `adequate` |
  `poor` (prompt `answer_explain_v1.md`), separately from the facts.

The report records every model with the settings it ran with (`models`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.evals import customer
from newsrag.evals import retrieval as rt

PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "answer_grade_v1.md"
EXPLAIN_PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "answer_explain_v1.md"
CORRECT, PARTIAL, WRONG = "correct", "partial", "wrong"
GRADES = (CORRECT, PARTIAL, WRONG)
EXPLAIN_GRADES = ("clear", "adequate", "poor")
LANGS = ("te", "en", "te_latn")

Echo = Callable[[str], None]


class InputError(ValueError):
    """The set or the retrieval report can't be used (exit 1)."""


@dataclass
class Item:
    q: dict[str, Any]
    query: str  # what is searched and asked (transliterated for te_latn)
    hits: list[Any] = field(default_factory=list)
    sources: list[Any] = field(default_factory=list)
    answer: str = ""
    error: str = ""
    grade: str | None = None
    no_sources: bool = False
    failed: bool = False
    retrieved_gold: bool = False
    explain: str | None = None  # how well `answer` explains itself
    # the second answer, by the reasoning model (`thinking` pass)
    answer_thinking: str = ""
    reasoning: str = ""
    error_thinking: str = ""
    failed_thinking: bool = False
    grade_thinking: str | None = None
    explain_thinking: str | None = None

    def record(self, thinking: bool = False, explain: bool = False) -> dict[str, Any]:
        out = {
            "qid": self.q["qid"],
            "lang": self.q["lang"],
            "query": self.query,
            "answer": self.answer,
            "grade": self.grade,
            "retrieved_gold": self.retrieved_gold,
            "no_sources": self.no_sources,
            "gold_headline": self.q["gold_text"].split("\n", 1)[0],
            "sources": [
                {
                    "n": getattr(src, "n", n),
                    "headline": getattr(src, "headline", None),
                    "pub_date": getattr(src, "pub_date", None),
                    "page_id": getattr(src, "page_id", None),
                }
                for n, src in enumerate(self.sources, start=1)
            ],
        }
        if explain:
            out["explain"] = self.explain
        if thinking:
            out["answer_thinking"] = self.answer_thinking
            out["reasoning"] = self.reasoning
            out["grade_thinking"] = self.grade_thinking
            if explain:
                out["explain_thinking"] = self.explain_thinking
            if self.error_thinking:
                out["error_thinking"] = self.error_thinking
        if self.query != self.q["question"]:
            out["query_typed"] = self.q["question"]
        if self.error:
            out["error"] = self.error
        return out


def translit_queries(
    report: Mapping[str, Any], questions: Sequence[Mapping[str, Any]], set_file: Path
) -> dict[str, str]:
    """qid -> transliterated query of every `te_latn` question, checked against the set."""
    if Path(str(report.get("set"))).resolve() != Path(set_file).resolve():
        raise InputError(f"retrieval report is for {report.get('set')}, not {set_file}")
    found = {
        str(p["qid"]): p["translit_query"]
        for p in report.get("per_question") or []
        if p.get("translit_query") is not None
    }
    latn = [str(q["qid"]) for q in questions if q["lang"] == rt.TE_LATN]
    if latn and (not (report.get("by_lang") or {}).get(rt.TRANSLIT) or not found):
        raise InputError("no transliterated queries (run eval retrieval --translit)")
    missing = [qid for qid in latn if qid not in found]
    if missing:
        raise InputError(f"no transliterated query for {', '.join(missing[:5])}")
    return found


def grade_prompt(question: str, answer: str, gold_text: str) -> str:
    story = gold_text[: config.settings.retrieval_story_chars]
    return (
        PROMPT_FILE.read_text("utf-8")
        .replace("{question}", question)
        .replace("{answer}", answer)
        .replace("{story}", story)
    )


def explain_prompt(question: str, answer: str) -> str:
    return (
        EXPLAIN_PROMPT_FILE.read_text("utf-8")
        .replace("{question}", question)
        .replace("{answer}", answer[: config.settings.eval_2026_explain_max_chars])
    )


def split_reasoning(content: str) -> tuple[str, str]:
    """(reasoning, answer) of a reasoning model's reply `reasoning</think>answer` (the opening
    `<think>` may be missing). A reply cut off while still reasoning has no answer."""
    head, sep, tail = (content or "").partition("</think>")
    if not sep:
        if head.lstrip().startswith("<think>"):
            return head.replace("<think>", "").strip(), ""
        return "", head.strip()
    return head.replace("<think>", "").strip(), tail.strip()


def thinking_params() -> dict[str, Any]:
    s = config.settings
    return {
        "temperature": s.eval_2026_thinking_temperature,
        "top_p": s.eval_2026_thinking_top_p,
        "max_tokens": s.eval_2026_thinking_max_tokens,
        "extra_body": {"repetition_penalty": s.customer_answer_repetition_penalty},
    }


def ask_thinking(client: Any, prompt: str) -> tuple[str, str]:
    """(reasoning, answer) of the reasoning model for the answer prompt. ValueError when it gave
    no answer (it only reasoned until `eval_2026_thinking_max_tokens`)."""
    params = thinking_params()
    raw = getattr(client, "raw", None)
    separate = ""
    if raw is not None:  # the server may give the reasoning in its own field
        from newsrag.models.client import build_messages

        message = (
            raw.chat.completions.create(
                model=client.profile, messages=build_messages(prompt, None), **params
            )
            .choices[0]
            .message
        )
        content = message.content or ""
        separate = (getattr(message, "reasoning_content", None) or "").strip()
    else:
        content = client.chat(prompt, **params)
    reasoning, answer = split_reasoning(content)
    if not answer:
        raise ValueError("no answer after the reasoning")
    return separate or reasoning, answer


def _profile_info(name: str) -> dict[str, Any]:
    try:
        from newsrag.models import get_profile

        profile = get_profile(name)
    except Exception:  # noqa: BLE001 - a profile that is not in models.yaml (tests)
        return {"profile": name}
    out = {"profile": name}
    for key in ("hf_id", "family", "kind", "dtype", "max_model_len", "extra_args"):
        value = getattr(profile, key, None)
        if value:
            out[key] = value
    return out


def model_settings(
    thinking: bool, explain: bool, k: int, embed_profile: str, meta: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Every model of the test with the settings it ran with, for the report."""
    s = config.settings
    out: dict[str, Any] = {
        "question_writer": {
            **_profile_info(str((meta or {}).get("writer_model") or s.enrich_model)),
            **((meta or {}).get("writer_settings") or {}),
            "style_file": (meta or {}).get("style_file"),
            "style_examples_per_prompt": (meta or {}).get("style_examples_per_prompt"),
        },
        "search": {
            **_profile_info(embed_profile),
            "k": k,
            "source_chars": s.customer_source_chars,
        },
        "answer": {
            **_profile_info(s.answer_model),
            "thinking": False,
            "temperature": 0,
            "max_tokens": s.customer_answer_max_tokens,
            "repetition_penalty": s.customer_answer_repetition_penalty,
            "retries": s.customer_answer_retries,
            "prompt": customer.ANSWER_PROMPT.name,
        },
        "judge": {
            **_profile_info(customer.judge_profile()),
            "thinking": False,
            "temperature": 0,
            "max_tokens": s.customer_judge_max_tokens,
            "retries": s.customer_judge_retries,
            "gold_story_chars": s.retrieval_story_chars,
            "prompt": PROMPT_FILE.name,
        },
    }
    if thinking:
        params = thinking_params()
        out["answer_thinking"] = {
            **_profile_info(s.eval_2026_thinking_model),
            "thinking": True,
            "temperature": params["temperature"],
            "top_p": params["top_p"],
            "max_tokens": params["max_tokens"],
            "repetition_penalty": params["extra_body"]["repetition_penalty"],
            "retries": s.customer_answer_retries,
            "prompt": customer.ANSWER_PROMPT.name,
        }
    if explain:
        out["judge"]["explain_prompt"] = EXPLAIN_PROMPT_FILE.name
        out["judge"]["explain_answer_chars"] = s.eval_2026_explain_max_chars
    return out


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def shares(
    items: Sequence[Item], attr: str = "grade", labels: Sequence[str] = GRADES
) -> dict[str, Any]:
    graded = [it for it in items if getattr(it, attr) is not None]
    out: dict[str, Any] = {"n": len(items), "n_graded": len(graded)}
    for g in labels:
        out[f"{g}_pct"] = _pct(sum(1 for it in graded if getattr(it, attr) == g), len(graded))
    return out


def summarize(
    items: Sequence[Item], attr: str = "grade", labels: Sequence[str] = GRADES
) -> dict[str, Any]:
    overall = shares(items, attr, labels)
    return {
        "n": len(items),
        "n_failed": sum(1 for it in items if getattr(it, attr) is None),
        "overall": {f"{g}_pct": overall[f"{g}_pct"] for g in labels},
        "by_lang": {
            lang: shares([it for it in items if it.q["lang"] == lang], attr, labels)
            for lang in LANGS
            if any(it.q["lang"] == lang for it in items)
        },
    }


def _settings_table(models: Mapping[str, Mapping[str, Any]]) -> list[str]:
    names = {
        "question_writer": "Question writer",
        "search": "Search (embeddings)",
        "answer": "Answer, reasoning off",
        "answer_thinking": "Answer, reasoning on",
        "judge": "Judge (grades)",
    }
    lines = ["| Step | Model | Settings |", "|---|---|---|"]
    for key, label in names.items():
        m = models.get(key)
        if not m:
            continue
        model = f"`{m.get('profile')}`" + (f" ({m['hf_id']})" if m.get("hf_id") else "")
        rest = [
            f"{k} = {json.dumps(v, ensure_ascii=False) if isinstance(v, list | dict) else v}"
            for k, v in m.items()
            if k not in ("profile", "hf_id") and v is not None
        ]
        lines.append(f"| {label} | {model} | {'; '.join(rest)} |".replace("\n", " "))
    return lines


def _score_table(report: Mapping[str, Any]) -> list[str]:
    rows = [("Reasoning off", report)]
    if report.get("thinking"):
        rows.append(("Reasoning on", report["thinking"]))
    explain = report.get("explain") or {}
    head = "| Answers | Language | n | correct % | partial % | wrong % | not graded |"
    rule = "|---|---|---|---|---|---|---|"
    if explain:
        head += " clear % | adequate % | poor % |"
        rule += "---|---|---|"
    lines = [head, rule]
    for label, part in rows:
        ex = explain.get("thinking" if part is not report else "plain") or {}
        scopes = [("all", {"n": part["n"], **part["overall"]}, ex.get("overall") or {})]
        scopes += [
            (lang, v, (ex.get("by_lang") or {}).get(lang) or {})
            for lang, v in part["by_lang"].items()
        ]
        for scope, v, e in scopes:
            failed = part["n_failed"] if scope == "all" else v["n"] - v["n_graded"]
            line = (
                f"| {label} | {scope} | {v['n']} | {v['correct_pct']:.2f} | "
                f"{v['partial_pct']:.2f} | {v['wrong_pct']:.2f} | {failed} |"
            )
            if explain:
                line += "".join(f" {e.get(f'{g}_pct', 0.0):.2f} |" for g in EXPLAIN_GRADES)
            lines.append(line)
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Trilingual 2026 test: answers",
        "",
        (
            f"Set `{report['set']}`, answer model `{report['answer_model']}`, judge "
            f"`{report['judge_model']}`, k = {report['k']}."
        ),
        "",
    ]
    if report.get("models"):
        lines += ["## Models and settings", "", *_settings_table(report["models"]), ""]
        lines += ["## Scores", "", *_score_table(report), ""]
        lines += [
            (
                "Facts grade: `correct` = the asked facts are given and match the story the "
                "question was written from; `partial` = some right, some missing, none "
                "contradicting; `wrong` = missing, contradicting, or the answer says the sources "
                "do not cover it."
            ),
            "",
        ]
        if report.get("explain"):
            lines += [
                (
                    "Explanation grade (separate from the facts): `clear` = direct, with the "
                    "supporting details, well ordered, cited; `adequate` = answers but thin, badly "
                    "ordered or padded; `poor` = does not explain."
                ),
                "",
            ]
        lines += ["## Questions", ""]
    thinking = bool(report.get("thinking"))
    for it in report["items"]:
        level = "###" if report.get("models") else "##"
        lines += [
            f"{level} {it['qid']} ({it['lang']}): {it['grade'] or 'not graded'}",
            "",
            f"**Question:** {it.get('query_typed') or it['query']}",
        ]
        if it.get("query_typed"):
            lines.append(f"**Searched as:** {it['query']}")
        lines += [
            f"**Gold story:** {it['gold_headline']}",
            f"**Gold story in the sources:** {'yes' if it['retrieved_gold'] else 'no'}",
            "",
        ]
        if thinking or "explain" in it:
            tag = f"facts: {it['grade'] or 'not graded'}"
            if "explain" in it:
                tag += f", explanation: {it['explain'] or 'not graded'}"
            lines += [f"**Answer, reasoning off** ({tag}):", ""]
        lines += [it["answer"] or "(no answer)", ""]
        if thinking:
            tag = f"facts: {it.get('grade_thinking') or 'not graded'}"
            if "explain_thinking" in it:
                tag += f", explanation: {it['explain_thinking'] or 'not graded'}"
            lines += [
                f"**Answer, reasoning on** ({tag}):",
                "",
                it.get("answer_thinking") or "(no answer)",
                "",
                "<details><summary>Reasoning of the model</summary>",
                "",
                it.get("reasoning") or "(none recorded)",
                "",
                "</details>",
                "",
            ]
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def run_answers(
    set_file: Path | None = None,
    retrieval_file: Path | None = None,
    out: Path | None = None,
    k: int | None = None,
    search_fn: Callable[..., list[Any]] | None = None,
    opener: Callable[[str], Any] | None = None,
    story_meta: Callable[[Sequence[str]], dict[str, dict[str, Any]]] | None = None,
    covered_years: Sequence[int] | None = None,
    embed_profile: str | None = None,
    today: date | None = None,
    echo: Echo = print,
    thinking: bool = False,
    explain: bool = False,
) -> dict[str, Any]:
    """Run the three passes, write the JSON and the Markdown next to it and return the report.
    `thinking` adds the second answer by the reasoning model, `explain` the explanation grade.

    Tests replace the stores (`search_fn`, `story_meta`, `covered_years`, `embed_profile`) and
    the models (`opener(profile)` yields a client with `chat(prompt, **params) -> str`)."""
    s = config.settings
    customer.check_families()
    set_file = Path(set_file or s.eval_2026_set_file)
    retrieval_file = Path(retrieval_file or s.eval_2026_retrieval_file)
    out = Path(out or s.eval_2026_answers_file)
    k = k or s.customer_k
    today = today or datetime.now(UTC).date()
    for path in (set_file, retrieval_file):
        if not path.exists():
            raise InputError(f"missing {path}")
    questions = rt.read_set(set_file)
    report_in = json.loads(retrieval_file.read_text("utf-8"))
    translit = translit_queries(report_in, questions, set_file)
    items = [Item(dict(q), translit.get(str(q["qid"]), q["question"])) for q in questions]
    opener = opener or customer._default_opener()
    timings: dict[str, float] = {}

    # pass 1: search
    real_search = search_fn is None
    if real_search or embed_profile is None or covered_years is None:
        from newsrag.search.hybrid import profile_of
        from newsrag.warehouse import db as wdb

        generation = wdb.active_generation()
        if generation is None:
            raise RuntimeError("no active generation: run 'newsrag stage run index' first")
        embed_profile = embed_profile or profile_of(generation)
        if covered_years is None:
            covered_years = customer.load_covered_years(generation)
    if real_search:
        from newsrag import search as search_mod

        search_fn = search_mod.search
    start = time.monotonic()
    with opener(embed_profile):
        try:
            for it in items:
                it.hits = list(search_fn(it.query, k=k))
        finally:
            if real_search:
                search_mod.close()
    meta = (story_meta or customer.load_story_meta)(
        sorted({h.story_id for it in items for h in it.hits})
    )
    for it in items:
        it.sources = customer.make_sources(it.hits, meta)
        it.retrieved_gold = rt.first_correct_rank(it.hits, it.q) is not None
        if not it.sources:
            it.no_sources, it.grade = True, WRONG  # nothing to answer from: wrong, no judge call
    timings["search_s"] = round(time.monotonic() - start, 1)
    echo(f"pass 1: searched {len(items)} questions in {timings['search_s']} s")

    # pass 2: answers
    start = time.monotonic()
    todo = [it for it in items if not it.no_sources]
    if todo:
        with opener(s.answer_model) as client:

            def answer(it: Item) -> None:
                prompt = customer.answer_prompt(it.query, it.sources, today, covered_years)
                for _ in range(1 + max(0, s.customer_answer_retries)):
                    try:
                        it.answer, it.error = customer.ask_answer(client, prompt), ""
                        return
                    except Exception as e:  # noqa: BLE001 - retried, then recorded
                        it.error = f"{type(e).__name__}: {e}"[:300]
                it.failed = True

            with ThreadPoolExecutor(max_workers=max(1, s.customer_concurrency)) as ex:
                list(ex.map(answer, todo))
    timings["answer_s"] = round(time.monotonic() - start, 1)
    n_bad = sum(1 for it in items if it.failed)
    echo(f"pass 2: answered {len(todo) - n_bad}, failed {n_bad} in {timings['answer_s']} s")

    # pass 2b: the same questions answered by the reasoning model
    if thinking:
        start = time.monotonic()
        if todo:
            with opener(s.eval_2026_thinking_model) as client:

                def answer_thinking(it: Item) -> None:
                    prompt = customer.answer_prompt(it.query, it.sources, today, covered_years)
                    for _ in range(1 + max(0, s.customer_answer_retries)):
                        try:
                            it.reasoning, it.answer_thinking = ask_thinking(client, prompt)
                            it.error_thinking = ""
                            return
                        except Exception as e:  # noqa: BLE001 - retried, then recorded
                            it.error_thinking = f"{type(e).__name__}: {e}"[:300]
                    it.failed_thinking = True

                with ThreadPoolExecutor(max_workers=max(1, s.customer_concurrency)) as ex:
                    list(ex.map(answer_thinking, todo))
        for it in items:
            if it.no_sources:
                it.grade_thinking = WRONG
        timings["answer_thinking_s"] = round(time.monotonic() - start, 1)
        n_bad_t = sum(1 for it in items if it.failed_thinking)
        echo(
            f"pass 2b: reasoning model answered {len(todo) - n_bad_t}, failed {n_bad_t} "
            f"in {timings['answer_thinking_s']} s"
        )

    # pass 3: grades
    start = time.monotonic()
    work = [it for it in items if not it.no_sources and not it.failed]
    work_t = [it for it in items if thinking and not it.no_sources and not it.failed_thinking]
    if work or work_t:
        with (
            opener(customer.judge_profile()) as client,
            ThreadPoolExecutor(max_workers=max(1, s.customer_concurrency)) as ex,
        ):

            def grade(job: tuple[Item, str]) -> str | None:
                it, answer = job
                return customer.ask_label(
                    client, grade_prompt(it.query, answer, it.q["gold_text"]), GRADES
                )

            def rate(job: tuple[Item, str]) -> str | None:
                it, answer = job
                return customer.ask_label(
                    client, explain_prompt(it.q["question"], answer), EXPLAIN_GRADES
                )

            plain = [(it, it.answer) for it in work]
            thought = [(it, it.answer_thinking) for it in work_t]
            for (it, _), label in zip(plain, ex.map(grade, plain), strict=True):
                it.grade = label
            for (it, _), label in zip(thought, ex.map(grade, thought), strict=True):
                it.grade_thinking = label
            if explain:
                for (it, _), label in zip(plain, ex.map(rate, plain), strict=True):
                    it.explain = label
                for (it, _), label in zip(thought, ex.map(rate, thought), strict=True):
                    it.explain_thinking = label
    timings["judge_s"] = round(time.monotonic() - start, 1)
    echo(f"pass 3: graded {len(work) + len(work_t)} answers in {timings['judge_s']} s")

    report = {
        "set": str(set_file),
        **summarize(items),
        "k": k,
        "answer_model": s.answer_model,
        "judge_model": customer.judge_profile(),
        "embed_profile": embed_profile,
        "retrieval_file": str(retrieval_file),
        "covered_years": list(covered_years),
        "today": today.isoformat(),
        "timings": timings,
        "items": [it.record(thinking, explain) for it in items],
    }
    meta_path = rt.meta_file(set_file)
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else None
    report["models"] = model_settings(thinking, explain, k, embed_profile, meta)
    if thinking:
        report["thinking"] = {
            "answer_model": s.eval_2026_thinking_model,
            **summarize(items, "grade_thinking"),
        }
    if explain:
        answered = [it for it in items if not it.no_sources and not it.failed]
        report["explain"] = {"plain": summarize(answered, "explain", EXPLAIN_GRADES)}
        if thinking:
            answered_t = [it for it in items if not it.no_sources and not it.failed_thinking]
            report["explain"]["thinking"] = summarize(
                answered_t, "explain_thinking", EXPLAIN_GRADES
            )
    _write(out, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _write(out.with_suffix(".md"), render_markdown(report))
    return report


def summary_line(report: Mapping[str, Any]) -> str:
    o = report["overall"]
    return (
        f"correct={o['correct_pct']:.2f}% partial={o['partial_pct']:.2f}% "
        f"wrong={o['wrong_pct']:.2f}% n={report['n']} n_failed={report['n_failed']}"
    )
