"""Text model Telugu bake-off (ticket 032): pick the model for enrichment (019-022), the test
questions (025) and the customer answers (028) by measuring it on our own Telugu stories.

Every candidate, one at a time through `newsrag.models.use`, runs four tasks on the same sampled
canonical stories with ticket 019's settings (temperature 0): the `enrich_core` prompt, the
`enrich_facts` prompt (both with their own validation code, reused as they are), a Telugu summary
and a Telugu question. Objective scores come from the validation; the summary, question and names
of each candidate are also graded 1-5 by every candidate of a *different family* (nobody grades
itself or its own family). The report `<reports_dir>/textmodel-bakeoff.md` names the winner, which
`newsrag textmodel bakeoff --apply` writes into `pipeline.yaml`.

Outputs and judgements are saved under `<work_dir>/textmodel-bakeoff/` and reused on a rerun when
nothing they depend on changed. Nothing here touches the ledger.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import random
import re
import secrets
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.enrich import core, facts
from newsrag.enrich import validate as v
from newsrag.models.client import parse_json_reply

SUMMARY_PROMPT = core.PROMPTS / "summary_v1.md"
QUESTION_PROMPT = core.PROMPTS / "textmodel_question_v1.md"
JUDGE_PROMPT = core.PROMPTS / "textmodel_judge_v1.md"

TELUGU_RANGE = (0x0C00, 0x0C7F)  # the Telugu Unicode block
TASKS = ("core", "facts", "summary", "question")
JUDGED_PARTS = ("summary", "question", "names")
JUDGED_KEYS = ("correct", "natural")
THINK_OFF = {
    "chat_template_kwargs": {"enable_thinking": False}
}  # Sarvam-M, Gemma 4 think by default
_THINK = re.compile(r"<think>.*?</think>|^.*?</think>", re.DOTALL)

DONE, FAILED, UNJUDGED = "done", "failed", "unjudged"

Echo = Callable[[str], None]
Opener = Callable[[str], Any]  # opener(profile) -> context manager yielding a chat client


class BakeoffError(ValueError):
    """A bad request (unknown or non-text candidate): exit code 2."""


# --- the stories -------------------------------------------------------------------------------


def letters(text: str) -> str:
    """The letters of `text`, with their combining marks: no digits, spaces or punctuation."""
    return "".join(c for c in text if unicodedata.category(c)[0] in "LM")


def telugu_share(text: str) -> float:
    """Share of the letters of `text` that are Telugu script (0 when there are no letters)."""
    chars = letters(text)
    if not chars:
        return 0.0
    lo, hi = TELUGU_RANGE
    return sum(lo <= ord(c) <= hi for c in chars) / len(chars)


def eligible(story: Mapping[str, Any]) -> bool:
    """FR-2: at least `textmodel_min_telugu_share` Telugu letters, 300-4,000 characters."""
    s = config.settings
    text = story.get("text") or ""
    if not s.textmodel_min_chars <= len(text) <= s.textmodel_max_chars:
        return False
    return telugu_share(f"{story.get('headline') or ''}\n{text}") >= s.textmodel_min_telugu_share


def dedup_stories() -> list[dict[str, Any]]:
    """Every story of every done `dedup` artifact, with its print date, sorted by (date, id)."""
    from newsrag.dedup import load

    out: list[dict[str, Any]] = []
    for day, path in load.done_dedup().items():
        for story in load.load_dedup(path).get("stories") or []:
            out.append(
                {
                    "pub_date": day,
                    "story_id": story["story_id"],
                    "headline": story.get("headline") or "",
                    "text": story.get("text") or "",
                }
            )
    return sorted(out, key=lambda st: (st["pub_date"], st["story_id"]))


def choose_stories(pool: Sequence[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """`n` eligible stories drawn with `seed` (all of them when there are fewer), sorted."""
    ok = sorted(
        (st for st in pool if eligible(st)), key=lambda st: (st["pub_date"], st["story_id"])
    )
    chosen = ok if len(ok) <= n else random.Random(seed).sample(ok, n)
    return sorted(chosen, key=lambda st: (st["pub_date"], st["story_id"]))


def read_stories_file(path: Path) -> list[tuple[str, str]]:
    """The (print date, story id) lines of the committed story list."""
    out: list[tuple[str, str]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and not line.startswith("#"):
            out.append((parts[0], parts[1]))
    return out


def ensure_stories_file(
    path: Path,
    n: int,
    seed: int,
    pool: Callable[[], list[dict[str, Any]]] | None = None,
    echo: Echo = lambda _m: None,
) -> list[dict[str, Any]]:
    """The bake-off stories (with their text). The list in `path` is reused when it has `n`
    entries; otherwise it is drawn again and written. Raises `BakeoffError` when a listed story is
    no longer in the `dedup` artifacts."""
    path = Path(path)
    stories = (pool or dedup_stories)()
    if path.is_file():
        wanted = read_stories_file(path)
        if len(wanted) == n:
            by_key = {(st["pub_date"], st["story_id"]): st for st in stories}
            missing = [key for key in wanted if key not in by_key]
            if missing:
                raise BakeoffError(
                    f"{path}: {len(missing)} listed stories are not in the dedup artifacts "
                    f"(first: {missing[0][0]} {missing[0][1]}); delete the file to draw again"
                )
            return [by_key[key] for key in wanted]
        echo(f"{path} lists {len(wanted)} stories, not {n}: drawing again")
    chosen = choose_stories(stories, n, seed)
    if not chosen:
        raise BakeoffError("no dedup story is Telugu enough and of the right length")
    if len(chosen) < n:
        echo(f"only {len(chosen)} eligible stories (asked for {n})")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{st['pub_date']} {st['story_id']}\n" for st in chosen), encoding="utf-8"
    )
    return chosen


# --- the model client ---------------------------------------------------------------------------


def strip_thinking(reply: str) -> str:
    """A reply without a `<think>...</think>` block a reasoning model may still write."""
    return _THINK.sub("", reply).strip()


class TextClient:
    """A `ModelClient` for the bake-off: thinking off (reasoning models would spend the token
    budget on it) and no `raw` attribute, so `core.ask` goes through `chat`."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.profile = client.profile

    def chat(self, messages: Any, **params: Any) -> str:
        reply = self._client.chat(messages, extra_body=THINK_OFF, **params)
        return strip_thinking(reply)


