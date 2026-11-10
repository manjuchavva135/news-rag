"""use(), ModelClient, Embedder, profiles and the fit formula, CPU only (ticket 006)."""

from __future__ import annotations

import json
import threading
import time

import numpy as np
import pytest
from _models_env import SERVE_CODE, wait_for
from PIL import Image

from newsrag.models import (
    ModelBusyError,
    client,
    fit,
    gpu_lock,
    load_profiles,
    loaded_profiles,
    lock,
    use,
)
from newsrag.models.cli import smoke_image
from newsrag.models.profiles import Profile


def test_served_model_name_and_temperature(models_env):
    with use("fake-llm") as model:
        assert model.chat("hello") == "echo: hello"
        model.chat([{"role": "user", "content": "warm"}], temperature=0.7, max_tokens=5)
    (start,) = models_env.starts()
    argv = json.loads(start.read_text())["argv"]
    assert argv[:2] == ["serve", "fake/llm"]
    assert argv[argv.index("--served-model-name") + 1] == "fake-llm"
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--max-model-len") + 1] == "1024"
    first, second = (json.loads(p.read_text()) for p in models_env.requests())
    assert first["model"] == "fake-llm" and first["temperature"] == 0
    assert second["temperature"] == 0.7 and second["max_tokens"] == 5


def test_use_starts_and_stops_under_lock(models_env):
    with use("fake-llm") as model:
        record = lock.probe(models_env.settings.gpu_lock_path)
        assert record.kind == "use" and record.profile == "fake-llm"
        assert record.base_url == model.base_url
        pgid = record.pgid
    assert not lock.group_alive(pgid)
    assert lock.probe(models_env.settings.gpu_lock_path) is None


def test_reentrant_use_same_profile(models_env):
    with use("fake-llm") as outer:
        with use("fake-llm") as inner:
            assert inner is outer
        pgid = lock.read_record(models_env.settings.gpu_lock_path).pgid
        assert lock.group_alive(pgid)  # the inner exit did not stop it
        assert outer.chat("still") == "echo: still"
    assert len(models_env.starts()) == 1
    assert not lock.group_alive(pgid)


def test_reentrant_use_across_threads(models_env):
    results = []
    with use("fake-llm") as outer:

        def worker():
            with use("fake-llm") as m:
                results.append((m is outer, m.chat("t")))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert results == [(True, "echo: t")] * 4
    assert len(models_env.starts()) == 1


def test_different_profile_raises(models_env):
    with use("fake-llm"):
        with pytest.raises(ModelBusyError) as info, use("fake-vlm"):
            pass
        assert (info.value.held, info.value.wanted) == ("fake-llm", "fake-vlm")
    with use("fake-vlm"):  # free again once the first scope ended
        pass


def test_loaded_profiles_marks_reused(models_env):
    with use("fake-vlm"):
        pass
    assert loaded_profiles() == ["fake-vlm"]
    serve = models_env.run_python(SERVE_CODE, "fake-llm")
    try:
        assert serve.stdout.readline().startswith("serving fake-llm at ")
        assert len(models_env.starts()) == 2
        record = lock.read_record(models_env.settings.gpu_lock_path)
        with use("fake-llm") as model:
            assert model.base_url == record.base_url
            assert model.chat("reuse") == "echo: reuse"
        assert len(models_env.starts()) == 2  # no second server
        assert lock.group_alive(record.pgid)  # and the reused one was not stopped
        assert serve.poll() is None
        assert loaded_profiles() == ["fake-vlm", "fake-llm (reused)"]
    finally:
        serve.terminate()
        serve.communicate(timeout=20)


def test_use_waits_while_other_profile_served(models_env):
    """A `models serve` of another profile holds the lock: use() waits instead of starting."""
    serve = models_env.run_python(SERVE_CODE, "fake-llm")
    try:
        assert serve.stdout.readline().startswith("serving fake-llm")
        messages = []
        done = threading.Event()

        def user():
            with use("fake-vlm", report=messages.append):
                done.set()

        t = threading.Thread(target=user)
        t.start()
        assert wait_for(lambda: messages, 5)
        assert "waiting for GPU lock held by models serve fake-llm" in messages[0]
        assert not done.is_set() and len(models_env.starts()) == 1
        serve.terminate()
        serve.communicate(timeout=20)
        t.join(timeout=20)
        assert done.is_set()
    finally:
        if serve.poll() is None:
            serve.kill()


class FakeST:
    def __init__(self):
        self.seen = []

    def encode(self, texts, **kwargs):
        self.seen.extend(texts)
        return np.array([[len(t), 1.0, 0.0] for t in texts], dtype=np.float64)


def test_cpu_embed_no_lock(models_env, monkeypatch):
    fake = FakeST()
    calls = []

    def load(path, device, dtype):
        calls.append(device)
        return fake

    monkeypatch.setattr(client, "_load_sentence_transformer", load)
    with use("fake-embed", device="cpu") as emb:
        assert lock.probe(models_env.settings.gpu_lock_path) is None  # no lock taken
        docs = emb.embed(["ab", "abcd"])
        query = emb.embed("x", kind="query")
    assert calls == ["cpu"]
    assert fake.seen == ["passage: ab", "passage: abcd", "query: x"]
    assert docs.shape == (2, 3) and docs.dtype == np.float32
    assert np.allclose(np.linalg.norm(docs, axis=1), 1.0)
    assert query.shape == (1, 3)
    with pytest.raises(ValueError):
        emb.embed(["a"], kind="other")


