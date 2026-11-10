"""Per-article check, text only: for each news article of the stored grouping (controls) and the
merged article (the corruption), ask the model whether the headlines + full text are one story.
Read-only. argv: model, out.jsonl, pairs log."""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from newsrag import models
from newsrag.grouping import input as gin
from newsrag.grouping import judge as jg
from newsrag.models.client import build_messages, parse_json_reply

PROMPT = """You check one article cut out of a Telugu newspaper page (Sakshi). An automatic system \
collected the text boxes below into this article. Sometimes it wrongly joins two or more different \
news stories into one article. Read the headlines and the text and decide: is this ONE news story \
(one event or topic; a headline, deck, sub-headings, a table or a photo caption of the same story \
are fine), or TWO OR MORE different stories joined together?

Return only JSON on one line: {{"stories": 1}} or {{"stories": 2}} (2 means two or more).

Headline boxes: {heads}

Text:
{text}
"""

model, out, pairs_log = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
pairs = []
for line in pairs_log.read_text().splitlines():
    m = re.match(r"(\S+) pair=(a\d+)\+(a\d+) ", line)
    if m:
        pairs.append((m[1], f"{m[1]}#{m[2]}", f"{m[1]}#{m[3]}"))


def items_for(pid, first, second):
    g = gin.load_artifact(gin.done_artifact("group", pid))
    d = gin.load_ocr_final(pid)
    text = {b["block_id"]: " ".join(str(b.get("text") or "").split()) for b in d["blocks"]}
    merged = jg.merge_articles(g, first, second)
    out = []
    for tag, grouping in (("control", g), ("merged", merged)):
        for a in grouping["articles"]:
            if a["kind"] != "news" or (tag == "merged" and a["article_id"] != first):
                continue
            heads = [text.get(b["block_id"], "") for b in a["blocks"] if b.get("role") == "headline"]
            body = "\n".join(text.get(b["block_id"], "") for b in a["blocks"])[:6000]
            out.append((pid, tag, a["article_id"], " / ".join(h for h in heads if h) or "-", body))
    return out


items = [it for p in pairs for it in items_for(*p)]
print(model, len(items), "articles", flush=True)
with models.use(model) as client, out.open("w") as f:
    raw = client.raw.with_options(timeout=300, max_retries=2)
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "stories",
            "schema": {
                "type": "object",
                "properties": {"stories": {"type": "integer", "enum": [1, 2]}},
                "required": ["stories"],
                "additionalProperties": False,
            },
        },
    }

    def ask(msgs, _n):
        r = raw.chat.completions.create(
            model=client.profile, messages=msgs, temperature=0, max_tokens=20, response_format=schema
        )
        return r.choices[0].message.content

    def one(item):
        pid, tag, aid, heads, body = item
        msgs = build_messages(PROMPT.format(heads=heads, text=body), [])
        try:
            reply = parse_json_reply(ask(msgs, 1))
            n = int(reply.get("stories"))
        except Exception as exc:
            n = f"error: {exc!r}"[:120]
        return {"page_id": pid, "tag": tag, "article_id": aid, "stories": n}

    with ThreadPoolExecutor(16) as pool:
        results = list(pool.map(one, items))
    for r in results:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

merged = [r for r in results if r["tag"] == "merged"]
control = [r for r in results if r["tag"] == "control"]
print("merged caught:", sum(r["stories"] == 2 for r in merged), "/", len(merged))
print("control false alarms:", sum(r["stories"] == 2 for r in control), "/", len(control))
print("errors:", sum(isinstance(r["stories"], str) for r in results))
for r in merged:
    print(" ", r["page_id"], r["stories"])
print("DONE", flush=True)
