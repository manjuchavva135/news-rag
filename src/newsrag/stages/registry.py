"""Every pipeline stage, by name.

Later tickets add one line each: ``REGISTRY["ocr"] = OcrStage()``. Importing this module must not
touch the GPU, the network or files that may be missing: stages compute versions that depend on
installed files lazily.
"""

from __future__ import annotations

from newsrag.stages.base import Stage

REGISTRY: dict[str, Stage] = {}
