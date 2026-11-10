"""Grouping model bake-off (ticket 014): pick `group_model` by evidence.

Every candidate VLM groups the same few pages with the ticket-012 code (`group_page`) and reads
the same Telugu headline crops; then the judge (ticket 013's `judge_grouping`) scores every
candidate's groupings. The report `<reports_dir>/group-bakeoff.md` names the winner, which
`newsrag group bakeoff --apply` writes into `pipeline.yaml`.

Nothing here touches the ledger's `group`/`judge` rows or `work/group/`: outputs go to
`<work_dir>/group_bakeoff/` and `<work_dir>/group_bakeoff_judge/` and are reused on a rerun.
Models are opened one at a time through `newsrag.models.use` (the GPU lock).
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.grouping import judge as jg

REPORT_NAME = "group-bakeoff.md"
READ_PROMPT = "Read the Telugu text in the image exactly. Reply with the text only."
READ_PROMPT_VERSION = 1
TIMING_FILE = "timing.json"
TELUGU_FILE = "telugu.json"
TELUGU_RANGE = (0x0C00, 0x0C7F)  # the Telugu Unicode block

DONE, FAILED, SKIPPED = "done", "failed", "skipped"

Ask = Callable[..., str]  # ask(messages, max_items=None) -> the model's reply text
LoadPage = Callable[[str], dict[str, Any]]  # page id -> its `ocr_final` artifact


class BakeoffError(ValueError):
    """A bad request (unknown profile, judge not a vlm): exit code 2."""


# --- the pages ---------------------------------------------------------------------------------


def page_parts(page_id: str) -> tuple[str, str, str, str]:
    """(date, edition, section, page) of ``<date>_<edition>_<section>_<page>``."""
    parts = page_id.split("_")
    if len(parts) < 4:
        return page_id, "", "", ""
    return parts[0], parts[1], "_".join(parts[2:-1]), parts[-1]


def page_number(page: str) -> int | None:
    match = re.match(r"\d+", page)
    return int(match.group()) if match else None


def _year(page_id: str) -> str:
    return page_parts(page_id)[0][:4]


def _main_01(year: str) -> Callable[[str], bool]:
    def match(pid: str) -> bool:
        _, _, section, page = page_parts(pid)
        return _year(pid) == year and section == "main" and page_number(page) == 1

    return match


def _single_letter_inner(year: str) -> Callable[[str], bool]:
    def match(pid: str) -> bool:
        _, edition, _, page = page_parts(pid)
        return _year(pid) == year and len(edition) == 1 and page_number(page) not in (1, None)

    return match


def _all_edition(pid: str) -> bool:
    return _year(pid) == "2025" and page_parts(pid)[1] == "ALL"


def _feature(pid: str) -> bool:
    return page_parts(pid)[2] in ("fea", "splus")


# FR-2's five slots; a slot is a list of rules tried in turn
PAGE_SLOTS: list[list[Callable[[str], bool]]] = [
    [_main_01("2020")],
    [_main_01("2026")],
    [_all_edition],
    [_single_letter_inner("2026"), _single_letter_inner("2020")],
    [_feature],
]


def choose_pages(sample: Sequence[str]) -> list[str]:
    """Five distinct pages from the dev sample (FR-2): each slot takes the first sample line (in
    sorted order) that matches it and is not chosen yet; a slot with no match then takes the next
    unused sample line. Fewer pages only when the sample has fewer than five lines."""
    lines = sorted(dict.fromkeys(sample))
    chosen: list[str | None] = []
    for rules in PAGE_SLOTS:
        pick = next(
            (pid for rule in rules for pid in lines if rule(pid) and pid not in chosen), None
        )
        chosen.append(pick)
    for k, pick in enumerate(chosen):
        if pick is None:
            chosen[k] = next((pid for pid in lines if pid not in chosen), None)
    return [pid for pid in chosen if pid is not None]


def ensure_pages_file(path: Path, sample_file: Path) -> list[str]:
    """The bake-off pages: read `path`, or choose them from `sample_file` and write `path`."""
    from newsrag.stages.runner import read_pages_file

    if path.is_file():
        return read_pages_file(path)
    pages = choose_pages(read_pages_file(sample_file))
    # no header line: the file is exactly one page id per line
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{p}\n" for p in pages), encoding="utf-8")
    return pages


# --- the Telugu reading score ------------------------------------------------------------------


def _letters(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace())


def telugu_share(text: str) -> float:
    """Share of the non-space characters that are in the Telugu block."""
    chars = _letters(text)
    if not chars:
        return 0.0
    lo, hi = TELUGU_RANGE
    return sum(1 for ch in chars if lo <= ord(ch) <= hi) / len(chars)


def is_telugu_headline(block: Mapping[str, Any]) -> bool:
    s = config.settings
    text = str(block.get("text") or "")
    return (
        block.get("type") == "title"
        and len(_letters(text)) >= s.bakeoff_telugu_min_chars
        and telugu_share(text) >= s.bakeoff_telugu_min_share
    )


def choose_headlines(
    pages: Sequence[tuple[str, Sequence[Mapping[str, Any]]]], count: int
) -> list[tuple[str, str]]:
    """(page_id, block_id) of `count` Telugu title blocks, taken round-robin over the pages (in
    the given order, blocks in reading order) so no page dominates."""
    queues = [
        [(pid, b["block_id"]) for b in blocks if is_telugu_headline(b)] for pid, blocks in pages
    ]
    out: list[tuple[str, str]] = []
    depth = 0
    while len(out) < count and any(depth < len(q) for q in queues):
        for queue in queues:
            if depth < len(queue) and len(out) < count:
                out.append(queue[depth])
        depth += 1
    return out


def read_headlines_file(path: Path) -> list[tuple[str, str]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            page_id, block_id = line.split()[:2]
            out.append((page_id, block_id))
    return out


def ensure_headlines_file(
    path: Path, sample_file: Path, load_page: LoadPage
) -> list[tuple[str, str]]:
    """The Telugu headline blocks: read `path`, or choose them from the dev sample's `ocr_final`
    and write `path`."""
    from newsrag.stages.runner import read_pages_file

    if path.is_file():
        return read_headlines_file(path)
    s = config.settings
    pages = [
        (pid, load_page(pid).get("blocks") or []) for pid in sorted(read_pages_file(sample_file))
    ]
    chosen = choose_headlines(pages, s.bakeoff_telugu_count)
    header = (
        "# Ticket 014 Telugu reading score: <page_id> <block_id> of `title` blocks of the dev "
        f"sample with >= {s.bakeoff_telugu_min_share:.0%} Telugu letters and >= "
        f"{s.bakeoff_telugu_min_chars} characters (round-robin over pages). Reference text: "
        "the block's ocr_final text.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "".join(f"{p}\t{b}\n" for p, b in chosen), encoding="utf-8")
    return chosen


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def similarity(reply: str, reference: str) -> float:
    """1 - normalised edit distance after NFC and with all whitespace removed (1.0 = exact)."""
    a = _letters(unicodedata.normalize("NFC", reply))
    b = _letters(unicodedata.normalize("NFC", reference))
    if not a and not b:
        return 1.0
    return 1.0 - levenshtein(a, b) / max(len(a), len(b))


def crop_png(data: Mapping[str, Any], block: Mapping[str, Any]) -> bytes:
    """PNG of one block cut from the rendered page, with `bakeoff_crop_pad_px` margin."""
    from PIL import Image

    s = config.settings
    image = (data.get("image") or {}).get("path") or Path(s.images_dir) / f"{data['page_id']}.png"
    pad = s.bakeoff_crop_pad_px
    with Image.open(image) as page:
        x1, y1, x2, y2 = (float(v) for v in block["bbox"])
        box = (
            max(0, round(x1) - pad),
            max(0, round(y1) - pad),
            min(page.width, round(x2) + pad),
            min(page.height, round(y2) + pad),
        )
        picture = page.convert("RGB").crop(box)
    buf = io.BytesIO()
    picture.save(buf, format="PNG")
    return buf.getvalue()


def read_one(ask: Ask, data: Mapping[str, Any], block_id: str) -> dict[str, Any]:
    from newsrag.models.client import build_messages

    block = next((b for b in data.get("blocks") or [] if b["block_id"] == block_id), None)
    if block is None:
        raise KeyError(f"{data['page_id']} has no block {block_id}")
    reference = str(block.get("text") or "")
    try:
        reply = ask(build_messages(READ_PROMPT, [crop_png(data, block)]), None)
        error = None
    except Exception as exc:  # noqa: BLE001 - a reading that fails scores 0
        reply, error = "", f"{type(exc).__name__}: {exc}"
    item = {
        "page_id": data["page_id"],
        "block_id": block_id,
        "reference": reference,
        "reply": reply,
        "similarity": round(similarity(reply, reference) if error is None else 0.0, 4),
    }
    if error:
        item["error"] = error
    return item


# --- versions and paths ------------------------------------------------------------------------


def group_version(candidate: str) -> str:
    """The ticket-012 `group` version with `group_model` set to the candidate."""
    from newsrag.stages.base import config_hash
    from newsrag.stages.group import PROMPT_VERSION, VERSION_SETTINGS

    names = [n for n in VERSION_SETTINGS if n != "group_model"]
    return f"{candidate}-group-p{PROMPT_VERSION}-{config_hash(names, [gin.PROMPT_FILE])}"


def telugu_version(candidate: str) -> str:
    from newsrag.stages.base import config_hash

    # the headline list itself is checked by `cached_telugu`
    names = ["bakeoff_crop_pad_px", "bakeoff_read_max_tokens"]
    return f"{candidate}-telugu-r{READ_PROMPT_VERSION}-{config_hash(names)}"


def judge_version(judge: str) -> str:
    from newsrag.stages.base import config_hash

    names = [n for n in jg.JUDGE_SETTINGS if n != "judge_model"]
    return f"{judge}-judge-p{jg.PROMPT_VERSION}-{config_hash(names, jg.PROMPT_FILES)}"


def group_dir(candidate: str) -> Path:
    from newsrag.stages.base import version_slug

    return config.settings.work_dir / "group_bakeoff" / version_slug(group_version(candidate))


def telugu_dir(candidate: str) -> Path:
    from newsrag.stages.base import version_slug

    return config.settings.work_dir / "group_bakeoff" / version_slug(telugu_version(candidate))


def judge_root(candidate: str) -> Path:
    """All judgements of the candidate's current grouping version."""
    from newsrag.stages.base import version_slug

    root = config.settings.work_dir / "group_bakeoff_judge"
    return root / version_slug(group_version(candidate))