# --- the four tasks -----------------------------------------------------------------------------


def _text_task(tc: TextClient, system: str, story: Mapping[str, Any]) -> dict[str, Any]:
    given = core.model_input(story["headline"], story["text"])
    messages = [{"role": "system", "content": system}, {"role": "user", "content": given}]
    try:
        reply = core.ask(tc, messages, max_tokens=config.settings.textmodel_summary_max_tokens)
    except Exception as exc:  # noqa: BLE001 - recorded, the story just has no output
        return {"text": None, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return {"text": reply.strip() or None, "error": None}


def _core_task(tc: TextClient, prompt: str, story: Mapping[str, Any]) -> dict[str, Any]:
    outcome = core.enrich_story(
        tc,
        prompt,
        story["headline"],
        story["text"],
        date.fromisoformat(story["pub_date"]),
    )
    p = outcome.parsed
    return {
        "ok": v.FAILED_FLAG not in p.flags,
        "category": p.category,
        "people": p.people,
        "organisations": p.organisations,
        "dropped_names": p.dropped_names,
        "error": outcome.error,
    }


def _facts_task(tc: TextClient, prompt: str, story: Mapping[str, Any]) -> dict[str, Any]:
    print_date = date.fromisoformat(story["pub_date"])
    outcome = facts.enrich_story(
        tc, prompt, story["headline"], story["text"], print_date, print_date
    )
    p = outcome.parsed
    return {
        "ok": not outcome.failed,
        "kept": len(p.facts) if p else 0,
        "dropped_non_verbatim": p.dropped_non_verbatim if p else 0,
        "dropped_unparsed": p.dropped_unparsed if p else 0,
        "dropped_invalid": p.dropped_invalid if p else 0,
        "error": outcome.error,
    }


def generate(
    client: Any, stories: Sequence[Mapping[str, Any]], workers: int | None = None
) -> tuple[dict[str, dict[str, Any]], float]:
    """Run the four tasks of every story. Returns ({story_id: {task: result}}, seconds)."""
    from newsrag import geo

    s = config.settings
    tc = TextClient(client)
    workers = max(1, workers if workers is not None else s.enrich_concurrency)
    core_prompts = {
        d: core.build_prompt(date.fromisoformat(d)) for d in {x["pub_date"] for x in stories}
    }
    facts_prompt = facts.build_prompt()
    summary_prompt = SUMMARY_PROMPT.read_text(encoding="utf-8")
    question_prompt = QUESTION_PROMPT.read_text(encoding="utf-8")

    def run(job: tuple[int, str]) -> dict[str, Any]:
        i, task = job
        story = stories[i]
        if task == "core":
            return _core_task(tc, core_prompts[story["pub_date"]], story)
        if task == "facts":
            return _facts_task(tc, facts_prompt, story)
        return _text_task(tc, summary_prompt if task == "summary" else question_prompt, story)

    geo.resolve("")  # build the geo index once, before the pool resolves places in threads
    # Longest first: the long stories are the slow requests, so they must not start last.
    order = sorted(range(len(stories)), key=lambda i: -len(stories[i]["text"]))
    jobs = [(i, task) for i in order for task in TASKS]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, jobs))
    seconds = time.monotonic() - started
    records: dict[str, dict[str, Any]] = {st["story_id"]: {} for st in stories}
    for (i, task), result in zip(jobs, results, strict=True):
        records[stories[i]["story_id"]][task] = result
    return records, seconds


