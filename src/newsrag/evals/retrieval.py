"""The retrieval test (ticket 025 FR-3, FR-4): a set of questions written by the local text
model from sampled stories, and its scoring with recall@k and MRR.

A hit is correct when its pages meet the story's pages **and** enough of its text lies in the
story's text (character n-grams), so stores with different chunking and ids score alike (A-31).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import random
import re
import statistics
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.embed.records import script_shares

Ask = Callable[[str], str]
StoryFilter = Callable[[Mapping[str, Any]], bool]
WINDOW = "window"
LANG_NAMES = {"te": "Telugu", "en": "English", "te_latn": "Romanized Telugu"}
TE_LATN, TRANSLIT = "te_latn", "te_latn_translit"
_LATIN_WORD = re.compile(r"[a-z]+")
_TELUGU_LETTER = re.compile(r"[\u0c00-\u0c7f]")

# --- the hit rule -----------------------------------------------------------------------------


def ngrams(text: str, n: int) -> set[str]:
    """Distinct character n-grams of `text` after NFC and with all whitespace removed."""
    flat = "".join(unicodedata.normalize("NFC", text or "").split())
    return {flat[i : i + n] for i in range(len(flat) - n + 1)}


def overlap_share(hit_text: str, gold: set[str] | str, n: int) -> float:
    """Share of the hit text's n-grams that occur in the gold text (0 for a too-short hit)."""
    grams = ngrams(hit_text, n)
    if not grams:
        return 0.0
    gold_grams = gold if isinstance(gold, set) else ngrams(gold, n)
    return len(grams & gold_grams) / len(grams)


def is_correct(
    hit_page_ids: Iterable[str],
    hit_text: str,
    gold_page_ids: Iterable[str],
    gold_text: set[str] | str,
    n: int | None = None,
    overlap: float | None = None,
) -> bool:
    """FR-4: the hit's pages intersect the gold pages and >= `overlap` of its n-grams are in the
    gold text."""
    s = config.settings
    if not set(hit_page_ids) & set(gold_page_ids):
        return False
    return overlap_share(hit_text, gold_text, n or s.retrieval_ngram) >= (
        s.retrieval_hit_overlap if overlap is None else overlap
    )


def first_correct_rank(
    hits: Sequence[Any], question: Mapping[str, Any], gold_grams: set[str] | None = None
) -> int | None:
    """1-based rank of the first correct hit, None when no hit is correct."""
    gold = gold_grams if gold_grams is not None else question["gold_text"]
    for rank, hit in enumerate(hits, start=1):
        if is_correct(hit.page_ids, hit.text, question["gold_page_ids"], gold):
            return rank
    return None


def metrics(ranks: Sequence[int | None], k: int) -> dict[str, float]:
    """recall@k (share of questions with a correct hit in the top k) and MRR over them, plus
    recall@1 and recall@5 (ticket 034)."""
    n = len(ranks)
    if not n:
        return {"n": 0, "recall_at_10": 0.0, "mrr": 0.0, "recall_at_1": 0.0, "recall_at_5": 0.0}
    found = [r for r in ranks if r is not None and r <= k]
    return {
        "n": n,
        "recall_at_10": round(len(found) / n, 4),
        "mrr": round(sum(1 / r for r in found) / n, 4),
        "recall_at_1": round(sum(1 for r in found if r <= 1) / n, 4),
        "recall_at_5": round(sum(1 for r in found if r <= 5) / n, 4),
    }


# --- question checks --------------------------------------------------------------------------


def words(text: str) -> list[str]:
    """Lower-cased NFC words with punctuation and symbols stripped from their ends."""
    out = []
    for raw in unicodedata.normalize("NFC", text or "").lower().split():
        word = "".join(ch for ch in raw if unicodedata.category(ch)[0] not in "PS")
        if word:
            out.append(word)
    return out


