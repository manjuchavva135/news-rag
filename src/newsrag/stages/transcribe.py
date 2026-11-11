"""Stage `transcribe` (ticket 015): the grouping VLM reads tables, infographics and notices.

indic-ocr does not read figures, charts or ads. This pass cuts three kinds of target from the
rendered page and has `group_model` transcribe each one into Markdown:

- (c) every article of a `transcribe_article_kinds` kind (notices/tenders and commercial ads), as
  one crop of its union box; the VLM says whether it is a notice (kept with its fields) or a
  commercial ad (kept as `type: commercial` with no text, so ticket 017 can leave it out);
- (a) table/infographic blocks (by role, or by a table/chart label) of the other articles;
- (b) table, figure and chart blocks that are in no article (standalone items).

Each block is a target at most once. Photos (`photo_no_text`) are dropped. Item ids `tNN` count
all targets before dropping, ordered by their lowest block `order`, so they stay stable.
"""

from __future__ import annotations

import functools
import io
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.grouping import input as gin
from newsrag.grouping import parse
from newsrag.models.client import parse_json_reply
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    StageUnavailable,
    artifact_path,
    versioned,
    write_json_atomic,
)

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
BLOCK_PROMPT_FILE = PROMPTS / "transcribe_v1.md"
NOTICE_PROMPT_FILE = PROMPTS / "transcribe_notice_v1.md"
PROMPT_VERSION = 1
JPEG_QUALITY = 92
FAILED = "low_confidence:transcribe_failed"
LOW = "low_confidence:"

BLOCK_TYPES = ("table", "infographic", "photo_no_text")
NOTICE_CLASSES = ("notice_tender", "commercial")
TARGET_ROLES = ("table", "infographic")
KEPT_TYPES = ("table", "infographic", "notice_tender")  # what the spot-check samples

VERSION_SETTINGS = [
    "group_model",
    "transcribe_pad_px",
    "transcribe_block_max_px",
    "transcribe_article_max_px",
    "transcribe_max_tokens",
    "transcribe_parse_retries",
    "transcribe_article_kinds",
    "transcribe_standalone_kinds",
    "table_labels",
    "figure_labels",
    "chart_labels",
]
PROMPT_FILES = [BLOCK_PROMPT_FILE, NOTICE_PROMPT_FILE]

Ask = Callable[..., str]  # ask(messages, max_items=None) -> the model's reply text
BLOCK_REQUEST = 1  # `max_items` of a block request: `make_create` sends the block schema
NOTICE_REQUEST = 2  # ... of an article (notice/ad) request: the notice schema


# --- which regions ------------------------------------------------------------------------------


@dataclass
class Target:
    """One region to transcribe."""

    source: str  # "block" | "article"
    block_ids: list[str]
    article_id: str | None
    flags: list[str] = field(default_factory=list)
    inlined: bool = False
    first_order: int = 0
    item_id: str = ""


def _low(flags: Sequence[str] | None) -> list[str]:
    return [f for f in flags or [] if f.startswith(LOW)]


