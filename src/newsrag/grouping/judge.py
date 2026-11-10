"""Judging a page's article grouping (ticket 013 FR-2, FR-7).

A vision-language model of another family than the grouping model sees the page with the articles
painted and numbered (`draw_articles`), plus one list line per article, and says for each article
whether it is one correct story. The page score is the share of correct articles.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.grouping import parse
from newsrag.grouping.overlay import article_number, draw_articles
from newsrag.models.client import parse_json_reply

PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "judge_v1.md"
PROMPT_VERSION = 1
VERDICTS = ("correct", "merged", "split", "wrong_blocks", "not_an_article")
MISSING = "missing"  # an article the judge never answered for: counts as not correct
JPEG_QUALITY = 92

# every setting a judging stage reads (its version hash)
JUDGE_SETTINGS = [
    "judge_model",
    "judge_image_max_px",
    "judge_text_chars",
    "judge_skip_kinds",
    "judge_max_tokens",
    "group_parse_retries",
]

Ask = Callable[..., str]  # ask(messages, max_items=None) -> the model's reply text


class JudgeError(RuntimeError):
    """The judge never gave a usable answer."""


# --- families ----------------------------------------------------------------------------------


def check_families() -> None:
    """Raise StageUnavailable unless the judge is a vlm of another family than the grouper."""
    from newsrag.models import get_profile
    from newsrag.stages.base import StageUnavailable

    s = config.settings
    try:
        judge, group = get_profile(s.judge_model), get_profile(s.group_model)
    except KeyError as exc:
        raise StageUnavailable(str(exc)) from None
    if judge.kind != "vlm":
        raise StageUnavailable(f"judge_model {judge.name} is not a vlm profile")
    if judge.family == group.family:
        raise StageUnavailable("judge_model and group_model must be different families")


# --- what the judge is asked -------------------------------------------------------------------


def judged_kinds() -> list[str]:
    return [k for k in parse.KINDS if k not in config.settings.judge_skip_kinds]


def judged_articles(grouping: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The articles the judge is asked about (not mastheads and ads), in grouping order."""
    skip = set(config.settings.judge_skip_kinds)
    return [a for a in grouping.get("articles") or [] if a["kind"] not in skip]


def article_line(article: Mapping[str, Any], text_chars: int) -> str:
    """``#<n> [<kind>] <headline> | <first text_chars chars of the text>``."""
    headline = " ".join(str(article.get("headline") or "").split())
    text = " ".join(str(article.get("text") or "").split())[:text_chars]
    line = f"#{int(article_number(dict(article)))} [{article['kind']}] {headline}"
    return f"{line} | {text}" if text_chars > 0 else line


def prompt_text(articles: Sequence[Mapping[str, Any]], text_chars: int) -> str:
    lines = "\n".join(article_line(a, text_chars) for a in articles) or "(none)"
    template = PROMPT_FILE.read_text(encoding="utf-8")
    return template.replace("{count}", str(len(articles))).replace("{articles}", lines)


def overlay_image(page: Mapping[str, Any], grouping: Mapping[str, Any], long_side: int) -> bytes:
    """JPEG of the page with the judged articles painted and numbered; nothing else is drawn."""
    image = (page.get("image") or {}).get("path") or (
        Path(config.settings.images_dir) / f"{page['page_id']}.png"
    )
    if not Path(image).is_file():
        raise FileNotFoundError(f"rendered page missing: {image}")
    picture = draw_articles(
        image,
        dict(grouping),
        max_long_side=long_side,
        mark_flags=False,
        kinds=judged_kinds(),
        blocks=page["blocks"],
    )
    buf = io.BytesIO()
    picture.save(buf, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
    return buf.getvalue()


def response_format(max_items: int) -> dict[str, Any]:
    """The reply's JSON schema (known verdicts, no list longer than the article count)."""
    cap = max(1, max_items)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "judgement",
            "schema": {
                "type": "object",
                "properties": {
                    "articles": {
                        "type": "array",
                        "maxItems": cap,
                        "items": {
                            "type": "object",
                            "properties": {
                                "n": {"type": "integer"},
                                "verdict": {"enum": list(VERDICTS)},
                            },
                            "required": ["n", "verdict"],
                            "additionalProperties": False,
                        },
                    },
                    "missed_regions": {"type": "integer", "minimum": 0},
                },
                "required": ["articles", "missed_regions"],
                "additionalProperties": False,
            },
        },
    }