def judge_dir(candidate: str, judge: str) -> Path:
    from newsrag.stages.base import version_slug

    return judge_root(candidate) / version_slug(judge_version(judge))


def report_path() -> Path:
    return Path(config.settings.reports_dir) / REPORT_NAME


# --- talking to the models ---------------------------------------------------------------------


def make_read_create(client: Any) -> Callable[..., Any]:
    """`create(messages, max_items)` for one headline reading: free text, temperature 0."""
    s = config.settings
    raw = client.raw.with_options(timeout=s.group_request_timeout_s, max_retries=0)

    def create(messages: list[dict[str, Any]], max_items: int | None = None) -> Any:
        return raw.chat.completions.create(
            model=client.profile,
            messages=messages,
            temperature=0,
            max_tokens=s.bakeoff_read_max_tokens,
        )

    return create


@contextlib.contextmanager
def connect(profile: str) -> Iterator[SimpleNamespace]:
    """Open a profile; yields `group`, `judge` and `read` ask functions on it."""
    from newsrag import models
    from newsrag.stages import group as group_stage

    with models.use(profile) as client:
        yield SimpleNamespace(
            group=group_stage.send(group_stage.make_create(client)),
            judge=group_stage.send(jg.make_create(client)),
            read=group_stage.send(make_read_create(client)),
        )


