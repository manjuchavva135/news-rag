#!/usr/bin/env bash
# Automated checks and E-4..E-8, as written in eval.md (run from the repo root).
set -u
export PATH="$HOME/.local/bin:$PATH"
echo "--- automated: ruff check"; uv run ruff check .; echo "exit=$?"
echo "--- automated: ruff format --check"; uv run ruff format --check .; echo "exit=$?"
echo "--- automated: unit tests"; uv run pytest -q -m "not gpu and not data and not stores"; echo "exit=$?"
echo "--- E-4"; uv run pytest -q tests/test_ocr_stage.py -k missing_token; echo "exit=$?"
echo "--- E-5"; uv run pytest -q tests/test_ocr_stage.py -k zero_blocks; echo "exit=$?"
echo "--- E-6"; uv run python -c "import importlib.util;print(importlib.util.find_spec('indic_ocr'))"
echo "--- E-7 step 1"
uv run pytest -q tests/test_ocr_stage.py -k "surya_layout_then_indic_blocks or figures_not_sent or tables_sent_as_table or error_reply_keeps_worker or worker_restart_on_crash or timeout_kills_worker or not_installed_preflight"
echo "exit=$?"
echo "--- E-7 step 2"
uv run python -c "import yaml;d=yaml.safe_load(open('labels.yaml'));print(len(d['labels']), sorted(set(d['labels'].values())))"
echo "--- E-8"
uv run pytest -q tests/test_ocr_stage.py -k "label_settings or process_group_killed or unknown_label_other or labels_in_version or nfc_in_client"
echo "exit=$?"
