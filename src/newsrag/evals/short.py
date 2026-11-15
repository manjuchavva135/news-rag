"""The short-query test: search the way the newspaper's staff search (one to five words).

Three parts, one report (`newsrag eval short`):

- **names**: a person, party, place or subject as a bare name (`short_names_file`), in Telugu
  script, in Latin letters and misspelled. No single story is the answer, so the top hits are
  scored on what they are: does the hit's story name it (`mention`), is the story about it
  (`about`: named in the headline or at least `short_about_mentions` times), how many different
  stories the hits are (`distinct`) and how many are not news or editorials (`other_kind`).
  `same_as_te` is the share of the Telugu query's stories a Latin or misspelled query also finds.
- **stories**: a 2-5 word query written by the local text model from one story, the way staff
  type (`newsrag eval build-short`); scored like the retrieval test (recall@k, MRR).
- **real**: the questions staff asked (`real_questions_file`). They are about other dates than
  the stores hold, so only the shape of the result is reported per kind of question.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import random
import statistics
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.embed.records import script_shares
from newsrag.evals import retrieval

PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "eval_short_query_v1.md"
TE, LATIN, TYPO = "te", "latin", "typo"
FORMS = (TE, LATIN, TYPO)
LANGUAGE_RULES = {
    TE: "Write the query in Telugu script.",
    LATIN: (
        "Write the query in English (Latin) letters only, the way people type in a search box: "
        "English words, or Telugu words typed in English letters. No Telugu script letters."
    ),
}
_ZERO_WIDTH = dict.fromkeys((0x200B, 0x200C, 0x200D, 0xFEFF))


# --- names ------------------------------------------------------------------------------------


def norm(text: str) -> str:
    """NFC, zero-width characters removed, runs of whitespace as one space."""
    return " ".join(unicodedata.normalize("NFC", text or "").translate(_ZERO_WIDTH).split())


def surface_forms(surfaces: Sequence[str]) -> list[str]:
    """Every surface as written and with its spaces removed ("అమిత్ షా" also as "అమిత్షా")."""
    out: list[str] = []
    for surface in surfaces:
        for form in (norm(surface), norm(surface).replace(" ", "")):
            if form and form not in out:
                out.append(form)
    return out


def count_mentions(text: str, forms: Sequence[str]) -> int:
    """How often a form occurs in `text` (already `norm`ed); a spaced form and its joined form
    never match the same place, so the counts add up."""
    return sum(text.count(form) for form in forms)


def load_names(path: Path | None = None) -> list[dict[str, Any]]:
    path = Path(path or config.settings.short_names_file)
    names = json.loads(path.read_text("utf-8"))["names"]
    for name in names:
        if not name.get("surfaces") or not name.get(TE):
            raise ValueError(f"{path}: name {name.get('id')!r} needs `te` and `surfaces`")
    return names


def name_queries(names: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One query per name and form it has, `n0001`..."""
    out = []
    for name in names:
        for form in FORMS:
            if name.get(form):
                out.append(
                    {
                        "qid": f"n{len(out) + 1:04d}",
                        "part": "name",
                        "form": form,
                        "question": name[form],
                        "name_id": name["id"],
                        "name_type": name.get("type", ""),
                    }
                )
    return out


def name_stories(
    names: Sequence[Mapping[str, Any]], stories: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, bool]]:
    """name id -> {story_id: is the story about the name} for every story that names it."""
    s = config.settings
    forms = {name["id"]: surface_forms(name["surfaces"]) for name in names}
    out: dict[str, dict[str, bool]] = {name["id"]: {} for name in names}
    for story_id, story in stories.items():
        headline, text = norm(story["headline"]), norm(story["text"])
        for name_id, name_forms in forms.items():
            in_headline = count_mentions(headline, name_forms)
            total = in_headline + count_mentions(text, name_forms)
            if total:
                out[name_id][story_id] = bool(in_headline) or total >= s.short_about_mentions
    return out