def copies_headline(question: str, headline: str, run: int | None = None) -> bool:
    """True when the question shares a run of >= `run` consecutive words with the headline."""
    run = run or config.settings.retrieval_copy_words
    q, h = words(question), words(headline)
    if len(q) < run or len(h) < run:
        return False
    head_runs = {tuple(h[i : i + run]) for i in range(len(h) - run + 1)}
    return any(tuple(q[i : i + run]) in head_runs for i in range(len(q) - run + 1))


_markers_cache: dict[tuple[str, int], frozenset[str]] = {}


def te_latn_markers(path: Path | None = None) -> frozenset[str]:
    """The common Romanized Telugu words of `te_latn_markers_file` (one per line)."""
    path = Path(path or config.settings.te_latn_markers_file)
    key = (str(path), path.stat().st_mtime_ns)
    if key not in _markers_cache:
        words_ = path.read_text("utf-8").split()
        _markers_cache[key] = frozenset(w.strip().lower() for w in words_ if w.strip())
    return _markers_cache[key]


def is_romanized_telugu(question: str) -> bool:
    """Latin-letter share >= `retrieval_lang_share`, no Telugu letter and at least
    `retrieval_te_latn_min_markers` words from the marker list (ticket 034 FR-1)."""
    s = config.settings
    if _TELUGU_LETTER.search(question):
        return False
    _, latin = script_shares(question)
    if latin < s.retrieval_lang_share:
        return False
    markers = te_latn_markers()
    found = sum(1 for w in _LATIN_WORD.findall(question.lower()) if w in markers)
    return found >= s.retrieval_te_latn_min_markers


def check_question(question: Any, lang: str, headline: str) -> str | None:
    """Why the question can't be used, None when it can."""
    if not isinstance(question, str) or len(question.strip()) < 8:
        return "empty"
    telugu, latin = script_shares(question)
    share = config.settings.retrieval_lang_share
    if (lang == "te" and telugu < share) or (lang == "en" and latin < share):
        return f"not in {LANG_NAMES[lang]}"
    if lang == "en" and is_romanized_telugu(question):
        return "not in English"
    if lang == TE_LATN and not is_romanized_telugu(question):
        return "not in Romanized Telugu"
    if copies_headline(question, headline):
        return "copies the headline"
    return None


def question_prompt(story: Mapping[str, Any], lang: str, problem: str | None = None) -> str:
    s = config.settings
    body = f"{story['headline']}\n\n{story['text']}"[: s.retrieval_story_chars]
    language = LANG_NAMES[lang]
    lines = [
        "You write test questions for a search engine over a Telugu newspaper archive.",
        (
            f"Read the newspaper story below and write ONE question in {language} that a "
            "reader could answer only from this story (name its specific people, places, "
            "numbers or events, so no other story answers it)."
        ),
        "Use your own words: paraphrase. Do not copy phrases from the headline.",
        _language_rule(lang, language),
        'Reply with JSON only: {"question": "..."}',
    ]
    if problem:
        lines.append(f"Your previous question was rejected ({problem}). Write a different one.")
    return "\n".join(lines) + f"\n\nStory:\n{body}"


def _language_rule(lang: str, language: str) -> str:
    if lang == "te":
        return f"Write the question in {language} script."
    if lang == TE_LATN:
        return (
            "Write the question the way people type Telugu on a phone keyboard: Telugu words "
            "in English (Latin) letters, for example 'Tirumala lo darshanam tokens eppudu "
            "istharu?'. Common English words (tokens, results, MLA, collector, ...) may stay "
            "English. Do not use any Telugu script letters."
        )
    return f"Write the question in {language}, even though the story is in Telugu."


RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "question",
        "schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
}


def parse_reply(reply: str) -> Any:
    from newsrag.models.client import parse_json_reply

    try:
        data = parse_json_reply(reply)
    except ValueError:
        return None
    return data.get("question") if isinstance(data, dict) else None


