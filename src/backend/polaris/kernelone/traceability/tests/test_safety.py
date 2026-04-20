"""Tests for traceability safety wrappers.

These tests verify that the safety layer correctly suppresses
exceptions and never propagates them to the caller.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.kernelone.traceability.internal.safety import (
    safe_link,
    safe_persist_matrix,
    safe_register_node,
    safe_reset,
)
from polaris.kernelone.traceability.public.contracts import (
    TraceabilityMatrix,
    TraceLink,
    TraceNode,
)


class TestSafeRegisterNode:
    """Tests for safe_register_node."""

    def test_returns_node_on_success(self) -> None:
        svc = MagicMock()
        expected = TraceNode(
            node_id="n1",
            node_kind="task",
            role="pm",
            external_id="T-1",
            content_hash="h1",
            timestamp_ms=1,
        )
        svc.register_node.return_value = expected
        result = safe_register_node(svc, node_kind="task", role="pm", external_id="T-1", content="c")
        assert result == expected
        svc.register_node.assert_called_once()

    def test_returns_none_when_service_is_none(self) -> None:
        result = safe_register_node(None, node_kind="task", role="pm", external_id="T-1", content="c")
        assert result is None

    def test_returns_none_on_exception(self) -> None:
        svc = MagicMock()
        svc.register_node.side_effect = RuntimeError("disk full")
        result = safe_register_node(svc, node_kind="task", role="pm", external_id="T-1", content="c")
        assert result is None


class TestSafeLink:
    """Tests for safe_link."""

    def test_returns_link_on_success(self) -> None:
        svc = MagicMock()
        expected = TraceLink(
            link_id="l1", source_node_id="n1", target_node_id="n2", link_kind="derives_from", timestamp_ms=1
        )
        svc.link.return_value = expected
        source = MagicMock(spec=TraceNode, external_id="S")
        target = MagicMock(spec=TraceNode, external_id="T")
        result = safe_link(svc, source, target)
        assert result == expected

    def test_returns_none_when_any_argument_is_none(self) -> None:
        source = MagicMock(spec=TraceNode)
        target = MagicMock(spec=TraceNode)
        assert safe_link(None, source, target) is None
        assert safe_link(MagicMock(), None, target) is None
        assert safe_link(MagicMock(), source, None) is None

    def test_returns_none_on_exception(self) -> None:
        svc = MagicMock()
        svc.link.side_effect = ValueError("bad node")
        source = MagicMock(spec=TraceNode, external_id="S")
        target = MagicMock(spec=TraceNode, external_id="T")
        result = safe_link(svc, source, target)
        assert result is None


class TestSafePersistMatrix:
    """Tests for safe_persist_matrix."""

    def test_returns_true_on_success(self) -> None:
        svc = MagicMock()
        matrix = MagicMock(spec=TraceabilityMatrix)
        result = safe_persist_matrix(svc, matrix, "/tmp/matrix.json")
        assert result is True
        svc.persist.assert_called_once_with(matrix, "/tmp/matrix.json")

    def test_returns_false_when_service_or_matrix_is_none(self) -> None:
        matrix = MagicMock(spec=TraceabilityMatrix)
        assert safe_persist_matrix(None, matrix, "/tmp/matrix.json") is False
        assert safe_persist_matrix(MagicMock(), None, "/tmp/matrix.json") is False

    def test_returns_false_on_exception(self) -> None:
        svc = MagicMock()
        svc.persist.side_effect = OSError("permission denied")
        matrix = MagicMock(spec=TraceabilityMatrix)
        result = safe_persist_matrix(svc, matrix, "/tmp/matrix.json")
        assert result is False


class TestSafeReset:
    """Tests for safe_reset."""

    def test_returns_true_on_success(self) -> None:
        svc = MagicMock()
        assert safe_reset(svc) is True
        svc.reset.assert_called_once()

    def test_returns_false_when_service_is_none(self) -> None:
        assert safe_reset(None) is False

    def test_returns_false_on_exception(self) -> None:
        svc = MagicMock()
        svc.reset.side_effect = Exception("unexpected")
        assert safe_reset(svc) is False
