"""`newsrag models`: install vLLM, download, serve, smoke-test, fit-check and lock status."""

from __future__ import annotations

import signal
import time
from typing import Annotated

import typer

from newsrag import config

app = typer.Typer(help="Local models on the GPU (one at a time).", no_args_is_help=True)

SMOKE_IMAGE_SIZE = (400, 120)
SMOKE_IMAGE_TEXT = "NEWS 2026"
SMOKE_VLM_PROMPT = "Read the text in the image. Reply with the text only."
SMOKE_LLM_PROMPT = "Reply with the word OK."
SMOKE_MAX_TOKENS = 64


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def _profile(name: str):
    from newsrag.models.profiles import UnknownProfileError, get_profile

    try:
        return get_profile(name)
    except UnknownProfileError as exc:
        raise _fail(str(exc), 2) from None


def _echo(message: str) -> None:
    typer.echo(message)


def _raise_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


@app.command()
def install() -> None:
    """Create envs/vllm with vLLM (kept when it already has a current vLLM)."""
    from newsrag.models.install import InstallError
    from newsrag.models.install import install as do_install

    try:
        kept, report = do_install()
    except InstallError as exc:
        raise _fail(str(exc), 1) from None
    typer.echo(f"{'kept' if kept else 'installed'} {config.settings.vllm_env_dir}")
    typer.echo(report)


@app.command()
def download(profile: str) -> None:
    """Download a profile's weights into hf_home."""
    from newsrag.models import hub

    p = _profile(profile)
    path = hub.download(p)
    typer.echo(f"{profile}: {path}")


@app.command()
def serve(
    profile: str,
    port: Annotated[
        int | None, typer.Option(help="first port to try (default: setting model_port, 8100)")
    ] = None,
) -> None:
    """Serve a vLLM profile on 127.0.0.1 under the GPU lock until Ctrl-C / SIGTERM."""
    from newsrag.models import fit, hub
    from newsrag.models.lock import gpu_lock
    from newsrag.models.serve import ModelStartError, run_server

    p = _profile(profile)
    if not p.is_vllm:
        raise _fail(f"{profile} is an {p.kind} profile: only vlm/llm profiles are served", 2)
    hub.download(p)
    try:
        fit.require_fit(p)
    except fit.ModelDoesNotFitError as exc:
        raise _fail(str(exc), 2) from None

    previous = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        holder = f"models serve {profile}"
        with (
            gpu_lock(holder, profile=profile, kind="serve", report=_echo) as lk,
            run_server(p, port, lock=lk) as server,
        ):
            typer.echo(f"serving {profile} at {server.base_url}")
            typer.echo(f"log: {server.log_path}")
            code = server.proc.wait()
            typer.echo(f"vllm exited with code {code}; see {server.log_path}", err=True)
            raise typer.Exit(1)
    except ModelStartError as exc:
        raise _fail(str(exc), 1) from None
    except KeyboardInterrupt:
        typer.echo(f"stopped {profile}")
    finally:
        signal.signal(signal.SIGTERM, previous)


def smoke_image():
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", SMOKE_IMAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    left, top, right, bottom = draw.textbbox((0, 0), SMOKE_IMAGE_TEXT, font=font)
    x = (SMOKE_IMAGE_SIZE[0] - (right - left)) // 2 - left
    y = (SMOKE_IMAGE_SIZE[1] - (bottom - top)) // 2 - top
    draw.text((x, y), SMOKE_IMAGE_TEXT, fill="black", font=font)
    return image


@app.command()
def smoke(profile: str) -> None:
    """Open a profile with use() and send one fixed request; print the reply and latency."""
    from newsrag.models import ModelDoesNotFitError, ModelStartError, use

    p = _profile(profile)
    t_open = time.monotonic()
    try:
        with use(profile, report=_echo) as model:
            opened_s = time.monotonic() - t_open
            t0 = time.monotonic()
            if p.kind == "vlm":
                reply = model.chat(
                    SMOKE_VLM_PROMPT, images=[smoke_image()], max_tokens=SMOKE_MAX_TOKENS
                )
            elif p.kind == "llm":
                reply = model.chat(SMOKE_LLM_PROMPT, max_tokens=SMOKE_MAX_TOKENS)
            else:
                reply = f"dim={model.embed(['test']).shape[1]}"
            latency = time.monotonic() - t0
    except ModelDoesNotFitError as exc:
        raise _fail(str(exc), 2) from None
    except ModelStartError as exc:
        raise _fail(str(exc), 1) from None
    typer.echo(f"{profile} reply: {reply.strip()}")
    typer.echo(f"{profile} latency={latency:.2f}s (open {opened_s:.0f}s)")


@app.command()
def status() -> None:
    """Who holds the GPU lock, or `GPU free`."""
    from newsrag.models.lock import probe

    record = probe()
    if record is None:
        typer.echo("GPU free")
        return
    for line in record.lines():
        typer.echo(line)


@app.command(name="fit")
def fit_cmd(profile: Annotated[str | None, typer.Argument()] = None) -> None:
    """Estimate whether each profile (or one) fits the GPU; exit 1 if any does not."""
    from newsrag.models import fit
    from newsrag.models.profiles import load_profiles

    profiles = [_profile(profile)] if profile else list(load_profiles().values())
    all_fit = True
    for p in profiles:
        result = fit.estimate(p)
        all_fit &= result.fits
        typer.echo(result.line())
    if not all_fit:
        raise typer.Exit(1)
