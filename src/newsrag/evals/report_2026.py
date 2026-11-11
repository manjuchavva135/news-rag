"""`newsrag eval report-2026` (ticket 034 FR-5): one Markdown page with the per-language numbers
of the trilingual 2026 test and exactly how each was measured.

Every value comes from the files the runs wrote (the set and its `.meta.json`, the retrieval and
answers reports), never from the current settings.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.evals import retrieval as rt

EXAMPLES = 3
# (table row, retrieval by_lang key, answers by_lang key or None, per_question rank/top1/query keys)
ROWS = (
    ("te", "te", "te", ("rank", "top1_headline", "query")),
    ("en", "en", "en", ("rank", "top1_headline", "query")),
    ("te_latn", "te_latn", None, ("rank", "top1_headline", "query")),
    (
        "te_latn (transliterated)",
        rt.TRANSLIT,
        "te_latn",
        ("rank_translit", "top1_headline_translit", "translit_query"),
    ),
)


class MissingInput(FileNotFoundError):
    pass


def _read(path: Path) -> Any:
    if not Path(path).exists():
        raise MissingInput(f"missing {path}")
    return json.loads(Path(path).read_text("utf-8"))


def _f(x: Any, digits: int = 4) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def table(retrieval: Mapping[str, Any], answers: Mapping[str, Any]) -> list[str]:
    lines = [
        (
            "| language | n | recall@1 | recall@5 | recall@10 | MRR | answers correct % | "
            "partial % | wrong % |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, rkey, akey, _ in ROWS:
        r = (retrieval.get("by_lang") or {}).get(rkey)
        if not r or not r.get("n"):
            continue
        a = (answers.get("by_lang") or {}).get(akey) if akey else None
        cells = [
            name,
            str(r["n"]),
            _f(r.get("recall_at_1")),
            _f(r.get("recall_at_5")),
            _f(r.get("recall_at_10")),
            _f(r.get("mrr")),
            _f(a.get("correct_pct"), 2) if a else "-",
            _f(a.get("partial_pct"), 2) if a else "-",
            _f(a.get("wrong_pct"), 2) if a else "-",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def examples(retrieval: Mapping[str, Any], gold: Mapping[str, str]) -> list[str]:
    per = retrieval.get("per_question") or []
    out: list[str] = []
    for name, _, _, (rank_key, top_key, query_key) in ROWS:
        lang = "te_latn" if name.startswith("te_latn") else name
        rows = [p for p in per if p["lang"] == lang and query_key in p]
        if not rows:
            continue
        out += [f"### {name}", ""]
        for title, pick in (
            ("Correct top-1 hit", lambda p, rk=rank_key: p.get(rk) == 1),
            ("No correct hit in the top 10", lambda p, rk=rank_key: p.get(rk) is None),
        ):
            chosen = [p for p in rows if pick(p)][:EXAMPLES]
            note = f" (only {len(chosen)})" if len(chosen) < EXAMPLES else ""
            out += [f"{title}{note}:", ""]
            for p in chosen:
                out.append(
                    f"- `{p['qid']}` {p[query_key]} | gold: {gold.get(p['qid'], '-')} | "
                    f"top-1: {p.get(top_key) or '-'}"
                )
            out.append("")
    return out


def method(
    set_file: Path,
    meta: Mapping[str, Any],
    retrieval: Mapping[str, Any],
    answers: Mapping[str, Any],
) -> list[str]:
    sha1 = hashlib.sha1(Path(set_file).read_bytes()).hexdigest()
    strata = ", ".join(f"{k}: {v}" for k, v in (meta.get("strata") or {}).items()) or "-"
    k = retrieval.get("k")
    return [
        "## How it was measured",
        "",
        (
            f"- **Question set:** `{set_file}` (sha1 `{sha1}`), {meta.get('n')} questions, "
            f"per language {meta.get('by_lang')}, seed {meta.get('seed')}, built "
            f"{meta.get('built_at')}."
        ),
        (
            f"- **Stories:** printed on or after {meta.get('from_date')}, "
            f"{meta.get('stories_available')} stories available (kinds news / editorial / "
            f"notice_tender with chunks in the active generation), spread over the year-months "
            f"({strata}); one question per story."
        ),
        (
            f"- **Writer:** the local model `{meta.get('writer_model')}` (enrich_model) wrote each "
            "question from one story, in its own words, at temperature 0. A question sharing 4+ "
            "consecutive words with the headline, or not in its language, was asked again (3 tries), "
            "then the story was replaced. Romanized Telugu: Latin letters >= 50%, no Telugu letter, "
            "at least 2 common Telugu words from `data/eval/te-latn-markers.txt`; an English question "
            "that passes that test is rejected."
        ),
        (
            f"- **Search:** hybrid (dense + BM25, reciprocal-rank fusion) over generation "
            f"`{retrieval.get('generation')}`, top k = {k}."
        ),
        (
            f"- **Correct hit:** the hit's pages overlap the story's pages **and** at least "
            f"{retrieval.get('hit_overlap')} of the hit text's character {retrieval.get('ngram')}-grams "
            "(NFC, whitespace removed) occur in the story text."
        ),
        (
            "- **recall@k:** share of the questions with a correct hit in the top k (k = 1, 5, 10). "
            f"**MRR:** mean of 1 / rank of the first correct hit, 0 when none is in the top {k}."
        ),
        (
            f"- **Transliteration:** Romanized questions were rewritten in Telugu script by "
            f"`{retrieval.get('translit_model')}` (prompt `translit_v1`) and searched again; the "
            "`te_latn` row is as typed, `te_latn (transliterated)` after the rewrite."
        ),
        (
            f"- **Answers:** `{answers.get('answer_model')}` answered each question from its top "
            f"{answers.get('k')} hits (ticket 028 prompt; Romanized questions with the transliterated "
            f"text). `{answers.get('judge_model')}` (answer_judge_model, another model family) "
            "graded each answer against the story the question was written from: **correct** = the "
            "asked facts are given and match the story; **partial** = some asked facts right, some "
            "missing, none contradicting; **wrong** = missing, contradicting, or 'not covered'. "
            "No hits = wrong. Percentages are over graded answers "
            f"({answers.get('n_failed')} not graded). Accuracy = correct %."
        ),
        (
            "- **Caveat:** the questions are synthetic (written by an LLM from one story each, so "
            "each has a known answer). These numbers estimate search and answer quality on this "
            "corpus; they are not the accuracy on real customer questions."
        ),
        "",
    ]


def build(
    set_file: Path | None = None,
    retrieval_file: Path | None = None,
    answers_file: Path | None = None,
    out: Path | None = None,
) -> Path:
    s = config.settings
    set_file = Path(set_file or s.eval_2026_set_file)
    meta_path = rt.meta_file(set_file)
    for path in (set_file, meta_path):
        if not path.exists():
            raise MissingInput(f"missing {path}")
    meta = _read(meta_path)
    retrieval = _read(Path(retrieval_file or s.eval_2026_retrieval_file))
    answers = _read(Path(answers_file or s.eval_2026_answers_file))
    gold = {str(q["qid"]): q["gold_text"].split("\n", 1)[0].strip() for q in rt.read_set(set_file)}
    lines = [
        "# Trilingual 2026 test",
        "",
        (
            "Search and answer accuracy per language on 300 synthetic questions from the 2026 "
            "stories. Accuracy = answers graded correct."
        ),
        "",
        *table(retrieval, answers),
        "",
        *method(set_file, meta, retrieval, answers),
        "## Examples",
        "",
        *examples(retrieval, gold),
    ]
    out = Path(out or s.eval_2026_report_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


__all__: Sequence[str] = ("MissingInput", "build")
