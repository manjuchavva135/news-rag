"""`newsrag ocr install` (FR-1): both OCR models, idempotently, at pinned versions.

- indic-ocr: the snapshot of `indic_ocr_repo` at `indic_ocr_revision` (pinned in pipeline.yaml
  the first time) goes into `hf_home`. Its text model runs on vLLM from `envs/vllm` (ticket
  006: `newsrag models install`), so no venv of its own: install writes indic-ocr's tokenizer
  files to `tokenizer_dir()` and checks in `envs/vllm` that they encode and decode Telugu
  exactly, and that indic-ocr's prompts still match ours.
- Surya: a Python 3.12 venv `envs/surya/` with surya-ocr at `surya_version` (the newest release
  the first time, then pinned), and its layout + reading-order models in `hf_home`.

A venv that already has the pinned version is kept as it is.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

from newsrag import config
from newsrag.config import set_pipeline_key
from newsrag.ocr.client import SURYA_WORKER

LICENCE_MESSAGE = (
    "cannot download bodhan-ai/indic-ocr: accept the licence on huggingface.co "
    "and set HF_TOKEN in .env"
)
MARKER = ".newsrag-ocr.json"  # in each venv: what `ocr install` put there

TORCH_REPORT = (
    "import json, torch; "
    "print(json.dumps({'cuda_available': torch.cuda.is_available(), "
    "'cuda': torch.version.cuda, 'torch': torch.__version__}))"
)


class InstallError(RuntimeError):
    pass


class LicenceError(InstallError):
    """No token, or the token may not read the gated repo. `ocr install` exits 2."""


@dataclass
class EnvReport:
    name: str
    kept: bool
    version: str
    cuda_available: bool
    cuda: str | None

    def line(self) -> str:
        state = "kept" if self.kept else "installed"
        return (
            f"{self.name}: {state} {self.version}; torch cuda={self.cuda} "
            f"available={self.cuda_available}"
        )


# --- helpers ---


def _uv() -> str:
    uv = os.environ.get("UV") or shutil.which(config.settings.uv_bin)
    if not uv:
        raise InstallError(f"uv not found (setting uv_bin = {config.settings.uv_bin!r})")
    return uv


def _env(venv: Path | None = None, uv: str | None = None) -> dict[str, str]:
    env = config.settings.subprocess_env()
    env.pop("VIRTUAL_ENV", None)  # never install into the main venv by accident
    path = env.get("PATH", "")
    if uv is not None:
        path = os.pathsep.join([str(Path(uv).parent), path])
    if venv is not None:
        env["VIRTUAL_ENV"] = str(venv)
        env["PYTHON"] = str(venv / "bin" / "python")
        path = os.pathsep.join([str(venv / "bin"), path])
    env["PATH"] = path
    return env


def _run(cmd: list[str], env: dict[str, str], cwd: Path | None = None) -> None:
    if subprocess.run(cmd, env=env, cwd=cwd, check=False).returncode != 0:
        raise InstallError(f"failed: {' '.join(cmd)}")


def _python(venv: Path) -> Path:
    return venv / "bin" / "python"


def _read_marker(venv: Path) -> dict:
    try:
        return json.loads((venv / MARKER).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _write_marker(venv: Path, **values: str) -> None:
    (venv / MARKER).write_text(json.dumps(values, indent=1) + "\n", encoding="utf-8")


def torch_report(venv: Path) -> dict:
    """{'cuda_available', 'cuda', 'torch'} of the venv's torch; {} when it cannot import."""
    py = _python(venv)
    if not py.exists():
        return {}
    result = subprocess.run(
        [str(py), "-I", "-c", TORCH_REPORT],
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def cuda_ok(cuda: str | None) -> bool:
    if not cuda:
        return False
    try:
        return Version(cuda) >= Version(config.settings.ocr_min_cuda)
    except InvalidVersion:
        return False


def _ensure_new_torch(venv: Path, uv: str) -> None:
    """Reinstall torch + torchvision for the driver's CUDA when the venv's is too old."""
    if cuda_ok(torch_report(venv).get("cuda")):
        return
    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(_python(venv)),
            "--reinstall-package",
            "torch",
            "--reinstall-package",
            "torchvision",
            "torch",
            "torchvision",
            f"--torch-backend={config.settings.ocr_torch_backend}",
        ],
        _env(uv=uv),
    )
    cuda = torch_report(venv).get("cuda")
    if not cuda_ok(cuda):
        raise InstallError(
            f"{venv}: torch is built for CUDA {cuda}; need >= {config.settings.ocr_min_cuda}"
        )


