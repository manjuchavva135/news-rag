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
from datetime import date
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
    "baseline_dir": "baseline",
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
    "bakeoff_pages_file": "data/bakeoff-pages.txt",
    "bakeoff_telugu_file": "data/telugu-headlines.txt",
    "continuations_file": "continuations.yaml",
    "alias_overrides_file": "data/aliases/overrides.yaml",
    "textmodel_stories_file": "data/textmodel-bakeoff-stories.txt",
    "retrieval_set_file": "data/eval/retrieval-v1.jsonl",
    "baseline_compose_file": "docker/docker-compose.baseline.yml",
    "baseline_set_file": "data/eval/retrieval-baseline-v1.jsonl",
    "customer_questions_file": "data/eval/customer-questions.json",
}

# Paths under reports_dir that follow it unless overridden themselves.
REPORTS_DIR_DERIVED = {
    "ocr_check_dir": "ocr-check",
    "transcribe_spotcheck_file": "transcription-spotcheck.json",
    "stitch_verify_file": "stitch-verify.json",
    "dedup_verify_file": "dedup-verify.json",
    "enrich_verify_file": "enrich-core-verify.json",
    "enrich_facts_verify_file": "enrich-facts-verify.json",
    "alias_review_file": "alias-review.csv",
    "alias_verify_file": "alias-verify.json",
    "textmodel_report_file": "textmodel-bakeoff.md",
    "embed_bakeoff_report_file": "embed-bakeoff.md",
    "embed_bakeoff_dir": "embed-bakeoff",
    "baseline_page_map_file": "baseline-page-map.csv",
    "baseline_calibration_file": "baseline-calibration.json",
    "baseline_compare_file": "baseline-compare.json",
    "runs_dir": "runs",
    "customer_answers_md_file": "customer-answers.md",
    "customer_answers_file": "customer-answers.json",
    "customer_signoff_file": "customer-answers-signoff.json",
    "quality_report_dir": "quality",
}

