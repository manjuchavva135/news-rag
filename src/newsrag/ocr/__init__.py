"""OCR on our own GPU (ticket 007): Surya finds the blocks, indic-ocr reads their text.

Surya lives in its own venv (`envs/surya/`) and runs as a standalone stdin/stdout worker script
(`surya_worker.py`). indic-ocr's text model (IndicBlockOCR) is served by vLLM from `envs/vllm/`
(ticket 006) with indic-ocr's own tokenizer files (`text.py`). The main venv imports neither
surya nor indic-ocr. `newsrag.ocr.client.OcrClient` is the only way other code calls them.

Importing this package touches neither the GPU, the network nor the venvs.
"""
