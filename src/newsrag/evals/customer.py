"""The customer-question answer check (ticket 028): answer the customer's 30 Telugu questions
from the new stores with a local LLM, check every claim against its cited sources with a local
model of another family, and write the answers next to the old system's answers.

Three passes, so only one model is loaded at a time:

1. `search(k)` for every question inside one `use(<embed profile>)` scope;
2. `use(answer_model)`: a Telugu answer per question from the numbered sources only;
3. `use(answer_judge_model or judge_model)`: every cited claim is `supported` | `not_supported`
   against each source it cites (supported if any does), every uncited unit `factual` |
   `not_factual`.

This is not the phase-2 answer service: it only shows whether the new data supports good answers.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.models.client import parse_json_reply

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
ANSWER_PROMPT = PROMPTS / "answer_check_v1.md"
CLAIM_PROMPT = PROMPTS / "claim_check_v1.md"

NO_SOURCES = "మూలాలు దొరకలేదు"
ANSWERED, NO_HITS, FAILED = "answered", "no_sources", "failed"
SUPPORTED, NOT_SUPPORTED = "supported", "not_supported"
FACTUAL, NOT_FACTUAL = "factual", "not_factual"
THINK_OFF = {"chat_template_kwargs": {"enable_thinking": False}}
_THINK = re.compile(r"<think>.*?</think>|^.*?</think>", re.DOTALL)

Echo = Callable[[str], None]
Opener = Callable[[str], Any]  # opener(profile) -> context manager (a chat client for LLMs)
SearchFn = Callable[..., list[Any]]


class FamilyError(ValueError):
    """The judge and the answer model are of the same family (exit 2)."""


# --- settings -----------------------------------------------------------------------------------


def judge_profile() -> str:
    s = config.settings
    return s.answer_judge_model or s.judge_model


def check_families(answer: str | None = None, judge: str | None = None) -> None:
    from newsrag.models import get_profile

    answer = answer or config.settings.answer_model
    judge = judge or judge_profile()
    if get_profile(answer).family == get_profile(judge).family:
        raise FamilyError("judge_model and answer_model must be different families")


# --- the questions and the old answers ----------------------------------------------------------


def load_questions(path: Path | None = None) -> list[dict[str, Any]]:
    data = json.loads(Path(path or config.settings.customer_questions_file).read_text("utf-8"))
    return list(data["questions"] if isinstance(data, dict) else data)


_OLD_HEAD = re.compile(r"^## (Q\d+)\b.*$", re.MULTILINE)


def extract_old_answers(markdown: str) -> dict[str, str]:
    """qid -> the old system's answer text: the section under `## Qnn — ...`, without its
    `**ప్రశ్న:**` line and without its `**Sources**` list."""
    heads = list(_OLD_HEAD.finditer(markdown))
    out: dict[str, str] = {}
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(markdown)
        body = markdown[head.end() : end]
        body = body.split("\n**Sources**", 1)[0]
        lines = [ln for ln in body.splitlines() if not ln.startswith("**ప్రశ్న:**")]
        out[head.group(1)] = "\n".join(lines).strip()
    return out


def read_old_answers(path: Path | None = None) -> dict[str, str]:
    path = Path(path or config.settings.customer_old_results_file)
    if not path.exists():
        return {}
    return extract_old_answers(path.read_text("utf-8"))


# --- sources ------------------------------------------------------------------------------------


@dataclass
class Source:
    n: int
    story_id: str
    chunk_id: str
    headline: str
    pub_date: str
    editions: list[str]
    page_id: str
    text: str  # cut to customer_source_chars

    def header(self) -> str:
        editions = ", ".join(self.editions) or "-"
        return f"[{self.n}] {self.headline} | {self.pub_date} | {editions} | page {self.page_id}"

    def block(self) -> str:
        return f"{self.header()}\n{self.text}"

    def listing(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "story_id": self.story_id,
            "chunk_id": self.chunk_id,
            "headline": self.headline,
            "pub_date": self.pub_date,
            "editions": self.editions,
            "page_id": self.page_id,
        }


def story_meta_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """story_id -> {page_id, editions} from rows (story_id, canonical_page_id, edition_names) of
    each printing's page: the editions are the union over all printings (FR-6)."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        meta = out.setdefault(
            str(row["story_id"]), {"page_id": row.get("canonical_page_id") or "", "editions": set()}
        )
        meta["editions"].update(row.get("edition_names") or [])
    return {k: {"page_id": v["page_id"], "editions": sorted(v["editions"])} for k, v in out.items()}


