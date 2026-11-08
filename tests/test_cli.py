from typer.testing import CliRunner

from newsrag.cli import app

runner = CliRunner()


def test_help_lists_config():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "config" in result.output


def test_config_show_sorted_and_masked(clean_env):
    clean_env.setenv("HF_TOKEN", "hf_secret123")
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    names = [line.split(" = ", 1)[0] for line in lines]
    assert names == sorted(names)
    assert "hf_token = ***" in lines
    assert "sarvam_api_key = (unset)" in lines
    assert "render_dpi = 300" in lines
    assert "hf_secret123" not in result.output
