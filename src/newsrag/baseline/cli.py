"""`newsrag baseline up|down|status|load|map-pages|build-set|calibrate|compare` (ticket 027)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer

from newsrag import config

app = typer.Typer(help="The old pipeline's bundle as a baseline.", no_args_is_help=True)

COMPOSE_PROJECT = "newsrag-baseline"


def _ping_all() -> dict[str, bool]:
    from newsrag.baseline import stores

    return stores.ping_all(config.settings.stores_ping_timeout_s)


@app.command()
def up() -> None:
    """Start the baseline stores and wait until all three answer."""
    from newsrag.stores import cli as stores_cli

    stores_cli.compose_up(
        COMPOSE_PROJECT, config.settings.baseline_compose_file, _ping_all, "baseline stores"
    )


@app.command()
def down() -> None:
    """Stop the baseline stores. Volumes and data are kept."""
    from newsrag.stores import cli as stores_cli

    stores_cli.compose_stop(
        COMPOSE_PROJECT, config.settings.baseline_compose_file, "baseline stores"
    )


@app.command()
def status() -> None:
    """Print `<store>: ok|down` for each baseline store; exit 0 only when all are ok."""
    from newsrag.stores import cli as stores_cli

    stores_cli.report_status(_ping_all())


def _fail(message: str) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(1)


def _write_page_map() -> None:
    from newsrag.baseline import load, pages

    mapping = pages.build_page_map(load.old_page_ids())
    typer.echo(f"page map: {pages.summary(mapping)} -> {config.settings.baseline_page_map_file}")


@app.command()
def load(
    bundle: Annotated[Path, typer.Argument(help="The old stage-9 bundle (rag-snapshot-*.tar.gz).")],
    force: Annotated[bool, typer.Option(help="Drop an existing baseline and reload.")] = False,
) -> None:
    """Stream the bundle into the baseline stores; exit 1 when the counts don't match."""
    from newsrag.baseline import load as load_mod

    start = time.monotonic()
    try:
        result = load_mod.load_bundle(bundle, force=force, echo=typer.echo)
    except load_mod.LoadError as e:
        raise _fail(str(e)) from None
    expected = result["manifest"].get("counts") or {}
    typer.echo(f"manifest counts: {json.dumps(expected, sort_keys=True)}")
    typer.echo(f"loaded counts:   {json.dumps(result['loaded'], sort_keys=True)}")
    if result["skipped"]:
        typer.echo(f"skipped: {json.dumps(result['skipped'], sort_keys=True)}")
    typer.echo(f"took {time.monotonic() - start:.0f} s")
    if result["mismatches"]:
        for key, v in sorted(result["mismatches"].items()):
            typer.echo(f"mismatch {key}: manifest={v['manifest']} loaded={v['loaded']}", err=True)
        raise typer.Exit(1)
    try:
        _write_page_map()
    except Exception as e:  # noqa: BLE001 - the load itself succeeded
        typer.echo(f"page map not written ({e}): run 'newsrag baseline map-pages'", err=True)


@app.command("map-pages")
def map_pages() -> None:
    """Map every old page id to the new page ids of the ledger (baseline-page-map.csv)."""
    _write_page_map()


@app.command("build-set")
def build_set(
    n: Annotated[int | None, typer.Option(help="Questions (default baseline_set_n).")] = None,
    seed: Annotated[int | None, typer.Option(help="Default baseline_set_seed.")] = None,
    out: Annotated[Path | None, typer.Option(help="Default baseline_set_file.")] = None,
) -> None:
    """The comparison set: questions from new stories printed on pages the old pipeline has."""
    from newsrag.baseline import compare

    try:
        _write_page_map()
        summary = compare.build_set(n=n, seed=seed, out=out, echo=typer.echo)
    except compare.CompareError as e:
        raise _fail(str(e)) from None
    typer.echo(
        f"wrote {summary['n']} questions to {summary['out']} "
        f"(te={summary['te']} replaced={summary['replaced']} "
        f"stories={summary['stories_available']})"
    )


@app.command()
def calibrate(
    n: Annotated[int | None, typer.Option(help="Stories (default baseline_calibration_n).")] = None,
    seed: Annotated[int | None, typer.Option(help="Default baseline_calibration_seed.")] = None,
) -> None:
    """Share of sampled stories that some old chunk on their page is credited for."""
    from newsrag.baseline import compare

    try:
        report = compare.calibrate(n=n, seed=seed)
    except compare.CompareError as e:
        raise _fail(str(e)) from None
    typer.echo(f"calibration={report['calibration']:.1f} ({report['passed']}/{report['n']})")


@app.command("compare")
def compare_cmd() -> None:
    """The retrieval test on baseline and on new (restricted to the mapped pages)."""
    from newsrag import search
    from newsrag.baseline import compare

    try:
        result = compare.compare()
    except (compare.CompareError, search.StoreNotConfigured, ValueError) as e:
        raise _fail(str(e)) from None
    typer.echo(compare.compare_line(result))
