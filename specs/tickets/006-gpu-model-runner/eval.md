# 006: GPU model runner (one model at a time) - Evaluation

## Setup

- Prerequisites: `HF_TOKEN` in `.env` with access to the listed models (Gemma requires accepting its licence on Hugging Face). GPU idle (`nvidia-smi` shows no compute process).
- Start: `uv sync && uv run newsrag models install`
- Base URL: n/a
- Test account: n/a
- Seed / reset: none

## Automated checks

- `uv run ruff check .` exits 0
- `uv run pytest -q -m "not gpu and not data and not stores"` exits 0 (includes `tests/test_gpu_lock.py`)

## Acceptance checks

### E-1: vLLM venv sees Blackwell (covers FR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag models install`
- **Expected:** exits 0 and prints a vLLM version and a device capability tuple whose first number is ≥ 12.

### E-2: Serve and smoke each profile (covers FR-1, FR-3, FR-5, FR-6)

- **Type:** CLI
- **Steps:**
  1. `for p in qwen3-vl-32b gemma3-27b qwen3-30b-a3b bge-m3; do uv run newsrag models download $p && uv run newsrag models smoke $p; done`
- **Expected:** for both VLM profiles the reply contains `NEWS 2026` (case-insensitive); for `qwen3-30b-a3b` the reply contains `OK`; for `bge-m3` it prints `dim=1024`; each prints a latency; GPU free afterwards (`uv run newsrag models status` prints `GPU free`).

### E-3: Second server waits for the lock (covers NFR-1, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag models serve qwen3-30b-a3b --port 8100 > /tmp/e3a.log 2>&1 &`
  2. wait until `/tmp/e3a.log` contains `serving qwen3-30b-a3b`
  3. `timeout 90 uv run newsrag models serve gemma3-27b --port 8101 > /tmp/e3b.log 2>&1; true`
  4. `uv run newsrag models status`
  5. `nvidia-smi --query-compute-apps=pid --format=csv,noheader | wc -l`
  6. `kill %1; wait`
- **Expected:** `/tmp/e3b.log` contains `waiting for GPU lock held by`; step 4 names `qwen3-30b-a3b` and a PID; step 5 prints `1`.

## Edge cases

### E-4: Dead holder is recovered (covers NFR-2)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_gpu_lock.py -k dead_holder`
- **Expected:** passes; a lock file naming a non-existent PID is taken over within 5 s, and `test_dead_holder_process_group_killed` (a dummy `sleep` child in its own group recorded in the lock file) shows the child killed.

### E-5: Reuse a running server (covers FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q -m gpu tests/test_models_smoke.py -k reuse`
- **Expected:** passes; with `models serve qwen3-30b-a3b` running, `use("qwen3-30b-a3b")` returns a client without starting a second vLLM process and does not stop the server on exit.

### E-6: Startup failure and gpu_lock (covers FR-3, FR-4)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_gpu_lock.py -k "gpu_lock_holder_recorded or start_failure_releases_lock or port_busy_picks_next"`
- **Expected:** 3 passed (fake vLLM command that exits at once: exit 1, message `vllm failed to start`, lock free afterwards).

### E-7: Second-review clarifications (covers FR-7)

- **Type:** CLI
- **Steps:**
  1. `uv run pytest -q tests/test_gpu_lock.py tests/test_models_client.py -k "reentrant or different_profile_raises or loaded_profiles or cpu_embed_no_lock or served_model_name or status_free_after_release"`
- **Expected:** all selected tests pass.

### E-8: Every profile fits the GPU (covers FR-8)

- **Type:** CLI
- **Steps:**
  1. `uv run newsrag models fit`
- **Expected:** one line per profile in `models.yaml`, every line ends with `fit=yes`.

## Traceability

| Requirement | Checks |
|-------------|--------|
| FR-1 | E-2 |
| FR-2 | E-1 |
| FR-3 | E-2, E-3, E-6 |
| FR-4 | E-3, E-5, E-6 |
| FR-5 | E-2 |
| FR-6 | E-2 |
| NFR-1 | E-3 |
| NFR-2 | E-4 |
| FR-7 | E-7 |
| FR-8 | E-8 |

## Definition of done

- [ ] All automated checks pass
- [ ] All acceptance checks E-1...E-3 pass
- [ ] All edge-case checks pass
- [ ] No items from "Out of scope" were built
- [ ] Nothing listed under "Don't touch" changed
- [ ] Evidence (command logs) saved in `evidence/` and listed in `results.md`
