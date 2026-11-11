"""`newsrag preflight` (ticket 031 FR-1): what must be true before the full-corpus run.

Every check returns a `Check`; `ok` or `MISSING <what to do>`. A check that raises is MISSING with
the error, so a store that is down never stops the other checks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from newsrag import config

# pipeline.yaml keys the earlier tickets chose (014: grouping, 032: text models, 026: embedding)
PIPELINE_KEYS = (
    "group_model",
    "judge_model",
    "enrich_model",
    "answer_model",
    "answer_judge_model",
    "embed_model",
)
GB = 2**30


@dataclass
class Check:
    name: str
    ok: bool
    todo: str = ""  # what to do when not ok

    def line(self) -> str:
        return f"{self.name}: ok" if self.ok else f"{self.name}: MISSING {self.todo}"


def pipeline_keys() -> Check:
    path = Path(config.settings.pipeline_file)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    data = data if isinstance(data, dict) else {}
    missing = [k for k in PIPELINE_KEYS if not data.get(k)]
    todo = f"set {', '.join(missing)} in {path} (tickets 014, 026, 032)"
    return Check("pipeline.yaml models", not missing, todo)


def _canonical_rules(raw: Any, source: str) -> list[str]:
    from newsrag.ocr import fallback

    if isinstance(raw, list):
        raw = [
            {k: v for k, v in r.items() if k != "note"} if isinstance(r, dict) else r for r in raw
        ]
    rules = fallback.parse_rules({"rules": raw or []}, source)
    return sorted(fallback.canonical_json(r) for r in rules)


def ocr_signoff() -> Check:
    from newsrag.ocr import fallback
    from newsrag.report.collect import ocr_verdicts_file, read_json

    verdicts = read_json(ocr_verdicts_file())
    signoff = (verdicts or {}).get("signoff") if isinstance(verdicts, dict) else None
    if not signoff or not signoff.get("name") or not signoff.get("signed_at"):
        return Check("OCR sign-off", False, "the Telugu reader signs the OCR check (ticket 009)")
    wanted = _canonical_rules(signoff.get("failing_page_types") or [], "sign-off")
    current = sorted(fallback.canonical_json(r) for r in fallback.load_rules())
    if wanted != current:
        todo = (
            f"make the rules in {config.settings.ocr_fallback_file} equal the sign-off's "
            "failing_page_types (the user writes them)"
        )
        return Check("OCR sign-off", False, todo)
    if current and config.settings.sarvam_api_key is None:
        return Check("OCR sign-off", False, "set SARVAM_API_KEY (fallback rules are not empty)")
    return Check("OCR sign-off", True)


def hf_token() -> Check:
    return Check("HF_TOKEN", config.settings.hf_token is not None, "set HF_TOKEN in .env")


def stores() -> Check:
    from newsrag.stores import ping_all

    states = ping_all(config.settings.stores_ping_timeout_s)
    down = [name for name, up in states.items() if not up]
    return Check("stores", not down, f"{', '.join(down)} down: uv run newsrag stores up")


def gpu() -> Check:
    from newsrag.models.lock import probe

    record = probe()
    if record is not None:
        return Check("GPU", False, f"GPU lock held: {'; '.join(record.lines())}")
    out = subprocess.run(
        [config.settings.nvidia_smi_bin, "--query-compute-apps=pid,name", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.strip()
    return Check("GPU", not out, f"processes on the GPU: {out.replace(chr(10), '; ')}")


def disk() -> Check:
    root = Path(config.settings.raw_root)
    free = shutil.disk_usage(root).free / GB
    need = config.settings.preflight_min_free_gb
    return Check(
        f"disk free under {root} ({free:.0f} GB)", free >= need, f"free up to >= {need:g} GB"
    )


def baseline() -> Check:
    from newsrag.baseline import stores as bstores
    from newsrag.baseline.load import COUNT_KEYS, StoreSink

    states = bstores.ping_all(config.settings.stores_ping_timeout_s)
    down = [name for name, up in states.items() if not up]
    if down:
        return Check("baseline", False, f"{', '.join(down)} down: uv run newsrag baseline up")
    path = Path(config.settings.baseline_dir) / "manifest-loaded.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Check("baseline", False, f"no {path}: uv run newsrag baseline load (ticket 027)")
    expected = manifest.get("counts") or {}
    loaded = StoreSink().counts()
    bad = [k for k, src in COUNT_KEYS.items() if expected.get(src) != loaded.get(k)]
    todo = f"row counts differ from the bundle manifest ({', '.join(bad)}): reload the bundle"
    return Check("baseline", not bad, todo)


CHECKS: list[tuple[str, Callable[[], Check]]] = [
    ("pipeline.yaml models", pipeline_keys),
    ("OCR sign-off", ocr_signoff),
    ("HF_TOKEN", hf_token),
    ("stores", stores),
    ("GPU", gpu),
    ("disk", disk),
    ("baseline", baseline),
]


def run_checks(checks: list[tuple[str, Callable[[], Check]]] | None = None) -> list[Check]:
    out = []
    for name, fn in checks if checks is not None else CHECKS:
        try:
            out.append(fn())
        except Exception as exc:  # noqa: BLE001 - one failing check must not hide the others
            out.append(Check(name, False, f"check failed: {type(exc).__name__}: {exc}"))
    return out