def load_story_meta(story_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    from newsrag.warehouse import db as wdb

    if not story_ids:
        return {}
    with wdb.conn() as c:
        rows = wdb.fetch_all(
            c,
            "select s.story_id, s.canonical_page_id, p.edition_names "
            "from stories s left join story_printings sp on sp.story_id = s.story_id "
            "left join pages p on p.page_id = sp.page_id where s.story_id = any(%s)",
            (list(set(story_ids)),),
        )
    return story_meta_from_rows(rows)


def load_covered_years(generation: str | None = None) -> list[int]:
    from newsrag.warehouse import db as wdb

    generation = generation or wdb.active_generation()
    with wdb.conn() as c:
        rows = wdb.fetch_all(
            c,
            "select distinct extract(year from pub_date)::int as y from chunks "
            "where generation = %s order by 1",
            (generation,),
        )
    return [int(r["y"]) for r in rows]


def make_sources(hits: Sequence[Any], meta: Mapping[str, Mapping[str, Any]]) -> list[Source]:
    chars = config.settings.customer_source_chars
    out = []
    for n, hit in enumerate(hits, start=1):
        m = meta.get(hit.story_id, {})
        out.append(
            Source(
                n=n,
                story_id=hit.story_id,
                chunk_id=hit.chunk_id,
                headline=" ".join((hit.headline or "").split()),
                pub_date=str(hit.pub_date),
                editions=list(m.get("editions") or []),
                page_id=str(m.get("page_id") or (hit.page_ids[0] if hit.page_ids else "")),
                text=(hit.text or "")[:chars],
            )
        )
    return out


# --- the answer ---------------------------------------------------------------------------------


def strip_thinking(reply: str) -> str:
    return _THINK.sub("", reply or "").strip()


def answer_prompt(
    question: str, sources: Sequence[Source], today: date, covered_years: Sequence[int]
) -> str:
    template = ANSWER_PROMPT.read_text("utf-8")
    years = ", ".join(str(y) for y in covered_years) or "-"
    return (
        template.replace("{today}", today.isoformat())
        .replace("{covered_years}", years)
        .replace("{question}", question)
        .replace("{sources}", "\n\n".join(src.block() for src in sources))
    )


def ask_answer(client: Any, prompt: str) -> str:
    s = config.settings
    reply = client.chat(
        prompt,
        temperature=0,
        max_tokens=s.customer_answer_max_tokens,
        extra_body={**THINK_OFF, "repetition_penalty": s.customer_answer_repetition_penalty},
    )
    answer = strip_thinking(reply)
    if not answer:
        raise ValueError("empty answer")
    return answer


# --- units and citations ------------------------------------------------------------------------

_CITE = re.compile(r"\[(\d+(?:\s*[,،]\s*\d+)*)\]")
_BULLET = re.compile(r"^\s*(?:[-*•+]|\d+[.)])\s+")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_ONLY_CITES = re.compile(r"^[\s\[\]\d,،.]*$")


def citations(unit: str) -> list[int]:
    """The source numbers a unit cites, in order, without repeats."""
    out: list[int] = []
    for match in _CITE.finditer(unit):
        for part in re.split(r"[,،]", match.group(1)):
            n = int(part)
            if n not in out:
                out.append(n)
    return out


def _sentences(line: str) -> list[str]:
    parts, start = [], 0
    for i, ch in enumerate(line):
        between_digits = 0 < i < len(line) - 1 and line[i - 1].isdigit() and line[i + 1].isdigit()
        if not (ch in "?!।" or (ch == "." and not between_digits)):
            continue
        end = i + 1
        parts.append(line[start:end])
        start = end
    parts.append(line[start:])
    return parts


def split_units(answer: str) -> list[str]:
    """FR-3: sentences (end at `?`, `!`, `।`, a newline, and a `.` not between digits); each
    bullet item and each table row is one unit. A piece that is only citations (`... ది. [2]`)
    joins the unit before it; table rules and pieces without letters or digits are dropped."""
    units: list[str] = []
    for raw in (answer or "").splitlines():
        line = raw.strip()
        if not line or _TABLE_RULE.match(line):
            continue
        pieces = [line] if line.startswith("|") or _BULLET.match(line) else _sentences(line)
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            if _ONLY_CITES.match(piece):
                if _CITE.search(piece) and units:
                    units[-1] = f"{units[-1]} {piece}"
                continue
            if not any(ch.isalnum() for ch in piece):
                continue
            units.append(piece)
    return units


# --- the claim check ----------------------------------------------------------------------------


def judge_templates() -> tuple[str, str]:
    text = CLAIM_PROMPT.read_text("utf-8")
    _, _, rest = text.partition("## cited")
    cited, _, uncited = rest.partition("## uncited")
    return cited.strip() + "\n", uncited.strip() + "\n"


def _label_schema(labels: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "label",
            "schema": {
                "type": "object",
                "properties": {"label": {"type": "string", "enum": list(labels)}},
                "required": ["label"],
                "additionalProperties": False,
            },
        },
    }