# --- objective scores ----------------------------------------------------------------------------


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def objective_scores(
    records: Mapping[str, Mapping[str, Any]], min_share: float
) -> dict[str, float]:
    """`json_ok`, `names_verbatim`, `facts_verbatim` and `telugu_output` over the stories' task
    results (FR-3). A missing result counts as a failure; a score with nothing to count is 0."""
    replies = ok = 0
    names = dropped_names = 0
    kept_facts = dropped_facts = 0
    outputs = telugu = 0
    for rec in records.values():
        core_r, facts_r = rec.get("core") or {}, rec.get("facts") or {}
        replies += 2
        ok += bool(core_r.get("ok")) + bool(facts_r.get("ok"))
        names += len(core_r.get("people") or []) + len(core_r.get("organisations") or [])
        dropped_names += int(core_r.get("dropped_names") or 0)
        kept_facts += int(facts_r.get("kept") or 0)
        dropped_facts += sum(
            int(facts_r.get(k) or 0)
            for k in ("dropped_non_verbatim", "dropped_unparsed", "dropped_invalid")
        )
        for task in ("summary", "question"):
            text = (rec.get(task) or {}).get("text")
            outputs += 1
            telugu += bool(text) and telugu_share(text) >= min_share
    return {
        "json_ok": _ratio(ok, replies),
        "names_verbatim": _ratio(names, names + dropped_names),
        "facts_verbatim": _ratio(kept_facts, kept_facts + dropped_facts),
        "telugu_output": _ratio(telugu, outputs),
    }


# --- cross-judging -------------------------------------------------------------------------------


def judges_for(subject: str, candidates: Sequence[str], family: Mapping[str, str]) -> list[str]:
    """The candidates that may grade `subject`: a different family (never itself, never its
    family: a gemma candidate is not judged by the other gemma)."""
    return [c for c in candidates if c != subject and family[c] != family[subject]]


def _names_text(core_r: Mapping[str, Any]) -> str:
    people, orgs = core_r.get("people") or [], core_r.get("organisations") or []
    if not (core_r.get("ok") and (people or orgs)):
        return "(none)"
    lines = [
        f"- person: {p['name']}" + (f" ({p['role']})" if p.get("role") else "") for p in people
    ]
    lines += [f"- organisation: {o['name']} ({o.get('type')})" for o in orgs]
    return "\n".join(lines)