Connect = Callable[[str], contextlib.AbstractContextManager[Any]]


# --- one candidate -----------------------------------------------------------------------------


@dataclass
class Row:
    """One candidate's line in the report."""

    candidate: str
    status: str = DONE
    reason: str = ""
    seconds_per_page: float | None = None
    telugu_read: float | None = None
    scores: dict[str, float] = field(default_factory=dict)  # per page, as counted
    notes: dict[str, str] = field(default_factory=dict)  # per page: why it scored 0 or 1

    @property
    def mean_score(self) -> float | None:
        return round(sum(self.scores.values()) / len(self.scores), 3) if self.scores else None

    def below(self, threshold: float) -> int:
        return sum(1 for v in self.scores.values() if v < threshold)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def cached_grouping(candidate: str, pages: Sequence[str]) -> dict[str, Any] | None:
    """The saved timing of a finished grouping run of these pages, or None."""
    path = group_dir(candidate) / TIMING_FILE
    if not path.is_file():
        return None
    timing = _load_json(path)
    if list(timing.get("pages") or []) != list(pages):
        return None
    failed = timing.get("failed") or {}
    if any(p not in failed and not (group_dir(candidate) / f"{p}.json").is_file() for p in pages):
        return None
    return timing


def cached_telugu(candidate: str, items: Sequence[tuple[str, str]]) -> dict[str, Any] | None:
    path = telugu_dir(candidate) / TELUGU_FILE
    if not path.is_file():
        return None
    saved = _load_json(path)
    got = [(i["page_id"], i["block_id"]) for i in saved.get("items") or []]
    return saved if got == list(items) else None


