"""The `newsrag` command line. Only registers sub-apps; each module owns its own `cli.py`."""

import typer

from newsrag import config_cli
from newsrag.ingest import cli as ingest_cli
from newsrag.ledger import cli as ledger_cli
from newsrag.stages import cli as stages_cli
from newsrag.stores import cli as stores_cli

app = typer.Typer(
    name="newsrag",
    help="Sakshi newspaper RAG: process newspaper archives into search stores.",
    no_args_is_help=True,
)

# (sub-app, group name). Later tickets add one line each.
SUB_APPS: list[tuple[typer.Typer, str]] = [
    (config_cli.app, "config"),
    (stores_cli.app, "stores"),
    (stores_cli.db_app, "db"),
    (ledger_cli.app, "ledger"),
    (stages_cli.app, "stage"),
    (ingest_cli.app, "ingest"),
]

for sub_app, name in SUB_APPS:
    app.add_typer(sub_app, name=name)
