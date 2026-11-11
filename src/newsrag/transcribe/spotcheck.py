"""`newsrag transcribe spotcheck` (ticket 015 FR-6): how much of the transcribed text is invented.

A sample of kept items (tables, infographics, notices) goes to the judge model (`judge_model`)
with the item's crop and its transcription split into numbered lines; the judge says per line
`present`, `not_present` or `unsure`. `invented_pct` = not_present lines / all lines x 100.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.grouping import parse
from newsrag.models.client import parse_json_reply
from newsrag.stages import transcribe as tr

PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "spotcheck_v1.md"
VERDICTS = ("present", "not_present", "unsure")
NO_ANSWER = "unsure"  # a line the judge never answered for

Ask = Callable[..., str]

# a Markdown table separator row: `|---|:--:|`, `--- | ---`
_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


def is_separator(line: str) -> bool:
    return bool(_SEPARATOR.match(line.strip()))


def item_lines(item: Mapping[str, Any]) -> list[str]:
    """The lines the judge checks: the title (when it is not already the first line of the
    markdown) and every non-empty markdown line except table separator rows."""
    lines = [ln.strip() for ln in str(item.get("markdown") or "").splitlines()]
    lines = [ln for ln in lines if ln and not is_separator(ln)]
    title = str(item.get("title") or "").strip()
    if title and (not lines or lines[0].lstrip("#").strip() != title):
        lines.insert(0, title)
    return lines


def prompt_text(lines: Sequence[str]) -> str:
    numbered = "\n".join(f"{n}. {line}" for n, line in enumerate(lines, start=1))
    template = PROMPT_FILE.read_text(encoding="utf-8")
    return template.replace("{count}", str(len(lines))).replace("{lines}", numbered)


def response_format(count: int) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "spotcheck",
            "schema": {
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "array",
                        "maxItems": max(1, count),
                        "items": {
                            "type": "object",
                            "properties": {
                                "n": {"type": "integer"},
                                "verdict": {"enum": list(VERDICTS)},
                            },
                            "required": ["n", "verdict"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["lines"],
                "additionalProperties": False,
            },
        },
    }


def make_create(client: Any) -> Callable[..., Any]:
    s = config.settings
    raw = client.raw.with_options(timeout=s.group_request_timeout_s, max_retries=0)

    def create(messages: list[dict[str, Any]], max_items: int | None = None) -> Any:
        return raw.chat.completions.create(
            model=client.profile,
            messages=messages,
            temperature=0,
            max_tokens=s.transcribe_spotcheck_max_tokens,
            response_format=response_format(max_items or 1000),
        )

    return create


def parse_reply(reply: str, count: int) -> dict[int, str]:
    """Verdict per line number (1..count) of one reply; unknown/repeated numbers are ignored.
    `parse.SchemaError` for invalid JSON or a verdict outside the enum."""
    try:
        data = parse_json_reply(reply)
    except ValueError as exc:
        raise parse.SchemaError(str(exc)) from None
    entries = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise parse.SchemaError("the reply must be a JSON object with a `lines` list")
    verdicts: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise parse.SchemaError("every `lines` entry must be an object")
        n, verdict = entry.get("n"), entry.get("verdict")
        if verdict not in VERDICTS:
            raise parse.SchemaError(f"verdict {verdict!r} is not one of {', '.join(VERDICTS)}")
        if parse._is_int(n) and 1 <= n <= count and n not in verdicts:
            verdicts[n] = verdict
    return verdicts


def check_item(data: Mapping[str, Any], item: Mapping[str, Any], ask: Ask) -> dict[str, Any]:
    """The per-item result: its lines with the judge's verdicts."""
    from newsrag.models.client import build_messages

    s = config.settings
    lines = item_lines(item)
    long_side = (
        s.transcribe_article_max_px if item["source"] == "article" else s.transcribe_block_max_px
    )
    result: dict[str, Any] = {
        "item_id": item["item_id"],
        "type": item["type"],
        "lines": [],
        "error": None,
    }
    if not lines:
        return result
    image = tr.crop_jpeg(data, item["block_ids"], long_side)
    messages = build_messages(prompt_text(lines), [image])
    verdicts: dict[int, str] = {}
    sent = list(messages)
    for _ in range(s.transcribe_parse_retries + 1):
        try:
            reply = ask(sent, len(lines))
        except parse.TooLong as exc:
            result["error"] = f"too long: {exc}"
            break
        try:
            verdicts = parse_reply(reply, len(lines))
        except parse.SchemaError as exc:
            result["error"] = str(exc)
            sent = [
                *messages,
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"Your reply was not valid: {exc}\nAnswer again."},
            ]
            continue
        result["error"] = None
        missing = len(lines) - len(verdicts)
        if missing:
            result["error"] = f"{missing} lines without a verdict (counted as unsure)"
        break
    result["lines"] = [
        {"n": n, "text": line, "verdict": verdicts.get(n, NO_ANSWER)}
        for n, line in enumerate(lines, start=1)
    ]
    return result