def select_targets(data: Mapping[str, Any], grouping: Mapping[str, Any]) -> list[Target]:
    """The page's targets, numbered `<page_id>#tNN` by their lowest block `order`.

    `data` is the page's `ocr_final` artifact, `grouping` its `group_final` artifact.
    """
    s = config.settings
    page_id = data["page_id"]
    blocks = {b["block_id"]: b for b in data.get("blocks") or []}
    page_low = _low(grouping.get("page_flags"))
    article_kinds = set(s.transcribe_article_kinds)
    standalone_kinds = set(s.transcribe_standalone_kinds)
    in_article_labels = {*s.table_labels, *s.chart_labels}
    loose_labels = {*s.table_labels, *s.figure_labels, *s.chart_labels}
    articles = list(grouping.get("articles") or [])

    taken: set[str] = set()
    targets: list[Target] = []

    def flags_of(article: Mapping[str, Any] | None) -> list[str]:
        return [*dict.fromkeys([*page_low, *_low((article or {}).get("flags"))])]

    # (c) whole notice/ad articles first: their blocks are not also (a)/(b) targets
    for article in articles:
        if article["kind"] not in article_kinds:
            continue
        ids = [b["block_id"] for b in article.get("blocks") or [] if b["block_id"] in blocks]
        ids = [i for i in dict.fromkeys(ids) if i not in taken]
        if not ids:
            continue
        taken.update(ids)
        targets.append(Target("article", ids, article["article_id"], flags_of(article)))

    # (a) table/infographic blocks of the other articles, by role or by label
    for article in articles:
        if article["kind"] in article_kinds:
            continue
        standalone = article["kind"] in standalone_kinds
        for entry in article.get("blocks") or []:
            bid = entry["block_id"]
            block = blocks.get(bid)
            if block is None or bid in taken:
                continue
            if entry.get("role") in TARGET_ROLES or block.get("label") in in_article_labels:
                taken.add(bid)
                targets.append(
                    Target(
                        "block",
                        [bid],
                        None if standalone else article["article_id"],
                        flags_of(article),
                        inlined=article["kind"] == "table_only",
                    )
                )

    # (b) table, figure and chart blocks in no article
    in_any = {e["block_id"] for a in articles for e in a.get("blocks") or []}
    for bid, block in blocks.items():
        if bid in in_any or bid in taken or block.get("label") not in loose_labels:
            continue
        taken.add(bid)
        targets.append(Target("block", [bid], None, flags_of(None)))

    for t in targets:
        t.first_order = min(int(blocks[b]["order"]) for b in t.block_ids)
    targets.sort(key=lambda t: t.first_order)
    for n, t in enumerate(targets, start=1):
        t.item_id = f"{page_id}#t{n:02d}"
    return targets


# --- the crop -----------------------------------------------------------------------------------


def union_box(boxes: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    return (
        min(float(b[0]) for b in boxes),
        min(float(b[1]) for b in boxes),
        max(float(b[2]) for b in boxes),
        max(float(b[3]) for b in boxes),
    )


def crop_jpeg(data: Mapping[str, Any], block_ids: Sequence[str], long_side: int) -> bytes:
    """JPEG of the union box of `block_ids` with `transcribe_pad_px` margin, shrunk (never grown)
    to `long_side`."""
    from PIL import Image

    s = config.settings
    blocks = {b["block_id"]: b for b in data.get("blocks") or []}
    image = (data.get("image") or {}).get("path") or Path(s.images_dir) / f"{data['page_id']}.png"
    if not Path(image).is_file():
        raise FileNotFoundError(f"rendered page missing: {image}")
    x1, y1, x2, y2 = union_box([blocks[b]["bbox"] for b in block_ids])
    pad = s.transcribe_pad_px
    with Image.open(image) as page:
        box = (
            max(0, round(x1) - pad),
            max(0, round(y1) - pad),
            min(page.width, round(x2) + pad),
            min(page.height, round(y2) + pad),
        )
        picture = page.convert("RGB").crop(box)
    longest = max(picture.size)
    if longest > long_side:
        scale = long_side / longest
        size = (max(1, round(picture.width * scale)), max(1, round(picture.height * scale)))
        picture = picture.resize(size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    picture.save(buf, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
    return buf.getvalue()


# --- talking to the model -----------------------------------------------------------------------

_NULLABLE = {"type": ["string", "null"]}

BLOCK_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "transcription",
        "schema": {
            "type": "object",
            "properties": {
                "type": {"enum": list(BLOCK_TYPES)},
                "title": _NULLABLE,
                "markdown": {"type": "string"},
            },
            "required": ["type", "title", "markdown"],
            "additionalProperties": False,
        },
    },
}

NOTICE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "notice",
        "schema": {
            "type": "object",
            "properties": {
                "class": {"enum": list(NOTICE_CLASSES)},
                "markdown": {"type": "string"},
                "department": _NULLABLE,
                "notice_number": _NULLABLE,
                "deadline": _NULLABLE,
            },
            "required": ["class", "markdown", "department", "notice_number", "deadline"],
            "additionalProperties": False,
        },
    },
}


def make_create(client: Any) -> Callable[..., Any]:
    """`create(messages, max_items)` on a `ModelClient`: temperature 0, the block schema
    (`BLOCK_REQUEST`) or the notice schema (`NOTICE_REQUEST`)."""
    s = config.settings
    raw = client.raw.with_options(timeout=s.group_request_timeout_s, max_retries=0)

    def create(messages: list[dict[str, Any]], max_items: int | None = None) -> Any:
        return raw.chat.completions.create(
            model=client.profile,
            messages=messages,
            temperature=0,
            max_tokens=s.transcribe_max_tokens,
            response_format=NOTICE_FORMAT if max_items == NOTICE_REQUEST else BLOCK_FORMAT,
        )

    return create