def make_create(client: Any) -> Callable[..., Any]:
    """`create(messages, max_items)` on a `ModelClient`: temperature 0, the reply schema."""
    s = config.settings
    raw = client.raw.with_options(timeout=s.group_request_timeout_s, max_retries=0)

    def create(messages: list[dict[str, Any]], max_items: int | None = None) -> Any:
        return raw.chat.completions.create(
            model=client.profile,
            messages=messages,
            temperature=0,
            max_tokens=s.judge_max_tokens,
            response_format=response_format(max_items or 1000),
            stop=["\n\n"],  # the reply is one compact line; blank lines are a loop
        )

    return create


# --- reading the answer ------------------------------------------------------------------------


def parse_reply(text: str, numbers: Collection[int]) -> tuple[dict[int, str], int, str | None]:
    """(verdict per article number, missed_regions, problem) of one reply.

    Raises `parse.SchemaError` for invalid JSON or a mistyped field or verdict. `problem` is None
    or the text of the number errors (an article number missing, repeated or unknown); the
    verdicts then hold the first use of every known number.
    """
    try:
        data = parse_json_reply(text)
    except ValueError as exc:
        raise parse.SchemaError(str(exc)) from None
    if not isinstance(data, dict):
        raise parse.SchemaError("the reply must be a JSON object with an `articles` list")
    entries = data.get("articles")
    if not isinstance(entries, list):
        raise parse.SchemaError("`articles` must be a list")
    missed = data.get("missed_regions")
    if not parse._is_int(missed) or missed < 0:
        raise parse.SchemaError("`missed_regions` must be a whole number, 0 or more")
    verdicts: dict[int, str] = {}
    unknown: list[int] = []
    repeated: list[int] = []
    for i, entry in enumerate(entries):
        at = f"articles[{i}]"
        if not isinstance(entry, dict):
            raise parse.SchemaError(f'{at} must be an object like {{"n": 1, "verdict": "correct"}}')
        n, verdict = entry.get("n"), entry.get("verdict")
        if not parse._is_int(n):
            raise parse.SchemaError(f"{at}.n must be an integer article number, got {n!r}")
        if verdict not in VERDICTS:
            raise parse.SchemaError(f"{at}.verdict {verdict!r} is not one of {', '.join(VERDICTS)}")
        if n not in numbers:
            unknown.append(n)
        elif n in verdicts:
            repeated.append(n)
        else:
            verdicts[n] = verdict
    problems = []
    if unknown:
        problems.append(f"article numbers {unknown} are not in the list")
    if repeated:
        problems.append(f"article numbers {repeated} are answered more than once")
    absent = sorted(set(numbers) - set(verdicts))
    if absent:
        problems.append(f"article numbers {absent} have no verdict")
    return verdicts, missed, "; ".join(problems) or None


def request_judgement(
    ask: Callable[[list[dict[str, Any]]], str],
    messages: list[dict[str, Any]],
    numbers: Collection[int],
    retries: int = 2,
) -> tuple[dict[int, str], int]:
    """`ask(messages)` returns the reply text. A reply that is invalid or has number errors is
    answered with the error appended; after `retries` more tries, number errors are tolerated
    (unknown and repeated numbers ignored, missing ones not correct) but invalid JSON or an
    invalid verdict fails with `parse.GroupingError`."""
    sent = list(messages)
    error = ""
    for attempt in range(retries + 1):
        reply = ask(sent)
        try:
            verdicts, missed, problem = parse_reply(reply, numbers)
        except parse.SchemaError as exc:
            error = str(exc)
        else:
            if problem is None or attempt == retries:
                return verdicts, missed
            error = problem
        sent = [
            *messages,
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": (
                    f"Your reply was not valid: {error}\n"
                    "Answer again with only the corrected JSON object, one entry for every "
                    "article number in the list."
                ),
            },
        ]
    raise parse.GroupingError(f"no valid judgement after {retries + 1} replies: {error}")