def judge_prompt(story: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    text = JUDGE_PROMPT.read_text(encoding="utf-8")
    return (
        text.replace("<<STORY>>", core.model_input(story["headline"], story["text"]))
        .replace("<<SUMMARY>>", (record.get("summary") or {}).get("text") or "(none)")
        .replace("<<QUESTION>>", (record.get("question") or {}).get("text") or "(none)")
        .replace("<<NAMES>>", _names_text(record.get("core") or {}))
    )


def judge_schema() -> dict[str, Any]:
    score = {"type": "integer", "enum": [1, 2, 3, 4, 5]}
    part = {
        "type": "object",
        "properties": {k: score for k in JUDGED_KEYS},
        "required": list(JUDGED_KEYS),
    }
    return {
        "type": "object",
        "properties": {p: part for p in JUDGED_PARTS},
        "required": list(JUDGED_PARTS),
    }


def parse_scores(reply: str) -> list[int] | None:
    """The six 1-5 scores of a judge reply (summary, question, names; correct and natural), or
    None when the reply is not JSON of that shape."""
    try:
        data = parse_json_reply(reply)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out: list[int] = []
    for part in JUDGED_PARTS:
        item = data.get(part)
        if not isinstance(item, dict):
            return None
        for key in JUDGED_KEYS:
            value = item.get(key)
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                return None
            out.append(value)
    return out


def judge_subject(
    client: Any,
    stories: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    workers: int | None = None,
) -> dict[str, list[int] | None]:
    """The scores one judge gives one subject, `{story_id: six scores | None}`; None when the
    judge's reply stayed unusable after `textmodel_judge_retries` more tries."""
    s = config.settings
    tc = TextClient(client)
    workers = max(1, workers if workers is not None else s.enrich_concurrency)
    extra = {
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "reply", "schema": judge_schema()},
        },
        "max_tokens": s.textmodel_judge_max_tokens,
    }

    def one(story: Mapping[str, Any]) -> list[int] | None:
        messages = [
            {"role": "user", "content": judge_prompt(story, records.get(story["story_id"]) or {})}
        ]
        for _ in range(s.textmodel_judge_retries + 1):
            try:
                scores = parse_scores(core.ask(tc, messages, **extra))
            except Exception:  # noqa: BLE001 - an unusable reply is asked again
                scores = None
            if scores is not None:
                return scores
        return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, stories))
    return {st["story_id"]: r for st, r in zip(stories, results, strict=True)}


def scale(mean_score: float) -> float:
    """A mean on the 1-5 scale as 0-1."""
    return (mean_score - 1.0) / 4.0


def judge_mean(scores: Mapping[str, list[int] | None]) -> float | None:
    """One judge's mean score for one subject, 0-1; None when no reply was usable."""
    per_story = [sum(x) / len(x) for x in scores.values() if x]
    return scale(sum(per_story) / len(per_story)) if per_story else None


# --- rows, the winner and the report ---------------------------------------------------------------


@dataclass
class Row:
    candidate: str
    family: str
    status: str = DONE
    reason: str = ""
    scores: dict[str, float] = field(default_factory=dict)  # the four objective scores
    judged: float | None = None
    judge_means: dict[str, float] = field(default_factory=dict)  # judge -> mean score, 0-1
    seconds_per_story: float | None = None

    @property
    def total(self) -> float | None:
        if self.judged is None or not self.scores:
            return None
        return total_score(self.judged, self.scores)


def total_score(judged: float, objective: Mapping[str, float]) -> float:
    """FR-3: 0.5 x judged + 0.125 x each of the four objective scores."""
    return 0.5 * judged + 0.125 * sum(
        objective[k] for k in ("json_ok", "names_verbatim", "facts_verbatim", "telugu_output")
    )


@dataclass(frozen=True)
class Ranked:
    """What the winner rule needs of a candidate (also read back from the report)."""

    candidate: str
    family: str
    total: float
    judged: float
    seconds: float | None


def ranked_rows(rows: Sequence[Row]) -> list[Ranked]:
    return [
        Ranked(r.candidate, r.family, r.total, r.judged or 0.0, r.seconds_per_story)
        for r in rows
        if r.status == DONE and r.total is not None
    ]


def pick_winner(items: Sequence[Ranked], tie: float | None = None) -> str | None:
    """Highest `total`; every candidate within `tie` of the best is a tie, broken by the higher
    `judged`, then the faster one (fewer seconds/story). None when no candidate finished."""
    tie = config.settings.textmodel_tie if tie is None else tie
    if not items:
        return None
    best = max(r.total for r in items)
    tied = [r for r in items if round(best - r.total, 6) <= tie]

    def key(r: Ranked) -> tuple[float, float]:
        speed = r.seconds if r.seconds is not None else float("inf")
        return (-round(r.judged, 6), speed)

    return min(tied, key=key).candidate


