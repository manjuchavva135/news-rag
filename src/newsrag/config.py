"""Central settings for newsrag.

Every path and knob lives here. Never hardcode one elsewhere: add a field to the block of the
module that needs it (one ``# --- <module> ---`` header per module).

Precedence, highest first:
    real environment  >  env_file (.env)  >  pipeline_file (pipeline.yaml)  >  code default

- Environment variables are ``NEWSRAG_<FIELD_UPPER>``. The secrets also accept their plain names
  (``HF_TOKEN``, ``SARVAM_API_KEY``); the prefixed name wins if both are set in the same source.
- ``env_file`` and ``pipeline_file`` can only be set from the real environment
  (``NEWSRAG_ENV_FILE``, ``NEWSRAG_PIPELINE_FILE``); ``/dev/null`` means "no file".
- Paths derived from ``raw_root`` follow an overridden ``raw_root`` unless set themselves.
- Unknown keys are ignored with one warning line on stderr.

Importing this module only reads the environment, ``.env`` and ``pipeline.yaml``: it creates no
directories, makes no network calls and never writes to ``os.environ``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args

import yaml
from dotenv import dotenv_values
from pydantic import Field, SecretStr, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

import newsrag

ENV_PREFIX = "NEWSRAG_"
MAIN_CHECKOUT = Path("/home/admin1/news-rag")
NO_FILE = Path("/dev/null")
DEFAULT_RAW_ROOT = Path("/home/admin1/raw")

# Plain environment names accepted for secret fields.
SECRET_ALIASES = {"HF_TOKEN": "hf_token", "SARVAM_API_KEY": "sarvam_api_key"}

# Settings that only the real environment (or init kwargs) may set.
REAL_ENV_ONLY = ("env_file", "pipeline_file")

# Paths under raw_root that follow it unless overridden themselves.
RAW_ROOT_DERIVED = {
    "source_dir": "source",
    "images_dir": "images",
    "work_dir": "work",
    "reports_dir": "reports",
    "logs_dir": "logs",
    "hf_home": "hf-cache",
    "gpu_lock_path": ".gpu.lock",
}

# Paths under repo_root that follow it unless overridden themselves.
REPO_ROOT_DERIVED = {
    "compose_file": "docker/docker-compose.yml",
}


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _default_repo_root() -> Path:
    return Path(newsrag.__file__).resolve().parents[2]


def _file_locations(
    environ: Mapping[str, str], overrides: Mapping[str, Any] | None = None
) -> tuple[Path, Path, Path]:
    """Return (repo_root, env_file, pipeline_file) as seen from the real environment."""
    overrides = overrides or {}

    def pick(name: str) -> str | None:
        if overrides.get(name) not in (None, ""):
            return str(overrides[name])
        value = environ.get(ENV_PREFIX + name.upper())
        return value if value else None

    repo_root = Path(pick("repo_root") or _default_repo_root())
    env_file_value = pick("env_file")
    if env_file_value:
        env_file = Path(env_file_value)
    elif (repo_root / ".env").exists():
        env_file = repo_root / ".env"
    else:
        env_file = MAIN_CHECKOUT / ".env"
    pipeline_file = Path(pick("pipeline_file") or repo_root / "pipeline.yaml")
    return repo_root, env_file, pipeline_file


def _is_no_file(path: Path) -> bool:
    return path == NO_FILE or not path.is_file()


def _from_env_mapping(
    mapping: Mapping[str, str | None], fields: set[str], source: str, *, real_env: bool
) -> dict[str, Any]:
    """Map NEWSRAG_* (and plain secret) names to field names; warn on unknown keys.

    In the real environment, names without the prefix are simply not ours and are skipped
    silently; in a .env file every key is expected to be a setting.
    """
    plain: dict[str, Any] = {}
    prefixed: dict[str, Any] = {}
    for key, value in mapping.items():
        if value is None:
            continue
        if key in SECRET_ALIASES:
            plain[SECRET_ALIASES[key]] = value
        elif key.startswith(ENV_PREFIX):
            name = key[len(ENV_PREFIX) :].lower()
            if name in REAL_ENV_ONLY:
                if not real_env:
                    _warn(
                        f"ignoring setting {key} from {source}: only the real environment sets it"
                    )
            elif name in fields:
                prefixed[name] = value
            else:
                _warn(f"ignoring unknown setting {key} from {source}")
        elif not real_env:
            _warn(f"ignoring unknown setting {key} from {source}")
    return {**plain, **prefixed}


def _read_dotenv(path: Path, fields: set[str]) -> dict[str, Any]:
    if _is_no_file(path):
        return {}
    return _from_env_mapping(dotenv_values(path), fields, str(path), real_env=False)


def _read_pipeline_yaml(path: Path, fields: set[str]) -> dict[str, Any]:
    if _is_no_file(path):
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        _warn(f"ignoring {path}: top level is not a mapping")
        return {}
    out: dict[str, Any] = {}
    for key, value in data.items():
        name = str(key)
        if name in REAL_ENV_ONLY:
            _warn(f"ignoring setting {name} from {path}: only the real environment sets it")
        elif name in fields:
            out[name] = value
        else:
            _warn(f"ignoring unknown setting {name} from {path}")
    return out


class _DictSource(PydanticBaseSettingsSource):
    """A settings source backed by an already-parsed dict."""

    def __init__(self, settings_cls: type[BaseSettings], data: dict[str, Any]) -> None:
        super().__init__(settings_cls)
        self._data = data

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    # --- core ---
    repo_root: Path = Field(default_factory=_default_repo_root)
    env_file: Path
    pipeline_file: Path
    archive_dir: Path = Path("/home/admin1/data")
    raw_root: Path = DEFAULT_RAW_ROOT
    source_dir: Path
    images_dir: Path
    work_dir: Path
    reports_dir: Path
    logs_dir: Path

    # --- secrets ---
    hf_token: SecretStr | None = None
    sarvam_api_key: SecretStr | None = None

    # --- stores ---
    postgres_dsn: str = "postgresql://newsrag:newsrag@localhost:5442/newsrag"
    qdrant_url: str = "http://localhost:6343"
    opensearch_url: str = "http://localhost:9210"
    compose_file: Path  # default {repo_root}/docker/docker-compose.yml
    stores_up_timeout_s: float = 180.0  # `stores up` waits this long for all three to answer
    stores_ping_timeout_s: float = 2.0  # per-store timeout for `stores status`
    pg_pool_max_size: int = 10

    # --- ledger ---
    ledger_schema: str = "ledger"  # Postgres schema holding the pipeline ledger tables
    max_attempts: int = 3  # a failed item is retried until it has failed this many times

    # --- ingest ---
    unrar_bin: str = "unrar"  # only needed to extract a .rar archive
    unpack_workers: int = 8  # threads extracting one ZIP

    # --- render ---
    render_dpi: int = 300

    # --- models ---
    hf_home: Path
    gpu_lock_path: Path

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        fields = set(settings_cls.model_fields)
        init_kwargs = getattr(init_settings, "init_kwargs", {})
        _, env_file, pipeline_file = _file_locations(os.environ, init_kwargs)
        return (
            init_settings,
            _DictSource(
                settings_cls,
                _from_env_mapping(os.environ, fields, "environment", real_env=True),
            ),
            _DictSource(settings_cls, _read_dotenv(env_file, fields)),
            _DictSource(settings_cls, _read_pipeline_yaml(pipeline_file, fields)),
        )

    @model_validator(mode="before")
    @classmethod
    def _fill_derived(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        repo_root, env_file, pipeline_file = _file_locations(os.environ, data)
        data.setdefault("repo_root", repo_root)
        data["env_file"] = env_file
        data["pipeline_file"] = pipeline_file
        raw_root = Path(data.get("raw_root") or DEFAULT_RAW_ROOT)
        for name, rel in RAW_ROOT_DERIVED.items():
            if data.get(name) in (None, ""):
                data[name] = raw_root / rel
        for name, rel in REPO_ROOT_DERIVED.items():
            if data.get(name) in (None, ""):
                data[name] = Path(data["repo_root"]) / rel
        for name in secret_fields():
            if data.get(name) == "":
                data[name] = None
        return data

    def subprocess_env(self) -> dict[str, str]:
        """A copy of os.environ plus HF_HOME and the secrets that are set, for subprocesses."""
        env = dict(os.environ)
        env["HF_HOME"] = str(self.hf_home)
        if self.hf_token is not None:
            env["HF_TOKEN"] = self.hf_token.get_secret_value()
        if self.sarvam_api_key is not None:
            env["SARVAM_API_KEY"] = self.sarvam_api_key.get_secret_value()
        return env

    def display_lines(self) -> list[str]:
        """Every setting as ``name = value``, sorted by name, secrets masked."""
        secrets = secret_fields()
        lines = []
        for name in sorted(type(self).model_fields):
            value = getattr(self, name)
            if name in secrets:
                shown = "(unset)" if value is None else "***"
            elif value is None:
                shown = "(unset)"
            else:
                shown = str(value)
            lines.append(f"{name} = {shown}")
        return lines


def secret_fields() -> set[str]:
    """Names of every field typed SecretStr (optional or not)."""
    names = set()
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        if annotation is SecretStr or SecretStr in get_args(annotation):
            names.add(name)
    return names


def set_pipeline_key(key: str, value: Any, path: Path | None = None) -> None:
    """Set one key in pipeline.yaml, keeping the other keys, their order and comments."""
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap

    path = Path(path) if path is not None else settings.pipeline_file
    if path == NO_FILE:
        raise ValueError("pipeline_file is /dev/null: there is no pipeline.yaml to write")
    if isinstance(value, Path):
        value = str(value)

    rt = YAML()
    rt.preserve_quotes = True
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    data = rt.load(text) if text.strip() else None
    header = ""
    if data is None:
        # Comment-only (or empty) file: keep its comment lines as the header.
        header = "".join(line + "\n" for line in text.splitlines() if line.startswith("#"))
        data = CommentedMap()
    if not isinstance(data, CommentedMap):
        raise TypeError(f"{path}: top level is not a mapping")
    if len(data) == 0:
        data.fa.set_block_style()
    data[key] = value

    with path.open("w", encoding="utf-8") as fh:
        if header:
            fh.write(header)
        rt.dump(data, fh)


settings = Settings()
