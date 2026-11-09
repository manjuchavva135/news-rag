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


@pytest.fixture
def ledger():
    """A throw-away ledger schema `test_<random>`, migrated, dropped afterwards (NFR-2)."""
    import secrets

    from psycopg import sql

    from newsrag.ledger import db
    from newsrag.stores import pg

    name = f"test_{secrets.token_hex(6)}"
    with db.use_schema(name):
        try:
            db.migrate()
            yield name
        finally:
            with pg().connection() as c:
                c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def registry():
    """An empty stage REGISTRY for the test; the real one is restored afterwards."""
    from newsrag.stages.registry import REGISTRY

    saved = dict(REGISTRY)
    REGISTRY.clear()
    try:
        yield REGISTRY
    finally:
        REGISTRY.clear()
        REGISTRY.update(saved)


# ticket 006: settings in tmp_path + a fake vLLM for the model-runner tests
from _models_env import models_env  # noqa: F401