def rank(items: Sequence[Ranked], tie: float | None = None) -> list[str]:
    """Every candidate, best first: the winner rule applied again to what is left."""
    left, out = list(items), []
    while left:
        name = pick_winner(left, tie)
        assert name is not None
        out.append(name)
        left = [r for r in left if r.candidate != name]
    return out


def _fmt(value: float | None, digits: int) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


TABLE_HEADER = (
    "| candidate | family | status | json_ok | names_verbatim | facts_verbatim "
    "| telugu_output | judged | total | seconds/story | note |"
)


def render_report(
    rows: Sequence[Row],
    stories: Sequence[Mapping[str, Any]],
    winner: str | None,
    notes: Sequence[str] = (),
    elapsed_s: float | None = None,
    stories_file: str = "data/textmodel-bakeoff-stories.txt",
) -> str:
    days = sorted({st["pub_date"] for st in stories})
    lines = [
        "# Text model Telugu bake-off (ticket 032)",
        "",
        f"stories: {len(stories)} ({stories_file}; print dates {', '.join(days)})",
        "tasks: enrich_core, enrich_facts, summary, question (temperature 0)",
        "judged: mean over the judges of another family of the mean 1-5 score, scaled (x-1)/4",
        "total = 0.5 x judged + 0.125 x (json_ok + names_verbatim + facts_verbatim + telugu_output)",
    ]
    if elapsed_s is not None:
        lines.append(f"wall time: {elapsed_s / 3600:.2f} h")
    lines += [
        "",
        TABLE_HEADER,
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        sc = r.scores
        lines.append(
            f"| {r.candidate} | {r.family} | {r.status} "
            f"| {_fmt(sc.get('json_ok'), 4)} | {_fmt(sc.get('names_verbatim'), 4)} "
            f"| {_fmt(sc.get('facts_verbatim'), 4)} | {_fmt(sc.get('telugu_output'), 4)} "
            f"| {_fmt(r.judged, 4)} | {_fmt(r.total, 4)} | {_fmt(r.seconds_per_story, 2)} "
            f"| {_cell(r.reason)} |"
        )
    names = [r.candidate for r in rows]
    judged_rows = [r for r in rows if r.judge_means]
    if judged_rows:
        lines += [
            "",
            "Judge matrix (mean score 0-1 that the column judge gave the row candidate):",
            "",
            "| candidate | " + " | ".join(names) + " |",
            "|---|" + "---|" * len(names),
        ]
        for r in rows:
            cells = [_fmt(r.judge_means.get(j), 3) for j in names]
            lines.append(f"| {r.candidate} | " + " | ".join(cells) + " |")
    if notes:
        lines += ["", "Notes:", ""] + [f"- {n}" for n in notes]
    lines += ["", f"winner: {winner or 'none'}", ""]
    return "\n".join(lines)


# --- reading a report back (--apply) -----------------------------------------------------------------


def parse_report(text: str) -> tuple[str | None, list[Ranked]]:
    """(winner or None, the finished candidates with their total, judged and seconds/story) of a
    report written by `render_report`."""
    winner: str | None = None
    items: list[Ranked] = []
    in_table = False
    for line in text.splitlines():
        match = re.match(r"^winner:\s*(\S+)\s*$", line)
        if match:
            winner = None if match.group(1) == "none" else match.group(1)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| candidate | family | status"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        if len(cells) < 10 or cells[2] != DONE or "-" in (cells[7], cells[8]):
            continue
        seconds = None if cells[9] == "-" else float(cells[9])
        items.append(Ranked(cells[0], cells[1], float(cells[8]), float(cells[7]), seconds))
    return winner, items


def choose_applied(winner: str | None, items: Sequence[Ranked]) -> tuple[str, str]:
    """(winner, answer judge): the judge is the best-ranked finished candidate of a different
    family from the winner. Raises `ValueError` when there is none."""
    if winner is None:
        raise ValueError("the report says winner: none; nothing to apply")
    families = {r.candidate: r.family for r in items}
    if winner not in families:
        raise ValueError(f"the report's winner {winner} has no finished row")
    others = [r for r in items if r.family != families[winner]]
    if not others:
        raise ValueError(
            f"no finished candidate of a different family from {winner} to be the answer judge"
        )
    ordered = rank(others)
    return winner, ordered[0]


def apply_report(path: Path | None = None) -> tuple[str, str, str]:
    """Write `enrich_model`, `answer_model` and `answer_judge_model` from the existing report
    (no rerun). Returns the three values; raises `FileNotFoundError` / `ValueError` and changes
    nothing when there is no usable winner."""
    path = Path(path) if path is not None else config.settings.textmodel_report_file
    if not path.is_file():
        raise FileNotFoundError(f"no report at {path}: run `newsrag textmodel bakeoff` first")
    winner, items = parse_report(path.read_text(encoding="utf-8"))
    winner, judge = choose_applied(winner, items)
    config.set_pipeline_key("enrich_model", winner)
    config.set_pipeline_key("answer_model", winner)
    config.set_pipeline_key("answer_judge_model", judge)
    return winner, winner, judge


# --- saved outputs and judgements -----------------------------------------------------------------------


def _hash(*parts: bytes | str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def gen_fingerprint(candidate: str, stories: Sequence[Mapping[str, Any]]) -> str:
    """What the saved outputs of `candidate` depend on: the stories, the four prompts, the
    settings the tasks read and the profile."""
    from newsrag.models.profiles import get_profile

    s = config.settings
    return _hash(
        json.dumps([(st["pub_date"], st["story_id"], _hash(st["text"])) for st in stories]),
        *(
            p.read_bytes()
            for p in (core.PROMPT_FILE, facts.PROMPT_FILE, SUMMARY_PROMPT, QUESTION_PROMPT)
        ),
        json.dumps(
            [
                s.enrich_max_tokens,
                s.enrich_max_listed,
                s.enrich_max_facts,
                s.enrich_input_chars,
                s.textmodel_summary_max_tokens,
            ]
        ),
        get_profile(candidate).model_dump_json(),
    )


def judge_fingerprint(judge: str, subject_id: str) -> str:
    from newsrag.models.profiles import get_profile

    s = config.settings
    return _hash(
        subject_id,
        JUDGE_PROMPT.read_bytes(),
        get_profile(judge).model_dump_json(),
        str(s.textmodel_judge_max_tokens),
    )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_json(path: Path, data: Any) -> None:
    from newsrag.stages.base import write_json_atomic

    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, data)


def gen_path(candidate: str) -> Path:
    return config.settings.textmodel_dir / candidate / "outputs.json"


def judged_path(judge: str, subject: str) -> Path:
    return config.settings.textmodel_dir / judge / f"judged-{subject}.json"


# --- the run -----------------------------------------------------------------------------------------------


@dataclass
class Generation:
    candidate: str
    gen_id: str
    seconds: float
    records: dict[str, dict[str, Any]]


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".replace("\n", " ")[:200]


def check_request(candidates: Sequence[str]) -> dict[str, str]:
    """{candidate: family}. Raises `BakeoffError` for an unknown or non-text profile, a repeated
    candidate, or fewer than two candidates."""
    from newsrag.models.profiles import UnknownProfileError, get_profile

    if len(set(candidates)) != len(candidates):
        raise BakeoffError("a candidate is listed twice")
    if len(candidates) < 2:
        raise BakeoffError("a bake-off needs at least two candidates")
    family: dict[str, str] = {}
    for name in candidates:
        try:
            profile = get_profile(name)
        except UnknownProfileError as exc:
            raise BakeoffError(str(exc)) from None
        if not profile.is_vllm:
            raise BakeoffError(f"{name} is an {profile.kind} profile, not a text model")
        family[name] = profile.family
    return family


def run_bakeoff(
    candidates: Sequence[str],
    stories: Sequence[Mapping[str, Any]],
    rerun: bool = False,
    echo: Echo = lambda _m: None,
    opener: Opener | None = None,
    report_path: Path | None = None,
) -> tuple[list[Row], str | None, Path]:
    """Run (or reuse) every candidate's outputs and judgements, write the report. Returns
    (rows in candidate order, winner or None, the report path)."""
    if opener is None:
        from newsrag import models

        opener = models.use
    s = config.settings
    started = time.monotonic()
    family = check_request(candidates)
    gens: dict[str, Generation] = {}
    failed: dict[str, str] = {}
    notes: list[str] = []

    def load_gen(name: str) -> Generation | None:
        data = None if rerun else _load_json(gen_path(name))
        if not data or data.get("fingerprint") != gen_fingerprint(name, stories):
            return None
        return Generation(name, data["gen_id"], float(data["seconds"]), data["records"])

    def load_judged(judge: str, subject: str) -> dict[str, list[int] | None] | None:
        """Saved grades; they carry the id of the outputs they graded, so a regenerated subject
        (a rerun) never reuses old ones."""
        data = _load_json(judged_path(judge, subject))
        if not data or data.get("fingerprint") != judge_fingerprint(judge, gens[subject].gen_id):
            return None
        return data["scores"]

    def subjects_of(judge: str) -> list[str]:
        """The finished candidates `judge` may grade: a different family (FR-3)."""
        return [x for x in gens if judge in judges_for(x, list(gens), family)]

    def judge_pending(judge: str, client: Any) -> None:
        for subject in subjects_of(judge):
            if load_judged(judge, subject) is not None:
                continue
            echo(f"  {judge} judges {subject}")
            got = judge_subject(client, stories, gens[subject].records)
            _save_json(
                judged_path(judge, subject),
                {
                    "fingerprint": judge_fingerprint(judge, gens[subject].gen_id),
                    "judge": judge,
                    "subject": subject,
                    "scores": got,
                },
            )

    def open_model(name: str, work: Callable[[Any], None], what: str) -> None:
        """Run `work(client)` with `name` loaded; a failure is recorded, not raised."""
        echo(f"{what} {name}")
        try:
            with opener(name) as client:
                work(client)
        except Exception as exc:  # noqa: BLE001 - a candidate that cannot run is reported failed
            if name in gens:
                notes.append(f"{name} as judge: {_reason(exc)}")
            else:
                failed[name] = _reason(exc)
            echo(f"  {name}: {_reason(exc)}")

    # Phase 1: each candidate writes its outputs, then grades the finished candidates of another
    # family while it is loaded (so the last candidate grades everybody else without a reload).
    for name in candidates:
        gen = load_gen(name)
        if gen is not None:
            gens[name] = gen
            echo(f"reusing the saved outputs of {name}")
            continue

        def work(client: Any, name: str = name) -> None:
            records, seconds = generate(client, stories)
            gen = Generation(name, secrets.token_hex(8), seconds, records)
            _save_json(
                gen_path(name),
                {
                    "fingerprint": gen_fingerprint(name, stories),
                    "gen_id": gen.gen_id,
                    "seconds": seconds,
                    "records": records,
                },
            )
            gens[name] = gen
            echo(f"  {name}: {seconds:.0f} s for {len(stories)} stories")
            judge_pending(name, client)

        open_model(name, work, "generating with")

    # Phase 2: a finished candidate that still has candidates to grade is loaded again.
    for name in candidates:
        if name in gens and any(load_judged(name, x) is None for x in subjects_of(name)):
            open_model(name, lambda client, name=name: judge_pending(name, client), "judging with")

    rows: list[Row] = []
    for name in candidates:
        row = Row(name, family[name])
        gen = gens.get(name)
        if gen is None:
            row.status, row.reason = FAILED, failed.get(name, "no outputs")
            rows.append(row)
            continue
        row.scores = objective_scores(gen.records, s.textmodel_output_telugu_share)
        row.seconds_per_story = gen.seconds / len(stories) if stories else None
        unusable = 0
        for judge in judges_for(name, list(gens), family):
            got = load_judged(judge, name)
            if got is None:
                continue
            unusable += sum(1 for x in got.values() if x is None)
            mean = judge_mean(got)
            if mean is not None:
                row.judge_means[judge] = mean
        if row.judge_means:
            row.judged = sum(row.judge_means.values()) / len(row.judge_means)
        else:
            row.status, row.reason = UNJUDGED, "no judge of another family finished"
        if unusable:
            row.reason = f"{unusable} judge replies unusable"
        rows.append(row)
    winner = pick_winner(ranked_rows(rows))
    path = Path(report_path) if report_path is not None else s.textmodel_report_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_report(
            rows,
            stories,
            winner,
            notes,
            elapsed_s=time.monotonic() - started,
            stories_file=_relative(s.textmodel_stories_file),
        ),
        encoding="utf-8",
    )
    return rows, winner, path


def _relative(path: Path) -> str:
    with contextlib.suppress(ValueError):
        return str(Path(path).relative_to(config.settings.repo_root))
    return str(path)
