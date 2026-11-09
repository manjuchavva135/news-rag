"""`newsrag ocr fallback`: the Sarvam fallback rules of stage `ocr_final` (ticket 010)."""

from __future__ import annotations

import typer

app = typer.Typer(help="Sarvam fallback rules (ocr_fallback_file).", no_args_is_help=True)


@app.command()
def preview() -> None:
    """Print `<year>\\t<section>\\t<count>` of the pages the current rules match (no API call)."""
    from newsrag.ocr import fallback

    try:
        rules = fallback.load_rules()
    except fallback.RulesError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    if not rules:
        typer.echo("no ocr_fallback rules: nothing is sent to Sarvam", err=True)
        return
    for year, section, count in fallback.preview_counts(fallback.registered_pages(), rules):
        typer.echo(f"{year}\t{section}\t{count}")
