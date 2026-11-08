# 006: GPU model runner (one model at a time)

**Status:** draft
**Mode:** solo
**Runs:** waits for 001: needs package and config

## Summary

One way to start, use and stop local models on the single RTX PRO 6000 (96 GB). Only one big model is
loaded at a time (grill A-28); a file lock makes concurrent jobs queue instead of running out of GPU
memory. Models are served by vLLM with an OpenAI-compatible API, so every later stage talks to a
local URL. All later GPU tickets use this.

## Functional requirements

- **FR-1** - `models.yaml` in the repo root lists named model profiles: `name`, `hf_id`, `family` (`qwen` | `gemma` | `internvl` | `bge` | `e5` …), `kind` (`vlm` | `llm` served by vLLM; `embed` run in-process by sentence-transformers), `dtype`, and for vLLM kinds `max_model_len`, `gpu_memory_utilization`, optional `extra_args` list; for `embed`: `dim`, `query_prefix`, `doc_prefix`. Initial profiles:
  - `qwen3-vl-32b`: `Qwen/Qwen3-VL-32B-Instruct`, qwen, vlm, bfloat16, `max_model_len: 32768`, `gpu_memory_utilization: 0.90`, `extra_args: ["--limit-mm-per-prompt", "{\"image\": 1}", "--mm-processor-kwargs", "{\"max_pixels\": 20000000}"]` (so a 5376-px page is not silently shrunk);
  - `gemma3-27b`: `google/gemma-3-27b-it`, gemma, vlm, bfloat16, `max_model_len: 16384`, `gpu_memory_utilization: 0.90`, `extra_args: ["--limit-mm-per-prompt", "{\"image\": 1}", "--mm-processor-kwargs", "{\"do_pan_and_scan\": true}"]` (pan-and-scan so a full page is not read at 896 px);
  - `qwen3-30b-a3b`: `Qwen/Qwen3-30B-A3B-Instruct-2507`, qwen, llm, bfloat16, `max_model_len: 32768`, `gpu_memory_utilization: 0.90`;
  - `bge-m3`: `BAAI/bge-m3`, bge, embed, `dim: 1024`, empty prefixes.
  Later tickets add profiles. All requests made through this runner use `temperature: 0` unless a ticket says otherwise.
- **FR-2** - A dedicated Python 3.12 venv `envs/vllm/` (created by `uv run newsrag models install`; idempotent — an existing venv with vLLM ≥ 0.11.0 is kept) holds a vLLM ≥ 0.11.0 build for CUDA ≥ 12.8 (Blackwell, Qwen3-VL support). The main venv does not import vLLM; it gets torch for CUDA ≥ 12.8 through `pyproject.toml` (index `https://download.pytorch.org/whl/cu128` or newer) plus `sentence-transformers`, `openai`, `pillow`, `pyyaml`. `models install` prints the vLLM version and `torch.cuda.get_device_capability()`. `uv run newsrag models download <profile>` downloads weights into `hf_home` (`HF_TOKEN` from `settings.subprocess_env()`); `serve`/`use` call it first, outside the start timeout.
- **FR-3** - `uv run newsrag models serve <profile> [--port 8100]` (vLLM profiles only) acquires the exclusive GPU lock at `settings.gpu_lock_path` (fcntl lock; while waiting it prints `waiting for GPU lock held by <holder>` immediately and then every 60 s, flushed), starts `vllm serve --host 127.0.0.1` from `envs/vllm/` with `settings.subprocess_env()` in its own process group (Linux `PR_SET_PDEATHSIG` so it dies with its parent), logging to `{raw_root}/logs/vllm-<profile>-<UTC ts>.log`, on the given port or, if busy, the next free port in 8100–8199; waits until `GET /v1/models` answers (timeout `model_start_timeout_s`, default 1200 s) and prints `serving <profile> at http://127.0.0.1:<port>/v1`. If vLLM exits early or the timeout passes, it kills the process group, releases the lock, prints `vllm failed to start <profile>:` plus the last 20 log lines, and exits 1. It stops vLLM and releases the lock on Ctrl-C / SIGTERM.
- **FR-4** - Python API:
  - `newsrag.models.gpu_lock(holder: str) -> ContextManager` — takes the exclusive GPU lock for any in-process GPU user (indic-ocr worker, sentence-transformers); `holder` is free text recorded in the lock file.
  - `newsrag.models.use(profile) -> ContextManager[Client]` — for vLLM profiles: if the lock file names a live `models serve` of the same profile, reuse its `base_url` without taking the lock (and never stop it); otherwise take the lock, start the server as in FR-3, yield an OpenAI-compatible client, and stop it on exit. For `embed` profiles: take `gpu_lock(profile)`, load the sentence-transformers model on CUDA and yield an object with `embed(texts, kind="doc"|"query") -> np.ndarray` (prefixes applied, L2-normalised).
  - The lock file records holder, PID, process-group id, profile, `base_url` (if any) and start time; `uv run newsrag models status` prints them or `GPU free`. A taker that finds a record whose PID is dead first kills that record's process group (orphaned vLLM) and then takes the lock.
