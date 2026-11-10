"""The `newsrag` command line. Only registers sub-apps; each module owns its own `cli.py`."""

import typer

from newsrag import config_cli
from newsrag.editions import cli as editions_cli
from newsrag.geo import cli as geo_cli
from newsrag.grouping import cli as group_cli
from newsrag.ingest import cli as ingest_cli
from newsrag.ledger import cli as ledger_cli
from newsrag.models import cli as models_cli
from newsrag.ocr import cli as ocr_cli
from newsrag.render import cli as render_cli
from newsrag.sample import cli as sample_cli
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
    (render_cli.app, "render"),
    (sample_cli.app, "sample"),
    (models_cli.app, "models"),
    (ocr_cli.app, "ocr"),
    (geo_cli.app, "geo"),
    (editions_cli.app, "editions"),
    (group_cli.app, "group"),
]

for sub_app, name in SUB_APPS:
    app.add_typer(sub_app, name=name)
