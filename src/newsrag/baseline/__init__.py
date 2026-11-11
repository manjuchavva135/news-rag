"""The old pipeline as a baseline (ticket 027).

- `stores`: clients of the baseline Postgres / Qdrant / OpenSearch (`newsrag-baseline` project).
- `load`: stream the old stage-9 bundle into them (schema `old`, collection/index `chunks_old`).
- `pages`: map old page ids to new ones (`baseline-page-map.csv`).
- `search`: the `baseline` backend of `newsrag.search.search`.
- `compare`: the comparison set, the calibration of the hit rule, and the comparison itself.
- `cli`: `newsrag baseline ...`.

Importing this package touches neither the stores nor the GPU.
"""
