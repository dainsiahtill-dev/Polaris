"""Exact GR1C dependency-cut regression; global SCC closure belongs to GR1D."""

from __future__ import annotations

from collections import deque
from pathlib import Path

import yaml


def _dependency_path(
    graph: dict[str, tuple[str, ...]],
    start: str,
    target: str,
) -> tuple[str, ...] | None:
    """Return a non-empty dependency path, including for ``start == target``."""
    queue: deque[tuple[str, tuple[str, ...]]] = deque(
        (dependency, (start, dependency)) for dependency in graph.get(start, ())
    )
    visited = {start}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        if node in visited:
            continue
        visited.add(node)
        queue.extend((dependency, (*path, dependency)) for dependency in graph.get(node, ()))
    return None


def _catalog_cells() -> dict[str, dict[str, object]]:
    backend_root = Path(__file__).resolve().parents[5]
    catalog_path = backend_root / "docs" / "graph" / "catalog" / "cells.yaml"
    payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    return {cell["id"]: cell for cell in payload["cells"]}


def test_gr1c_removes_only_the_proven_projection_director_factory_edges() -> None:
    backend_root = Path(__file__).resolve().parents[5]
    cells = _catalog_cells()
    projection_manifest = yaml.safe_load(
        (backend_root / "polaris" / "cells" / "runtime" / "projection" / "cell.yaml").read_text(encoding="utf-8")
    )
    director_manifest = yaml.safe_load(
        (backend_root / "polaris" / "cells" / "director" / "execution" / "cell.yaml").read_text(encoding="utf-8")
    )

    assert "director.execution" not in projection_manifest["depends_on"]
    assert "director.execution" not in cells["runtime.projection"]["depends_on"]
    assert "factory.pipeline" not in director_manifest["depends_on"]
    assert "factory.pipeline" not in cells["director.execution"]["depends_on"]
    assert "factory.cognitive_runtime" in director_manifest["depends_on"]
    assert "factory.cognitive_runtime" in cells["director.execution"]["depends_on"]


def test_director_execution_source_proves_pipeline_edge_is_stale() -> None:
    backend_root = Path(__file__).resolve().parents[5]
    director_root = backend_root / "polaris" / "cells" / "director" / "execution"
    pipeline_offenders: list[Path] = []
    cognitive_runtime_imports: list[Path] = []
    for path in director_root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if "polaris.cells.factory.pipeline" in source:
            pipeline_offenders.append(path.relative_to(backend_root))
        if "polaris.cells.factory.cognitive_runtime" in source:
            cognitive_runtime_imports.append(path.relative_to(backend_root))

    assert pipeline_offenders == []
    assert cognitive_runtime_imports == [Path("polaris/cells/director/execution/service.py")]


def test_reachability_guard_detects_negative_three_edge_fixture_and_self_cycle() -> None:
    graph = {
        "runtime.projection": ("middle.one",),
        "middle.one": ("middle.two",),
        "middle.two": ("factory.pipeline",),
        "factory.pipeline": ("runtime.projection",),
    }

    assert _dependency_path(graph, "runtime.projection", "factory.pipeline") == (
        "runtime.projection",
        "middle.one",
        "middle.two",
        "factory.pipeline",
    )
    assert _dependency_path(graph, "runtime.projection", "runtime.projection") == (
        "runtime.projection",
        "middle.one",
        "middle.two",
        "factory.pipeline",
        "runtime.projection",
    )
