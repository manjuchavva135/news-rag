"""Surya layout worker (runs in `envs/surya/`; stdlib + surya only, never imports newsrag).

    python -I surya_worker.py [--device cuda] [--threshold 0.4] [--checkpoint hf://...]
                              [--order-checkpoint hf://...]
    python -I surya_worker.py --labels      # Surya's layout labels, one per line, then exit
    python -I surya_worker.py --download    # fetch the layout + order models, then exit

Request  {"op": "layout", "image": "/path/page.png"}
Reply    {"regions": [{"bbox": [x1, y1, x2, y2], "label", "position", "confidence"}],
          "width", "height"}   (regions sorted by Surya's reading order)
Errors   {"error": "<message>"}; the worker keeps running.

Uses Surya's rf-detr layout detector with its learned reading-order head (surya-ocr 0.23:
`surya.fast_layout.server.LayoutEngine`), loaded in this process. Boxes come back in pixels of
the image as given.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path


def _protocol():
    path = Path(__file__).with_name("_protocol.py")
    spec = importlib.util.spec_from_file_location("_newsrag_ocr_protocol", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure(args) -> None:
    # Surya reads its settings from the environment of its own process.
    os.environ["FAST_DETECTOR_DEVICE"] = args.device
    os.environ["FAST_LAYOUT_MODEL_CHECKPOINT"] = args.checkpoint
    os.environ["FAST_ORDER_MODEL_CHECKPOINT"] = args.order_checkpoint
    os.environ["FAST_LAYOUT_USE_ORDER"] = "true"


def labels() -> list[str]:
    from surya.layout.label import LAYOUT_PRED_RELABEL

    return sorted(set(LAYOUT_PRED_RELABEL.values()))


def download(args) -> None:
    from surya.common.rfdetr_torch import resolve_model_dir

    for checkpoint in (args.checkpoint, args.order_checkpoint):
        print(resolve_model_dir(checkpoint), flush=True)


def load(args):
    from PIL import Image
    from surya.fast_layout.server import LayoutEngine

    Image.MAX_IMAGE_PIXELS = None  # newspaper pages at 300 DPI exceed PIL's bomb guard
    engine = LayoutEngine(checkpoint=args.checkpoint)
    if hasattr(engine, "_load_order"):  # load the reading-order head now, not on page 1
        engine._load_order()
    params = {"use_order": True, "threshold": args.threshold}

    def handle(request: dict) -> dict:
        if request.get("op") != "layout":
            raise ValueError(f"unknown op {request.get('op')!r}")
        with Image.open(request["image"]) as img:
            image = img.convert("RGB")
        result = engine.run_batch([image], [params])[0]
        if getattr(result, "error", False):
            raise RuntimeError("surya layout reported an error for this page")
        regions = []
        for box in result.bboxes:
            xs = [p[0] for p in box.polygon]
            ys = [p[1] for p in box.polygon]
            regions.append(
                {
                    "bbox": [min(xs), min(ys), max(xs), max(ys)],
                    "label": box.label,
                    "position": int(box.position),
                    "confidence": float(box.confidence or 0.0),
                }
            )
        regions.sort(key=lambda r: r["position"])
        return {"regions": regions, "width": image.width, "height": image.height}

    return handle


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--threshold", type=float, default=0.4)
    ap.add_argument("--checkpoint", default="hf://datalab-to/surya_layout2")
    ap.add_argument("--order-checkpoint", default="hf://datalab-to/surya_layout2/order")
    ap.add_argument("--labels", action="store_true")
    ap.add_argument("--download", action="store_true")
    args = ap.parse_args(argv)
    _configure(args)
    if args.labels:
        print("\n".join(labels()))
        return
    if args.download:
        download(args)
        return
    _protocol().serve(lambda: load(args))


if __name__ == "__main__":
    main(sys.argv[1:])