def write_question(story: Mapping[str, Any], lang: str, ask: Ask) -> str | None:
    """Up to `retrieval_max_tries` attempts; None when none gave a usable question."""
    problem = None
    for _ in range(max(1, config.settings.retrieval_max_tries)):
        try:
            reply = ask(question_prompt(story, lang, problem))
        except Exception as e:  # noqa: BLE001 - a failed request counts as a try
            problem = f"request failed: {e}"[:200]
            continue
        question = parse_reply(reply)
        problem = check_question(question, lang, story["headline"])
        if problem is None:
            return " ".join(str(question).split())
    return None


# --- the stories ------------------------------------------------------------------------------


def load_stories(generation: str | None = None) -> list[dict[str, Any]]:
    """Stories of the kinds `retrieval_kinds` that have chunks in the generation (default the
    active one), with the pages of all their printings."""
    from newsrag.warehouse import db as wdb

    generation = generation or wdb.active_generation()
    if generation is None:
        raise RuntimeError("no active generation: run 'newsrag stage run index' first")
    with wdb.conn() as c:
        rows = wdb.fetch_all(
            c,
            "select s.story_id, s.pub_date, s.kind, s.headline, s.text, "
            " coalesce((select array_agg(distinct p.page_id order by p.page_id) "
            "  from story_printings p where p.story_id = s.story_id), '{}') as page_ids "
            "from stories s where s.kind = any(%s) and exists "
            " (select 1 from chunks c where c.generation = %s and c.story_id = s.story_id) "
            "order by s.story_id",
            (list(config.settings.retrieval_kinds), generation),
        )
    return [dict(r, page_ids=list(r["page_ids"])) for r in rows]


def in_window(day: date) -> bool:
    s = config.settings
    return s.retrieval_window_start <= day <= s.retrieval_window_end


def stratum_of(day: date) -> str:
    return WINDOW if in_window(day) else f"{day.year:04d}-{day.month:02d}"


def even_quotas(sizes: Mapping[str, int], total: int) -> dict[str, int]:
    """Spread `total` as evenly as the stratum sizes allow (water-filling; the remainder goes to
    the earliest strata). A stratum smaller than its share gives all it has."""
    quotas = dict.fromkeys(sizes, 0)
    left = total
    open_ = sorted(name for name, size in sizes.items() if size > 0)
    while left > 0 and open_:
        share, extra = divmod(left, len(open_))
        for i, name in enumerate(open_):
            want = share + (1 if i < extra else 0)
            take = min(want, sizes[name] - quotas[name])
            quotas[name] += take
            left -= take
        open_ = [name for name in open_ if quotas[name] < sizes[name]]
    return quotas


@dataclass
class Stratum:
    name: str
    quota: int
    stories: list[dict[str, Any]] = field(default_factory=list)  # in seeded order
    next: int = 0  # index of the next unused story

    def take(self) -> dict[str, Any] | None:
        if self.next >= len(self.stories):
            return None
        self.next += 1
        return self.stories[self.next - 1]


