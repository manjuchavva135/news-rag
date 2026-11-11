"""Embedding model bake-off (ticket 026): the embed model is chosen by our own retrieval test.

The corpus is every row of `warehouse.chunks` of the active generation. Each candidate, one at a
time inside `models.use(candidate)`:

1. embeds `doc_prefix + prefix + "\\n" + text` of every corpus chunk (the profile adds
   `doc_prefix`) into the Qdrant collection `<store_alias_prefix>bakeoff__<candidate>`, with the
   point ids and payloads copied from the active generation's points (only the vectors are new);
2. runs the retrieval test (ticket 025) on the questions whose gold story is in the corpus, with
   the dense side from that collection and the query embedded by the candidate. BM25 and the
   chunk texts come from the active generation.

The report `<reports_dir>/embed-bakeoff.md` names the winner; `apply` writes it as `embed_model`
in `pipeline.yaml`. The active collection and alias are only read, never changed.

Importing this module touches neither the stores nor the GPU.
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from newsrag import config, embed

BASE = "bakeoff__"
WINNER_LINE = re.compile(r"^winner: (\S+)\s*$", re.MULTILINE)
COLUMNS = ("candidate", "recall@10", "mrr", "recall@10 te", "recall@10 en", "embed seconds")

Echo = Callable[[str], None]


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class BakeoffError(RuntimeError):
    """The bake-off cannot run (or `--apply` has nothing to apply); the CLI exits 1."""


class CorpusTooSmall(BakeoffError):
    """NFR-1: fewer test questions than `embed_bakeoff_min_questions` have their gold story in
    the corpus."""

    def __init__(self, n: int) -> None:
        super().__init__(f"bake-off corpus too small: {n} questions")
        self.n = n


@dataclass
class Row:
    """One candidate's line of the report; `error` set when it failed."""

    candidate: str
    dim: int
    recall: float = 0.0
    mrr: float = 0.0
    recall_te: float | None = None
    recall_en: float | None = None
    embed_seconds: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# --- names ------------------------------------------------------------------------------------


def collection_prefix() -> str:
    return f"{config.settings.store_alias_prefix}{BASE}"


def collection_for(candidate: str) -> str:
    """The Qdrant collection a candidate's vectors go to: `bakeoff__<candidate>`."""
    return f"{collection_prefix()}{candidate}"


# --- the corpus and the questions -----------------------------------------------------------


def active_generation() -> str:
    from newsrag.warehouse import db as wdb

    generation = wdb.active_generation()
    if generation is None:
        raise BakeoffError("no active generation: run 'newsrag stage run index' first")
    return generation


def load_corpus(generation: str) -> list[dict[str, Any]]:
    """Every chunk of the generation in `warehouse.chunks`: chunk_id, story_id, prefix, text."""
    from newsrag.warehouse import db as wdb

    with wdb.conn() as c:
        return wdb.fetch_all(
            c,
            "select chunk_id, story_id, prefix, text from chunks where generation = %s "
            "order by chunk_id",
            (generation,),
        )