def ask_factory(client: Any) -> Ask:
    from newsrag.stages.group import send

    return send(make_create(client))


def _opt_str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise parse.SchemaError(f"`{key}` must be a string or null")
    return value.strip() or None


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _deadline(value: str | None) -> str | None:
    """A valid `YYYY-MM-DD` date, else None (an unreadable date is not a reason to drop)."""
    if value is None or not _DATE.match(value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def parse_block(reply: str) -> dict[str, Any]:
    """`{type, title, markdown}` of a block reply; `parse.SchemaError` if invalid."""
    try:
        data = parse_json_reply(reply)
    except ValueError as exc:
        raise parse.SchemaError(str(exc)) from None
    if not isinstance(data, dict):
        raise parse.SchemaError("the reply must be a JSON object")
    kind = data.get("type")
    if kind not in BLOCK_TYPES:
        raise parse.SchemaError(f"`type` {kind!r} is not one of {', '.join(BLOCK_TYPES)}")
    markdown = data.get("markdown")
    if not isinstance(markdown, str):
        raise parse.SchemaError("`markdown` must be a string")
    return {"type": kind, "title": _opt_str(data, "title"), "markdown": markdown.strip()}


def parse_notice(reply: str) -> dict[str, Any]:
    """`{class, markdown, department, notice_number, deadline}` of a notice reply."""
    try:
        data = parse_json_reply(reply)
    except ValueError as exc:
        raise parse.SchemaError(str(exc)) from None
    if not isinstance(data, dict):
        raise parse.SchemaError("the reply must be a JSON object")
    klass = data.get("class")
    if klass not in NOTICE_CLASSES:
        raise parse.SchemaError(f"`class` {klass!r} is not one of {', '.join(NOTICE_CLASSES)}")
    markdown = data.get("markdown")
    if not isinstance(markdown, str):
        raise parse.SchemaError("`markdown` must be a string")
    return {
        "class": klass,
        "markdown": markdown.strip(),
        "department": _opt_str(data, "department"),
        "notice_number": _opt_str(data, "notice_number"),
        "deadline": _deadline(_opt_str(data, "deadline")),
    }


def request(
    ask: Ask,
    messages: list[dict[str, Any]],
    kind: int,
    parser: Callable[[str], dict[str, Any]],
    retries: int,
) -> dict[str, Any] | None:
    """The parsed reply, asking again with the error up to `retries` times; None when no reply
    was valid (or the reply did not fit `transcribe_max_tokens`)."""
    sent = list(messages)
    for _ in range(retries + 1):
        try:
            reply = ask(sent, kind)
        except parse.TooLong:
            return None
        try:
            return parser(reply)
        except parse.SchemaError as exc:
            sent = [
                *messages,
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": f"Your reply was not valid: {exc}\n"
                    "Answer again with only the corrected JSON object.",
                },
            ]
    return None


# --- one page -----------------------------------------------------------------------------------


def _item(target: Target, **fields: Any) -> dict[str, Any]:
    return {
        "item_id": target.item_id,
        "source": target.source,
        "block_ids": list(target.block_ids),
        "article_id": target.article_id,
        "type": fields["type"],
        "title": fields.get("title"),
        "markdown": fields.get("markdown"),
        "department": fields.get("department"),
        "notice_number": fields.get("notice_number"),
        "deadline": fields.get("deadline"),
        "flags": list(target.flags),
        "inlined": target.inlined,
    }


