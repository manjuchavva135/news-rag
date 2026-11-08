import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from newsrag.config import MAIN_CHECKOUT, Settings, set_pipeline_key


def test_defaults(clean_env):
    s = Settings()
    assert s.archive_dir == Path("/home/admin1/data")
    assert s.raw_root == Path("/home/admin1/raw")
    assert s.source_dir == Path("/home/admin1/raw/source")
    assert s.images_dir == Path("/home/admin1/raw/images")
    assert s.work_dir == Path("/home/admin1/raw/work")
    assert s.reports_dir == Path("/home/admin1/raw/reports")
    assert s.logs_dir == Path("/home/admin1/raw/logs")
    assert s.hf_home == Path("/home/admin1/raw/hf-cache")
    assert s.gpu_lock_path == Path("/home/admin1/raw/.gpu.lock")
    assert s.postgres_dsn == "postgresql://newsrag:newsrag@localhost:5442/newsrag"
    assert s.qdrant_url == "http://localhost:6343"
    assert s.opensearch_url == "http://localhost:9210"
    assert s.render_dpi == 300
    assert s.repo_root == Path(__file__).resolve().parents[1]
    assert s.pipeline_file == Path("/dev/null")
    assert s.hf_token is None and s.sarvam_api_key is None


def test_frozen(clean_env):
    s = Settings()
    with pytest.raises(ValidationError):
        s.render_dpi = 1


def test_derived_paths_follow_raw_root(clean_env):
    clean_env.setenv("NEWSRAG_RAW_ROOT", "/tmp/nr-test")
    clean_env.setenv("NEWSRAG_IMAGES_DIR", "/tmp/imgs")
    s = Settings()
    assert s.source_dir == Path("/tmp/nr-test/source")
    assert s.hf_home == Path("/tmp/nr-test/hf-cache")
    assert s.images_dir == Path("/tmp/imgs")


def test_secret_plain_and_prefixed_names(clean_env):
    clean_env.setenv("HF_TOKEN", "plain")
    clean_env.setenv("SARVAM_API_KEY", "sk")
    s = Settings()
    assert s.hf_token.get_secret_value() == "plain"
    assert s.sarvam_api_key.get_secret_value() == "sk"
    clean_env.setenv("NEWSRAG_HF_TOKEN", "prefixed")
    assert Settings().hf_token.get_secret_value() == "prefixed"


def test_empty_secret_is_unset(clean_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN=\nSARVAM_API_KEY=\n")
    clean_env.setenv("NEWSRAG_ENV_FILE", str(env_file))
    s = Settings()
    assert s.hf_token is None and s.sarvam_api_key is None


def test_pipeline_yaml_precedence(clean_env, tmp_path):
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        "render_dpi: 111\nqdrant_url: http://yaml:1\nopensearch_url: http://yaml:2\n"
    )
    env_file = tmp_path / ".env"
    env_file.write_text("NEWSRAG_RENDER_DPI=222\nNEWSRAG_QDRANT_URL=http://dotenv:1\n")
    clean_env.setenv("NEWSRAG_PIPELINE_FILE", str(pipeline))
    clean_env.setenv("NEWSRAG_ENV_FILE", str(env_file))

    s = Settings()
    assert s.opensearch_url == "http://yaml:2"  # only in pipeline.yaml
    assert s.qdrant_url == "http://dotenv:1"  # .env beats pipeline.yaml
    assert s.render_dpi == 222

    clean_env.setenv("NEWSRAG_RENDER_DPI", "333")  # real environment beats both
    assert Settings().render_dpi == 333


def test_env_file_fallback(clean_env, tmp_path):
    clean_env.delenv("NEWSRAG_ENV_FILE")
    clean_env.setenv("NEWSRAG_REPO_ROOT", str(tmp_path))
    assert Settings().env_file == MAIN_CHECKOUT / ".env"  # worktree without .env

    (tmp_path / ".env").write_text("NEWSRAG_RENDER_DPI=123\n")
    s = Settings()
    assert s.env_file == tmp_path / ".env"
    assert s.render_dpi == 123


