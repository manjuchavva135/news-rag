#!/usr/bin/env bash
# E-2, as written in eval.md (run from the repo root).
set -u
export PATH="$HOME/.local/bin:$PATH"
echo "--- step 1"
time uv run newsrag stage run ocr --pages-file data/dev-sample.txt
echo "--- failures"
uv run newsrag stage failures ocr
echo "--- step 2"
uv run newsrag stage status | grep '^ocr '
echo "--- step 3"
uv run python -c "import json,glob;fs=[f for f in glob.glob('/home/admin1/raw/work/ocr/*/*.json') if json.load(open(f))['blocks']];d=json.load(open(fs[0]));b=d['blocks'][0];print(sorted(d), sorted(b))"
