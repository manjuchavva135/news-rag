#!/usr/bin/env bash
# E-3 from specs/tickets/006-gpu-model-runner/eval.md, step by step.
set -u
export PATH=$HOME/.local/bin:$PATH
cd /home/admin1/news-rag/.claude/worktrees/006-gpu-model-runner
uv run newsrag models serve qwen3-30b-a3b --port 8100 > /tmp/e3a.log 2>&1 &
until grep -q "serving qwen3-30b-a3b" /tmp/e3a.log; do
  if ! kill -0 %1 2>/dev/null; then echo "serve died"; cat /tmp/e3a.log; exit 1; fi
  sleep 2
done
echo "== step 2: $(grep serving /tmp/e3a.log)"
timeout 90 uv run newsrag models serve gemma3-27b --port 8101 > /tmp/e3b.log 2>&1; true
echo "== step 3 (/tmp/e3b.log):"; grep -v Fetching /tmp/e3b.log
echo "== step 4 (models status):"; uv run newsrag models status
echo "== step 5 (compute processes):"; nvidia-smi --query-compute-apps=pid --format=csv,noheader | wc -l
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
kill %1; wait
echo "== after: $(uv run newsrag models status)"
