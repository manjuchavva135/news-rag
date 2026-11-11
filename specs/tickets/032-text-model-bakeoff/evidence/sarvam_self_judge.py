"""One-off: Sarvam-M grades its own bake-off outputs (self-judging), compared with the other judges."""
import json
from pathlib import Path
from newsrag import config, models
from newsrag.textmodel import bakeoff as bk

s = config.settings
stories = bk.ensure_stories_file(s.textmodel_stories_file, s.textmodel_n, s.textmodel_seed)
base = s.textmodel_dir
out = Path(__file__).with_name("sarvam_self_scores.json")

def mean(path):
    return bk.judge_mean(json.loads(Path(path).read_text())["scores"])

recs = {c: json.loads((base / c / "outputs.json").read_text())["records"]
        for c in ["sarvam-m-24b", "qwen3-30b-a3b", "gemma3-27b", "gemma4-31b"]}
with models.use("sarvam-m-24b") as client:
    self_scores = bk.judge_subject(client, stories, recs["sarvam-m-24b"])
out.write_text(json.dumps(self_scores))
print("sarvam grades sarvam (self):", round(bk.judge_mean(self_scores), 3))
for j in ["gemma3-27b", "gemma4-31b", "qwen3-30b-a3b"]:
    print(f"{j} grades sarvam:", round(mean(base / j / "judged-sarvam-m-24b.json"), 3))
for subj in ["qwen3-30b-a3b", "gemma3-27b", "gemma4-31b"]:
    print(f"sarvam grades {subj}:", round(mean(base / "sarvam-m-24b" / f"judged-{subj}.json"), 3))
