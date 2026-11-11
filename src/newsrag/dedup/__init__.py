"""Cross-edition story dedup (ticket 017): the same story printed in several editions of one
print date becomes one story that lists every printing.

- `minhash`: text normalisation, character shingles, MinHash + LSH candidate pairs.
- `units`: the articles and standalone items of a date as dedup units.
- `cluster`: duplicate pairs (MinHash and embedding cosine), clusters, the story record.
- `ids`: stable story ids in `ledger.story_ids`.
- `load`: reading the done `dedup` artifacts. `verify`, `cli`: the quality check and statistics.

The stage is `newsrag.stages.dedup`. Importing this package touches neither the database nor
the GPU.
"""
