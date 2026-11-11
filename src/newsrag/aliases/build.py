"""`newsrag aliases build` (ticket 022): names, candidate pairs, model verdicts, groups, ids."""

from __future__ import annotations

import contextlib
import csv
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from newsrag import config
from newsrag.aliases import judge, rules, version_dir
from newsrag.aliases.group import IdStore, Overrides, group_names, make_entities
from newsrag.aliases.judge import Decide, Verdict, Verdicts
from newsrag.aliases.names import NameInfo, collect, examples

REVIEW_HEADER = ("name_a", "name_b", "type", "confidence", "rule", "example_a", "example_b")
DeciderFactory = Callable[[], AbstractContextManager[Decide]]


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


@dataclass
class BuildResult:
    entities: dict[str, Any]
    stats: dict[str, Any]
    review_rows: list[dict[str, Any]]
    asked: int  # pairs sent to the model in this build
    paths: dict[str, Path]


def load_inputs(
    pub_date: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """(the done `enrich_core` artifacts, `{story_id: text}` of the stories that name something).

    The texts come from the dates' done `dedup` artifacts; a date without one has no examples."""
    from newsrag import enrich

    artifacts, wanted = [], set()
    paths = enrich.done_artifacts("enrich_core", pub_date)
    for day, path in paths.items():
        art = enrich.read_artifact(path)
        art.setdefault("pub_date", day)
        artifacts.append(art)
        for story_id, record in (art.get("stories") or {}).items():
            if record.get("people") or record.get("organisations"):
                wanted.add(story_id)
    texts: dict[str, str] = {}
    if wanted:
        from newsrag.dedup import load

        done = load.done_dedup()
        for day in paths:
            if day not in done:
                continue
            for story in load.load_dedup(done[day]).get("stories") or []:
                if story["story_id"] in wanted and story.get("text"):
                    texts.setdefault(story["story_id"], story["text"])
    return artifacts, texts


@contextlib.contextmanager
def model_decider(profile: str | None = None) -> Iterator[Decide]:
    """Load the model (`enrich_model` by default) on the GPU and give a `Decide` that asks it."""
    from newsrag import models

    with models.use(profile or config.settings.enrich_model) as client:
        yield judge.decide_with(client)


def ask_all(
    names: Mapping[str, NameInfo],
    texts: Mapping[str, str],
    pairs: Sequence[rules.Pair],
    verdicts: Verdicts,
    decide: Decide,
) -> None:
    """Ask `decide` about each pair (parallel) and record the verdicts."""
    s = config.settings
    cache: dict[str, list[str]] = {}

    def examples_of(nk: str) -> list[str]:
        if nk not in cache:
            cache[nk] = examples(names[nk], texts, s.alias_examples, s.alias_example_chars)
        return cache[nk]

    stop = threading.Event()  # a transport failure: the server is down, do not try the rest

    def one(pair: rules.Pair) -> None:
        if stop.is_set():
            return
        a, b = names[pair[0]], names[pair[1]]
        try:
            verdict = decide(
                a.type, a.display, b.display, examples_of(a.name_key), examples_of(b.name_key)
            )
        except Exception:
            stop.set()
            raise
        verdicts.add(a.type, a.key, b.key, verdict)

    with ThreadPoolExecutor(max_workers=max(1, s.alias_concurrency)) as pool:
        list(pool.map(one, pairs))


def run_build(
    artifacts: Sequence[Mapping[str, Any]],
    texts: Mapping[str, str],
    *,
    id_store: IdStore,
    overrides: Overrides,
    out_dir: Path,
    review_path: Path,
    decider: DeciderFactory = model_decider,
    warn: Callable[[str], None] = eprint,
    log: Callable[[str], None] = eprint,
) -> BuildResult:
    """Build the alias table from `enrich_core` artifacts. `decider()` is a context manager that
    gives the `Decide` function; it is entered only when there are pairs to ask about."""
    from newsrag.stages.base import write_json_atomic

    s = config.settings
    names = collect(artifacts)
    candidates = rules.candidate_pairs(names, s.alias_jw_threshold, s.alias_min_shared_stories)
    ordered = sorted(candidates, key=lambda p: rules.priority(p, names))
    verdicts = Verdicts(out_dir / "verdicts.jsonl")

    def cached(pair: rules.Pair) -> Verdict | None:
        return verdicts.get(names[pair[0]].type, names[pair[0]].key, names[pair[1]].key)

    asked = 0
    pending = [p for p in ordered if cached(p) is None]
    log(f"aliases: names={len(names)} candidates={len(candidates)} to ask={len(pending)}")
    if pending:
        with decider() as decide:
            while pending:  # a batch of at most `alias_max_llm_pairs`; loop until none is left
                batch = pending[: max(1, s.alias_max_llm_pairs)]
                ask_all(names, texts, batch, verdicts, decide)
                asked += len(batch)
                log(f"aliases: asked {asked} pairs")
                pending = [p for p in ordered if cached(p) is None]

    # Classify: auto-apply only `same` + confidence + a spelling rule (A-47).
    applied: list[tuple[float, str, str]] = []
    wanted: list[tuple[rules.Pair, Verdict]] = []  # same: true (or no valid reply) but not applied
    for pair in ordered:
        verdict = cached(pair)
        assert verdict is not None
        if verdict.same is False:
            continue
        auto = (
            verdict.same is True
            and (verdict.confidence or 0) >= s.alias_auto_confidence
            and candidates[pair] in rules.AUTO_RULES
        )
        if auto:
            applied.append((verdict.confidence or 0.0, pair[0], pair[1]))
        else:
            wanted.append((pair, verdict))
    final, auto_groups = group_names(names, applied, overrides, warn)
    auto_pairs = sum(1 for _, a, b in applied if final.same(a, b))

    # Review rows: not applied, and the names are still apart.
    example_cache: dict[str, str] = {}

    def first_example(nk: str) -> str:
        if nk not in example_cache:
            found = examples(names[nk], texts, 1, s.alias_example_chars)
            example_cache[nk] = found[0] if found else ""
        return example_cache[nk]

    review_rows = []
    for pair, verdict in wanted:
        a, b = pair
        if final.same(a, b) or final.blocked(a, b):
            continue
        review_rows.append(
            {
                "name_a": names[a].display,
                "name_b": names[b].display,
                "type": names[a].type,
                "confidence": "" if verdict.confidence is None else f"{verdict.confidence:g}",
                "rule": candidates[pair],
                "example_a": first_example(a),
                "example_b": first_example(b),
            }
        )
    review_rows.sort(
        key=lambda r: (
            r["type"],
            -(float(r["confidence"]) if r["confidence"] else -1),
            r["name_a"],
            r["name_b"],
        )
    )

    entities, _ = make_entities(final, names, id_store)
    largest = max((len(e["aliases"]) for e in entities["entities"]), default=0)
    stats = {
        "names": len(names),
        "entities": len(entities["entities"]),
        "auto_pairs": auto_pairs,
        "review_pairs": len(review_rows),
        "largest_group": largest,
        "auto_groups": auto_groups,
    }
    paths = {
        "entities": write_json_atomic(out_dir / "entities.json", entities),
        "stats": write_json_atomic(out_dir / "stats.json", stats),
        "review": write_review(review_path, review_rows),
        "verdicts": verdicts.path,
    }
    return BuildResult(entities, stats, review_rows, asked, paths)


def write_review(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """The review CSV, always with the header (even with no rows)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)
    return path


def build(
    *,
    id_store: IdStore | None = None,
    decider: DeciderFactory = model_decider,
    pub_date: str | None = None,
) -> BuildResult:
    """`aliases build` on the real data (done artifacts, ledger ids, the committed overrides)."""
    from newsrag.aliases.group import LedgerIdStore, load_overrides
    from newsrag.ledger import db

    s = config.settings
    artifacts, texts = load_inputs(pub_date)
    if not artifacts:
        raise LookupError("no done enrich_core artifact")
    db.migrate()
    return run_build(
        artifacts,
        texts,
        id_store=id_store or LedgerIdStore(),
        overrides=load_overrides(s.alias_overrides_file, eprint),
        out_dir=version_dir(),
        review_path=s.alias_review_file,
        decider=decider,
    )