def page_score(correct: int, judged: int, missed: int) -> float | None:
    """correct / (judged + missed), 3 decimals; None when there is nothing to judge."""
    total = judged + missed
    return round(correct / total, 3) if total else None


# --- judging one grouping ----------------------------------------------------------------------


def _resolve_page(page: Any) -> dict[str, Any]:
    """The page's `ocr_final` data from a dict, a page id or a `PageRow`."""
    if isinstance(page, Mapping):
        return dict(page)
    return gin.load_ocr_final(getattr(page, "page_id", page))


def judge_grouping(
    page: Any,
    grouping: Mapping[str, Any],
    image_long_side: int | None = None,
    *,
    ask: Ask | None = None,
) -> dict[str, Any]:
    """Score one grouping artifact of a page.

    `page` is the page's `ocr_final` data (or a page id / `PageRow`). Returns
    ``{"score": float | None, "verdicts": {article_id: verdict}, "missed_regions": int,
    "n_judged": int, "text_chars": int}``; masthead and ad articles are not judged and not in `verdicts`; an article
    the judge did not answer for has the verdict ``"missing"``. A page with no OCR blocks is not
    sent (score None); a page whose blocks all went unassigned is sent, so missed stories count. `ask(messages, max_items)` is the model call
    (`newsrag.stages.group.send(make_create(client))`); without it the judge model is opened with
    `newsrag.models.use`.
    """
    s = config.settings
    data = _resolve_page(page)
    articles = judged_articles(grouping)
    if not (data.get("blocks") or []):  # nothing on the page to judge (no model call)
        return {"score": None, "verdicts": {}, "missed_regions": 0, "n_judged": 0}
    if ask is None:
        from newsrag import models
        from newsrag.stages.group import send

        with models.use(s.judge_model) as client:
            return judge_grouping(data, grouping, image_long_side, ask=send(make_create(client)))

    from newsrag.models.client import build_messages

    numbers = {int(article_number(a)): a["article_id"] for a in articles}
    image = overlay_image(data, grouping, image_long_side or s.judge_image_max_px)
    chars = s.judge_text_chars
    # a dense page can exceed the judge's context: send shorter article texts, then none
    for text_chars in dict.fromkeys((chars, chars // 3, 0)):
        messages = build_messages(prompt_text(articles, text_chars), [image])
        try:
            verdicts, missed = request_judgement(
                lambda sent: ask(sent, len(numbers)), messages, numbers, s.group_parse_retries
            )
            break
        except parse.TooLong:
            continue
    else:
        raise JudgeError("the page does not fit the judge model")
    by_id = {article_id: verdicts.get(n, MISSING) for n, article_id in numbers.items()}
    correct = sum(1 for v in by_id.values() if v == "correct")
    return {
        "score": page_score(correct, len(by_id), missed),
        "verdicts": by_id,
        "missed_regions": missed,
        "n_judged": len(by_id),
        "text_chars": text_chars,  # article text length sent (shorter than judge_text_chars = fallback)
    }


# --- helper for the sanity check ---------------------------------------------------------------


def merge_two_longest(grouping: Mapping[str, Any], kind: str = "news") -> dict[str, Any] | None:
    """A copy of the grouping with the two `kind` articles of longest text merged into one (blocks
    united, headline of the earlier one, text rebuilt, bbox united, no flags); None when the page
    has fewer than two such articles. Nothing is written anywhere."""
    from newsrag.grouping import geometry

    articles = list(grouping.get("articles") or [])
    news = [(i, a) for i, a in enumerate(articles) if a["kind"] == kind]
    if len(news) < 2:
        return None
    longest = sorted(news, key=lambda ia: -len(ia[1].get("text") or ""))[:2]
    (i, first), (j, second) = sorted(longest, key=lambda ia: ia[0])
    merged = {
        **first,
        "blocks": [*first["blocks"], *second["blocks"]],
        "text": "\n\n".join(t for t in (first.get("text"), second.get("text")) if t),
        "bbox_union": geometry.union_box([first["bbox_union"], second["bbox_union"]]),
        "flags": [],
    }
    out = dict(grouping)
    out["articles"] = [merged if k == i else a for k, a in enumerate(articles) if k != j]
    return out
