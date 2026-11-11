"""Generations: activating one, listing them, validating the three stores (ticket 024 FR-4, FR-5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from newsrag import embed
from newsrag.embed import index
from newsrag.stores import opensearch, qdrant
from newsrag.warehouse import db as wdb
from newsrag.warehouse import write as wwrite


class UnknownGeneration(ValueError):
    """The generation has no collection in Qdrant or no index in OpenSearch."""


def activate(generation: str) -> None:
    """FR-4: switch the Qdrant alias, the OpenSearch alias and `warehouse.active_generation`
    to the generation, together. The collection and the index must exist."""
    name = embed.collection_name(generation)
    missing = []
    if not qdrant().collection_exists(name):
        missing.append(f"Qdrant collection {name}")
    if not opensearch().indices.exists(index=name):
        missing.append(f"OpenSearch index {name}")
    if missing:
        raise UnknownGeneration(f"unknown generation {generation}: no {' and no '.join(missing)}")
    index.switch_aliases(generation)
    wdb.set_active_generation(generation)


def activate_if_first(generation: str) -> bool:
    """FR-4: the first generation ever written is activated automatically."""
    if wdb.active_generation() is not None:
        return False
    activate(generation)
    return True


@dataclass(frozen=True)
class GenerationInfo:
    generation: str
    qdrant: int
    opensearch: int
    postgres: int
    active: bool


def _total(counts: dict[date, int]) -> int:
    return sum(counts.values())


def collections() -> list[GenerationInfo]:
    """Every generation found in any of the three stores, with its point / document / row counts
    and whether it is the active one (`warehouse.active_generation`)."""
    with wdb.conn() as c:
        pg_generations = [r[0] for r in c.execute("select distinct generation from chunks")]
    names = sorted({*index.qdrant_generations(), *index.os_generations(), *pg_generations})
    active = wdb.active_generation()
    out = []
    for name in names:
        out.append(
            GenerationInfo(
                name,
                _total(index.qdrant_counts(name)),
                _total(index.os_counts(name)),
                _total(wwrite.counts(name)),
                name == active,
            )
        )
    return out


def ledger_index_dates() -> set[date]:
    """Print dates with the `index` stage done in the ledger."""
    from newsrag.ledger import db as ledger_db

    with ledger_db.conn() as c:
        rows = c.execute(
            "select pub_date from date_runs where stage = 'index' and status = 'done'"
        ).fetchall()
    return {r[0] for r in rows}


def validate(generation: str, only: date | None = None) -> list[tuple[date, int, int, int]]:
    """FR-5: (date, Qdrant points, OpenSearch documents, Postgres rows) of every date of the
    generation, over the union of the dates `index` has done and the dates present in any store.
    With `only`, just that date (a date without chunks gives zeros)."""
    q, o, p = (
        index.qdrant_counts(generation),
        index.os_counts(generation),
        wwrite.counts(generation),
    )
    if only is not None:
        days = [only]
    else:
        days = sorted({*ledger_index_dates(), *q, *o, *p})
    return [(day, q.get(day, 0), o.get(day, 0), p.get(day, 0)) for day in days]


def line(result: tuple[date, int, int, int]) -> str:
    """`ok <date> <n>` when the three counts agree, else `MISMATCH <date> q=<n> os=<n> pg=<n>`."""
    day, q, o, p = result
    if q == o == p:
        return f"ok {day.isoformat()} {q}"
    return f"MISMATCH {day.isoformat()} q={q} os={o} pg={p}"