def corpus_questions(
    questions: Sequence[Mapping[str, Any]], corpus: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The questions whose gold `story_id` appears among the corpus chunks."""
    stories = {str(c["story_id"]) for c in corpus}
    return [dict(q) for q in questions if str(q.get("story_id")) in stories]


def check_size(n_questions: int) -> None:
    if n_questions < config.settings.embed_bakeoff_min_questions:
        raise CorpusTooSmall(n_questions)


def doc_texts(chunks: Sequence[Mapping[str, Any]]) -> list[str]:
    """What is embedded per chunk, as in stage `index`: `prefix + "\\n" + text`."""
    return [f"{c['prefix'] or ''}\n{c['text'] or ''}" for c in chunks]


# --- Qdrant -----------------------------------------------------------------------------------


def load_payloads(generation: str) -> dict[str, dict[str, Any]]:
    """point id -> payload of every point of the generation's collection (read-only scroll)."""
    from newsrag.stores import qdrant

    name = embed.collection_name(generation)
    client = qdrant()
    payloads: dict[str, dict[str, Any]] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            name,
            limit=max(1, config.settings.index_write_batch),
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payloads[str(point.id)] = dict(point.payload or {})
        if offset is None:
            return payloads


def build_points(
    chunks: Sequence[Mapping[str, Any]],
    vectors: Sequence[Sequence[float]],
    payloads: Mapping[str, Mapping[str, Any]],
) -> list[tuple[str, list[float], dict[str, Any]]]:
    """(point id, vector, payload) per chunk: the id and payload of the active generation's point
    for the chunk, the new vector."""
    from newsrag.embed import records

    out = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        pid = records.point_id(str(chunk["chunk_id"]))
        payload = payloads.get(pid)
        if payload is None:
            raise BakeoffError(
                f"chunk {chunk['chunk_id']} has no point in the active collection: "
                "run 'newsrag index validate'"
            )
        out.append((pid, [float(x) for x in vector], dict(payload)))
    return out


def recreate_collection(name: str, dim: int) -> None:
    """Drop the collection when it exists and create it empty (cosine, payload indexes)."""
    from newsrag.embed import index
    from newsrag.stores import qdrant

    if qdrant().collection_exists(name):
        qdrant().delete_collection(name)
    index.ensure_named_collection(name, dim)


def write_points(name: str, points: Sequence[tuple[str, list[float], dict[str, Any]]]) -> None:
    from qdrant_client import models as qm

    from newsrag.stores import qdrant

    size = max(1, config.settings.index_write_batch)
    for start in range(0, len(points), size):
        batch = [
            qm.PointStruct(id=pid, vector=vector, payload=payload)
            for pid, vector, payload in points[start : start + size]
        ]
        qdrant().upsert(name, points=batch, wait=True)


def bakeoff_collections() -> list[str]:
    from newsrag.stores import qdrant

    prefix = collection_prefix()
    return sorted(
        c.name for c in qdrant().get_collections().collections if c.name.startswith(prefix)
    )


# --- one candidate ----------------------------------------------------------------------------


def embed_corpus(
    embedder: Any,
    corpus: Sequence[Mapping[str, Any]],
    payloads: Mapping[str, Mapping[str, Any]],
    collection: str,
    echo: Echo = _stderr,
) -> float:
    """Embed the corpus into a fresh `collection`; returns the seconds spent embedding (the
    writes are not counted)."""
    s = config.settings
    dim = int(embedder.dim)
    recreate_collection(collection, dim)
    seconds = 0.0
    slab = max(1, s.embed_bakeoff_slab)
    for start in range(0, len(corpus), slab):
        chunks = corpus[start : start + slab]
        began = time.monotonic()
        vectors = embedder.embed(doc_texts(chunks), "doc", s.embed_batch_size)
        seconds += time.monotonic() - began
        if len(vectors) and vectors.shape[1] != dim:
            raise ValueError(f"embedder gave {vectors.shape[1]} dimensions, expected {dim}")
        write_points(collection, build_points(chunks, vectors, payloads))
        echo(f"  embedded {min(start + slab, len(corpus))}/{len(corpus)} chunks")
    return seconds


def evaluate(candidate: str, collection: str, questions: Sequence[Mapping[str, Any]]) -> dict:
    """Ticket 025's retrieval test with the dense side from `collection` and the query embedded
    by `candidate` (inside the caller's `use(candidate)`, so the open embedder is reused)."""
    from newsrag.evals import retrieval

    out = config.settings.embed_bakeoff_dir / f"retrieval-{candidate}.json"
    return retrieval.run_retrieval(
        questions=questions,
        qdrant_collection=collection,
        embed_profile=candidate,
        out=out,
    )


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {text[0] if text else ''}".rstrip(": ")


def run_candidate(
    candidate: str,
    corpus: Sequence[Mapping[str, Any]],
    payloads: Mapping[str, Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]],
    echo: Echo = _stderr,
) -> Row:
    """Embed, index and score one candidate; any failure gives a `failed` row."""
    from newsrag import models, search
    from newsrag.models import get_profile

    dim = int(get_profile(candidate).dim or 0)
    collection = collection_for(candidate)
    search.close()  # a query model left open by an earlier search would hold the GPU
    try:
        with models.use(candidate) as embedder:  # download and load: not timed
            embedder.embed(["warm-up"], "doc", 1)  # CUDA set-up: not timed either
            echo(f"{candidate}: embedding {len(corpus)} chunks into {collection}")
            seconds = embed_corpus(embedder, corpus, payloads, collection, echo)
            echo(f"{candidate}: searching {len(questions)} questions")
            report = evaluate(candidate, collection, questions)
    except Exception as exc:  # noqa: BLE001 - download, OOM, unsupported: reported `failed`
        echo(f"{candidate}: failed ({_first_line(exc)})")
        return Row(candidate, dim, error=_first_line(exc))
    by_lang = report.get("by_lang") or {}

    def lang_recall(lang: str) -> float | None:
        m = by_lang.get(lang) or {}
        return m.get("recall_at_10") if m.get("n") else None

    row = Row(
        candidate,
        dim,
        recall=float(report["recall_at_10"]),
        mrr=float(report["mrr"]),
        recall_te=lang_recall("te"),
        recall_en=lang_recall("en"),
        embed_seconds=round(seconds, 1),
    )
    echo(f"{candidate}: recall@10={row.recall:.4f} mrr={row.mrr:.4f} embed={seconds:.1f}s")
    return row


# --- the winner and the report ----------------------------------------------------------------


