"""Ticket 012 live checks: the real model on the 009 page. Needs the GPU and the `ocr_final`
artifacts of the dev sample. Writes nothing to the ledger (the stage itself is run by eval E-2)."""

from __future__ import annotations

import pytest

from newsrag import config

pytestmark = [pytest.mark.gpu, pytest.mark.data]

PAGE = "2024-12-06_GNOCLT_main_16"


def test_009_page_grouped_with_the_real_model():
    from newsrag.grouping import input as gin
    from newsrag.models import use
    from newsrag.stages.group import group_page, make_create, send

    data = gin.load_ocr_final(PAGE)
    with use(config.settings.group_model) as client:
        artifact = group_page(data, send(make_create(client)), config.settings.group_model)

    block_ids = {b["block_id"] for b in data["blocks"]}
    used = [b["block_id"] for a in artifact["articles"] for b in a["blocks"]]
    used += artifact["unassigned"]
    assert set(used) == block_ids and len(used) == len(set(used))
    first = next(
        a for a in artifact["articles"] if any(b["block_id"] == "b007" for b in a["blocks"])
    )
    second = next(
        a for a in artifact["articles"] if any(b["block_id"] == "b013" for b in a["blocks"])
    )
    ids1 = [b["block_id"] for b in first["blocks"]]
    ids2 = [b["block_id"] for b in second["blocks"]]
    assert {"b005", "b004", "b007", "b008"} <= set(ids1)  # b012 repeats the headline
    assert ids1.index("b007") < ids1.index("b008")
    assert {"b009", "b010", "b014", "b013", "b015", "b016", "b011"} <= set(ids2)
