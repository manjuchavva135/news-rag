# 006: GPU model runner (one model at a time) - Results

Built in its own worktree on branch `worktree-006-gpu-model-runner` (from main at `8431b78`,
after 001-003). Tickets 004 and 005 were in progress in the main checkout at the same time and
are not part of this branch. Everything below ran on the RTX PRO 6000 Blackwell (95 GiB), driver
595.99 / CUDA 13.2.

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| `uv run ruff check .` / `ruff format --check .` | PASS | `evidence/auto-ruff-*.log` |
| unit tests (`not gpu and not data and not stores`) | PASS, 61 passed | `evidence/auto-pytest-unit.log` |
| E-1 `models install` | PASS: `vllm 0.33.0`, `device capability (12, 0)`; a second run prints `kept` | `evidence/e1-models-install*.log` |
| E-2 serve + smoke each profile (the eval loop as written) | PASS: qwen3-vl-32b `NEWS 2026` 0.57 s, gemma3-27b `NEWS 2026` 0.59 s, qwen3-30b-a3b `OK` 0.07 s, bge-m3 `dim=1024` 0.46 s; then `GPU free` | `evidence/e2-smoke-all.log`, `e2.sh` |
| E-3 second server waits | PASS: `/tmp/e3b.log` has `waiting for GPU lock held by models serve qwen3-30b-a3b (pid 682367)` (twice, 60 s apart); status names the profile and PID; 1 compute process (`VLLM::EngineCore`) | `evidence/e3-lock-wait.log`, `e3a.log`, `e3b.log`, `e3.sh` |
| E-4 dead holder | PASS, 3 passed (incl. a real SIGKILLed holder whose orphan group is killed by the next taker) | `evidence/e4-dead-holder.log` |
| E-5 reuse a running `models serve` | PASS, 1 passed (150 s): same `base_url`, same compute PIDs, server still alive after `use()` exits | `evidence/e5-reuse.log` |
| E-6 start failure, holder record, busy port | PASS, 3 passed | `evidence/e6-start-failure.log` |
| E-7 second-review clarifications | PASS, 8 passed | `evidence/e7-clarifications.log` |
| E-8 `models fit` | PASS: every line `fit=yes` (qwen3-vl-32b 62.1 + 16.0 + 4 ≤ 86.4) | `evidence/e8-models-fit.log` |
| extra: Qwen3-VL keeps page resolution (D-7) | PASS: a 5376×7600 page (40.9 MP) costs 19,438 image tokens = 19.9 MP seen (the default cap would be 16.7 MP); line 07 read back correctly | `evidence/extra-qwen3-vl-max-pixels.log`, `maxpixels.py` |

Gemma's `do_pan_and_scan` is passed to vLLM and accepted at start-up; I did not measure its crop
count.

## What was built

- `models.yaml`: the four FR-1 profiles. `src/newsrag/models/`: `profiles.py`, `lock.py`,
  `serve.py`, `client.py` (`use()`, `ModelClient`, `Embedder`, `loaded_profiles()`), `fit.py`,
  `hub.py` (downloads), `install.py`, `cli.py` (`models install|download|serve|smoke|status|fit`).
- Settings in the `# --- models ---` block: `models_file`, `vllm_env_dir` (both under
  `repo_root`), `vllm_spec`, `vllm_min_version`, `vllm_torch_backend`, `uv_bin`, `model_port`,
  `model_port_max`, `model_start_timeout_s`, `model_stop_timeout_s`, `model_request_timeout_s`,
  `gpu_lock_report_s`, `gpu_memory_gb`, `fit_overhead_gb`.
- Main venv: torch 2.11 from the `pytorch-cu128` index, sentence-transformers, openai, pillow,
  numpy, packaging. vLLM lives only in `envs/vllm/` (vLLM 0.33.0 with its own torch 2.14+cu132).
- Tests: `tests/test_gpu_lock.py` and `tests/test_models_client.py` run on the CPU against a fake
  `vllm` (`tests/_fake_vllm.py`, fixture `models_env` in `tests/_models_env.py`, registered in
  `conftest.py`); `tests/test_models_smoke.py` is marked `gpu`.

## Problems found while testing, and fixes

- **vLLM would not start on this host.** Its FlashInfer sampler needs precompiled kernels or
  `nvcc` (there is no CUDA toolkit installed). `models install` now always runs
  `vllm download-kernels` (installs `flashinfer-cubin` + `flashinfer-jit-cache`; a no-op when
  present). The first failed start also showed the FR-3 failure path working: exit 1,
  `vllm failed to start qwen3-30b-a3b:` and the log tail.
- **vLLM could be killed while still in use.** `PR_SET_PDEATHSIG` fires when the *thread* that
  forked exits. A stage worker thread that opened `use()` and finished while other threads still
  used the model would have taken vLLM down. Servers are now always started from one
  process-lifetime spawner thread; `test_server_survives_exit_of_opening_thread` fails without
  this and passes with it.
- `preexec_fn` is unsafe in threaded callers, so the death signal is set by a tiny launcher
  (`python -I -c …` → `prctl` → `execvp vllm`) that keeps the same PID and process group.

## Where I read the spec a particular way

- **GB = 2³⁰ bytes** in the fit check (the unit nvidia-smi and vLLM report). With 10⁹-byte GB,
  qwen3-vl-32b at `max_model_len` 65536 would print `fit=no` even though it runs. The budget uses
  the spec's 96 GB (`gpu_memory_gb`); the card reports 94.97 GiB. Embed profiles get the whole
  96 GB as budget (they have no `gpu_memory_utilization`).
- **qwen3-vl-32b uses `max_model_len: 65536`** (FR-7), not FR-1's 32768. FR-7 also names a
  `qwen3-vl-30b-a3b` profile; it is not in FR-1's initial list, so I did not add it (a later
  ticket can, e.g. the 014 bake-off).
- **One lock file** is both the flock and the record. The record is deleted on release, before
  unlocking; takers check that the path still names the inode they locked. A taker kills a dead
  holder's process group only if the record's PID is dead (PID + start time, so a reused PID is
  not mistaken for the holder).
- **`loaded_profiles()`** lists profiles in first-use order for the life of the process; a reused
  server appears as `"<profile> (reused)"`.
- **`use()` nesting:** a different profile inside `use()` raises `ModelBusyError`; `use()` inside
  a plain `gpu_lock(...)` scope of the same process shares that lock.
- **Downloads** run in-process with `cache_dir`/`token` taken from `settings.subprocess_env()`
  (`HF_HOME/hub`, `HF_TOKEN`), so `os.environ` is never written. `original/`, `onnx/`, `*.pth`
  and `*.bin` (when safetensors exist) are skipped. vLLM starts with `HF_HUB_OFFLINE=1` since
  the weights are already there.
- **`--port`** defaults to the `model_port` setting (8100); when busy, the next free port up to
  8199, then wrapping from 8100.

## Notes for the merge

- `envs/` is gitignored and the venv is per checkout: after merging, run
  `uv run newsrag models install` once in the main checkout (uv's cache makes it faster than the
  first install). Weights are in `/home/admin1/raw/hf-cache` and shared, so nothing downloads
  again.
- Downloads from the Hub ran at 3-60 MB/s here; the `hf_xet` downloader stalled once (concurrency
  dropped to 1) and was restarted with `HF_HUB_DISABLE_XET=1`. All four snapshots are complete.
