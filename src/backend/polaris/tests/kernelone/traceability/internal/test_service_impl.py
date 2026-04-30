"""Tests for polaris.kernelone.traceability.internal.service_impl module.

Covers all 9 public methods with in-memory testing and mocked persistence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.kernelone.traceability.internal.service_impl import TraceabilityServiceImpl
from polaris.kernelone.traceability.public.contracts import TraceabilityMatrix, TraceLink, TraceNode

# -----------------------------------------------------------------------------
# Initialization
# -----------------------------------------------------------------------------


def test_init_sets_workspace() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    assert svc._workspace == "/tmp/ws"
    assert svc._nodes == []
    assert svc._links == []


# -----------------------------------------------------------------------------
# register_node
# -----------------------------------------------------------------------------


def test_register_node_returns_trace_node() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    node = svc.register_node(
        node_kind="doc",
        role="pm",
        external_id="doc-1",
        content="hello world",
    )
    assert isinstance(node, TraceNode)
    assert node.node_kind == "doc"
    assert node.role == "pm"
    assert node.external_id == "doc-1"


def test_register_node_computes_hash() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    node = svc.register_node(
        node_kind="task",
        role="director",
        external_id="t1",
        content="content-a",
    )
    assert len(node.content_hash) == 64  # SHA-256 hex
    assert node.content_hash != "content-a"


def test_register_node_stores_in_list() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    node = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="x")
    assert svc._nodes == [node]


def test_register_node_with_metadata() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    meta: dict[str, Any] = {"priority": "high", "tags": ["a", "b"]}
    node = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="x", metadata=meta)
    assert node.metadata == meta


def test_register_node_default_empty_metadata() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    node = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="x")
    assert node.metadata == {}


def test_register_node_unique_ids() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    n1 = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    n2 = svc.register_node(node_kind="doc", role="pm", external_id="d2", content="b")
    assert n1.node_id != n2.node_id


# -----------------------------------------------------------------------------
# link
# -----------------------------------------------------------------------------


def test_link_creates_trace_link() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    src = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    tgt = svc.register_node(node_kind="task", role="director", external_id="t1", content="b")
    link = svc.link(src, tgt)
    assert isinstance(link, TraceLink)
    assert link.source_node_id == src.node_id
    assert link.target_node_id == tgt.node_id
    assert link.link_kind == "derives_from"


def test_link_custom_kind() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    src = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    tgt = svc.register_node(node_kind="task", role="director", external_id="t1", content="b")
    link = svc.link(src, tgt, link_kind="implements")
    assert link.link_kind == "implements"


def test_link_stores_in_list() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    src = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    tgt = svc.register_node(node_kind="task", role="director", external_id="t1", content="b")
    link = svc.link(src, tgt)
    assert svc._links == [link]


def test_link_unique_ids() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    src = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    tgt = svc.register_node(node_kind="task", role="director", external_id="t1", content="b")
    l1 = svc.link(src, tgt)
    l2 = svc.link(tgt, src)
    assert l1.link_id != l2.link_id


# -----------------------------------------------------------------------------
# find_node
# -----------------------------------------------------------------------------


def test_find_node_existing() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    node = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    found = svc.find_node("d1", "doc")
    assert found == node


def test_find_node_not_found() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    assert svc.find_node("d2", "doc") is None


def test_find_node_wrong_kind() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    assert svc.find_node("d1", "task") is None


def test_find_node_strips_whitespace() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    node = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    found = svc.find_node("  d1  ", "doc")
    assert found == node


def test_find_node_empty_external_id() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    assert svc.find_node("", "doc") is None


# -----------------------------------------------------------------------------
# list_nodes / list_links
# -----------------------------------------------------------------------------


def test_list_nodes_shallow_copy() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    node = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    nodes = svc.list_nodes()
    assert nodes == [node]
    nodes.clear()
    assert len(svc._nodes) == 1  # original unaffected


def test_list_links_shallow_copy() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    src = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    tgt = svc.register_node(node_kind="task", role="director", external_id="t1", content="b")
    link = svc.link(src, tgt)
    links = svc.list_links()
    assert links == [link]
    links.clear()
    assert len(svc._links) == 1


def test_list_nodes_empty() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    assert svc.list_nodes() == []


def test_list_links_empty() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    assert svc.list_links() == []


# -----------------------------------------------------------------------------
# build_matrix
# -----------------------------------------------------------------------------


def test_build_matrix_returns_traceability_matrix() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    matrix = svc.build_matrix(run_id="run-1", iteration=1)
    assert isinstance(matrix, TraceabilityMatrix)
    assert matrix.run_id == "run-1"
    assert matrix.iteration == 1
    assert len(matrix.nodes) == 1


def test_build_matrix_immutable_tuples() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    matrix = svc.build_matrix(run_id="run-1", iteration=1)
    assert isinstance(matrix.nodes, tuple)
    assert isinstance(matrix.links, tuple)


def test_build_matrix_includes_links() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    src = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    tgt = svc.register_node(node_kind="task", role="director", external_id="t1", content="b")
    svc.link(src, tgt)
    matrix = svc.build_matrix(run_id="run-1", iteration=1)
    assert len(matrix.links) == 1


def test_build_matrix_unique_matrix_id() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    m1 = svc.build_matrix(run_id="run-1", iteration=1)
    m2 = svc.build_matrix(run_id="run-1", iteration=1)
    assert m1.matrix_id != m2.matrix_id


# -----------------------------------------------------------------------------
# persist
# -----------------------------------------------------------------------------


def test_persist_writes_json(tmp_path: Path) -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    matrix = svc.build_matrix(run_id="run-1", iteration=1)
    path = str(tmp_path / "matrix.json")
    svc.persist(matrix, path)
    assert Path(path).exists()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["run_id"] == "run-1"
    assert data["iteration"] == 1
    assert len(data["nodes"]) == 1


def test_persist_creates_parent_dirs(tmp_path: Path) -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    matrix = svc.build_matrix(run_id="run-1", iteration=1)
    path = str(tmp_path / "nested" / "dir" / "matrix.json")
    svc.persist(matrix, path)
    assert Path(path).exists()


def test_persist_uses_utf8(tmp_path: Path) -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    svc.register_node(
        node_kind="doc",
        role="pm",
        external_id="d1",
        content="x",
        metadata={"note": "café"},
    )
    matrix = svc.build_matrix(run_id="run-1", iteration=1)
    path = str(tmp_path / "matrix.json")
    svc.persist(matrix, path)
    text = Path(path).read_text(encoding="utf-8")
    assert "café" in text


# -----------------------------------------------------------------------------
# reset
# -----------------------------------------------------------------------------


def test_reset_clears_nodes_and_links() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    src = svc.register_node(node_kind="doc", role="pm", external_id="d1", content="a")
    tgt = svc.register_node(node_kind="task", role="director", external_id="t1", content="b")
    svc.link(src, tgt)
    assert len(svc._nodes) == 2
    assert len(svc._links) == 1
    svc.reset()
    assert svc._nodes == []
    assert svc._links == []


def test_reset_idempotent() -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    svc.reset()
    svc.reset()
    assert svc._nodes == []
    assert svc._links == []


# -----------------------------------------------------------------------------
# Integration — graph workflow
# -----------------------------------------------------------------------------


def test_full_traceability_workflow(tmp_path: Path) -> None:
    svc = TraceabilityServiceImpl("/tmp/ws")
    # PM creates a doc
    doc = svc.register_node(node_kind="doc", role="pm", external_id="bp-1", content="blueprint")
    # CE refines it
    refined = svc.register_node(node_kind="doc", role="chief_engineer", external_id="bp-1-v2", content="refined")
    svc.link(doc, refined, link_kind="evolves_from")
    # Director implements a task
    task = svc.register_node(node_kind="task", role="director", external_id="task-1", content="implement")
    svc.link(refined, task, link_kind="derives_from")
    # QA verifies
    verdict = svc.register_node(node_kind="qa_verdict", role="qa", external_id="v-1", content="pass")
    svc.link(task, verdict, link_kind="verifies")

    assert len(svc.list_nodes()) == 4
    assert len(svc.list_links()) == 3

    matrix = svc.build_matrix(run_id="run-1", iteration=2)
    assert matrix.query_by_kind("doc") == [doc, refined]
    assert matrix.query_by_kind("qa_verdict") == [verdict]

    # Verify ancestors
    ancestors = matrix.query_ancestors(verdict.node_id)
    ancestor_ids = {n.node_id for n in ancestors}
    assert task.node_id in ancestor_ids
    assert refined.node_id in ancestor_ids
    assert doc.node_id in ancestor_ids

    path = str(tmp_path / "workflow.json")
    svc.persist(matrix, path)
    assert Path(path).exists()

    svc.reset()
    assert len(svc.list_nodes()) == 0
