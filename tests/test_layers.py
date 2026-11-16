"""The package layering of `src/newsrag` (architecture review, 2026-11-16).

Every import between two `newsrag` packages, at module level or inside a function, must point to
the same layer or a lower one, and the packages must not import each other in a cycle.
Command-line modules (`cli.py`, `*_cli.py`) are the composition root and may import anything.

Layers, lowest first:

0. foundation: `config`, `core` (helpers shared by stages and domain code)
1. infrastructure: models, stores, ledger, warehouse, reference data
2. domain: the ingestion steps, chunking, embedding and search
3. pipeline framework: `stages` (stage base classes, runner, the stage adapters)
4. tools: orchestrator, evaluation, reports, answers, bake-offs, the dev sample
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "newsrag"

LAYERS: dict[str, int] = {
    "config": 0,
    "core": 0,
    "models": 1,
    "stores": 1,
    "ledger": 1,
    "warehouse": 1,
    "geo": 1,
    "ingest": 2,
    "render": 2,
    "ocr": 2,
    "editions": 2,
    "grouping": 2,
    "stitch": 2,
    "dedup": 2,
    "enrich": 2,
    "aliases": 2,
    "chunking": 2,
    "embed": 2,
    "search": 2,
    "stages": 3,
    "orchestrator": 4,
    "preflight": 4,
    "report": 4,
    "evals": 4,
    "baseline": 4,
    "answer": 4,
    "textmodel": 4,
    "transcribe": 4,
    "sample": 4,
}


def _package(path: Path) -> str:
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _is_cli(path: Path) -> bool:
    return path.name == "cli.py" or path.name.endswith("_cli.py")


def _imported(tree: ast.AST) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            parts = node.module.split(".")
            if parts[0] != "newsrag":
                continue
            if len(parts) > 1:
                out.append((parts[1], node.lineno))
            else:
                out.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "newsrag" and len(parts) > 1:
                    out.append((parts[1], node.lineno))
    return out


def edges() -> dict[tuple[str, str], list[str]]:
    """(importer, imported) -> ["file:line", ...] for every non-CLI module."""
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    for path in sorted(SRC.rglob("*.py")):
        if _is_cli(path) or path.name == "__init__.py" and path.parent == SRC:
            continue
        src = _package(path)
        if src == "cli":
            continue
        for dst, line in _imported(ast.parse(path.read_text(encoding="utf-8"))):
            if dst != src:
                found[(src, dst)].append(f"{path.relative_to(SRC)}:{line}")
    return found


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: list[list[str]] = []

    def visit(v: str) -> None:
        index[v] = low[v] = len(index)
        stack.append(v)
        on.add(v)
        for w in sorted(graph.get(v, ())):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    for v in sorted(graph):
        if v not in index:
            visit(v)
    return out


def test_every_package_has_a_layer():
    packages = {
        _package(p) for p in SRC.rglob("*.py") if not _is_cli(p) and _package(p) != "__init__"
    } - {"cli"}
    assert sorted(packages - LAYERS.keys()) == []


def test_no_import_points_to_a_higher_layer():
    upward = [
        f"{src} (layer {LAYERS[src]}) -> {dst} (layer {LAYERS[dst]}): {', '.join(where)}"
        for (src, dst), where in sorted(edges().items())
        if dst in LAYERS and src in LAYERS and LAYERS[dst] > LAYERS[src]
    ]
    assert upward == []


def test_packages_do_not_import_each_other_in_a_cycle():
    graph: dict[str, set[str]] = defaultdict(set)
    for src, dst in edges():
        graph[src].add(dst)
    assert _cycles(graph) == []
