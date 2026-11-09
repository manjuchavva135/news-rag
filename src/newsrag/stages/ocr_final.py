"""Stage `ocr_final` (ticket 010): the OCR blocks every later stage reads.

A page that matches no rule of `ocr_fallback_file` is the `ocr_split` artifact with every block
given `ocr_source: "indic"` (no network call). A page that matches a rule has its text blocks
re-read by Sarvam, keeping our block ids (`newsrag.ocr.sarvam`). With the committed default
`rules: []` nothing is ever sent.

Versions (`version_for`): `final-v1` for a page that matches no rule (nothing configurable
changes its output); `final-v1-sarvam-<hash>` for a matching page, where the hash covers the
canonical JSON of the rules it matches and the Sarvam settings that change the text. Editing the
rules reruns only the pages whose matching rules changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.ocr import fallback
from newsrag.stages.base import (
    PageRow,
    PageStage,
    StageResult,
    StageUnavailable,
    artifact_path,
    write_json_atomic,
)

BASE_VERSION = "final-v1"
NO_KEY = "SARVAM_API_KEY not set but page matches an ocr_fallback rule"
SARVAM_FAILED = "low_confidence:sarvam_failed"
# Settings that change the text of a re-read page (part of its version).
SARVAM_VERSION_SETTINGS = (
    "sarvam_language",
    "sarvam_output_format",
    "sarvam_min_block_px",
    "sarvam_crop_pad_px",
    "text_labels",
    "title_labels",
)


def split_artifact(page_id: str) -> Path:
    """The page's done `ocr_split` artifact, from the ledger."""
    from newsrag.ledger import db

    with db.conn() as c:
        row = db.fetch_one(
            c,
            "select artifact_path from stage_runs where page_id = %s and stage = 'ocr_split' "
            "and status = 'done' and artifact_path is not null",
            (page_id,),
        )
    if row is None:
        raise FileNotFoundError(f"no done ocr_split artifact for {page_id}")
    path = Path(row["artifact_path"])
    if not path.is_file():
        raise FileNotFoundError(f"ocr_split artifact missing: {path}")
    return path


def make_api() -> Any:
    """The Sarvam client (`newsrag.ocr.sarvam.SarvamApi`); tests replace this with a fake."""
    from newsrag.ocr.sarvam import SdkApi

    key = config.settings.sarvam_api_key
    if key is None:
        raise RuntimeError(NO_KEY)
    return SdkApi(key.get_secret_value())


def version_for_rules(matched: list[fallback.Rule]) -> str:
    if not matched:
        return BASE_VERSION
    s = config.settings
    extra = {
        name: list(v) if isinstance(v := getattr(s, name), tuple) else v
        for name in SARVAM_VERSION_SETTINGS
    }
    return f"{BASE_VERSION}-sarvam-{fallback.rules_hash(matched, extra)}"


class OcrFinalStage(PageStage):
    name = "ocr_final"
    depends_on = ("ocr_split",)
    version = BASE_VERSION
    concurrency = 1  # Sarvam jobs run in parallel inside a page (sarvam_max_jobs)

    def __init__(self) -> None:
        self._api: Any = None

    def version_for(self, item: PageRow) -> str:
        return version_for_rules(fallback.matching_rules(fallback.load_rules(), item))

    def preflight(self) -> None:
        try:
            fallback.load_rules()
        except fallback.RulesError as exc:
            raise StageUnavailable(str(exc)) from None

    def api(self) -> Any:
        if self._api is None:
            self._api = make_api()
        return self._api

    def close(self) -> None:
        self._api = None

    def handle(self, page: PageRow) -> StageResult:
        data = json.loads(split_artifact(page.page_id).read_text(encoding="utf-8"))
        matched = fallback.matching_rules(fallback.load_rules(), page)
        return final_page(data, matched, self.api)


def final_page(data: dict[str, Any], matched: list[fallback.Rule], api: Any) -> StageResult:
    """Build one page's `ocr_final` artifact from its `ocr_split` artifact (`data`).

    `api()` gives the Sarvam client; it is only called for a page with blocks to send.
    """
    s = config.settings
    page_id = data["page_id"]
    blocks = data.get("blocks") or []
    version = version_for_rules(matched)
    page_flags: list[str] = []

    if not matched:
        out_blocks = [{**b, "ocr_source": "indic"} for b in blocks]
        detail: dict[str, Any] = {"fallback": False}
    else:
        if s.sarvam_api_key is None:
            raise RuntimeError(NO_KEY)
        from newsrag.ocr import sarvam

        labels = fallback.labels_for(matched)
        image = Path((data.get("image") or {}).get("path") or Path(s.images_dir) / f"{page_id}.png")
        wanted = any(b.get("label") in labels for b in blocks)
        if wanted and not image.is_file():
            raise FileNotFoundError(f"rendered page missing: {image}")

        class _LazyApi:
            """Creates the client only when a job is actually run (all crops may be cached)."""

            def __getattr__(self, attr: str) -> Any:
                return getattr(api(), attr)

        reread = sarvam.reread_blocks(image, blocks, labels, _LazyApi())
        out_blocks = reread.blocks
        if reread.failed_blocks:
            page_flags.append(SARVAM_FAILED)
        detail = {
            "fallback": True,
            "n_sent": reread.n_sent,
            "n_cached": reread.n_cached,
            "n_replaced": reread.n_replaced,
            "sarvam_job_ids": reread.job_ids,
            "sarvam_failed_blocks": reread.failed_blocks,
        }
        if reread.errors:
            detail["sarvam_errors"] = reread.errors[:20]

    artifact = {
        "page_id": page_id,
        "image": data.get("image"),
        "model": data.get("model"),
        "fallback_rules": matched,
        "page_flags": page_flags,
        "blocks": out_blocks,
        "markdown": "\n\n".join(b["text"] for b in out_blocks if b.get("text")),
    }
    path = write_json_atomic(artifact_path("ocr_final", version, page_id), artifact)
    return StageResult(str(path), detail)