def invented_pct(results: Sequence[Mapping[str, Any]]) -> float | None:
    lines = [ln for r in results for ln in r["lines"]]
    if not lines:
        return None
    bad = sum(1 for ln in lines if ln["verdict"] == "not_present")
    return round(bad / len(lines) * 100, 2)


# --- the sample ---------------------------------------------------------------------------------


def kept_items(artifacts: Sequence[Mapping[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """(page_id, item) of every kept item (tables, infographics, notices), in id order."""
    out = [
        (a["page_id"], dict(item))
        for a in artifacts
        for item in a.get("items") or []
        if item["type"] in tr.KEPT_TYPES
    ]
    return sorted(out, key=lambda pair: pair[1]["item_id"])


def sample(items: Sequence[Any], n: int, seed: int) -> list[Any]:
    if len(items) <= n:
        return list(items)
    return random.Random(seed).sample(list(items), n)


def done_artifacts(page_ids: Collection[str] | None = None) -> list[dict[str, Any]]:
    """The done `transcribe` artifacts of the current version (optionally only `page_ids`)."""
    from newsrag.grouping.input import load_artifact
    from newsrag.ledger import db

    where = "stage = 'transcribe' and status = 'done' and artifact_path is not null"
    params: list[Any] = []
    if page_ids is not None:
        where += " and page_id = any(%s)"
        params.append(list(page_ids))
    with db.conn() as c:
        rows = db.fetch_all(
            c, f"select page_id, artifact_path from stage_runs where {where}", params
        )
    return [load_artifact(r["artifact_path"]) for r in rows if Path(r["artifact_path"]).is_file()]


def run_spotcheck(
    artifacts: Sequence[Mapping[str, Any]],
    n: int,
    seed: int,
    load_page: Callable[[str], Mapping[str, Any]],
    ask: Ask | None = None,
) -> dict[str, Any]:
    """The report. `ask` defaults to the judge model opened with `newsrag.models.use`."""
    s = config.settings
    chosen = sample(kept_items(artifacts), n, seed)
    if chosen and ask is None:
        from newsrag import models
        from newsrag.stages.group import send

        with models.use(s.judge_model) as client:
            return run_spotcheck(artifacts, n, seed, load_page, send(make_create(client)))

    def one(pair: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        page_id, item = pair
        assert ask is not None
        return {"page_id": page_id, **check_item(load_page(page_id), item, ask)}

    with ThreadPoolExecutor(max_workers=max(1, s.judge_concurrency)) as pool:
        results = list(pool.map(one, chosen))
    lines = [ln for r in results for ln in r["lines"]]
    counts = {v: sum(1 for ln in lines if ln["verdict"] == v) for v in VERDICTS}
    return {
        "judge_model": s.judge_model,
        "n_requested": n,
        "seed": seed,
        "n_items": len(results),
        "n_lines": len(lines),
        "verdicts": counts,
        "invented_pct": invented_pct(results),
        "items": results,
    }