def plan_strata(
    stories: Sequence[Mapping[str, Any]], n: int, window_n: int, rng: random.Random
) -> tuple[list[Stratum], list[str]]:
    """The strata (window first, then year-months in order) with their quotas, and the names of
    the strata that have fewer stories than their share. `window_n == 0`: no window stratum,
    window stories count in their months (ticket 034)."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for story in sorted(stories, key=lambda x: x["story_id"]):
        day = story["pub_date"]
        name = stratum_of(day) if window_n else f"{day.year:04d}-{day.month:02d}"
        by_name.setdefault(name, []).append(dict(story))
    window = by_name.pop(WINDOW, [])
    months = dict(sorted(by_name.items()))
    short = []
    window_quota = min(window_n, len(window))
    if window_quota < window_n:
        short.append(WINDOW)
    # (with window_n == 0 the window stratum is empty and dropped below)
    other_total = max(0, n - window_n)
    quotas = even_quotas({m: len(v) for m, v in months.items()}, other_total)
    if months:
        # a month with fewer stories than an even share gives them all (the others fill in)
        even = other_total / len(months)
        short.extend(m for m in months if len(months[m]) < even)
    strata = [Stratum(WINDOW, window_quota, window)] + [
        Stratum(m, quotas[m], v) for m, v in months.items()
    ]
    for stratum in strata:
        rng.shuffle(stratum.stories)
    return [st for st in strata if st.quota > 0], short


def parse_langs(spec: str, n: int) -> dict[str, int]:
    """`te:100,en:100,te_latn:100` -> {code: count}; the counts must sum to `n` (ValueError)."""
    out: dict[str, int] = {}
    for part in spec.split(","):
        code, sep, count = part.strip().partition(":")
        code = code.strip()
        if not sep or not count.strip().isdigit():
            raise ValueError(f"bad --langs {spec!r}: use code:count,code:count")
        if code not in LANG_NAMES:
            raise ValueError(f"unknown language {code}")
        out[code] = out.get(code, 0) + int(count)
    total = sum(out.values())
    if total != n:
        raise ValueError(f"--langs counts must sum to --n ({total} != {n})")
    return out


def scale_langs(counts: Mapping[str, int], total: int) -> dict[str, int]:
    """`counts` cut down to `total` slots in proportion (largest remainder; ties in the order
    given)."""
    want = sum(counts.values())
    if total >= want:
        return dict(counts)
    exact = {code: c * total / want for code, c in counts.items()}
    out = {code: int(x) for code, x in exact.items()}
    order = sorted(counts, key=lambda c: -(exact[c] - out[c]))
    for code in order[: total - sum(out.values())]:
        out[code] += 1
    return out


def model_ask(client: Any) -> Ask:
    """`ask(prompt) -> reply` on a `ModelClient` at temperature 0, JSON-constrained."""
    s = config.settings

    def ask(prompt: str) -> str:
        return client.chat(
            prompt,
            temperature=0,
            max_tokens=s.retrieval_max_tokens,
            response_format=RESPONSE_FORMAT,
        )

    return ask


def build_retrieval_set(
    n: int | None = None,
    seed: int | None = None,
    out: Path | None = None,
    window_n: int | None = None,
    story_filter: StoryFilter | None = None,
    ask: Ask | None = None,
    stories: Sequence[Mapping[str, Any]] | None = None,
    echo: Callable[[str], None] = print,
    langs: Mapping[str, int] | None = None,
    from_date: date | None = None,
) -> dict[str, Any]:
    """FR-3: write the question set to `out` (JSON lines) and return a summary.

    Ticket 034: `langs` = exact questions per language code (summing to n), `from_date` keeps
    only stories printed on or after it; a `<out>.meta.json` records how the set was built.

    `story_filter(story) -> bool` keeps only some stories (ticket 027). `ask(prompt) -> reply`
    defaults to `enrich_model`; `stories` defaults to `load_stories()` (tests pass both)."""
    s = config.settings
    n = s.retrieval_n if n is None else n
    seed = s.retrieval_seed if seed is None else seed
    window_n = s.retrieval_window_n if window_n is None else window_n
    out = Path(out or s.retrieval_set_file)
    pool = list(stories if stories is not None else load_stories())
    if story_filter is not None:
        pool = [st for st in pool if story_filter(st)]
    if from_date is not None:
        pool = [st for st in pool if st["pub_date"] >= from_date]
    if langs is not None and sum(langs.values()) != n:
        raise ValueError(f"--langs counts must sum to --n ({sum(langs.values())} != {n})")

    rng = random.Random(seed)
    strata, short = plan_strata(pool, n, window_n, rng)
    slots = [st for st in strata for _ in range(st.quota)]  # one entry per question
    lang_short: dict[str, int] = {}
    if langs is None:
        n_te = round(len(slots) * s.retrieval_te_share)
        slot_langs = ["te"] * n_te + ["en"] * (len(slots) - n_te)
    else:
        counts = scale_langs(langs, len(slots))
        lang_short = {c: langs[c] - counts[c] for c in langs if langs[c] > counts[c]}
        slot_langs = [code for code, count in counts.items() for _ in range(count)]
    rng.shuffle(slot_langs)
    langs_of = slot_langs

    with contextlib.ExitStack() as stack:
        if ask is None and slots:
            from newsrag import models

            ask = model_ask(stack.enter_context(models.use(s.enrich_model)))
        done: dict[int, tuple[dict[str, Any], str]] = {}
        pending = [(i, st.take()) for i, st in enumerate(slots)]
        replaced = 0
        with ThreadPoolExecutor(max_workers=max(1, s.retrieval_concurrency)) as ex:
            while pending:
                todo = [(i, story) for i, story in pending if story is not None]
                found = list(
                    ex.map(lambda item: write_question(item[1], langs_of[item[0]], ask), todo)
                )
                pending = []
                for (i, story), question in zip(todo, found, strict=True):
                    if question is not None:
                        done[i] = (story, question)
                    else:
                        replaced += 1
                        nxt = slots[i].take()
                        if nxt is not None:
                            pending.append((i, nxt))

    for st in strata:
        got = sum(1 for i in done if slots[i] is st)
        if got < st.quota and st.name not in short:
            short.append(st.name)
    for name in short:
        echo(f"short: {name}")
    for code, k in lang_short.items():
        echo(f"short: lang {code} -{k}")

    lines = []
    for number, i in enumerate(sorted(done), start=1):
        story, question = done[i]
        lines.append(
            {
                "qid": f"q{number:04d}",
                "lang": langs_of[i],
                "question": question,
                "story_id": story["story_id"],
                "gold_page_ids": sorted(story["page_ids"]),
                "gold_text": f"{story['headline']}\n{story['text']}",
                "pub_date": story["pub_date"].isoformat(),
                "in_baseline_window": in_window(story["pub_date"]),
            }
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    tmp.replace(out)
    by_lang = {code: sum(1 for x in lines if x["lang"] == code) for code in LANG_NAMES}
    summary = {
        "out": str(out),
        "n": len(lines),
        "in_window": sum(1 for x in lines if x["in_baseline_window"]),
        "te": by_lang["te"],
        "by_lang": by_lang,
        "replaced": replaced,
        "short": short,
        "stories_available": len(pool),
    }
    if langs is not None or from_date is not None:
        meta = {
            "n": len(lines),
            "langs": dict(langs) if langs is not None else None,
            "by_lang": by_lang,
            "from_date": from_date.isoformat() if from_date else None,
            "window_n": window_n,
            "seed": seed,
            "writer_model": s.enrich_model,
            "stories_available": len(pool),
            "strata": {st.name: st.quota for st in strata},
            "short": short,
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "sha1": hashlib.sha1(out.read_bytes()).hexdigest(),
        }
        meta_path = meta_file(out)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return summary


def meta_file(set_file: Path) -> Path:
    return Path(f"{set_file}.meta.json")


# --- scoring ----------------------------------------------------------------------------------


def read_set(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_pages(path: Path) -> list[str]:
    with Path(path).open(encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def default_out(store: str) -> Path:
    return config.settings.reports_dir / f"retrieval-{store}.json"


def run_retrieval(
    set_file: Path | None = None,
    store: str = "new",
    only_window: bool = False,
    restrict_pages: Path | None = None,
    qdrant_collection: str | None = None,
    embed_profile: str | None = None,
    out: Path | None = None,
    search_fn: Callable[..., list[Any]] | None = None,
    questions: Sequence[Mapping[str, Any]] | None = None,
    translit: bool = False,
    translit_fn: Callable[[Sequence[str]], list[Any]] | None = None,
) -> dict[str, Any]:
    """FR-4: search every question, score the hits, write the report JSON and return it.

    `questions` replaces the questions read from `set_file` (ticket 026 passes a filtered list;
    `set_file` is then only named in the report).

    Ticket 034: `translit` also searches every `te_latn` question after `to_telugu_many`
    (`translit_fn` in tests); all transliterations run first, then the model is released and the
    searches run, so two models are never held at once."""
    from newsrag import search as search_mod

    s = config.settings
    set_file = Path(set_file or s.retrieval_set_file)
    questions = list(questions) if questions is not None else read_set(set_file)
    if only_window:
        questions = [q for q in questions if q.get("in_baseline_window")]
    filters = None
    if restrict_pages is not None:
        if store != "new":
            raise ValueError("--restrict-pages applies to store new only")
        filters = {"page_ids": read_pages(restrict_pages)}
    real_search = search_fn is None
    search_fn = search_fn or search_mod.search
    k = s.retrieval_k

    translits: dict[str, Any] = {}
    if translit:
        latn = [q for q in questions if q["lang"] == TE_LATN]
        if translit_fn is None:
            from newsrag.search.translit import to_telugu_many

            translit_fn = to_telugu_many
        for q, result in zip(latn, translit_fn([q["question"] for q in latn]), strict=True):
            translits[q["qid"]] = result

    def one(query: str, gold: set[str], q: Mapping[str, Any]) -> tuple[int | None, str | None]:
        start = time.perf_counter()
        hits = search_fn(
            query,
            k=k,
            store=store,
            filters=filters,
            qdrant_collection=qdrant_collection,
            embed_profile=embed_profile,
        )
        times.append((time.perf_counter() - start) * 1000)
        top1 = " ".join((hits[0].headline or "").split()) if hits else None
        return first_correct_rank(hits, q, gold), top1

    ranks: list[int | None] = []
    times: list[float] = []
    by_lang: dict[str, list[int | None]] = {}
    per_question: list[dict[str, Any]] = []
    try:
        for q in questions:
            gold = ngrams(q["gold_text"], s.retrieval_ngram)
            rank, top1 = one(q["question"], gold, q)
            ranks.append(rank)
            by_lang.setdefault(q["lang"], []).append(rank)
            record: dict[str, Any] = {
                "qid": q["qid"],
                "lang": q["lang"],
                "query": q["question"],
                "rank": rank,
                "top1_headline": top1,
            }
            result = translits.get(q["qid"])
            if result is not None:
                rank_t, top1_t = one(result.text, gold, q)
                by_lang.setdefault(TRANSLIT, []).append(rank_t)
                record.update(
                    translit_query=result.text,
                    translit_failed=result.failed,
                    rank_translit=rank_t,
                    top1_headline_translit=top1_t,
                )
            per_question.append(record)
    finally:
        if search_fn is search_mod.search:
            search_mod.close()

    warm = times[1:] or times  # the first query pays for loading the model
    overall = metrics(ranks, k)
    report = {
        "store": store,
        "set": str(set_file),
        "n": overall["n"],
        "recall_at_10": overall["recall_at_10"],
        "mrr": overall["mrr"],
        "median_search_ms": round(statistics.median(warm), 1) if warm else 0.0,
        "by_lang": {
            lang: metrics(by_lang.get(lang, []), k) for lang in sorted({"te", "en", *by_lang})
        },
        "k": k,
        "hit_overlap": s.retrieval_hit_overlap,
        "ngram": s.retrieval_ngram,
        "translit_model": s.enrich_model if translit else None,
        "generation": _generation(store) if real_search else None,
        "per_question": per_question,
    }
    out = Path(out or default_out(store))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _generation(store: str) -> str | None:
    if store != "new":
        return None
    try:
        from newsrag.warehouse import db as wdb

        return wdb.active_generation()
    except Exception:  # noqa: BLE001 - only named in the report
        return None


def summary_line(report: Mapping[str, Any]) -> str:
    return (
        f"recall@10={report['recall_at_10']:.4f} mrr={report['mrr']:.4f} "
        f"median_search_ms={report['median_search_ms']:.1f} n={report['n']}"
    )
