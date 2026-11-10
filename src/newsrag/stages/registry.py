"""Every pipeline stage, by name.

Later tickets add one line each: ``REGISTRY["ocr"] = OcrStage()``. Importing this module must not
touch the GPU, the network or files that may be missing: stages compute versions that depend on
installed files lazily.
"""

from __future__ import annotations

from newsrag.stages.base import Stage
from newsrag.stages.folio import FolioStage
from newsrag.stages.group import GroupStage
from newsrag.stages.ocr import OcrStage
from newsrag.stages.ocr_final import OcrFinalStage
from newsrag.stages.ocr_split import OcrSplitStage
from newsrag.stages.render import RenderStage

REGISTRY: dict[str, Stage] = {}
REGISTRY["render"] = RenderStage()
REGISTRY["ocr"] = OcrStage()
REGISTRY["ocr_split"] = OcrSplitStage()
REGISTRY["ocr_final"] = OcrFinalStage()
REGISTRY["folio"] = FolioStage()
REGISTRY["group"] = GroupStage()
