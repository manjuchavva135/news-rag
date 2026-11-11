import os

import pytest

from newsrag.config import SECRET_ALIASES


@pytest.fixture(autouse=True)
def pin_model_choices(monkeypatch):
    """Unit tests use the code defaults for the model choices, not whatever `pipeline.yaml` was
    set to (e.g. by `newsrag group bakeoff --apply` or `newsrag textmodel bakeoff --apply`
    or `newsrag embed bakeoff --apply`)."""
    import newsrag.stages.base as base_mod
    from newsrag import config

    fields = config.Settings.model_fields
    names = (
        "group_model",
        "judge_model",
        "enrich_model",
        "answer_model",
        "answer_judge_model",
        "embed_model",
        "corpus_from_date",  # ticket 031 scopes the real corpus; tests see every date
    )
    pinned = {name: fields[name].default for name in names}
    current = config.settings.model_copy(update=pinned)
    for mod in (config, base_mod):
        monkeypatch.setattr(mod, "settings", current)


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
