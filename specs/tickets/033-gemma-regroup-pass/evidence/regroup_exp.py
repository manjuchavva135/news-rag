"""Experiment: does giving Gemma 4 more detail (page parts) improve grouping?

Variants per page (all gemma4-31b, judged by gemma4-31b `judge_grouping`):
  base    one request, whole page overlay at group_image_max_px (current behaviour)
  halves  two requests, left/right half (by block centre), each with a cropped full-res overlay
  multi2  one request: whole page + left/right half crops (overlap), all blocks at once
  multi4  one request: whole page + 4 quadrant crops (overlap), all blocks at once
Run with NEWSRAG_MODELS_FILE pointing at a models.yaml whose gemma4-31b allows 5 images.
Usage: python regroup_exp.py OUT_DIR PAGE_ID...
"""

from __future__ import annotations

import io
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path

from newsrag import config
from newsrag.grouping import bakeoff as bk
from newsrag.grouping import geometry, parse
from newsrag.grouping import input as gin
from newsrag.grouping import judge as jg
from newsrag.models.client import build_messages
from newsrag.ocr.overlay import draw_blocks
from newsrag.stages import group as gs

OVERLAP = 0.06  # share of the page added on each side of a cut
MAX_TOKENS = 4096
MODEL = "gemma4-31b"


def jpeg(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92, subsampling=0)
    return buf.getvalue()


def crops(data, boxes):
    """Full-resolution overlay of all blocks, cut into `boxes` (x1,y1,x2,y2 fractions)."""
    full = draw_blocks(data["image"]["path"], data["blocks"], max_long_side=None)
    w, h = full.size
    out = []
    for x1, y1, x2, y2 in boxes:
        out.append(jpeg(full.crop((round(x1 * w), round(y1 * h), round(x2 * w), round(y2 * h)))))
    return out


def lr(o=OVERLAP):
    return [(0, 0, 0.5 + o, 1), (0.5 - o, 0, 1, 1)]


def quads(o=OVERLAP):
    return [
        (0, 0, 0.5 + o, 0.5 + o),
        (0.5 - o, 0, 1, 0.5 + o),
        (0, 0.5 - o, 0.5 + o, 1),
        (0.5 - o, 0.5 - o, 1, 1),
    ]


MULTI_NOTE = (
    "\n\n## Images\n\nYou get {n} images of the same page. Image 1 is the whole page. "
    "Images 2 to {n} are parts of it at higher resolution so you can read the box numbers and "
    "text: {parts}. The parts overlap a little at their edges. Box numbers are the same in every "
    "image. Group the whole page in one answer, using image 1 for the layout."
)


def multi_messages(data, parts, names):
    width, height = gin.page_size(data)
    blocks = data["blocks"]
    text = gin.prompt_text(width, height, blocks) + MULTI_NOTE.format(
        n=len(parts) + 1, parts=", ".join(names)
    )
    images = [gin.page_image(data, blocks), *crops(data, parts)]
    return build_messages(text, images)


def half_messages(data, blocks, box):
    """The ticket-012 prompt for these blocks, with the page cropped to `box` (fractions) and the
    coordinates shifted into the crop."""
    W, H = gin.page_size(data)
    x0, y0 = round(box[0] * W), round(box[1] * H)
    cw, ch = round(box[2] * W) - x0, round(box[3] * H) - y0
    shifted = [
        {**b, "bbox": [b["bbox"][0] - x0, b["bbox"][1] - y0, b["bbox"][2] - x0, b["bbox"][3] - y0]}
        for b in blocks
    ]
    full = draw_blocks(data["image"]["path"], blocks, max_long_side=None)
    image = jpeg(full.crop((x0, y0, x0 + cw, y0 + ch)))
    return build_messages(gin.prompt_text(cw, ch, shifted), [image])


def build(data, raw, flags=()):
    params = geometry.Params.from_settings()
    built = geometry.build_grouping(
        raw["articles"], raw["unassigned"], data["blocks"], data["page_id"], params
    )
    return {
        "page_id": data["page_id"],
        "model": MODEL,
        "prompt_version": gin.PROMPT_VERSION,
        "articles": built.articles,
        "unassigned": built.unassigned,
        "page_flags": [*dict.fromkeys([*(data.get("page_flags") or []), *flags, *built.page_flags])],
    }