def pick_winner(rows: Sequence[Row], tie: float | None = None) -> str | None:
    """FR-3: the candidates within `tie` of the best recall@10; of those, the ones within `tie`
    of their best MRR; then the smaller dim; then the order of `rows` (the `--candidates`
    order). None when no candidate finished."""
    tie = config.settings.embed_bakeoff_tie if tie is None else tie
    eps = 1e-9  # 0.995 - 0.985 is not exactly 0.01 in floating point
    order = {row.candidate: i for i, row in enumerate(rows)}
    ok = [r for r in rows if r.ok]
    if not ok:
        return None
    best_recall = max(r.recall for r in ok)
    tied = [r for r in ok if best_recall - r.recall <= tie + eps]
    best_mrr = max(r.mrr for r in tied)
    tied = [r for r in tied if best_mrr - r.mrr <= tie + eps]
    return min(tied, key=lambda r: (r.dim, order[r.candidate])).candidate


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def render_report(
    rows: Sequence[Row], n_questions: int, winner: str | None, generation: str, n_chunks: int
) -> str:
    lines = [
        "# Embedding model bake-off (ticket 026)",
        "",
        f"questions: {n_questions}",
        f"corpus: {n_chunks} chunks of generation {generation}",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "|".join("---" for _ in COLUMNS) + "|",
    ]
    for r in rows:
        if r.ok:
            cells = [
                r.candidate,
                _num(r.recall),
                _num(r.mrr),
                _num(r.recall_te),
                _num(r.recall_en),
                f"{r.embed_seconds:.1f}",
            ]
        else:
            cells = [r.candidate, "failed", "-", "-", "-", "-"]
        lines.append("| " + " | ".join(cells) + " |")
    failed = [r for r in rows if not r.ok]
    if failed:
        lines.append("")
        lines.extend(f"failed {r.candidate}: {r.error}" for r in failed)
    lines += ["", f"winner: {winner or 'none'}", ""]
    return "\n".join(lines)


def check_candidates(candidates: Sequence[str]) -> list[str]:
    """The candidates, each an `embed` profile of models.yaml (else BakeoffError)."""
    from newsrag.models import get_profile

    names = [c.strip() for c in candidates if c.strip()]
    if not names:
        raise BakeoffError("no candidates")
    if len(set(names)) != len(names):
        raise BakeoffError(f"a candidate is listed twice: {', '.join(names)}")
    for name in names:
        try:
            profile = get_profile(name)
        except KeyError as exc:
            raise BakeoffError(str(exc)) from None
        if profile.kind != "embed":
            raise BakeoffError(f"{name} is a {profile.kind} profile, not an embed profile")
    return names


@dataclass
class Outcome:
    rows: list[Row]
    winner: str | None
    n_questions: int
    report_file: Path


def run_bakeoff(candidates: Sequence[str] | None = None, echo: Echo = _stderr) -> Outcome:
    """FR-2, FR-3: score every candidate and write the report. The corpus-size check runs before
    anything is embedded."""
    from newsrag.evals import retrieval

    s = config.settings
    names = check_candidates(candidates or s.embed_bakeoff_candidates)
    generation = active_generation()
    corpus = load_corpus(generation)
    questions = corpus_questions(retrieval.read_set(s.retrieval_set_file), corpus)
    check_size(len(questions))
    payloads = load_payloads(generation)
    echo(f"corpus: {len(corpus)} chunks of {generation}; {len(questions)} questions")

    rows = [run_candidate(name, corpus, payloads, questions, echo) for name in names]
    winner = pick_winner(rows)
    path = Path(s.embed_bakeoff_report_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_report(rows, len(questions), winner, generation, len(corpus)), encoding="utf-8"
    )
    return Outcome(rows, winner, len(questions), path)


# --- --apply and --cleanup --------------------------------------------------------------------


def read_winner(path: Path | None = None) -> str | None:
    """The `winner:` of the report (None for `winner: none`); BakeoffError without a report."""
    path = Path(path) if path is not None else config.settings.embed_bakeoff_report_file
    if not path.is_file():
        raise BakeoffError(f"no bake-off report at {path}: run 'newsrag embed bakeoff' first")
    match = WINNER_LINE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise BakeoffError(f"{path} has no 'winner:' line")
    return None if match.group(1) == "none" else match.group(1)


def apply(path: Path | None = None) -> tuple[str, bool]:
    """FR-4: write the report's winner as `embed_model` in pipeline.yaml. Returns (winner,
    changed). Deletes nothing; the active generation stays as it is."""
    winner = read_winner(path)
    if winner is None:
        raise BakeoffError("the bake-off report has no winner (winner: none): nothing to apply")
    if config.settings.embed_model == winner:
        return winner, False
    config.set_pipeline_key("embed_model", winner)
    return winner, True


def cleanup() -> list[str]:
    """FR-5: delete every `bakeoff__*` collection; returns their names."""
    from newsrag.stores import qdrant

    names = bakeoff_collections()
    for name in names:
        qdrant().delete_collection(name)
    return names
