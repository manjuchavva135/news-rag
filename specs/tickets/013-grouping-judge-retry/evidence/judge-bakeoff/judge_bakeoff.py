"""Judge bake-off on fixed clean pairs: for each page the model judges the stored grouping (control:
are the two pair articles called correct?) and the grouping with the pair merged (caught: is the
merged article called anything but correct?). Read-only; argv: model, out.jsonl, pairs log."""

import json
import re
import sys
import time
from pathlib import Path

from newsrag import models
from newsrag.grouping import input as gin
from newsrag.grouping import judge as jg
from newsrag.stages.group import send

model, out, pairs_log = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
variant = sys.argv[4] if len(sys.argv) > 4 else "v1"


def all_headlines(grouping, data):
    """v2 input: an article's headline is every headline-role block's text, joined by ' / '."""
    text = {b["block_id"]: " ".join(str(b.get("text") or "").split()) for b in data["blocks"]}
    out = dict(grouping)
    out["articles"] = []
    for a in grouping["articles"]:
        heads = [text.get(b["block_id"], "") for b in a["blocks"] if b.get("role") == "headline"]
        heads = [h for h in heads if h]
        out["articles"].append(dict(a, headline=" / ".join(heads)) if heads else a)
    return out
pairs = []
for line in pairs_log.read_text().splitlines():
    m = re.match(r"(\S+) pair=(a\d+)\+(a\d+) ", line)
    if m:
        pairs.append((m[1], f"{m[1]}#{m[2]}", f"{m[1]}#{m[3]}"))
print(model, len(pairs), "pairs", flush=True)

with models.use(model) as client, out.open("w") as f:
    ask = send(jg.make_create(client))
    for pid, first, second in pairs:
        grouping = gin.load_artifact(gin.done_artifact("group", pid))
        data = gin.load_ocr_final(pid)
        rec = {"page_id": pid, "model": model, "variant": variant, "pair": [first, second]}
        for tag, g in (("stored", grouping), ("merged", jg.merge_articles(grouping, first, second))):
            t0 = time.time()
            if variant == "v2":
                g = all_headlines(g, data)
            try:
                r = jg.judge_grouping(data, g, ask=ask)
            except Exception as exc:  # record and go on
                r = {"error": repr(exc)}
            r["seconds"] = round(time.time() - t0, 1)
            rec[tag] = r
        sv, mv = rec["stored"].get("verdicts", {}), rec["merged"].get("verdicts", {})
        rec["control"] = [sv.get(first), sv.get(second)]
        rec["merged_verdict"] = mv.get(first)
        rec["caught"] = jg.merge_caught(mv.get(first, jg.MISSING))
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        print(
            pid,
            f"control={rec['control']} merged={rec['merged_verdict']}",
            "caught" if rec["caught"] else "missed",
            f"score {rec['stored'].get('score')}->{rec['merged'].get('score')}",
            rec["stored"].get("error") or rec["merged"].get("error") or "",
            flush=True,
        )
print("DONE", flush=True)
