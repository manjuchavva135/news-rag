"""The structured warehouse in Postgres (ticket 024): stories, printings, chunks, entities, places,
facts and the active generation, in the schema `settings.warehouse_schema`.

- `db`: connections (search_path = the warehouse schema) and migrations.
- `rows`: the rows of one print date, built from plain data (pure).
- `write`: replacing the rows of one print date in one transaction.
- `cli`: `newsrag warehouse init`.

Importing this package touches neither the database nor the network.
"""