def ask_label(client: Any, prompt: str, labels: Sequence[str]) -> str | None:
    """The judge's label, asked again up to `customer_judge_retries` times; None when no reply
    was a valid label."""
    s = config.settings
    for _ in range(1 + max(0, s.customer_judge_retries)):
        try:
            reply = client.chat(
                prompt,
                temperature=0,
                max_tokens=s.customer_judge_max_tokens,
                response_format=_label_schema(labels),
                extra_body=THINK_OFF,
            )
            data = parse_json_reply(strip_thinking(reply))
        except Exception:  # noqa: BLE001, S112 - a failed or unparseable reply counts as a try
            continue
        label = data.get("label") if isinstance(data, dict) else None
        if label in labels:
            return str(label)
    return None


def judge_unit(client: Any, unit: str, sources: Sequence[Source]) -> dict[str, Any]:
    """One unit's verdict. A cited claim is supported if any source it cites (in 1..len(sources))
    supports it; an out-of-range citation or an unparseable judge reply supports nothing. An
    uncited unit whose label stays unparseable counts as `factual` (the worse case)."""
    cited_tpl, uncited_tpl = judge_templates()
    cited = citations(unit)
    claim = _CITE.sub("", unit).strip()
    if cited:
        by_n = {src.n: src for src in sources}
        checked: dict[str, str] = {}
        verdict = NOT_SUPPORTED
        for n in cited:
            src = by_n.get(n)
            if src is None:
                checked[str(n)] = "out_of_range"
                continue
            prompt = cited_tpl.replace("{claim}", claim).replace("{source}", src.block())
            label = ask_label(client, prompt, (SUPPORTED, NOT_SUPPORTED))
            checked[str(n)] = label or "unparseable"
            if label == SUPPORTED:
                verdict = SUPPORTED
                break
        return {"text": unit, "cited": cited, "verdict": verdict, "per_source": checked}
    label = ask_label(client, uncited_tpl.replace("{unit}", claim), (FACTUAL, NOT_FACTUAL))
    return {
        "text": unit,
        "cited": [],
        "verdict": label or FACTUAL,
        "per_source": {} if label else {"judge": "unparseable"},
    }


# --- the run ------------------------------------------------------------------------------------


@dataclass
class Item:
    question: dict[str, Any]
    hits: list[Any] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    status: str = ""
    answer: str = ""
    error: str = ""
    units: list[dict[str, Any]] = field(default_factory=list)

    @property
    def qid(self) -> str:
        return str(self.question["id"])


def summarize(items: Sequence[Item]) -> dict[str, Any]:
    """FR-3: supported_pct over the cited claims of the answered questions (0 without any),
    uncited = uncited factual units, n_failed = failed questions."""
    answered = [it for it in items if it.status != FAILED]
    claims = [u for it in answered for u in it.units if u["cited"]]
    supported = sum(1 for u in claims if u["verdict"] == SUPPORTED)
    return {
        "supported_pct": round(supported / len(claims) * 100, 2) if claims else 0.0,
        "n_claims": len(claims),
        "n_supported": supported,
        "uncited": sum(
            1 for it in answered for u in it.units if not u["cited"] and u["verdict"] == FACTUAL
        ),
        "n_failed": sum(1 for it in items if it.status == FAILED),
    }


def summary_line(report: Mapping[str, Any]) -> str:
    return (
        f"supported_pct={report['supported_pct']} n_claims={report['n_claims']} "
        f"uncited={report['uncited']} n_failed={report['n_failed']}"
    )


