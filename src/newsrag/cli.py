"""The `newsrag` command line. Only registers sub-apps; each module owns its own `cli.py`."""

import typer

from newsrag import config_cli

app = typer.Typer(
    name="newsrag",
    help="Sakshi newspaper RAG: process newspaper archives into search stores.",
    no_args_is_help=True,
)

# (sub-app, group name). Later tickets add one line each.
SUB_APPS: list[tuple[typer.Typer, str]] = [
    (config_cli.app, "config"),
]

for sub_app, name in SUB_APPS:
    app.add_typer(sub_app, name=name)
