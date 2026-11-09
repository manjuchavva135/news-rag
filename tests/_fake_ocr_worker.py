"""A fake Surya layout worker for the CPU tests of ticket 007.

    python _fake_ocr_worker.py

It runs the real protocol loop (`newsrag/ocr/_protocol.py`, loaded by path as the real worker
does). Behaviour by image file name:

- `*crash*`  the worker exits with code 3 without answering
- `*hang*`   the worker never answers
- `*error*`  the request raises, so the reply is {"error": ...}

Regions come from the JSON file `$FAKE_OCR_LAYOUTS` ({image name: [region, ...]}, and
`"<name>:size": [w, h]` for the image size Surya saw), default two Text regions. Every request
is appended to `$FAKE_OCR_DIR/surya-requests.jsonl`. With `FAKE_OCR_CHILD=1` the worker starts
a `sleep` child in its own process group and writes its pid to `$FAKE_OCR_DIR/surya-child.pid`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

PROTOCOL = Path(__file__).resolve().parents[1] / "src" / "newsrag" / "ocr" / "_protocol.py"
DEFAULT_REGIONS = [
    {"bbox": [10, 10, 90, 40], "label": "Text", "position": 0, "confidence": 0.9},
    {"bbox": [10, 50, 90, 90], "label": "Text", "position": 1, "confidence": 0.8},
]


def _protocol():
    spec = importlib.util.spec_from_file_location("_fake_protocol", PROTOCOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _log(request: dict) -> None:
    folder = os.environ.get("FAKE_OCR_DIR")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        with open(Path(folder) / "surya-requests.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(request, ensure_ascii=False) + "\n")


def _behave(name: str) -> None:
    if "crash" in name:
        os._exit(3)
    if "hang" in name:
        time.sleep(3600)
    if "error" in name:
        raise ValueError(f"cannot read {name}")


def load():
    print("loading fake model (noise on stdout must not reach the client)", flush=True)
    if os.environ.get("FAKE_OCR_CHILD") == "1":
        child = subprocess.Popen(["sleep", "1000"])
        folder = Path(os.environ["FAKE_OCR_DIR"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "surya-child.pid").write_text(str(child.pid))
    layouts_file = os.environ.get("FAKE_OCR_LAYOUTS")

    def surya(request: dict) -> dict:
        _log(request)
        name = Path(request["image"]).name
        _behave(name)
        layouts = json.loads(Path(layouts_file).read_text()) if layouts_file else {}
        regions = layouts.get(name, DEFAULT_REGIONS)
        size = layouts.get(f"{name}:size", [100, 100])
        return {"regions": regions, "width": size[0], "height": size[1]}

    return surya


if __name__ == "__main__":
    _protocol().serve(load)
