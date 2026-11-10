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

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args

import yaml
from dotenv import dotenv_values
from pydantic import Field, SecretStr, field_validator, model_validator
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
    "ocr_tokenizer_root": "indic-ocr-tokenizer",
}

# Paths under repo_root that follow it unless overridden themselves.
REPO_ROOT_DERIVED = {
    "compose_file": "docker/docker-compose.yml",
    "dev_sample_file": "data/dev-sample.txt",
    "models_file": "models.yaml",
    "vllm_env_dir": "envs/vllm",
    "surya_env_dir": "envs/surya",
    "labels_file": "labels.yaml",
    "ocr_check_file": "data/ocr-check.txt",
    "ocr_fallback_file": "ocr_fallback.yaml",
    "geo_dir": "data/geo",
    "editions_file": "data/editions.yaml",
}

# Paths under reports_dir that follow it unless overridden themselves.
REPORTS_DIR_DERIVED = {
    "ocr_check_dir": "ocr-check",
}

# Paths under work_dir that follow it unless overridden themselves.
WORK_DIR_DERIVED = {
    "sarvam_cache_dir": "sarvam-cache",
}

# The layout-label groups of labels.yaml (ticket 007 FR-6), and the label-set settings derived
# from them: setting name -> the groups whose labels it holds.
LABEL_GROUPS = (
    "text",
    "title",
    "header_footer",
    "page_number",
    "table",
    "figure",
    "chart",
    "advertisement",
    "caption",
    "other",
)
LABEL_SET_GROUPS = {
    "text_labels": ("text",),
    "title_labels": ("title",),
    "folio_labels": ("header_footer", "page_number"),
    "table_labels": ("table",),
    "figure_labels": ("figure",),
    "chart_labels": ("chart",),
    "ad_labels": ("advertisement",),
    "caption_labels": ("caption",),
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


def read_label_groups(path: Path) -> dict[str, str]:
    """labels.yaml as {Surya label: group}; {} when the file is missing or has no mapping."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    labels = data.get("labels") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}


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
    render_workers: int = 96  # default threads of `stage run render` (PNG encoding is CPU-bound)
    pdftoppm_bin: str = "pdftoppm"
    pdfinfo_bin: str = "pdfinfo"
    render_timeout_s: float = 120.0  # one pdftoppm call; a timeout is a failed attempt
    pdfinfo_timeout_s: float = 30.0
    render_small_width_px: int = 1500  # a narrower render gets detail.small_render = true
    # A failed render with less free space than this in images_dir is a disk problem, not a bad
    # PDF: it is retried but never quarantined.
    render_min_free_bytes: int = 1_000_000_000

    # --- sample ---
    dev_sample_file: Path  # default {repo_root}/data/dev-sample.txt (committed)
    dev_sample_seed: int = 42

    # --- models ---
    hf_home: Path
    gpu_lock_path: Path
    models_file: Path  # default {repo_root}/models.yaml: the model profiles
    vllm_env_dir: Path  # default {repo_root}/envs/vllm: the separate venv holding vLLM
    vllm_spec: str = "vllm>=0.11.0"  # what `models install` installs into vllm_env_dir
    vllm_min_version: str = "0.11.0"  # an existing vllm_env_dir with at least this is kept
    vllm_torch_backend: str = "auto"  # uv --torch-backend for the vLLM venv (CUDA from driver)
    uv_bin: str = "uv"  # used by `models install` when not started through `uv run`
    model_port: int = 8100  # first port tried by `models serve` / use()
    model_port_max: int = 8199  # last port of the range searched when model_port is busy
    model_start_timeout_s: float = 1200.0  # vLLM must answer /v1/models within this
    model_stop_timeout_s: float = 60.0  # SIGTERM grace before SIGKILL when stopping vLLM
    model_request_timeout_s: float = 600.0  # one chat request to a vLLM server
    gpu_lock_report_s: float = 60.0  # "waiting for GPU lock" is repeated this often
    gpu_memory_gb: float = 96.0  # GPU memory used by `models fit` (GB = 2**30 bytes)
    fit_overhead_gb: float = 4.0  # activations + vision encoder headroom in `models fit`

    # --- ocr ---
    surya_env_dir: Path  # default {repo_root}/envs/surya: the venv holding surya-ocr
    labels_file: Path  # default {repo_root}/labels.yaml: Surya label -> group (committed)
    # `ocr install` writes indic-ocr's own tokenizer files to <this>/<indic_ocr_revision>/ for
    # vLLM (transformers would otherwise swap in a byte-level Qwen tokenizer).
    ocr_tokenizer_root: Path  # default {raw_root}/indic-ocr-tokenizer
    # Pinned in pipeline.yaml by `ocr install` (None = take the newest and pin it).
    surya_version: str | None = None
    indic_ocr_revision: str | None = None  # a 40-hex commit of indic_ocr_repo
    indic_ocr_repo: str = "bodhan-ai/indic-ocr"
    ocr_torch_backend: str = "auto"  # uv --torch-backend for the Surya venv (CUDA from driver)
    ocr_min_cuda: str = "12.8"  # the Surya venv needs a torch built for at least this (Blackwell)
    surya_layout_checkpoint: str = "hf://datalab-to/surya_layout2"  # Surya's rf-detr layout
    surya_order_checkpoint: str = "hf://datalab-to/surya_layout2/order"  # its reading order
    # Surya layout boxes scoring below this are dropped. Was 0.4: the ticket-009 review found
    # missed headlines and text columns that Surya scored 0.3-0.4 (on the 10 check pages, 0.3
    # adds 114 text boxes over uncovered ground and keeps 330 of the 338 boxes found at 0.4).
    ocr_layout_threshold: float = 0.3
    # Two layout boxes overlapping by at least this IoU are one area read twice (under two
    # labels): the lower-scoring one is dropped before OCR.
    ocr_dup_box_iou: float = 0.8
    ocr_device: str = "cuda"  # Surya's device
    # indic-ocr's text model runs on vLLM (envs/vllm from ticket 006) under the ocr GPU lock.
    ocr_vllm_max_model_len: int = 8192
    ocr_vllm_gpu_memory_utilization: float = 0.3  # Surya needs ~2 GB beside it
    ocr_vllm_max_num_seqs: int = 256
    ocr_text_concurrency: int = 128  # block requests in flight to vLLM, over all pages
    ocr_pages_in_flight: int = 8  # pages the ocr stage works on at once (its concurrency)
    # Crops as indic-ocr makes them: area scaled into [min_side^2, max_side^2].
    ocr_crop_min_px_side: int = 256
    ocr_crop_max_px_side: int = 1536
    ocr_max_new_tokens: int = 2048  # longest transcription of one block (tables get this)
    # Token budget of a text block: this many tokens per megapixel of its crop (real text needs
    # <= ~560), at least ocr_min_new_tokens. Stops tiny boxes (logos read as LaTeX) babbling.
    ocr_tokens_per_mpx: int = 1500
    ocr_min_new_tokens: int = 96
    ocr_start_timeout_s: float = 900.0  # the Surya worker must print "model loaded" within this
    ocr_request_timeout_s: float = 300.0  # one Surya or vLLM request; then it fails/restarts
    ocr_stop_timeout_s: float = 10.0  # SIGTERM grace before SIGKILL when stopping a worker
    # Label sets derived from labels.yaml groups (LABEL_SET_GROUPS). Later tickets use these,
    # never raw label strings.
    text_labels: tuple[str, ...]
    title_labels: tuple[str, ...]
    folio_labels: tuple[str, ...]  # header_footer + page_number
    table_labels: tuple[str, ...]
    figure_labels: tuple[str, ...]
    chart_labels: tuple[str, ...]
    ad_labels: tuple[str, ...]
    caption_labels: tuple[str, ...]

    # --- colsplit ---
    # Stage `ocr_split` (ticket 008): a text block wider than this × the median width of the
    # page's text_labels blocks is checked for an interior column gap.
    colsplit_width_factor: float = 1.6
    # A gap is at least this many blank pixel columns (300 DPI). The ticket said 15; on the dev
    # sample real gutters measured 54-63 px and the word spaces of display/ad type 15-32 px.
    colsplit_min_gap_px: int = 40
    colsplit_min_piece_px: int = 120  # ...with at least this many inked columns on each side

    # --- ocr_check ---
    # The OCR sanity check (ticket 009): 10 pages a Telugu reader reviews in a local web page.
    ocr_check_file: Path  # default {repo_root}/data/ocr-check.txt: the 10 page ids (committed)
    ocr_check_dir: Path  # default {reports_dir}/ocr-check: verdicts.json and the image cache
    ocr_check_stage: str = "ocr_split"  # the OCR blocks shown next to the scan
    ocr_check_host: str = "0.0.0.0"  # all interfaces, so the reader can open it from the LAN
    ocr_check_port: int = 8700
    ocr_check_image_long_side: int = 2400  # the page JPEG is downscaled to this long side...
    ocr_check_image_max_bytes: int = 2_000_000  # ...and at most this big
    ocr_check_jpeg_quality: int = 85  # first JPEG quality tried; lowered until it fits

    # --- ocr_fallback ---
    # Stage `ocr_final` (ticket 010): pages matching a rule in this file have their text blocks
    # re-read by Sarvam Document Intelligence. The committed default is `rules: []` (no calls).
    ocr_fallback_file: Path  # default {repo_root}/ocr_fallback.yaml
    sarvam_cache_dir: Path  # default {work_dir}/sarvam-cache: results by sha1 of the crop PNG
    sarvam_language: str = "te-IN"
    sarvam_output_format: str = "md"
    sarvam_min_block_px: int = 20  # blocks narrower or lower than this are not sent
    sarvam_crop_pad_px: int = 2  # padding around a block's box when cropping it
    sarvam_crops_per_job: int = 10  # one crop per PDF page; the API takes at most 10 pages
    sarvam_max_jobs: int = 4  # Sarvam jobs running at once in one process
    sarvam_job_timeout_s: float = 600.0  # from start until the job reaches a final state
    sarvam_poll_interval_s: float = 3.0  # between two job-status calls
    sarvam_http_timeout_s: float = 300.0  # one SDK request, upload or download
    # Every SDK call is retried after these waits on HTTP 429/5xx or a connection error
    # (len + 1 attempts in all).
    sarvam_retry_backoff_s: tuple[float, ...] = (2.0, 8.0, 30.0)

    # --- geo ---
    # The district/mandal reference list (ticket 018): periods.yaml, one district file per
    # period, mandals.yaml (committed).
    geo_dir: Path  # default {repo_root}/data/geo
    # Extra spellings -> district/town/mandal id. None = the file shipped in newsrag/geo/.
    geo_aliases_file: Path | None = None

    # --- editions ---
    # Stage `folio` (ticket 011): a block lying wholly in this top fraction of the page height
    # is read as part of the folio, whatever its label (besides the folio_labels blocks).
    folio_top_fraction: float = 0.08
    # When the folio blocks give no place, the whole top band (folio_top_fraction) is read too,
    # cut into this many side-by-side pieces (layout sometimes misses the folio strip). 0 = off.
    folio_band_pieces: int = 2
    editions_file: Path  # default {repo_root}/data/editions.yaml (committed, generated)
    # A code's most common folio place names it when it has at least this share of the code's
    # pages with a place, over at least this many pages; otherwise the code is `ambiguous`.
    editions_min_agreement: float = 0.6
    editions_min_pages: int = 3
    # Pages of these sections are state pages shared by many editions: their folio names the
    # printing centre, not the edition (011, decided 2026-11-10). They get the state only, no
    # edition name or district, and do not feed the code -> name map.
    editions_state_sections: tuple[str, ...] = ("main",)

    # --- group ---
    group_model: str = "qwen3-vl-32b"  # the VLM profile that groups blocks into articles (012)
    group_concurrency: int = 16  # requests in flight (threads of `stage run group`)
    group_image_max_px: int = 3584  # long side of the numbered-box page image sent to the model
    group_text_chars: int = 80  # characters of each block's text in the model's block list...
    group_long_page_blocks: int = 150  # ...cut to group_long_page_chars on pages with more blocks
    group_long_page_chars: int = 40
    group_max_tokens: int = 8192
    group_request_timeout_s: float = 300.0  # one chat request
    group_transport_retries: int = 2  # timeouts, connection errors, HTTP 5xx
    group_retry_backoff_s: tuple[float, ...] = (2.0, 8.0)
    group_parse_retries: int = 2  # invalid JSON / schema error: ask again with the error
    # Geometry checks (300-DPI page pixels): blocks of an article within this edge-to-edge gap
    # are connected; an article box covering more than group_overlap_frac of another article's
    # block, or a headline lower than group_headline_slack_px below its body, is flagged.
    group_max_gap_px: int = 60
    group_overlap_frac: float = 0.2
    group_headline_slack_px: int = 50
    # Reading order inside an article (FR-9): x-ranges overlapping by this share of the narrower
    # block are one column; overlaps between group_order_min_overlap_px and that share are
    # "unsure" (the model's order is kept). A block touching another by <= the min overlap is not
    # counted as overlapping it when looking for wide blocks.
    group_order_same_column_frac: float = 0.5
    group_order_min_overlap_px: int = 30
    # Duplicate boxes (FR-10): share of the box inside a bigger one and share of its text matched.
    group_dup_overlap_frac: float = 0.8
    group_dup_text_frac: float = 0.8

    # --- judge ---
    # The VLM that scores a grouping: another family than group_model (checked by `judge`).
    # Gemma 4 won the 013 bake-off (6/10 merges caught on the page view; Gemma 3 and InternVL 0/10).
    judge_model: str = "gemma4-31b"
    judge_threshold: float = 0.8  # a page scoring below this is grouped again at higher resolution
    judge_concurrency: int = 16  # requests in flight (threads of `stage run judge`)
    judge_image_max_px: int = 3584  # long side of the article overlay sent to the judge
    judge_text_chars: int = 300  # characters of each article's text in the judge's list
    judge_skip_kinds: tuple[str, ...] = ("masthead", "ad_commercial")  # not judged, not counted
    judge_max_tokens: int = 4096
    judge_retry_scale: float = 1.5  # the regroup pass sends the image at group_image_max_px x this
    # One-story check: every article of these kinds that the page judge called `correct` is sent
    # again on its own (headline boxes + text, no image): "one story or several?". Two or more
    # turns the verdict into `merged`. Empty turns the check off.
    judge_story_kinds: tuple[str, ...] = ("news", "editorial")
    judge_story_chars: int = 6000  # characters of the article's text sent to the one-story check

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
        for name, rel in REPORTS_DIR_DERIVED.items():
            if data.get(name) in (None, ""):
                data[name] = Path(data["reports_dir"]) / rel
        for name, rel in WORK_DIR_DERIVED.items():
            if data.get(name) in (None, ""):
                data[name] = Path(data["work_dir"]) / rel
        missing = [n for n in LABEL_SET_GROUPS if data.get(n) in (None, "")]
        if missing:
            groups = read_label_groups(Path(data["labels_file"]))
            for name in missing:
                wanted = LABEL_SET_GROUPS[name]
                data[name] = tuple(sorted(lab for lab, grp in groups.items() if grp in wanted))
        for name in secret_fields():
            if data.get(name) == "":
                data[name] = None
        return data

    @field_validator(*LABEL_SET_GROUPS, mode="before")
    @classmethod
    def _label_list(cls, value: Any) -> Any:
        """A label set from the environment: a JSON list or comma-separated names."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return tuple(json.loads(text))
            return tuple(part.strip() for part in text.split(",") if part.strip())
        return value

    @field_validator("editions_state_sections", "judge_skip_kinds", mode="before")
    @classmethod
    def _str_list(cls, value: Any) -> Any:
        """Names from the environment: a JSON list or comma-separated names."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return tuple(json.loads(text))
            return tuple(part.strip() for part in text.split(",") if part.strip())
        return value

    @field_validator("sarvam_retry_backoff_s", "group_retry_backoff_s", mode="before")
    @classmethod
    def _float_list(cls, value: Any) -> Any:
        """Waits from the environment: a JSON list or comma-separated numbers."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return tuple(json.loads(text))
            return tuple(float(part) for part in text.split(",") if part.strip())
        return value

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