def run_grouping(
    candidate: str, pages: Sequence[str], datas: Mapping[str, dict], ask: Ask
) -> dict[str, Any]:
    """Group the pages (concurrently, `group_concurrency` threads); write each artifact and
    `timing.json`. Earlier judgements of this grouping version are removed."""
    from newsrag.stages.base import write_json_atomic
    from newsrag.stages.group import group_page

    out = group_dir(candidate)
    shutil.rmtree(judge_root(candidate), ignore_errors=True)
    failed: dict[str, str] = {}

    def one(page_id: str) -> None:
        try:
            artifact = group_page(datas[page_id], ask, candidate)
        except Exception as exc:  # noqa: BLE001 - a page that fails after the retries scores 0
            failed[page_id] = f"{type(exc).__name__}: {exc}"
            return
        write_json_atomic(out / f"{page_id}.json", artifact)

    started = time.monotonic()
    with ThreadPoolExecutor(max(1, min(config.settings.group_concurrency, len(pages)))) as pool:
        list(pool.map(one, pages))
    seconds = time.monotonic() - started
    timing = {
        "candidate": candidate,
        "version": group_version(candidate),
        "pages": list(pages),
        "seconds": round(seconds, 2),
        "seconds_per_page": round(seconds / max(1, len(pages)), 2),
        "failed": failed,
    }
    write_json_atomic(out / TIMING_FILE, timing)
    return timing


def run_telugu(
    candidate: str, items: Sequence[tuple[str, str]], datas: Mapping[str, dict], ask: Ask
) -> dict[str, Any]:
    from newsrag.stages.base import write_json_atomic

    def one(item: tuple[str, str]) -> dict[str, Any]:
        return read_one(ask, datas[item[0]], item[1])

    with ThreadPoolExecutor(
        max(1, min(config.settings.group_concurrency, len(items) or 1))
    ) as pool:
        read = list(pool.map(one, items))
    mean = round(sum(i["similarity"] for i in read) / len(read), 4) if read else None
    saved = {
        "candidate": candidate,
        "version": telugu_version(candidate),
        "mean": mean,
        "items": read,
    }
    write_json_atomic(telugu_dir(candidate) / TELUGU_FILE, saved)
    return saved


def page_score(result: Mapping[str, Any] | None) -> float:
    """A judged page's score as counted: a null (judge_skipped) score is 1."""
    if result is None:
        return 0.0
    score = result.get("score")
    return 1.0 if score is None else float(score)


# --- the winner and the report -----------------------------------------------------------------


def pick_winner(rows: Sequence[Row], tie: float | None = None) -> str | None:
    """Highest mean judge score; when the top two are within `tie` (default `bakeoff_tie`), the
    higher `telugu_read`, then fewer seconds/page. None when no candidate finished."""
    tie = config.settings.bakeoff_tie if tie is None else tie
    done = [r for r in rows if r.status == DONE and r.mean_score is not None]
    if not done:
        return None
    ranked = sorted(done, key=lambda r: -(r.mean_score or 0.0))
    if len(ranked) == 1:
        return ranked[0].candidate
    first, second = ranked[0], ranked[1]
    if round((first.mean_score or 0.0) - (second.mean_score or 0.0), 6) > tie:
        return first.candidate

    def tie_key(r: Row) -> tuple[float, float]:
        read = r.telugu_read if r.telugu_read is not None else -1.0
        speed = r.seconds_per_page if r.seconds_per_page is not None else float("inf")
        return (-read, speed)

    return min((first, second), key=tie_key).candidate