# Paths under work_dir that follow it unless overridden themselves.
WORK_DIR_DERIVED = {
    "sarvam_cache_dir": "sarvam-cache",
    "alias_dir": "aliases",
    "textmodel_dir": "textmodel-bakeoff",
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
    # Ticket 024: where the index writes. Tests point both at throw-away values.
    warehouse_schema: str = "warehouse"  # Postgres schema of the structured warehouse
    store_alias_prefix: str = ""  # prefix of the `chunks` aliases and `chunks__<generation>` names

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
    # The VLM that scores a grouping. Since 2026-11-11 (user decision, ticket 014 D-6) it may be of
    # the same family as group_model, even the same model; False brings back the family check.
    judge_allow_same_family: bool = True
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

    # --- bakeoff ---
    # `newsrag group bakeoff` (ticket 014): candidate grouping VLMs group the same pages, the
    # judge scores them, each candidate also reads Telugu headline crops; the winner goes to
    # pipeline.yaml with `--apply`.
    bakeoff_candidates: tuple[str, ...] = (
        "qwen3-vl-32b",
        "qwen3-vl-30b-a3b",
        "gemma3-27b",
        "gemma4-31b",
    )
    bakeoff_pages_file: Path  # default {repo_root}/data/bakeoff-pages.txt (committed, generated)
    bakeoff_telugu_file: (
        Path  # default {repo_root}/data/telugu-headlines.txt (committed, generated)
    )
    bakeoff_telugu_count: int = 30  # headline blocks read by every candidate
    bakeoff_telugu_min_share: float = 0.8  # share of Telugu letters (non-space) in a chosen block
    bakeoff_telugu_min_chars: int = 8  # non-space characters of a chosen block
    bakeoff_crop_pad_px: int = 8  # margin around a headline box in its crop (300-DPI pixels)
    bakeoff_read_max_tokens: int = 256  # reply length of one headline reading
    bakeoff_tie: float = 0.01  # mean judge scores this close are a tie

    # --- transcribe ---
    # Stage `transcribe` (ticket 015): the grouping VLM (group_model) reads tables, infographics
    # and notice/ad regions into Markdown. Crops get this margin (300-DPI pixels) and are shrunk
    # to the long side below (block crops / whole-article crops).
    transcribe_pad_px: int = 8
    transcribe_block_max_px: int = 2048
    transcribe_article_max_px: int = 3072
    transcribe_max_tokens: int = 8192  # reply length of one transcription
    transcribe_parse_retries: int = 2  # invalid JSON / value outside the enums: ask again
    # A notice crop larger than this (megapixels at 300 DPI) is read in strips cut between its
    # blocks (the VLM sees ~2.6 MP per image: bigger crops get shrunk and small Telugu misread).
    # A strip that has to cut through a block overlaps the next one by transcribe_tile_overlap_px.
    transcribe_tile_max_mp: float = 2.4
    transcribe_tile_overlap_px: int = 48
    # The indic-ocr text of the region goes with every request as a spelling reference, cut to
    # this many characters (a request that does not fit is sent with a third, then without).
    transcribe_hint_chars: int = 4000
    # Articles of these kinds are read whole (notice or commercial, the VLM decides) ...
    transcribe_article_kinds: tuple[str, ...] = ("notice_tender", "ad_commercial")
    # ... and table/infographic blocks of these kinds' articles become standalone items.
    transcribe_standalone_kinds: tuple[str, ...] = ("masthead", "other")
    # `newsrag transcribe spotcheck`: the judge (judge_model) checks each line of the sample.
    transcribe_spotcheck_n: int = 50
    transcribe_spotcheck_seed: int = 1
    transcribe_spotcheck_max_tokens: int = 8192
    transcribe_spotcheck_file: Path  # default {reports_dir}/transcription-spotcheck.json

    # --- stitch ---
    # Stage `stitch` (ticket 016): joins the parts of a story that continues on another page.
    continuations_file: Path  # default {repo_root}/continuations.yaml: the marker regexes
    stitch_kinds: tuple[str, ...] = ("news", "editorial", "brief_item")  # articles that take part
    stitch_min_score: float = 0.15  # a link needs at least this word-overlap (Jaccard) score
    stitch_ambiguity_margin: float = 0.02  # a second candidate this close flags the link
    stitch_page_ref_bonus: float = 0.2  # added when the continuation's marker names the source page
    stitch_tail_chars: int = 300  # source end / continuation start compared by word overlap
    stitch_token_min_chars: int = 2  # Telugu tokens shorter than this are ignored
    stitch_attach_px: int = 100  # an unassigned marker block joins an article within this distance
    # `newsrag stitch verify` (NFR-1): the text model answers "same story? yes/no" for each link.
    stitch_verify_model: str = "qwen3-30b-a3b"
    stitch_verify_n: int = 20
    stitch_verify_seed: int = 3
    stitch_verify_part_chars: int = 600  # source end / continuation start shown to the model
    stitch_verify_max_tokens: int = 8
    stitch_verify_workers: int = 8  # parallel requests
    stitch_verify_file: Path  # default {reports_dir}/stitch-verify.json

    # --- dedup ---
    # Stage `dedup` (ticket 017): near-identical printings of a story in different editions of
    # one print date become one story. MinHash over character shingles finds the candidates, the
    # embedding cosine confirms them.
    dedup_jaccard: float = 0.85  # estimated MinHash Jaccard a duplicate pair needs
    dedup_cosine: float = 0.90  # ... and this embedding cosine
    dedup_device: str = "cuda"  # "cuda" or "cpu" (cpu: the model loads without the GPU lock)
    dedup_model: str = "bge-m3"  # embed profile of models.yaml (fixed, independent of search)
    dedup_num_perm: int = 128  # MinHash permutations
    dedup_shingle_size: int = 5  # characters per shingle
    dedup_lsh_threshold: float = 0.5  # LSH candidate net, wider than dedup_jaccard (recall)
    dedup_embed_batch: int = 64
    dedup_kinds: tuple[str, ...] = (
        "news",
        "editorial",
        "brief_item",
        "notice_tender",
        "table_only",
    )  # article kinds that become units
    # `newsrag dedup verify` (NFR-1): the text model answers "same story? yes/no" for merged pairs.
    dedup_verify_model: str = "qwen3-30b-a3b"
    dedup_verify_n: int = 30
    dedup_verify_seed: int = 5
    dedup_verify_min_yes_ratio: float = 28 / 30  # at least 28 of 30
    dedup_verify_part_chars: int = 1200  # headline + start of each printing shown to the model
    dedup_verify_max_tokens: int = 8
    dedup_verify_workers: int = 8  # parallel requests
    dedup_verify_file: Path  # default {reports_dir}/dedup-verify.json

    # --- enrich ---
    # Stage `enrich_core` (ticket 019): category, people/organisations and event date of every
    # canonical story, from a local text model. Later enrich stages add their own settings here.
    enrich_model: str = "qwen3-30b-a3b"  # llm profile of models.yaml
    enrich_concurrency: int = 64  # parallel requests inside one date (32 gives about 4.7 stories/s)
    enrich_input_chars: int = 6000  # headline + text sent to the model, truncated to this
    enrich_max_tokens: int = 1024
    enrich_max_listed: int = 8  # people and organisations listed per story, at most each
    enrich_max_places: int = 16  # places listed per story by `enrich_places` (ticket 020), at most
    enrich_request_timeout_s: float = 120.0  # one chat request
    enrich_transport_retries: int = 3  # timeout / 5xx / connection errors
    enrich_retry_backoff_s: tuple[float, ...] = (2.0, 5.0, 15.0)  # waits between those retries
    enrich_format_retries: int = 2  # invalid JSON / schema errors: asks again this many times
    enrich_fail_ratio: float = 0.20  # more failed stories than this share fails the date
    enrich_event_past_years: int = 3  # an event date may lie this many years before the print date
    enrich_event_future_days: int = 366  # ... and this many days after it
    # `newsrag enrich verify-core` (NFR-1): a second model judges a sample of enriched stories.
    enrich_verify_model: str = "gemma3-27b"
    enrich_verify_n: int = 50
    enrich_verify_seed: int = 11
    enrich_verify_min_ok_ratio: float = 0.9  # category_ok and names_ok each need this share
    enrich_verify_max_tokens: int = 128
    enrich_verify_workers: int = 16  # parallel requests
    enrich_verify_file: Path  # default {reports_dir}/enrich-core-verify.json
    # Stage `enrich_facts` (ticket 021): amounts, counts and percentages as structured facts.
    enrich_max_facts: int = 16  # facts listed per story, at most (the reply must fit max_tokens)
    enrich_facts_scale_window: int = 40  # characters before a number searched for a scale phrase
    # `newsrag enrich verify-facts` (NFR-1): the judge model (`enrich_verify_model`) checks facts.
    enrich_facts_verify_n: int = 50
    enrich_facts_verify_seed: int = 13
    enrich_facts_verify_file: Path  # default {reports_dir}/enrich-facts-verify.json

    # --- aliases ---
    # Ticket 022: which names (people, organisations) of the `enrich_core` artifacts are one entity.
    # The model is `enrich_model`; the alias version is `<enrich_model>-alias-p1`.
    alias_dir: Path  # default {work_dir}/aliases: <version>/entities.json, verdicts.jsonl, ...
    alias_review_file: Path  # default {reports_dir}/alias-review.csv
    alias_overrides_file: Path  # default {repo_root}/data/aliases/overrides.yaml (committed)
    alias_jw_threshold: float = 0.90  # rule (b): Jaro-Winkler on the folded names
    alias_min_shared_stories: int = 2  # rule (d): stories two names with a shared surname share
    alias_max_llm_pairs: int = 50_000  # new pairs sent to the model per batch, most frequent first
    alias_auto_confidence: float = 0.9  # auto-apply needs `same` and at least this confidence
    alias_examples: int = 3  # example sentences per name in the prompt
    alias_example_chars: int = 200  # one example sentence is cut to this many characters
    alias_concurrency: int = 64  # parallel requests
    alias_format_retries: int = 2  # invalid JSON / schema: asks again this many times
    alias_max_tokens: int = 64
    # `newsrag aliases verify` (NFR-1): the judge model asks about pairs of an auto-merged group.
    alias_verify_model: str = "gemma3-27b"
    alias_verify_n: int = 40
    alias_verify_seed: int = 17
    alias_verify_min_ok_ratio: float = 0.95  # share of pairs the judge must call the same entity
    alias_verify_file: Path  # default {reports_dir}/alias-verify.json

    # --- chunking ---
    # Ticket 023: stage `chunk` cuts every canonical story (and its tables) into retrieval chunks.
    # The tokenizer is the one of this embed profile of models.yaml; a model change re-chunks.
    embed_model: str = "bge-m3"  # embed profile of models.yaml (ticket 026 may change it)
    chunk_max_tokens: int = 512  # the whole embedded string (prefix + text) fits this many tokens

    # --- index ---
    # Ticket 024: stage `index` embeds the chunks and writes Qdrant, OpenSearch and the warehouse.
    # A generation is `<embed_model>__<index_generation_tag>`: one set of collections per model.
    embed_batch_size: int = 64  # texts per embedding batch
    index_generation_tag: str = "v1"  # change it to build a second generation of the same model
    index_write_batch: int = 256  # points / documents / rows per write request
    index_script_share: float = 0.2  # share of letters from a script to put a text in its field

    # --- search ---
    # Ticket 025: plain hybrid search (dense + BM25, reciprocal-rank fusion). No reranking.
    search_candidates: int = 50  # top hits taken from each of Qdrant and OpenSearch
    search_rrf_c: float = 60.0  # RRF: score = sum of 1 / (c + rank)
    search_bm25_fields: tuple[str, ...] = ("headline_te^2", "text_te", "headline_en^2", "text_en")

    # --- evals ---
    # Ticket 025: the retrieval test set (`newsrag eval build-retrieval`) and its scoring
    # (`newsrag eval retrieval`). The questions are written by `enrich_model`.
    retrieval_set_file: Path  # default {repo_root}/data/eval/retrieval-v1.jsonl (committed)
    retrieval_n: int = 300  # questions in a built set
    retrieval_seed: int = 7
    retrieval_window_n: int = 100  # questions from the baseline window (the old pipeline's pages)
    retrieval_window_start: date = date(2026, 1, 1)
    retrieval_window_end: date = date(2026, 6, 30)
    retrieval_kinds: tuple[str, ...] = ("news", "editorial", "notice_tender")
    retrieval_te_share: float = 0.7  # share of the questions written in Telugu (the rest English)
    retrieval_copy_words: int = 4  # a question sharing this many consecutive headline words...
    retrieval_max_tries: int = 3  # ...or an invalid reply is asked again up to this many times
    retrieval_story_chars: int = 4000  # headline + text shown to the model, cut to this
    retrieval_max_tokens: int = 256
    retrieval_concurrency: int = 32  # parallel question requests
    retrieval_lang_share: float = 0.5  # letters of the asked script a question needs at least
    retrieval_k: int = 10  # hits scored per question (recall@k)
    retrieval_ngram: int = 5  # characters per n-gram of the hit rule
    retrieval_hit_overlap: float = 0.3  # share of a hit's n-grams that must occur in the gold text
    # Ticket 028: `newsrag eval customer` answers the customer's 30 questions with `answer_model`
    # from the new stores and has the text judge (`answer_judge_model`, else `judge_model`) check
    # every claim against the cited sources; `eval customer-signoff` records that they were read.
    customer_questions_file: Path  # default {repo_root}/data/eval/customer-questions.json
    customer_old_results_file: Path = Path("/home/admin1/RAG/evals/results-2026-07-11.md")
    customer_answers_md_file: Path  # default {reports_dir}/customer-answers.md
    customer_answers_file: Path  # default {reports_dir}/customer-answers.json
    customer_signoff_file: Path  # default {reports_dir}/customer-answers-signoff.json
    customer_k: int = 12  # sources (search hits) per question
    customer_source_chars: int = 1500  # each source's text is cut to this for the prompts
    customer_answer_max_tokens: int = 2048
    customer_answer_repetition_penalty: float = 1.05  # vLLM; 1.0 = off (temp 0 loops without)
    customer_answer_retries: int = 2  # a failed answer request is asked again this many times
    customer_judge_retries: int = 2  # an unparseable judge reply is asked again this many times
    customer_judge_max_tokens: int = 64
    customer_concurrency: int = 16  # parallel answer / judge requests

    # --- embed_bakeoff ---
    # `newsrag embed bakeoff` (ticket 026): every candidate embeds the active generation's chunks
    # into Qdrant `<store_alias_prefix>bakeoff__<candidate>` and runs the retrieval test on it;
    # `--apply` writes the winner as `embed_model`.
    embed_bakeoff_candidates: tuple[str, ...] = (
        "bge-m3",
        "qwen3-embedding-8b",
        "multilingual-e5-large-instruct",
    )
    embed_bakeoff_report_file: Path  # default {reports_dir}/embed-bakeoff.md
    embed_bakeoff_dir: Path  # default {reports_dir}/embed-bakeoff: per-candidate retrieval JSON
    embed_bakeoff_min_questions: int = 200  # questions with their gold story in the corpus
    embed_bakeoff_tie: float = 0.01  # recall@10 / MRR this close count as a tie
    embed_bakeoff_slab: int = 4096  # chunks embedded and written per round

    # --- baseline ---
    # Ticket 027: the old pipeline's stage-9 bundle in its own stores (project newsrag-baseline),
    # searched with the same plain hybrid search and compared on the retrieval test.
    baseline_postgres_dsn: str = "postgresql://newsrag:newsrag@localhost:5452/newsrag_baseline"
    baseline_qdrant_url: str = "http://localhost:6353"
    baseline_opensearch_url: str = "http://localhost:9220"
    baseline_compose_file: Path  # default {repo_root}/docker/docker-compose.baseline.yml
    baseline_dir: Path  # default {raw_root}/baseline: the bundle and manifest-loaded.json
    baseline_schema: str = "old"  # Postgres schema of the old warehouse tables
    baseline_collection: str = "chunks_old"  # Qdrant collection and OpenSearch index
    baseline_embed_profile: str = "bge-m3"  # the old vectors' model: embeds the queries
    baseline_load_batch: int = 1000  # rows / points / documents per write while loading
    baseline_page_map_file: Path  # default {reports_dir}/baseline-page-map.csv
    baseline_set_file: Path  # default {repo_root}/data/eval/retrieval-baseline-v1.jsonl
    baseline_set_n: int = 100  # questions of the comparison set
    baseline_set_seed: int = 7
    baseline_calibration_n: int = 50
    baseline_calibration_seed: int = 9
    baseline_calibration_min: float = 0.8  # `compare` refuses below this calibration
    baseline_calibration_file: Path  # default {reports_dir}/baseline-calibration.json
    baseline_compare_file: Path  # default {reports_dir}/baseline-compare.json

    # --- textmodel ---
    # `newsrag textmodel bakeoff` (ticket 032): candidate text models run four Telugu tasks on the
    # same canonical stories and are cross-judged by candidates of another family; `--apply` writes
    # the winner as `enrich_model` and `answer_model`, and `answer_judge_model` (ticket 028 uses
    # it for its claim check when set).
    textmodel_candidates: tuple[str, ...] = (
        "gemma3-27b",
        "gemma4-31b",
        "qwen3-30b-a3b",
        "sarvam-m-24b",
    )
    textmodel_stories_file: (
        Path  # default {repo_root}/data/textmodel-bakeoff-stories.txt (committed)
    )
    textmodel_report_file: Path  # default {reports_dir}/textmodel-bakeoff.md
    textmodel_dir: Path  # default {work_dir}/textmodel-bakeoff: saved outputs and judgements
    textmodel_n: int = 40
    textmodel_seed: int = 21
    textmodel_min_telugu_share: float = 0.7  # a sampled story: share of Telugu letters
    textmodel_min_chars: int = 300  # a sampled story: length of its text
    textmodel_max_chars: int = 4000
    textmodel_output_telugu_share: float = 0.9  # a summary / question counts as Telugu from here
    textmodel_summary_max_tokens: int = 512  # summary and question replies
    textmodel_judge_max_tokens: int = 256
    textmodel_judge_retries: int = 2  # unparseable judge reply: asks again this many times
    textmodel_tie: float = 0.01  # totals this close are a tie (broken by judged, then speed)
    # Models that answer the customer questions (ticket 028): set by `textmodel bakeoff --apply`.
    answer_model: str = "qwen3-30b-a3b"
    answer_judge_model: str | None = None  # text judge of 028's claim check; judge_model when unset

    # --- orchestrator ---
    # `newsrag run` (ticket 029): every stage in dependency order, one pass per stage.
    runs_dir: Path  # default {reports_dir}/runs: one <UTC timestamp>.json per run
    run_stop_grace_s: float = 600  # after SIGINT/SIGTERM, items in flight get this long to finish
    run_requeue_older_than: str = "2h"  # `stage requeue-stale --older-than` before every run

    # --- report ---
    # `newsrag report build` (ticket 030): the phase-1 quality report, index.html + report.json.
    quality_report_dir: Path  # default {reports_dir}/quality

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

    @field_validator(
        "editions_state_sections",
        "judge_skip_kinds",
        "bakeoff_candidates",
        "transcribe_article_kinds",
        "transcribe_standalone_kinds",
        "stitch_kinds",
        "dedup_kinds",
        "textmodel_candidates",
        mode="before",
    )
    @classmethod
    def _str_list(cls, value: Any) -> Any:
        """Names from the environment: a JSON list or comma-separated names."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return tuple(json.loads(text))
            return tuple(part.strip() for part in text.split(",") if part.strip())
        return value

    @field_validator(
        "sarvam_retry_backoff_s", "group_retry_backoff_s", "enrich_retry_backoff_s", mode="before"
    )
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