def retire_signoff() -> Path | None:
    """FR-4: a new run makes the old sign-off stale: rename it to `<name>.old`."""
    path = Path(config.settings.customer_signoff_file)
    if not path.exists():
        return None
    old = path.with_name(path.name + ".old")
    path.replace(old)
    return old


def _default_opener() -> Opener:
    from newsrag import models

    return models.use


def run_customer(
    k: int | None = None,
    questions: Sequence[Mapping[str, Any]] | None = None,
    search_fn: SearchFn | None = None,
    opener: Opener | None = None,
    story_meta: Callable[[Sequence[str]], dict[str, dict[str, Any]]] | None = None,
    covered_years: Sequence[int] | None = None,
    embed_profile: str | None = None,
    old_answers: Mapping[str, str] | None = None,
    today: date | None = None,
    echo: Echo = print,
) -> dict[str, Any]:
    """Run the three passes, write `customer-answers.{md,json}` and return the report.

    Tests replace the stores (`search_fn`, `story_meta`, `covered_years`, `embed_profile`) and
    the models (`opener(profile)` yields a client with `chat(prompt, **params) -> str`)."""
    s = config.settings
    check_families()
    k = k or s.customer_k
    run_id = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = today or datetime.now(UTC).date()
    retired = retire_signoff()
    if retired is not None:
        echo(f"previous sign-off moved to {retired}")
    items = [Item(dict(q)) for q in (questions if questions is not None else load_questions())]
    opener = opener or _default_opener()
    timings: dict[str, float] = {}

    # pass 1: search, one embed scope
    real_search = search_fn is None
    if real_search or embed_profile is None or covered_years is None:
        from newsrag.search.hybrid import profile_of
        from newsrag.warehouse import db as wdb

        generation = wdb.active_generation()
        if generation is None:
            raise RuntimeError("no active generation: run 'newsrag stage run index' first")
        embed_profile = embed_profile or profile_of(generation)
        if covered_years is None:
            covered_years = load_covered_years(generation)
    if real_search:
        from newsrag import search as search_mod

        search_fn = search_mod.search
    start = time.monotonic()
    with opener(embed_profile):
        try:
            for it in items:
                it.hits = list(search_fn(it.question["telugu"], k=k))
        finally:
            if real_search:
                search_mod.close()
    meta = (story_meta or load_story_meta)(sorted({h.story_id for it in items for h in it.hits}))
    for it in items:
        it.sources = make_sources(it.hits, meta)
        if not it.sources:
            it.status, it.answer = NO_HITS, NO_SOURCES
    timings["search_s"] = round(time.monotonic() - start, 1)
    echo(f"pass 1: searched {len(items)} questions in {timings['search_s']} s")

    # pass 2: answers
    start = time.monotonic()
    todo = [it for it in items if it.status != NO_HITS]
    if todo:
        with opener(s.answer_model) as client:

            def answer(it: Item) -> None:
                prompt = answer_prompt(it.question["telugu"], it.sources, today, covered_years)
                for _ in range(1 + max(0, s.customer_answer_retries)):
                    try:
                        it.answer, it.status, it.error = ask_answer(client, prompt), ANSWERED, ""
                        return
                    except Exception as e:  # noqa: BLE001 - retried, then recorded
                        it.error = f"{type(e).__name__}: {e}"[:300]
                it.status = FAILED

            with ThreadPoolExecutor(max_workers=max(1, s.customer_concurrency)) as ex:
                list(ex.map(answer, todo))
    timings["answer_s"] = round(time.monotonic() - start, 1)
    n_failed = sum(1 for it in items if it.status == FAILED)
    echo(f"pass 2: answered {len(todo) - n_failed}, failed {n_failed} in {timings['answer_s']} s")

    # pass 3: the claim check
    start = time.monotonic()
    work = [(it, unit) for it in items if it.status == ANSWERED for unit in split_units(it.answer)]
    if work:
        with (
            opener(judge_profile()) as client,
            ThreadPoolExecutor(max_workers=max(1, s.customer_concurrency)) as ex,
        ):
            verdicts = list(ex.map(lambda w: judge_unit(client, w[1], w[0].sources), work))
        for (it, _), verdict in zip(work, verdicts, strict=True):
            it.units.append(verdict)
    timings["judge_s"] = round(time.monotonic() - start, 1)
    echo(f"pass 3: judged {len(work)} units in {timings['judge_s']} s")

    old = dict(old_answers) if old_answers is not None else read_old_answers()
    report = {
        "run_id": run_id,
        **summarize(items),
        "k": k,
        "answer_model": s.answer_model,
        "judge_model": judge_profile(),
        "embed_profile": embed_profile,
        "covered_years": list(covered_years),
        "today": today.isoformat(),
        "timings": timings,
        "per_question": [question_record(it, old.get(it.qid, "")) for it in items],
    }
    write_reports(report)
    return report