- **FR-5** - `uv run newsrag models smoke <profile>` opens the profile with `use()` (starting and stopping it if nothing is serving) and sends one fixed request — `vlm`: a generated 400×120 white PNG with black text `NEWS 2026` drawn with `ImageFont.load_default(size=48)` + prompt "Read the text in the image. Reply with the text only."; `llm`: "Reply with the word OK."; `embed`: embed the string "test" — and prints the reply (or `dim=<n>`) and the latency.
- **FR-6** - Hugging Face downloads and every subprocess use `settings.subprocess_env()` (ticket 001 FR-9), so `HF_TOKEN` and `HF_HOME` (= `settings.hf_home`, default `{raw_root}/hf-cache`) reach them.
- **FR-7** - Clarifications (second review):
  - `use()` is re-entrant inside one process: a nested `use()` of the same profile returns the already-loaded model/client (reference-counted; released when the outermost exits); a nested `use()` of a different profile raises `ModelBusyError(<held>, <wanted>)`. `gpu_lock` uses `fcntl.flock` on a dedicated file descriptor held for the outermost scope only.
  - `newsrag.models.loaded_profiles() -> list[str]` returns profiles started or loaded by this process (a reused `models serve` counts, marked `reused`), used by ticket 029.
  - `use(profile, device="cuda"|"cpu")`: `cpu` is allowed for `embed` profiles only and takes no lock.
  - For vLLM profiles `use()` yields `ModelClient` with `chat(messages, images=None, **overrides) -> str` (defaults `temperature=0`), `chat_json(...)`, and `.raw` (the `openai.OpenAI` client); vLLM is started with `--served-model-name <profile>` and calls pass `model=<profile>`.
  - `qwen3-vl-32b` and `qwen3-vl-30b-a3b` use `max_model_len: 65536` (room for a 5376-px page, the block list and 4096 output tokens); a request that still exceeds the context returns HTTP 400 and is treated by callers as a schema error (ticket 012).
  - The lock record is deleted on release; `models status` tries a non-blocking lock and prints `GPU free` when it succeeds, regardless of leftover files.
  - E-5's test starts `models serve qwen3-30b-a3b` itself as a subprocess, waits for `serving`, and stops it at the end.

## Non-functional requirements

- **NFR-1** - While one `models serve` holds the lock, a second `models serve` of a different profile waits and does not start a vLLM process (checked with `nvidia-smi --query-compute-apps`: exactly one compute process).
- **NFR-2** - A crashed holder (process killed with SIGKILL) does not block forever and leaves no orphan on the GPU: the next caller kills the dead holder's process group and takes the lock.

## Affected areas

- `models.yaml` (new); `src/newsrag/models/__init__.py`, `lock.py`, `serve.py`, `client.py` (new)
- `src/newsrag/config.py` - `model_start_timeout_s`, `vllm_env_dir`, `model_port` (changed)
- `pyproject.toml`, `uv.lock` - torch (cu128+), sentence-transformers, openai, pillow, pyyaml (changed)
- `src/newsrag/cli.py` - `models` group (changed)
- `tests/test_gpu_lock.py` (new, CPU only), `tests/test_models_smoke.py` (new, marked `gpu`)
- `.gitignore` - `envs/` (changed)

## Don't touch

- `/home/admin1/RAG/**`.

## Out of scope

- indic-ocr (it is not a vLLM-served model; ticket 007 wraps it and takes the same GPU lock).
- Choosing the final grouping/judge/embedding models (tickets 014, 026).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Serving engine | vLLM, OpenAI-compatible API | grill A-14 |
| D-2 | Concurrency | one big model at a time, exclusive file lock | grill A-28; 96 GB can't hold all models |
| D-3 | Separate venv for vLLM | yes (`envs/vllm/`) | grill A-29: avoid pin clashes with indic-ocr and the main app |
| D-4 | Initial profiles | Qwen3-VL-32B (grouping), Gemma 3 27B (judge, different family), Qwen3-30B-A3B (text) | grill A-28 examples; final picks by bake-offs |
| D-5 | Embedding models | run in-process with sentence-transformers under `gpu_lock` | batch embedding needs no server; keeps vLLM for generative models |
| D-6 | Bind address | 127.0.0.1 | the model API must not be open on the network |
| D-7 | Image limits | Qwen max_pixels 20 MP; Gemma pan-and-scan | otherwise high-resolution pages are silently downscaled (tickets 012, 013) |

## Open questions

None.
