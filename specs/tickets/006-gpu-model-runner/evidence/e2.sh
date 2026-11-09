#!/usr/bin/env bash
# E-2 from specs/tickets/006-gpu-model-runner/eval.md, as written, then `models status`.
export PATH=$HOME/.local/bin:$PATH
cd /home/admin1/news-rag/.claude/worktrees/006-gpu-model-runner
for p in qwen3-vl-32b gemma3-27b qwen3-30b-a3b bge-m3; do
  uv run newsrag models download $p && uv run newsrag models smoke $p
  echo "== $p exit=$?"
done
echo "== models status: $(uv run newsrag models status)"
