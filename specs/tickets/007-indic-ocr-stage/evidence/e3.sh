#!/usr/bin/env bash
# E-3, as written in eval.md (run from the repo root), plus a substitute for step 1: the dev
# sample has no 2026 dates, so eval.md's `grep '^2026'` picks no page.
set -u
export PATH="$HOME/.local/bin:$PATH"
echo "--- step 1 (as written)"
P=$(grep -v '^#' data/dev-sample.txt | grep '^2026' | grep '_main_01$' | head -1)
echo "page: ${P:-<none: no 2026 *_main_01 page in the dev sample>}"
echo "--- step 1 substitute: the newest _main_01 page of the dev sample"
P=$(grep -v '^#' data/dev-sample.txt | grep '_main_01$' | sort | tail -1)
echo "page: $P"
uv run newsrag ocr show "$P" | head -20
echo "--- step 2"
uv run python -c "import json,glob,unicodedata as u;t=''.join(b['text'] for f in glob.glob('/home/admin1/raw/work/ocr/*/*.json') for b in json.load(open(f))['blocks']);print(len(t), t.count('�')/max(1,len(t)), t==u.normalize('NFC',t))"
echo "--- extra: U+2581 (SentencePiece space marker) left in the text"
uv run python -c "import json,glob;t=''.join(b['text'] for f in glob.glob('/home/admin1/raw/work/ocr/*/*.json') for b in json.load(open(f))['blocks']);print(t.count(chr(0x2581)))"
