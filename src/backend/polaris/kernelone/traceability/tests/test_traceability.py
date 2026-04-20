"""Unit tests for the traceability subsystem.

Covers happy path, edge cases, persistence, and matrix querying.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.traceability.public.contracts import (
    TraceabilityMatrix,
    TraceLink,
    TraceNode,
)
from polaris.kernelone.traceability.public.service import create_traceability_service


@pytest.fixture
def service() -> Any:
    """Return a fresh traceability service instance."""
    return create_traceability_service(workspace="/tmp/test_traceability")


class TestRegisterNode:
    """Tests for node registration."""

    def test_register_basic(self, service: Any) -> None:
        node = service.register_node(
            node_kind="task",
            role="pm",
            external_id="T-001",
            content="Task content",
        )
        assert isinstance(node, TraceNode)
        assert node.node_kind == "task"
        assert node.role == "pm"
        assert node.external_id == "T-001"
        assert len(node.content_hash) == 64  # SHA-256 hex length
        assert node.timestamp_ms > 0

    def test_register_with_metadata(self, service: Any) -> None:
        node = service.register_node(
            node_kind="commit",
            role="director",
            external_id="src/foo.py",
            content="print('hello')",
            metadata={"file_path": "src/foo.py", "change_type": "modify"},
        )
        assert node.metadata["file_path"] == "src/foo.py"

    def test_register_empty_content(self, service: Any) -> None:
        node = service.register_node(
            node_kind="doc",
            role="pm",
            external_id="DOC-001",
            content="",
        )
        # SHA-256 of empty string is e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert node.content_hash == expected


class TestLink:
    """Tests for link creation."""

    def test_link_basic(self, service: Any) -> None:
        doc = service.register_node(node_kind="doc", role="pm", external_id="DOC-001", content="doc")
        task = service.register_node(node_kind="task", role="pm", external_id="T-001", content="task")
        link = service.link(doc, task, "derives_from")
        assert isinstance(link, TraceLink)
        assert link.source_node_id == doc.node_id
        assert link.target_node_id == task.node_id
        assert link.link_kind == "derives_from"

    def test_link_default_kind(self, service: Any) -> None:
        a = service.register_node(node_kind="a", role="x", external_id="A", content="a")
        b = service.register_node(node_kind="b", role="x", external_id="B", content="b")
        link = service.link(a, b)
        assert link.link_kind == "derives_from"


class TestBuildMatrix:
    """Tests for matrix assembly."""

    def test_build_matrix_empty(self, service: Any) -> None:
        matrix = service.build_matrix(run_id="run-001", iteration=3)
        assert isinstance(matrix, TraceabilityMatrix)
        assert matrix.run_id == "run-001"
        assert matrix.iteration == 3
        assert matrix.nodes == ()
        assert matrix.links == ()

    def test_build_matrix_with_content(self, service: Any) -> None:
        doc = service.register_node(node_kind="doc", role="pm", external_id="DOC-001", content="doc")
        task = service.register_node(node_kind="task", role="pm", external_id="T-001", content="task")
        service.link(doc, task, "derives_from")
        matrix = service.build_matrix(run_id="run-002", iteration=1)
        assert len(matrix.nodes) == 2
        assert len(matrix.links) == 1


class TestMatrixQueries:
    """Tests for matrix query methods."""

    def test_query_by_kind(self, service: Any) -> None:
        service.register_node(node_kind="doc", role="pm", external_id="D1", content="d1")
        service.register_node(node_kind="task", role="pm", external_id="T1", content="t1")
        service.register_node(node_kind="task", role="pm", external_id="T2", content="t2")
        matrix = service.build_matrix("run", 1)
        tasks = matrix.query_by_kind("task")
        assert len(tasks) == 2
        assert all(n.node_kind == "task" for n in tasks)

    def test_query_ancestors(self, service: Any) -> None:
        doc = service.register_node(node_kind="doc", role="pm", external_id="D1", content="d1")
        task = service.register_node(node_kind="task", role="pm", external_id="T1", content="t1")
        bp = service.register_node(node_kind="blueprint", role="ce", external_id="BP1", content="bp1")
        service.link(doc, task, "derives_from")
        service.link(task, bp, "implements")
        matrix = service.build_matrix("run", 1)
        ancestors = matrix.query_ancestors(bp.node_id)
        assert len(ancestors) == 2
        assert {a.node_kind for a in ancestors} == {"doc", "task"}

    def test_query_ancestors_empty_for_root(self, service: Any) -> None:
        node = service.register_node(node_kind="doc", role="pm", external_id="D1", content="d1")
        matrix = service.build_matrix("run", 1)
        assert matrix.query_ancestors(node.node_id) == []


class TestPersistAndLoad:
    """Tests for atomic JSON persistence."""

    def test_persist_creates_file(self, service: Any) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "traceability", "run.001.matrix.json")
            service.register_node(node_kind="doc", role="pm", external_id="D1", content="d1")
            matrix = service.build_matrix("run-001", 1)
            service.persist(matrix, path)
            assert os.path.isfile(path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            assert data["run_id"] == "run-001"
            assert data["iteration"] == 1
            assert len(data["nodes"]) == 1

    def test_persist_is_atomic(self, service: Any) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "matrix.json")
            matrix = service.build_matrix("run", 1)
            service.persist(matrix, path)
            # Ensure no .tmp file is left behind
            tmp_path = Path(str(path) + ".tmp")
            assert not tmp_path.exists()


class TestReset:
    """Tests for reset behaviour."""

    def test_reset_clears_state(self, service: Any) -> None:
        service.register_node(node_kind="doc", role="pm", external_id="D1", content="d1")
        service.register_node(node_kind="task", role="pm", external_id="T1", content="t1")
        service.reset()
        matrix = service.build_matrix("run", 1)
        assert matrix.nodes == ()
        assert matrix.links == ()

    def test_reset_after_build(self, service: Any) -> None:
        service.register_node(node_kind="doc", role="pm", external_id="D1", content="d1")
        matrix_before = service.build_matrix("run", 1)
        service.reset()
        service.register_node(node_kind="task", role="pm", external_id="T1", content="t1")
        matrix_after = service.build_matrix("run", 2)
        assert len(matrix_before.nodes) == 1
        assert len(matrix_after.nodes) == 1
        assert matrix_after.nodes[0].node_kind == "task"


class TestToDict:
    """Tests for dictionary serialization."""

    def test_matrix_to_dict_structure(self, service: Any) -> None:
        node = service.register_node(node_kind="doc", role="pm", external_id="D1", content="d1", metadata={"k": "v"})
        service.link(node, node, "evolves_from")
        matrix = service.build_matrix("run", 1)
        d = matrix.to_dict()
        assert set(d.keys()) == {
            "matrix_id",
            "run_id",
            "iteration",
            "nodes",
            "links",
            "created_at_ms",
        }
        assert d["run_id"] == "run"
        assert d["iteration"] == 1
        assert isinstance(d["nodes"], list)
        assert isinstance(d["links"], list)
        assert d["nodes"][0]["kind"] == "doc"
        assert d["links"][0]["kind"] == "evolves_from"
