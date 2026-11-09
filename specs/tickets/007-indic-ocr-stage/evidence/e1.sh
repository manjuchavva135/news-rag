#!/usr/bin/env bash
# E-1 (run from the repo root). After the switch to vLLM (see results.md) indic-ocr has no venv
# of its own: its text model runs in envs/vllm, so step 2 checks envs/vllm and envs/surya.
# In the ticket worktree envs/vllm is the main checkout's (NEWSRAG_VLLM_ENV_DIR).
set -u
export PATH="$HOME/.local/bin:$PATH"
VLLM_ENV=${NEWSRAG_VLLM_ENV_DIR:-envs/vllm}
echo "--- first install (pins already in pipeline.yaml from the first run of this ticket)"
time uv run newsrag ocr install
echo "--- step 1: grep pins, then install again (timed)"
grep -E 'indic_ocr_revision|surya_version' pipeline.yaml
time uv run newsrag ocr install
echo "--- step 2"
for e in "$VLLM_ENV" envs/surya; do echo "$e:"; "$e/bin/python" -c "import torch;print(torch.cuda.is_available(), torch.version.cuda)"; done
echo "--- tokenizer folder"
ls -l /home/admin1/raw/indic-ocr-tokenizer/*/
