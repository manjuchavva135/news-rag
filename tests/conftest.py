import os

import pytest

from newsrag.config import SECRET_ALIASES


@pytest.fixture
def clean_env(monkeypatch):
    """Remove every newsrag setting from the environment and point both files at /dev/null."""
    for key in list(os.environ):
        if key.startswith("NEWSRAG_") or key in SECRET_ALIASES:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NEWSRAG_ENV_FILE", "/dev/null")
    monkeypatch.setenv("NEWSRAG_PIPELINE_FILE", "/dev/null")
    return monkeypatch