def _report(name: str, venv: Path, kept: bool, version: str) -> EnvReport:
    torch = torch_report(venv)
    return EnvReport(name, kept, version, bool(torch.get("cuda_available")), torch.get("cuda"))


# --- indic-ocr ---


def _hub():
    from newsrag.models.hub import hub_args

    return hub_args()


def _licence_error(exc: Exception) -> bool:
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

    if isinstance(exc, GatedRepoError | RepositoryNotFoundError):
        return True
    if isinstance(exc, HfHubHTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status in (401, 403)
    return False


def indic_snapshot(revision: str | None = None) -> Path | None:
    """The cached indic-ocr snapshot at the pinned revision, or None (never downloads)."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    revision = revision or config.settings.indic_ocr_revision
    if not revision:
        return None
    cache_dir, token = _hub()
    try:
        path = Path(
            snapshot_download(
                config.settings.indic_ocr_repo,
                revision=revision,
                cache_dir=cache_dir,
                token=token,
                local_files_only=True,
            )
        )
    except (LocalEntryNotFoundError, FileNotFoundError, ValueError):
        return None
    required = ("idp_contract.py", "weights/ocr/config.json", "weights/ocr/tokenizer.json")
    return path if all((path / rel).exists() for rel in required) else None


def download_indic() -> tuple[Path, str, bool]:
    """(snapshot dir, revision, downloaded?). Pins the revision on first use."""
    from huggingface_hub import HfApi, snapshot_download

    s = config.settings
    if s.hf_token is None:
        raise LicenceError(LICENCE_MESSAGE)
    cache_dir, token = _hub()
    revision = s.indic_ocr_revision
    if revision:
        cached = indic_snapshot(revision)
        if cached is not None:
            return cached, revision, False
    try:
        if not revision:
            revision = HfApi().model_info(s.indic_ocr_repo, token=token).sha
            if not revision:
                raise InstallError(f"{s.indic_ocr_repo}: the Hub gave no commit hash")
        path = Path(
            snapshot_download(
                s.indic_ocr_repo,
                revision=revision,
                cache_dir=cache_dir,
                token=token,
                max_workers=8,
            )
        )
    except Exception as exc:
        if _licence_error(exc):
            raise LicenceError(LICENCE_MESSAGE) from exc
        raise
    if s.indic_ocr_revision != revision:
        set_pipeline_key("indic_ocr_revision", revision)
    return path, revision, True


VLLM_MISSING = "vLLM is not installed: run uv run newsrag models install"

# Run in envs/vllm: load the tokenizer folder the way vLLM does and check an exact round trip.
TOKENIZER_CHECK = r"""
import sys
from tokenizers import Tokenizer
from transformers import AutoTokenizer
folder, text = sys.argv[1], sys.argv[2]
raw = Tokenizer.from_file(folder + "/tokenizer.json")
tok = AutoTokenizer.from_pretrained(folder)
ids = raw.encode(text, add_special_tokens=False).ids
got = tok(text, add_special_tokens=False)["input_ids"]
back = tok.decode(ids, skip_special_tokens=True)
print(type(tok).__name__, len(ids), len(got))
sys.exit(0 if got == ids and back == text else 1)
"""
TOKENIZER_SAMPLE = "రఘురామకు సుప్రీంకోర్టులో షాక్ hello ₹ 6.50"


def snapshot_prompts(snapshot: Path) -> dict[str, str]:
    """indic-ocr's text and HTML-table prompts, read from idp_contract.py with `ast` (the
    snapshot's code is never run here)."""
    import ast

    tree = ast.parse((snapshot / "idp_contract.py").read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        name = getattr(node.targets[0], "id", None)
        if name == "TEXT_PROMPT" and isinstance(node.value, ast.Constant):
            found["text"] = node.value.value
        elif name == "TABLE_PROMPTS" and isinstance(node.value, ast.Dict):
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                if getattr(key, "attr", None) == "HTML" and isinstance(value, ast.Constant):
                    found["table"] = value.value
    return found


def write_tokenizer_dir(snapshot: Path, revision: str) -> Path:
    """Copy indic-ocr's tokenizer files (no config.json beside them, so transformers keeps
    tokenizer.json as it is) to tokenizer_dir(revision)."""
    from newsrag.ocr.text import TOKENIZER_FILES, tokenizer_dir

    folder = tokenizer_dir(revision)
    folder.mkdir(parents=True, exist_ok=True)
    for name in TOKENIZER_FILES:
        src = (snapshot / "weights" / "ocr" / name).resolve()
        dst = folder / name
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
    return folder


def check_tokenizer_dir(folder: Path) -> str:
    """Load `folder` with envs/vllm's transformers; raise unless Telugu round-trips exactly."""
    py = _python(Path(config.settings.vllm_env_dir))
    if not py.exists():
        raise InstallError(VLLM_MISSING)
    result = subprocess.run(
        [str(py), "-I", "-c", TOKENIZER_CHECK, str(folder), TOKENIZER_SAMPLE],
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    if result.returncode != 0:
        raise InstallError(
            f"indic-ocr tokenizer in {folder} does not round-trip in envs/vllm:\n"
            f"{(result.stdout + result.stderr).strip()[-2000:]}"
        )
    return result.stdout.strip().splitlines()[-1]


def install_indic() -> EnvReport:
    from newsrag.ocr.text import PROMPTS

    snapshot, revision, downloaded = download_indic()
    prompts = snapshot_prompts(snapshot)
    if prompts != PROMPTS:
        raise InstallError(
            f"indic-ocr {revision[:8]} changed its prompts; update newsrag/ocr/text.py:\n"
            f"{json.dumps(prompts, indent=1)}"
        )
    folder = write_tokenizer_dir(snapshot, revision)
    check_tokenizer_dir(folder)
    # The torch reported is envs/vllm's, where the text model runs.
    return _report("indic-ocr", Path(config.settings.vllm_env_dir), not downloaded, revision)


# --- Surya ---


def surya_installed(venv: Path | None = None) -> str | None:
    """surya-ocr's version in the venv, or None."""
    py = _python(Path(venv or config.settings.surya_env_dir))
    if not py.exists():
        return None
    result = subprocess.run(
        [str(py), "-I", "-c", "import importlib.metadata as m; print(m.version('surya-ocr'))"],
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def _surya_download(venv: Path, offline: bool) -> bool:
    s = config.settings
    env = _env()
    if offline:
        env["HF_HUB_OFFLINE"] = "1"
    cmd = [
        str(_python(venv)),
        "-I",
        str(SURYA_WORKER),
        "--download",
        "--checkpoint",
        s.surya_layout_checkpoint,
        "--order-checkpoint",
        s.surya_order_checkpoint,
    ]
    result = subprocess.run(cmd, env=env, check=False, capture_output=offline)
    return result.returncode == 0


def install_surya() -> EnvReport:
    s = config.settings
    venv = Path(s.surya_env_dir)
    pinned = s.surya_version
    have = surya_installed(venv)
    kept = bool(pinned) and have == pinned and cuda_ok(torch_report(venv).get("cuda"))
    if not kept:
        uv = _uv()
        spec = f"surya-ocr=={pinned}" if pinned else "surya-ocr"
        _run([uv, "venv", "--clear", "--python", "3.12", str(venv)], _env(uv=uv))
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(_python(venv)),
                spec,
                f"--torch-backend={s.ocr_torch_backend}",
            ],
            _env(uv=uv),
        )
        _ensure_new_torch(venv, uv)
        have = surya_installed(venv)
        if not have:
            raise InstallError(f"surya-ocr is not importable in {venv}")
        if pinned != have:
            set_pipeline_key("surya_version", have)
        _write_marker(venv, surya_version=have)
    # The layout + order models: from the cache when present, else downloaded into hf_home.
    if not _surya_download(venv, offline=True) and not _surya_download(venv, offline=False):
        raise InstallError("could not download Surya's layout models")
    return _report("surya", venv, kept, have or "")


def install() -> list[EnvReport]:
    """Both venvs and models; one report per venv."""
    return [install_indic(), install_surya()]