def test_files_only_from_real_env(clean_env, tmp_path, capsys):
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("env_file: /somewhere/.env\n")
    env_file = tmp_path / ".env"
    env_file.write_text("NEWSRAG_PIPELINE_FILE=/elsewhere.yaml\n")
    clean_env.setenv("NEWSRAG_PIPELINE_FILE", str(pipeline))
    clean_env.setenv("NEWSRAG_ENV_FILE", str(env_file))
    s = Settings()
    assert s.env_file == env_file and s.pipeline_file == pipeline
    err = capsys.readouterr().err
    assert "env_file" in err and "NEWSRAG_PIPELINE_FILE" in err


def test_unknown_keys_ignored(clean_env, tmp_path, capsys):
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("not_a_setting_yaml: 1\nrender_dpi: 150\n")
    env_file = tmp_path / ".env"
    env_file.write_text("NEWSRAG_NOT_A_SETTING_DOTENV=1\nOTHER_THING=2\n")
    clean_env.setenv("NEWSRAG_PIPELINE_FILE", str(pipeline))
    clean_env.setenv("NEWSRAG_ENV_FILE", str(env_file))
    clean_env.setenv("NEWSRAG_NOT_A_SETTING_ENV", "1")
    clean_env.setenv("PATHLIKE_UNRELATED", "x")  # not ours: no warning

    s = Settings()
    assert s.render_dpi == 150
    err = capsys.readouterr().err.splitlines()
    assert f"ignoring unknown setting not_a_setting_yaml from {pipeline}" in err
    assert f"ignoring unknown setting NEWSRAG_NOT_A_SETTING_DOTENV from {env_file}" in err
    assert f"ignoring unknown setting OTHER_THING from {env_file}" in err
    assert "ignoring unknown setting NEWSRAG_NOT_A_SETTING_ENV from environment" in err
    assert not any("PATHLIKE_UNRELATED" in line for line in err)


def test_subprocess_env(clean_env):
    clean_env.setenv("HF_TOKEN", "hf_x")
    clean_env.setenv("NEWSRAG_RAW_ROOT", "/tmp/nr-sub")
    clean_env.setenv("SOME_PARENT_VAR", "kept")
    clean_env.delenv("HF_HOME", raising=False)
    env = Settings().subprocess_env()
    assert env["HF_TOKEN"] == "hf_x"
    assert env["HF_HOME"] == "/tmp/nr-sub/hf-cache"
    assert env["SOME_PARENT_VAR"] == "kept"
    assert "SARVAM_API_KEY" not in env
    assert "HF_HOME" not in os.environ  # returns a copy, never writes os.environ


def test_set_pipeline_key_keeps_others(tmp_path):
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "# header comment\n"
        "ocr_model: a  # the OCR model\n"
        "# about thresholds\n"
        "judge_threshold: 0.7\n"
        "embed_model: e\n"
    )
    set_pipeline_key("judge_threshold", 0.8, path)
    set_pipeline_key("new_key", "n", path)
    text = path.read_text()
    assert text == (
        "# header comment\n"
        "ocr_model: a  # the OCR model\n"
        "# about thresholds\n"
        "judge_threshold: 0.8\n"
        "embed_model: e\n"
        "new_key: n\n"
    )


def test_set_pipeline_key_on_committed_empty_file(tmp_path):
    repo_file = Path(__file__).resolve().parents[1] / "pipeline.yaml"
    path = tmp_path / "pipeline.yaml"
    path.write_text(repo_file.read_text())
    set_pipeline_key("render_dpi", 200, path)
    text = path.read_text()
    assert text.startswith("#")
    assert text.endswith("render_dpi: 200\n")
    assert "{" not in text


def test_import_has_no_side_effects(tmp_path):
    root = tmp_path / "never-created"
    env = {"NEWSRAG_RAW_ROOT": str(root), "PATH": "/usr/bin:/bin"}
    code = (
        "import os, newsrag.config as c; "
        "assert 'HF_HOME' not in os.environ; "
        "assert os.environ.get('HF_TOKEN') is None"
    )
    subprocess.run([sys.executable, "-c", code], env=env, check=True)
    assert not root.exists()