def score_name_hits(
    hits: Sequence[Any], named: Mapping[str, bool], kinds: Mapping[str, str]
) -> dict[str, Any]:
    """The shares of one name query's hits (0 hits: every share 0)."""
    n = len(hits)
    news = set(config.settings.short_news_kinds)
    story_ids = [h.story_id for h in hits]

    def share(count: int) -> float:
        return round(count / n, 4) if n else 0.0

    return {
        "hits": n,
        "mention": share(sum(1 for sid in story_ids if sid in named)),
        "about": share(sum(1 for sid in story_ids if named.get(sid))),
        "distinct": share(len(set(story_ids))),
        "other_kind": share(sum(1 for sid in story_ids if kinds.get(sid) not in news)),
        "stories": sorted(set(story_ids)),
    }


def mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0


def summarise_names(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per form: the mean shares and, for latin / typo, the mean `same_as_te`."""
    out: dict[str, dict[str, Any]] = {}
    for form in FORMS:
        rows = [r for r in records if r["form"] == form]
        if not rows:
            continue
        out[form] = {
            "n": len(rows),
            "no_hits": sum(1 for r in rows if not r["hits"]),
            **{key: mean([r[key] for r in rows]) for key in ("mention", "about", "distinct")},
            "other_kind": mean([r["other_kind"] for r in rows]),
        }
        if form != TE:
            out[form]["same_as_te"] = mean([r["same_as_te"] for r in rows])
    return out


# --- story queries ----------------------------------------------------------------------------


def check_query(query: Any, form: str, headline: str) -> str | None:
    """Why the query can't be used, None when it can."""
    s = config.settings
    if not isinstance(query, str) or not query.strip():
        return "empty"
    n_words = len(query.split())
    if not 2 <= n_words <= s.short_max_words:
        return f"{n_words} words, not 2 to {s.short_max_words}"
    telugu, latin = script_shares(query)
    if form == TE and telugu < s.retrieval_lang_share:
        return "not in Telugu script"
    if form == LATIN and (telugu > 0 or latin < s.retrieval_lang_share):
        return "not in Latin letters only"
    if query.rstrip().endswith("?"):
        return "a question, not a query"
    if retrieval.copies_headline(query, headline, s.short_copy_words):
        return "copies the headline"
    return None


def query_prompt(story: Mapping[str, Any], form: str, problem: str | None = None) -> str:
    s = config.settings
    body = f"{story['headline']}\n\n{story['text']}"[: s.retrieval_story_chars]
    rejected = (
        f"Your previous query was rejected ({problem}). Write a different one.\n" if problem else ""
    )
    return (
        PROMPT.read_text("utf-8")
        .replace("{max_words}", str(s.short_max_words))
        .replace("{language_rule}", LANGUAGE_RULES[form])
        .replace("{problem}", rejected)
        .replace("{story}", body)
    )


def write_query(story: Mapping[str, Any], form: str, ask: retrieval.Ask) -> str | None:
    """Up to `retrieval_max_tries` attempts; None when none gave a usable query."""
    problem = None
    for _ in range(max(1, config.settings.retrieval_max_tries)):
        try:
            reply = ask(query_prompt(story, form, problem))
        except Exception as e:  # noqa: BLE001 - a failed request counts as a try
            problem = f"request failed: {e}"[:200]
            continue
        query = retrieval.parse_reply(reply)
        problem = check_query(query, form, story["headline"])
        if problem is None:
            return " ".join(str(query).split())
    return None


def build_short_set(
    n: int | None = None,
    seed: int | None = None,
    out: Path | None = None,
    ask: retrieval.Ask | None = None,
    stories: Sequence[Mapping[str, Any]] | None = None,
    names: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write the name queries and `n` story queries (half Telugu, half Latin, one story each) to
    `out` as JSON lines, with `<out>.meta.json`. `ask` defaults to `enrich_model`."""
    s = config.settings
    n = s.short_story_n if n is None else n
    seed = s.short_seed if seed is None else seed
    out = Path(out or s.short_set_file)
    names = list(names) if names is not None else load_names()
    pool = [
        st
        for st in (stories if stories is not None else retrieval.load_stories())
        if st["kind"] in s.short_news_kinds
        and st["pub_date"] >= s.short_from_date
        and len(st["text"]) >= s.short_min_story_chars
    ]
    random.Random(seed).shuffle(pool)
    forms = [TE if i % 2 == 0 else LATIN for i in range(n)]
    lines = name_queries(names)
    replaced = 0
    with contextlib.ExitStack() as stack:
        if ask is None and n:
            from newsrag import models

            ask = retrieval.model_ask(stack.enter_context(models.use(s.enrich_model)))
        spare = iter(pool[n:])
        pending = list(enumerate(pool[:n]))
        done: dict[int, tuple[Mapping[str, Any], str]] = {}
        with ThreadPoolExecutor(max_workers=max(1, s.retrieval_concurrency)) as ex:
            while pending:
                found = list(
                    ex.map(lambda item: write_query(item[1], forms[item[0]], ask), pending)
                )
                retry = []
                for (i, story), query in zip(pending, found, strict=True):
                    if query is not None:
                        done[i] = (story, query)
                        continue
                    replaced += 1
                    nxt = next(spare, None)
                    if nxt is not None:
                        retry.append((i, nxt))
                pending = retry
    for number, i in enumerate(sorted(done), start=1):
        story, query = done[i]
        lines.append(
            {
                "qid": f"s{number:04d}",
                "part": "story",
                "form": forms[i],
                "lang": forms[i],
                "question": query,
                "story_id": story["story_id"],
                "gold_page_ids": sorted(story["page_ids"]),
                "gold_text": f"{story['headline']}\n{story['text']}",
                "pub_date": story["pub_date"].isoformat(),
            }
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), "utf-8")
    tmp.replace(out)
    summary = {
        "out": str(out),
        "names": sum(1 for x in lines if x["part"] == "name"),
        "stories": len(done),
        "by_form": {f: sum(1 for x in lines if x["form"] == f) for f in FORMS},
        "replaced": replaced,
        "stories_available": len(pool),
    }
    meta = {
        **{k: v for k, v in summary.items() if k != "out"},
        "names_file": str(s.short_names_file),
        "seed": seed,
        "from_date": s.short_from_date.isoformat(),
        "writer_model": s.enrich_model,
        "prompt": PROMPT.name,
        "max_words": s.short_max_words,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha1": hashlib.sha1(out.read_bytes()).hexdigest(),
    }
    retrieval.meta_file(out).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    return summary


# --- the run ----------------------------------------------------------------------------------


def load_story_info() -> dict[str, dict[str, Any]]:
    """story_id -> {kind, headline, text} for every story with chunks in the active generation."""
    from newsrag.warehouse import db as wdb

    generation = wdb.active_generation()
    if generation is None:
        raise RuntimeError("no active generation: run 'newsrag stage run index' first")
    with wdb.conn() as c:
        rows = wdb.fetch_all(
            c,
            "select s.story_id, s.kind, s.headline, s.text from stories s where exists "
            "(select 1 from chunks c where c.generation = %s and c.story_id = s.story_id)",
            (generation,),
        )
    return {r["story_id"]: dict(r) for r in rows}


def read_real(path: Path | None = None) -> list[dict[str, Any]]:
    path = Path(path or config.settings.real_questions_file)
    return retrieval.read_set(path) if path.is_file() else []


def summarise_real(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per kind of real question (and `all`): how many found nothing and the mean shares."""
    out: dict[str, dict[str, Any]] = {}
    kinds = ["all", *sorted({r["kind"] for r in records})]
    for kind in kinds:
        rows = [r for r in records if kind == "all" or r["kind"] == kind]
        found = [r for r in rows if r["hits"]]
        out[kind] = {
            "n": len(rows),
            "no_hits": len(rows) - len(found),
            "distinct": mean([r["distinct"] for r in found]),
            "other_kind": mean([r["other_kind"] for r in found]),
        }
    return out


def run_short(
    set_file: Path | None = None,
    out: Path | None = None,
    search_fn: Callable[..., list[Any]] | None = None,
    stories: Mapping[str, Mapping[str, Any]] | None = None,
    names: Sequence[Mapping[str, Any]] | None = None,
    real: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Search every query of the set and every real question, write the report and return it."""
    from newsrag import search as search_mod

    s = config.settings
    set_file = Path(set_file or s.short_set_file)
    queries = retrieval.read_set(set_file)
    names = list(names) if names is not None else load_names()
    stories = stories if stories is not None else load_story_info()
    real = list(real) if real is not None else read_real()
    kinds = {sid: st["kind"] for sid, st in stories.items()}
    named = name_stories(names, stories)
    k = s.short_k
    real_search = search_fn is None
    search_fn = search_fn or search_mod.search

    name_records: list[dict[str, Any]] = []
    story_records: list[dict[str, Any]] = []
    real_records: list[dict[str, Any]] = []
    ranks: dict[str, list[int | None]] = {}
    try:
        for q in queries:
            hits = search_fn(q["question"], k=k)
            top1 = " ".join((hits[0].headline or "").split()) if hits else None
            if q["part"] == "name":
                scores = score_name_hits(hits, named.get(q["name_id"], {}), kinds)
                name_records.append(
                    {
                        **{key: q[key] for key in ("qid", "form", "question", "name_id")},
                        "named_stories": len(named.get(q["name_id"], {})),
                        **scores,
                        "top1_headline": top1,
                    }
                )
            else:
                gold = retrieval.ngrams(q["gold_text"], s.retrieval_ngram)
                rank = retrieval.first_correct_rank(hits, q, gold)
                ranks.setdefault(q["form"], []).append(rank)
                story_records.append(
                    {
                        **{key: q[key] for key in ("qid", "form", "question", "story_id")},
                        "rank": rank,
                        "top1_headline": top1,
                    }
                )
        for q in real:
            hits = search_fn(q["question"], k=k)
            scores = score_name_hits(hits, {}, kinds)
            real_records.append(
                {
                    **{key: q[key] for key in ("qid", "kind", "lang", "words", "question")},
                    **{key: scores[key] for key in ("hits", "distinct", "other_kind")},
                    "top1_headline": " ".join((hits[0].headline or "").split()) if hits else None,
                }
            )
    finally:
        if real_search:
            search_mod.close()

    te_stories = {r["name_id"]: set(r["stories"]) for r in name_records if r["form"] == TE}
    for record in name_records:
        base = te_stories.get(record["name_id"]) or set()
        found = set(record.pop("stories"))
        if record["form"] != TE:
            record["same_as_te"] = round(len(found & base) / len(base), 4) if base else 0.0
    all_ranks = [rank for form_ranks in ranks.values() for rank in form_ranks]
    report = {
        "set": str(set_file),
        "k": k,
        "about_mentions": s.short_about_mentions,
        "generation": retrieval._generation("new") if real_search else None,
        "names": summarise_names(name_records),
        "stories": {
            "all": retrieval.metrics(all_ranks, k),
            **{
                form: retrieval.metrics(form_ranks, k) for form, form_ranks in sorted(ranks.items())
            },
        },
        "real": summarise_real(real_records),
        "per_name": name_records,
        "per_story": story_records,
        "per_real": real_records,
    }
    out = Path(out or s.short_report_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return report


def summary_lines(report: Mapping[str, Any]) -> list[str]:
    lines = [f"names (top {report['k']}):"]
    for form, m in report["names"].items():
        same = f" same_as_te={m['same_as_te']:.2f}" if "same_as_te" in m else ""
        lines.append(
            f"  {form:<5} n={m['n']} no_hits={m['no_hits']} mention={m['mention']:.2f} "
            f"about={m['about']:.2f} distinct={m['distinct']:.2f} "
            f"other_kind={m['other_kind']:.2f}{same}"
        )
    lines.append("stories:")
    for form, m in report["stories"].items():
        lines.append(
            f"  {form:<5} n={m['n']} recall@1={m['recall_at_1']:.2f} "
            f"recall@5={m['recall_at_5']:.2f} recall@10={m['recall_at_10']:.2f} mrr={m['mrr']:.2f}"
        )
    lines.append("real questions:")
    for kind, m in report["real"].items():
        lines.append(
            f"  {kind:<12} n={m['n']} no_hits={m['no_hits']} distinct={m['distinct']:.2f} "
            f"other_kind={m['other_kind']:.2f}"
        )
    return lines