def test_cuda_embed_takes_lock(models_env, monkeypatch):
    monkeypatch.setattr(client, "_load_sentence_transformer", lambda p, d, t: FakeST())
    with use("fake-embed") as emb:
        record = lock.probe(models_env.settings.gpu_lock_path)
        assert record.holder == "fake-embed" and record.profile == "fake-embed"
        assert emb.dim == 3
    assert lock.probe(models_env.settings.gpu_lock_path) is None


def test_cpu_rejected_for_vllm_profile(models_env):
    with pytest.raises(ValueError, match="only allowed for embed"), use("fake-llm", device="cpu"):
        pass


def test_use_inside_gpu_lock_shares_it(models_env):
    with gpu_lock("ocr"), use("fake-llm") as model:
        assert model.chat("x") == "echo: x"
    assert lock.probe(models_env.settings.gpu_lock_path) is None


def test_images_attached_before_text(models_env):
    with use("fake-vlm") as model:
        model.chat("read", images=[Image.new("RGB", (4, 4), "white")])
    (req,) = (json.loads(p.read_text()) for p in models_env.requests())
    parts = req["messages"][-1]["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[1] == {"type": "text", "text": "read"}


def test_chat_json_parses_fence():
    assert client.parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert client.parse_json_reply(" [1, 2] ") == [1, 2]
    with pytest.raises(ValueError, match="not JSON"):
        client.parse_json_reply("nope")


def test_repo_profiles():
    profiles = load_profiles()
    assert list(profiles) == [
        "qwen3-vl-32b",
        "qwen3-vl-30b-a3b",
        "gemma3-27b",
        "gemma4-31b",
        "internvl3.5-38b",
        "qwen3-30b-a3b",
        "bge-m3",
    ]
    qwen_vl = profiles["qwen3-vl-32b"]
    assert qwen_vl.hf_id == "Qwen/Qwen3-VL-32B-Instruct" and qwen_vl.kind == "vlm"
    assert qwen_vl.max_model_len == 65536
    assert '{"max_pixels": 20000000}' in qwen_vl.extra_args
    moe_vl = profiles["qwen3-vl-30b-a3b"]
    assert (moe_vl.hf_id, moe_vl.family, moe_vl.kind) == (
        "Qwen/Qwen3-VL-30B-A3B-Instruct",
        "qwen",
        "vlm",
    )
    assert moe_vl.extra_args == qwen_vl.extra_args
    assert '{"do_pan_and_scan": true}' in profiles["gemma3-27b"].extra_args
    assert '{"max_soft_tokens": 1120}' in profiles["gemma4-31b"].extra_args
    assert profiles["internvl3.5-38b"].family == "internvl"
    assert '{"max_dynamic_patch": 40}' in profiles["internvl3.5-38b"].extra_args
    assert profiles["qwen3-30b-a3b"].kind == "llm"
    bge = profiles["bge-m3"]
    assert (bge.kind, bge.dim, bge.query_prefix, bge.doc_prefix) == ("embed", 1024, "", "")


def test_fit_formula():
    profile = Profile(
        name="p",
        hf_id="x/p",
        family="qwen",
        kind="llm",
        max_model_len=1000,
        gpu_memory_utilization=0.5,
    )
    cfg = {
        "torch_dtype": "bfloat16",
        "num_hidden_layers": 10,
        "num_attention_heads": 8,
        "num_key_value_heads": 2,
        "hidden_size": 1024,
    }
    sizes = {"model-1.safetensors": 30 * fit.GB, "original/x.safetensors": 99 * fit.GB}
    r = fit.estimate_from(profile, sizes, cfg)
    assert r.weights_gb == 30
    assert r.kv_one_seq_gb == 10 * 2 * 128 * 2 * 2 * 1000 / fit.GB
    assert r.budget_gb == 48 and r.fits
    assert r.line().endswith("fit=yes")
    fp8 = fit.estimate_from(profile.model_copy(update={"dtype": "fp8"}), sizes, cfg)
    assert fp8.weights_gb == 15
    big = fit.estimate_from(profile, {"m.safetensors": 45 * fit.GB}, cfg)
    assert not big.fits and big.line().endswith("fit=no")
    with pytest.raises(fit.ModelDoesNotFitError, match="profile p does not fit the GPU"):
        raise fit.ModelDoesNotFitError(big)


def test_fit_reads_text_config_for_vlms():
    cfg = {
        "text_config": {
            "num_hidden_layers": 64,
            "num_attention_heads": 64,
            "num_key_value_heads": 8,
            "head_dim": 128,
        }
    }
    assert fit.kv_bytes_per_token(cfg) == 64 * 8 * 128 * 4
    # InternVL keeps its language model's config under llm_config
    assert fit.kv_bytes_per_token({"llm_config": cfg["text_config"]}) == 64 * 8 * 128 * 4


def test_smoke_image():
    image = smoke_image()
    assert image.size == (400, 120)
    assert image.convert("L").getextrema()[0] < 50  # black text was drawn


def test_server_survives_exit_of_opening_thread(models_env):
    """PR_SET_PDEATHSIG fires on the forking *thread*'s exit: a stage worker thread that opened
    the model may finish while another thread still uses it."""
    opened, release = threading.Event(), threading.Event()
    holder = {}

    def opener():
        with use("fake-llm") as m:
            holder["model"] = m
            opened.set()
            release.wait(10)

    t = threading.Thread(target=opener)
    t.start()
    assert opened.wait(20)
    with use("fake-llm") as model:  # second scope, this thread
        release.set()
        t.join(10)
        assert not t.is_alive()  # the opening thread is gone ...
        time.sleep(1.0)
        assert model.chat("alive") == "echo: alive"  # ... and vLLM still answers
