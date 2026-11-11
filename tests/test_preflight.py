"""`newsrag preflight` (ticket 031 FR-1) with every outside check faked."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from newsrag import config, preflight
from newsrag.cli import app


def _settings(monkeypatch, **update):
    monkeypatch.setattr(config, "settings", config.settings.model_copy(update=update))


def test_pipeline_keys(tmp_path, monkeypatch):
    path = tmp_path / "pipeline.yaml"
    path.write_text("group_model: a\njudge_model: a\nembed_model: e\n")
    _settings(monkeypatch, pipeline_file=path)
    check = preflight.pipeline_keys()
    assert not check.ok
    assert "enrich_model, answer_model, answer_judge_model" in check.todo
    path.write_text("".join(f"{k}: x\n" for k in preflight.PIPELINE_KEYS))
    assert preflight.pipeline_keys().ok


@pytest.mark.parametrize(
    ("failing", "rules_yaml", "key", "ok"),
    [
        ([], "rules: []\n", None, True),
        ([{"years": [2024], "sections": ["main"], "note": "faded"}], "rules: []\n", None, False),
        (
            [{"years": [2024], "sections": ["main"], "note": "faded"}],
            "rules:\n  - {years: [2024], sections: [main]}\n",
            None,
            False,  # rules non-empty but no SARVAM_API_KEY
        ),
        (
            [{"years": [2024], "sections": ["main"], "note": "faded"}],
            "rules:\n  - {sections: [main], years: [2024]}\n",
            "k",
            True,
        ),
    ],
)
def test_ocr_signoff(tmp_path, monkeypatch, failing, rules_yaml, key, ok):
    check_dir = tmp_path / "ocr-check"
    check_dir.mkdir()
    signoff = {"name": "reader", "signed_at": "2026-11-09T00:00:00+00:00"}
    (check_dir / "verdicts.json").write_text(
        json.dumps({"pages": {}, "signoff": {**signoff, "failing_page_types": failing}})
    )
    rules = tmp_path / "ocr_fallback.yaml"
    rules.write_text(rules_yaml)
    _settings(monkeypatch, ocr_check_dir=check_dir, ocr_fallback_file=rules, sarvam_api_key=key)
    assert preflight.ocr_signoff().ok is ok


def test_unsigned_ocr_check(tmp_path, monkeypatch):
    _settings(monkeypatch, ocr_check_dir=tmp_path)
    assert not preflight.ocr_signoff().ok


def test_disk(tmp_path, monkeypatch):
    _settings(monkeypatch, raw_root=tmp_path, preflight_min_free_gb=1e9)
    assert not preflight.disk().ok
    _settings(monkeypatch, raw_root=tmp_path, preflight_min_free_gb=0)
    assert preflight.disk().ok


def test_raising_check_is_missing():
    def broken():
        raise RuntimeError("store down")

    (check,) = preflight.run_checks([("stores", broken)])
    assert not check.ok
    assert check.line() == "stores: MISSING check failed: RuntimeError: store down"


def test_cli_exit_codes(monkeypatch):
    good = [("a", lambda: preflight.Check("a", True))]
    bad = [*good, ("b", lambda: preflight.Check("b", False, "do b"))]
    monkeypatch.setattr(preflight, "CHECKS", good)
    result = CliRunner().invoke(app, ["preflight"])
    assert result.exit_code == 0 and result.output == "a: ok\n"
    monkeypatch.setattr(preflight, "CHECKS", bad)
    result = CliRunner().invoke(app, ["preflight"])
    assert result.exit_code == 1 and result.output == "a: ok\nb: MISSING do b\n"