def _fmt(value: float | None, digits: int) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render_report(
    rows: Sequence[Row], judge: str, pages: Sequence[str], winner: str | None, threshold: float
) -> str:
    lines = [
        "# Grouping model bake-off (ticket 014)",
        "",
        f"judge: {judge}",
        f"pages: {len(pages)} ({', '.join(pages)})",
        f"judge_threshold: {threshold}",
        "",
        (
            "| candidate | status | mean judge score | pages below judge_threshold "
            "| seconds/page | telugu_read | note |"
        ),
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ran = r.status == DONE
        lines.append(
            f"| {r.candidate} | {r.status} | {_fmt(r.mean_score, 3)} "
            f"| {r.below(threshold) if ran else '-'} | {_fmt(r.seconds_per_page, 2)} "
            f"| {_fmt(r.telugu_read, 3)} | {r.reason} |"
        )
    done = [r for r in rows if r.status == DONE]
    if done:
        lines += ["", "Per page (judge score as counted; `*` = see notes):", ""]
        lines.append("| page | " + " | ".join(r.candidate for r in done) + " |")
        lines.append("|---|" + "---|" * len(done))
        for page in pages:
            cells = []
            for r in done:
                mark = "*" if page in r.notes else ""
                cells.append(f"{_fmt(r.scores.get(page), 3)}{mark}")
            lines.append(f"| {page} | " + " | ".join(cells) + " |")
        notes = [f"- {r.candidate} {p}: {n}" for r in done for p, n in r.notes.items()]
        if notes:
            lines += ["", "Notes:", "", *notes]
    lines += ["", f"winner: {winner or 'none'}", ""]
    return "\n".join(lines)


def report_value(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(\S+)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


# --- the whole bake-off ------------------------------------------------------------------------


def check_request(candidates: Sequence[str], judge: str) -> tuple[list[str], dict[str, str]]:
    """(candidates to run, {skipped candidate: reason}). Raises `BakeoffError` for an unknown
    profile or a judge / candidate that is not a vlm."""
    from newsrag.models import get_profile

    try:
        judge_profile = get_profile(judge)
    except KeyError:
        raise BakeoffError(f"unknown judge profile: {judge}") from None
    if judge_profile.kind != "vlm":
        raise BakeoffError(f"judge {judge} is not a vlm profile")
    run: list[str] = []
    skipped: dict[str, str] = {}
    for name in dict.fromkeys(candidates):
        try:
            profile = get_profile(name)
        except KeyError:
            raise BakeoffError(f"unknown candidate profile: {name}") from None
        if profile.kind != "vlm":
            raise BakeoffError(f"candidate {name} is not a vlm profile")
        if profile.family == judge_profile.family:
            skipped[name] = f"same family as the judge {judge} ({profile.family})"
        else:
            run.append(name)
    return run, skipped


def run_bakeoff(
    candidates: Sequence[str],
    judge: str,
    pages: Sequence[str],
    headlines: Sequence[tuple[str, str]],
    *,
    rerun: bool = False,
    load_page: LoadPage | None = None,
    connect_model: Connect | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[list[Row], str | None, Path]:
    """Run (or reuse) every candidate, judge them, write the report. Returns (rows, winner,
    report path)."""
    from newsrag.stages.base import write_json_atomic

    s = config.settings
    load_page = load_page or gin.load_ocr_final
    connect_model = connect_model or connect
    run, skipped = check_request(candidates, judge)
    for name, reason in skipped.items():
        echo(f"skip {name}: {reason}")

    needed = sorted({*pages, *(p for p, _ in headlines)})
    datas = {pid: load_page(pid) for pid in needed}

    rows: dict[str, Row] = {}
    timings: dict[str, dict[str, Any]] = {}
    for name in candidates:
        if name in skipped:
            rows[name] = Row(name, SKIPPED, skipped[name])
    for name in run:
        row = rows[name] = Row(name)
        timing = None if rerun else cached_grouping(name, pages)
        telugu = None if rerun else cached_telugu(name, headlines)
        if timing is not None and telugu is not None:
            echo(f"{name}: reusing grouping and Telugu reading")
        else:
            echo(f"{name}: loading the model")
            try:
                with connect_model(name) as asks:
                    if timing is None:
                        timing = run_grouping(name, pages, datas, asks.group)
                        echo(f"{name}: grouped {len(pages)} pages in {timing['seconds']} s")
                    if telugu is None:
                        telugu = run_telugu(name, headlines, datas, asks.read)
                        echo(f"{name}: read {len(headlines)} headlines, mean {telugu['mean']}")
            except Exception as exc:  # noqa: BLE001 - the model did not start (or died): excluded
                if timing is None or telugu is None:
                    row.status, row.reason = FAILED, f"{type(exc).__name__}: {exc}"[:300]
                    echo(f"{name}: failed: {row.reason}")
                    continue
                echo(f"{name}: error while releasing the model (results kept): {exc}")
        timings[name] = timing
        row.seconds_per_page = timing["seconds_per_page"]
        row.telugu_read = telugu["mean"]

    # judge every finished candidate's groupings with one model load
    todo: list[tuple[str, str]] = []
    results: dict[tuple[str, str], dict[str, Any] | None] = {}
    for name, timing in timings.items():
        for page in pages:
            path = judge_dir(name, judge) / f"{page}.json"
            if page in (timing.get("failed") or {}):
                results[(name, page)] = None
            elif path.is_file() and not rerun:
                results[(name, page)] = _load_json(path)
            else:
                todo.append((name, page))
    errors: dict[tuple[str, str], str] = {}
    if todo:
        echo(f"judging {len(todo)} groupings with {judge}")
        try:
            with connect_model(judge) as asks:

                def one(item: tuple[str, str]) -> None:
                    name, page = item
                    grouping = _load_json(group_dir(name) / f"{page}.json")
                    try:
                        result = jg.judge_grouping(datas[page], grouping, ask=asks.judge)
                    except Exception as exc:  # noqa: BLE001 - judging that still fails scores 0
                        errors[item] = f"judge failed: {type(exc).__name__}: {exc}"[:300]
                        results[item] = None
                        return
                    write_json_atomic(judge_dir(name, judge) / f"{page}.json", result)
                    results[item] = result

                with ThreadPoolExecutor(max(1, min(s.judge_concurrency, len(todo)))) as pool:
                    list(pool.map(one, todo))
        except Exception as exc:  # noqa: BLE001 - the judge did not start: nothing can be scored
            reason = f"judge {judge} failed to start: {type(exc).__name__}: {exc}"[:300]
            echo(reason)
            for name in timings:
                rows[name].status, rows[name].reason = FAILED, reason
            timings = {}

    for name, timing in timings.items():
        row = rows[name]
        failed = timing.get("failed") or {}
        for page in pages:
            result = results.get((name, page))
            row.scores[page] = page_score(result)
            if page in failed:
                row.notes[page] = f"grouping failed: {failed[page]}"[:300]
            elif (name, page) in errors:
                row.notes[page] = errors[(name, page)]
            elif result is not None and result.get("score") is None:
                row.notes[page] = "judge_skipped (null score counted as 1)"

    ordered = [rows[name] for name in dict.fromkeys(candidates)]
    winner = pick_winner(ordered)
    path = report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_report(ordered, judge, pages, winner, s.judge_threshold), encoding="utf-8"
    )
    return ordered, winner, path


def apply_report(path: Path | None = None) -> tuple[str, str]:
    """Write the report's winner as `group_model` and its judge as `judge_model` into
    pipeline.yaml. Raises `FileNotFoundError` without a report and `ValueError` with
    `winner: none`; nothing is changed then."""
    path = path or report_path()
    if not path.is_file():
        raise FileNotFoundError(f"no bake-off report at {path}: run `newsrag group bakeoff`")
    text = path.read_text(encoding="utf-8")
    winner, judge = report_value(text, "winner"), report_value(text, "judge")
    if not winner or winner == "none":
        raise ValueError(f"{path}: no winner")
    if not judge:
        raise ValueError(f"{path}: no judge line")
    config.set_pipeline_key("group_model", winner)
    config.set_pipeline_key("judge_model", judge)
    return winner, judge