def run_variant(variant, data, ask):
    blocks = data["blocks"]
    valid = {b["order"] for b in blocks}
    retries = config.settings.group_parse_retries
    if variant == "base":
        return gs.group_page(data, ask, MODEL)
    if variant == "feedback":  # the judge's findings on the base grouping, sent back
        base_dir = Path(os.environ["BASE_DIR"])
        g = json.loads((base_dir / f"{data['page_id']}.json").read_text())
        j = json.loads((base_dir / f"{data['page_id']}.judge.json").read_text())
        order = {b["block_id"]: b["order"] for b in blocks}
        lines = []
        for a in g["articles"]:
            v = j["verdicts"].get(a["article_id"])
            if v and v != "correct":
                nums = sorted(order[b["block_id"]] for b in a["blocks"] if b["block_id"] in order)
                what = {
                    "merged": "mixes two or more different stories: split it",
                    "split": "is only a piece of a larger story: join it to the rest",
                    "wrong_blocks": "has blocks that belong to another story",
                    "not_an_article": "is not a real article (wrong kind or stray blocks)",
                    "missing": "was not checked",
                }[v]
                lines.append(f"- the {a['kind']} article with blocks {nums} {what}")
        if j.get("missed_regions"):
            lines.append(f"- {j['missed_regions']} visible stories were left out of every article")
        width, height = gin.page_size(data)
        text = gin.prompt_text(width, height, blocks)
        if lines:
            text += (
                "\n\n## A reviewer checked an earlier grouping of this page\n\n"
                + "\n".join(lines)
                + "\n\nGroup the whole page again and fix these problems. Keep what was right."
            )
        msgs = build_messages(text, [gin.page_image(data, blocks)])
        raw = parse.request_grouping(lambda m: ask(m, len(blocks)), msgs, valid, retries)
        return build(data, raw)
    if variant == "retry5376":  # today's 013 regroup pass: same request, image at 5376 px
        return gs.group_page(data, ask, MODEL, image_long_side=5376)
    if variant == "halves":
        W, _ = gin.page_size(data)
        left, right = gs.split_halves(blocks, W)
        raws = []
        for part, box in ((left, lr()[0]), (right, lr()[1])):
            if not part:
                continue
            msgs = half_messages(data, part, box)
            ids = {b["order"] for b in part}
            raws.append(parse.request_grouping(lambda m, n=len(part): ask(m, n), msgs, ids, retries))
        return build(data, parse.merge_raw(raws), ["split_halves"])
    if variant == "multi2":
        msgs = multi_messages(data, lr(), ["image 2 is the left half", "image 3 the right half"])
    elif variant == "multi4":
        msgs = multi_messages(
            data,
            quads(),
            ["image 2 top left", "image 3 top right", "image 4 bottom left", "image 5 bottom right"],
        )
    else:
        raise ValueError(variant)
    raw = parse.request_grouping(lambda m: ask(m, len(blocks)), msgs, valid, retries)
    return build(data, raw)


def main():
    out = Path(sys.argv[1])
    pages = sys.argv[2:]
    import os

    variants = os.environ.get("VARIANTS", "base,halves,multi2,multi4").split(",")
    datas = {p: gin.load_ocr_final(p) for p in pages}
    usage = {v: [0, 0, 0] for v in variants}
    lock = threading.Lock()
    current = {"v": None}

    def on_usage(prompt, completion):
        with lock:
            u = usage[current["v"]]
            u[0] += 1
            u[1] += prompt
            u[2] = max(u[2], prompt)

    # a group `create` with a smaller max_tokens (fits 16384 with several images)
    from newsrag.models import use

    s = config.settings
    timings = {}
    with use(MODEL) as client:
        raw_client = client.raw.with_options(timeout=s.group_request_timeout_s, max_retries=0)

        def create(messages, max_items=None):
            return raw_client.chat.completions.create(
                model=client.profile,
                messages=messages,
                temperature=0,
                max_tokens=MAX_TOKENS,
                response_format=parse.response_format(max_items or 1000),
                stop=["\n\n"],
            )

        ask = gs.send(create, on_usage)
        judge_ask = gs.send(jg.make_create(client))
        for v in variants:
            current["v"] = v
            (out / v).mkdir(parents=True, exist_ok=True)
            errors = {}

            def one(p, v=v, errors=errors):
                try:
                    g = run_variant(v, datas[p], ask)
                except Exception as exc:  # noqa: BLE001
                    errors[p] = f"{type(exc).__name__}: {exc}"[:300]
                    return
                (out / v / f"{p}.json").write_text(json.dumps(g, ensure_ascii=False))

            t0 = time.monotonic()
            with ThreadPoolExecutor(16) as pool:
                list(pool.map(one, pages))
            timings[v] = {"seconds": round(time.monotonic() - t0, 1), "errors": errors}
            print(v, timings[v], "requests/prompt_tokens/max_prompt", usage[v], flush=True)

        # judge everything
        def judge(item):
            v, p = item
            path = out / v / f"{p}.json"
            if not path.is_file():
                return item, None
            g = json.loads(path.read_text())
            try:
                r = jg.judge_grouping(datas[p], g, ask=judge_ask)
            except Exception as exc:  # noqa: BLE001
                return item, {"error": str(exc)[:200]}
            (out / v / f"{p}.judge.json").write_text(json.dumps(r, ensure_ascii=False))
            return item, r

        with ThreadPoolExecutor(16) as pool:
            results = dict(pool.map(judge, [(v, p) for v in variants for p in pages]))

    summary = {"timings": timings, "usage": usage, "scores": {}}
    for v in variants:
        summary["scores"][v] = {
            p: (None if results.get((v, p)) is None else results[(v, p)].get("score", "err"))
            for p in pages
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(json.dumps(summary["scores"], indent=1))
    for v in variants:
        sc = [bk.page_score(results.get((v, p)) if results.get((v, p)) and "error" not in results[(v, p)] else None) for p in pages]
        print(v, "mean", round(sum(sc) / len(sc), 3), "below0.8", sum(x < 0.8 for x in sc))


if __name__ == "__main__":
    main()