def question_record(it: Item, old_answer: str) -> dict[str, Any]:
    cited = [u for u in it.units if u["cited"]]
    return {
        "id": it.qid,
        "english": it.question.get("english", ""),
        "question": it.question.get("telugu", ""),
        "status": it.status,
        "error": it.error,
        "answer": it.answer,
        "sources": [src.listing() for src in it.sources],
        "units": it.units,
        "n_claims": len(cited),
        "n_supported": sum(1 for u in cited if u["verdict"] == SUPPORTED),
        "uncited": sum(1 for u in it.units if not u["cited"] and u["verdict"] == FACTUAL),
        "old_answer": old_answer,
    }


# --- the reports --------------------------------------------------------------------------------


def _quote(text: str) -> str:
    """A markdown blockquote, so the answers' own headings stay inside their question."""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in (text or "").splitlines())


def render_markdown(report: Mapping[str, Any]) -> str:
    out = [
        "# Customer questions: new answers and claim check",
        "",
        (
            f"Run {report['run_id']} · answer model `{report['answer_model']}` · judge "
            f"`{report['judge_model']}` · k={report['k']} · covered years "
            f"{', '.join(str(y) for y in report['covered_years']) or '-'}"
        ),
        "",
        f"**{summary_line(report)}**",
        "",
    ]
    for q in report["per_question"]:
        out += [f"## {q['id']} — {q['english']}", "", f"**ప్రశ్న:** {q['question']}", ""]
        status = f"status: {q['status']}" + (f" ({q['error']})" if q["error"] else "")
        out += [
            "### New answer",
            "",
            (
                f"_{status}; claims {q['n_supported']}/{q['n_claims']} supported, "
                f"uncited factual {q['uncited']}_"
            ),
            "",
            _quote(q["answer"]) or "> (none)",
            "",
            "### Sources",
            "",
        ]
        for src in q["sources"]:
            out.append(
                f"- [{src['n']}] {src['headline']} | {src['pub_date']} | "
                f"{', '.join(src['editions']) or '-'} | {src['page_id']}"
            )
        if not q["sources"]:
            out.append("- (none)")
        out += ["", "### Claim check", ""]
        for unit in q["units"]:
            cites = "".join(f"[{n}]" for n in unit["cited"]) or "uncited"
            text = " ".join(_CITE.sub("", unit["text"]).split())
            out.append(f"- **{unit['verdict']}** ({cites}) {text}")
        if not q["units"]:
            out.append("- (no units)")
        out += ["", "### Old answer", "", _quote(q["old_answer"]) or "> (not found)", ""]
    return "\n".join(out)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_reports(report: Mapping[str, Any]) -> None:
    s = config.settings
    _write_atomic(
        Path(s.customer_answers_file), json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    _write_atomic(Path(s.customer_answers_md_file), render_markdown(report))


# --- the sign-off -------------------------------------------------------------------------------


class NothingToSign(RuntimeError):
    pass


def signoff(name: str, note: str = "") -> dict[str, Any]:
    """FR-5: record that `name` read the current answers."""
    s = config.settings
    answers = Path(s.customer_answers_file)
    if not answers.exists():
        raise NothingToSign("no customer answers to sign")
    raw = answers.read_bytes()
    report = json.loads(raw)
    record = {
        "name": name,
        "note": note,
        "signed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "supported_pct": report.get("supported_pct"),
        "answers_sha1": hashlib.sha1(raw).hexdigest(),
        "run_id": report.get("run_id"),
    }
    _write_atomic(
        Path(s.customer_signoff_file), json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    )
    return record


__all__ = [
    "FamilyError",
    "NothingToSign",
    "check_families",
    "citations",
    "extract_old_answers",
    "run_customer",
    "signoff",
    "split_units",
    "summarize",
    "summary_line",
]