def read_target(
    data: Mapping[str, Any], target: Target, ask: Ask
) -> tuple[dict[str, Any] | None, str]:
    """(item or None, outcome): outcome is `kept`, `photo_no_text` or `failed`."""
    from newsrag.models.client import build_messages

    s = config.settings
    retries = s.transcribe_parse_retries
    if target.source == "article":
        image = crop_jpeg(data, target.block_ids, s.transcribe_article_max_px)
        prompt = NOTICE_PROMPT_FILE.read_text(encoding="utf-8")
        got = request(ask, build_messages(prompt, [image]), NOTICE_REQUEST, parse_notice, retries)
        if got is None:
            return None, "failed"
        if got["class"] == "commercial":
            return _item(target, type="commercial"), "kept"
        return (
            _item(
                target,
                type="notice_tender",
                markdown=got["markdown"],
                department=got["department"],
                notice_number=got["notice_number"],
                deadline=got["deadline"],
            ),
            "kept",
        )
    image = crop_jpeg(data, target.block_ids, s.transcribe_block_max_px)
    prompt = BLOCK_PROMPT_FILE.read_text(encoding="utf-8")
    got = request(ask, build_messages(prompt, [image]), BLOCK_REQUEST, parse_block, retries)
    if got is None:
        return None, "failed"
    if got["type"] == "photo_no_text":
        return None, "photo_no_text"
    return _item(target, type=got["type"], title=got["title"], markdown=got["markdown"]), "kept"


def transcribe_page(
    data: Mapping[str, Any], grouping: Mapping[str, Any], ask: Callable[[], Ask]
) -> dict[str, Any]:
    """The `transcribe` artifact of one page. `ask()` returns the model call; it is only called
    when the page has a target (the model opens lazily)."""
    targets = select_targets(data, grouping)
    items: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    failed = 0
    model_ask = ask() if targets else None
    for target in targets:
        assert model_ask is not None
        item, outcome = read_target(data, target, model_ask)
        if item is not None:
            items.append(item)
        else:
            dropped.append({"item_id": target.item_id, "reason": outcome})
            failed += outcome == "failed"
    page_flags = [FAILED] if failed else []
    if failed:
        for item in items:
            if FAILED not in item["flags"]:
                item["flags"].append(FAILED)
    return {
        "page_id": data["page_id"],
        "model": config.settings.group_model,
        "prompt_version": PROMPT_VERSION,
        "n_targets": len(targets),
        "items": items,
        "dropped": dropped,
        "transcribe_failed": failed,
        "page_flags": page_flags,
    }


# --- the stage ----------------------------------------------------------------------------------


class TranscribeStage(PageStage):
    name = "transcribe"
    depends_on = ("group_final",)
    concurrency = config.settings.group_concurrency

    def __init__(self) -> None:
        from newsrag.stages.judge import LazyModel

        self._model = LazyModel(lambda: config.settings.group_model, ask_factory)

    @functools.cached_property
    def version(self) -> str:  # type: ignore[override]
        prefix = f"{config.settings.group_model}-transcribe-p{PROMPT_VERSION}"
        return versioned(prefix, VERSION_SETTINGS, PROMPT_FILES)

    @property
    def models_needed(self) -> tuple[str, ...]:  # type: ignore[override]
        return (config.settings.group_model,)

    def preflight(self) -> None:
        from newsrag.models import get_profile

        try:
            profile = get_profile(config.settings.group_model)
        except KeyError as exc:
            raise StageUnavailable(str(exc)) from None
        if profile.kind != "vlm":
            raise StageUnavailable(f"group_model {profile.name} is not a vlm profile")

    def open(self) -> None:
        """The model opens at the first page that has a target."""
        self._model.reset()

    def close(self) -> None:
        self._model.close()

    def handle(self, page: PageRow) -> StageResult:
        pid = page.page_id
        grouping_path = gin.done_artifact("group_final", pid)
        if grouping_path is None:
            raise FileNotFoundError(f"no done group_final artifact for {pid}")
        grouping = gin.load_artifact(grouping_path)
        data = gin.load_ocr_final(pid)
        artifact = transcribe_page(data, grouping, self._model.open)
        path = write_json_atomic(artifact_path(self.name, self.version, pid), artifact)
        counts: dict[str, int] = {}
        for item in artifact["items"]:
            counts[item["type"]] = counts.get(item["type"], 0) + 1
        return StageResult(
            str(path),
            {
                "n_targets": artifact["n_targets"],
                "n_items": len(artifact["items"]),
                "types": counts,
                "photo_no_text": sum(1 for d in artifact["dropped"] if d["reason"] != "failed"),
                "transcribe_failed": artifact["transcribe_failed"],
                "page_flags": artifact["page_flags"],
            },
        )
