"""The graded answer check of the trilingual 2026 test (ticket 034 FR-4).

Three passes, one model at a time (as in ticket 028):

1. `search(k=customer_k)` for every question (a `te_latn` question with its transliterated text
   from the retrieval report) inside one embed scope;
2. `use(answer_model)`: an answer from the numbered sources (the 028 prompt);
3. `use(answer_judge_model)`: `correct` | `partial` | `wrong` against the story the question was
   written from.

Accuracy = share of the graded answers that are `correct`.
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
CORRECT, PARTIAL, WRONG = "correct", "partial", "wrong"
GRADES = (CORRECT, PARTIAL, WRONG)
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

    def record(self) -> dict[str, Any]:
        out = {
            "qid": self.q["qid"],
            "lang": self.q["lang"],
            "query": self.query,
            "answer": self.answer,
            "grade": self.grade,
            "retrieved_gold": self.retrieved_gold,
            "no_sources": self.no_sources,
            "gold_headline": self.q["gold_text"].split("\n", 1)[0],
        }
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


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def shares(items: Sequence[Item]) -> dict[str, Any]:
    graded = [it for it in items if it.grade is not None]
    out: dict[str, Any] = {"n": len(items), "n_graded": len(graded)}
    for g in GRADES:
        out[f"{g}_pct"] = _pct(sum(1 for it in graded if it.grade == g), len(graded))
    return out


def summarize(items: Sequence[Item]) -> dict[str, Any]:
    overall = shares(items)
    return {
        "n": len(items),
        "n_failed": sum(1 for it in items if it.grade is None),
        "overall": {f"{g}_pct": overall[f"{g}_pct"] for g in GRADES},
        "by_lang": {
            lang: shares([it for it in items if it.q["lang"] == lang])
            for lang in LANGS
            if any(it.q["lang"] == lang for it in items)
        },
    }


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
    for it in report["items"]:
        lines += [
            f"## {it['qid']} ({it['lang']}): {it['grade'] or 'not graded'}",
            "",
            f"**Question:** {it.get('query_typed') or it['query']}",
        ]
        if it.get("query_typed"):
            lines.append(f"**Searched as:** {it['query']}")
        lines += [
            f"**Gold story:** {it['gold_headline']}",
            f"**Gold story in the sources:** {'yes' if it['retrieved_gold'] else 'no'}",
            "",
            it["answer"] or "(no answer)",
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
) -> dict[str, Any]:
    """Run the three passes, write the JSON and the Markdown next to it and return the report.

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

    # pass 3: grades
    start = time.monotonic()
    work = [it for it in items if not it.no_sources and not it.failed]
    if work:
        with (
            opener(customer.judge_profile()) as client,
            ThreadPoolExecutor(max_workers=max(1, s.customer_concurrency)) as ex,
        ):

            def grade(it: Item) -> str | None:
                return customer.ask_label(
                    client, grade_prompt(it.query, it.answer, it.q["gold_text"]), GRADES
                )

            for it, label in zip(work, ex.map(grade, work), strict=True):
                it.grade = label
    timings["judge_s"] = round(time.monotonic() - start, 1)
    echo(f"pass 3: graded {len(work)} answers in {timings['judge_s']} s")

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
        "items": [it.record() for it in items],
    }
    _write(out, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _write(out.with_suffix(".md"), render_markdown(report))
    return report


def summary_line(report: Mapping[str, Any]) -> str:
    o = report["overall"]
    return (
        f"correct={o['correct_pct']:.2f}% partial={o['partial_pct']:.2f}% "
        f"wrong={o['wrong_pct']:.2f}% n={report['n']} n_failed={report['n_failed']}"
    )
