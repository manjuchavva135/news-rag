"""Ticket 013 live checks: the real judge model on the 009 page. Needs the GPU, the `ocr_final`
artifacts and the `group` artifact of the dev sample. Writes nothing to the ledger (the stages
are run by eval E-2)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.gpu, pytest.mark.data]

PAGE = "2024-12-06_GNOCLT_main_16"


def test_009_page_judged_and_a_merge_scores_lower():
    from newsrag.grouping import input as gin
    from newsrag.grouping import judge as jg

    group_path = gin.done_artifact("group", PAGE)
    assert group_path is not None, "run `stage run group` on the dev sample first"
    grouping = gin.load_artifact(group_path)
    data = gin.load_ocr_final(PAGE)

    good = jg.judge_grouping(data, grouping)  # opens the judge model
    judged = [a for a in grouping["articles"] if a["kind"] not in ("masthead", "ad_commercial")]
    assert good["score"] is not None and 0 <= good["score"] <= 1
    assert set(good["verdicts"]) == {a["article_id"] for a in judged}
    assert set(good["verdicts"].values()) <= set(jg.VERDICTS) | {jg.MISSING}

    merged = jg.merge_two_longest(grouping)
    assert merged is not None
    assert jg.judge_grouping(data, merged)["score"] < good["score"]
